"""
api/routes/receiver.py
=======================
Handles inbound messages from PI 5.

Both /detection and /greeting receive the same DetectionPayload:
  {
    "timestamp": str,
    "person_id": str,          # e.g. "Palm (Krittin Sakharin)" or "Unknown"
    "is_registered": bool,
    "track_id": int | null,
    "bbox": [x1,y1,x2,y2] | null,
    "stt": {
      "text": str,             # Thai transcribed text
      "confidence": float,
      "language": str,
      "duration": float
    }
  }

/detection  — every detection event; runs full chatbot pipeline if registered + STT confident
/greeting   — first contact with a registered person; fires one-shot greeting + ROS2 stop
/activate   — PI 5 polls to check if robot should be in active (conversation) mode
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, File, Form, Request, UploadFile

from api.routes.monitor import log_event
from api.schemas.receiver import ActivateResponse, DetectionPayload, GreetingPayload, STTResult
from database.mysql_client import fetch_student_by_id, fetch_student_by_nickname

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────────────────────────────
# In-memory state
# ─────────────────────────────────────────────────────────────────────

_active: int = 0
_last_greeting: Dict[str, datetime] = {}     # person_id → last greeted at
_sessions: Dict[str, dict] = {}             # person_id → session state


async def _push_active(state: int, pi5_base_url: str) -> None:
    """Push activation state to PI 5 /set_active (fire-and-forget)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{pi5_base_url}/set_active", json={"active": state})
        logger.debug("Pushed active=%d to PI 5", state)
    except Exception as exc:
        logger.warning("_push_active failed: %s", exc)


def _clean_name(person_id: str) -> str:
    """Extract display name from person_id like 'Palm (Krittin Sakharin)' → 'Palm'."""
    name = person_id.split("(")[0].strip()
    return name if name else person_id


def _get_or_create_session(person_id: str) -> dict:
    if person_id not in _sessions:
        _sessions[person_id] = {
            "session_id":  str(uuid.uuid4()),
            "person_id":   person_id,
            "history":     [],
            "created_at":  datetime.utcnow(),
            "last_active": datetime.utcnow(),
        }
    sess = _sessions[person_id]
    sess["last_active"] = datetime.utcnow()
    return sess


def _cleanup_expired_sessions(timeout_seconds: int) -> None:
    """Remove sessions idle longer than timeout_seconds."""
    now = datetime.utcnow()
    expired = [
        pid for pid, sess in list(_sessions.items())
        if (now - sess["last_active"]).total_seconds() > timeout_seconds
    ]
    for pid in expired:
        sess = _sessions.pop(pid)
        logger.info(
            "Session expired for %s (session_id=%s, %d history turns dropped)",
            pid, sess["session_id"], len(sess["history"]),
        )


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────

@router.post("/greeting")
async def on_greeting(payload: GreetingPayload, request: Request):
    """
    Triggered when a registered person is first identified.
    Rate-limited by greeting_cooldown_seconds per person_id.
    Fires one-shot LLM greeting + ROS2 stop in parallel.
    """
    global _active
    app_state = request.app.state
    cooldown = app_state.settings["session"]["greeting_cooldown_seconds"]

    # Skip unregistered or unknown persons
    if not payload.is_registered or payload.person_id == "Unknown":
        logger.info("Greeting skipped for unregistered/unknown: %s", payload.person_id)
        return {"status": "skipped"}

    last = _last_greeting.get(payload.person_id)
    if last and (datetime.utcnow() - last).total_seconds() < cooldown:
        logger.info("Greeting skipped for %s (cooldown)", payload.person_id)
        return {"status": "cooldown"}

    _last_greeting[payload.person_id] = datetime.utcnow()
    _active = 1
    # No activation push here — PI 5 is still active when greeting fires.
    # It only goes inactive after posting /detection (which comes later).

    # Resolve display name — prefer Thai name from PI 5, fall back to cleaned person_id
    # Apply _clean_name() to thai_name too in case PI 5 sends "ปาล์ม (กฤติน สาครินทร์)"
    clean_name = _clean_name(payload.thai_name) if payload.thai_name else _clean_name(payload.person_id)
    # Resolve student_id + year from MySQL (PI 5 may send student_id directly)
    mysql_cfg = app_state.settings.get("mysql", {})
    if payload.student_id:
        student_row = fetch_student_by_id(
            student_id=payload.student_id,
            host=mysql_cfg.get("host", "localhost"),
            port=int(mysql_cfg.get("port", 3306)),
            user=mysql_cfg.get("user", "root"),
            password=mysql_cfg.get("password", "root"),
            database=mysql_cfg.get("database", "capstone"),
        )
        student_id = payload.student_id
    else:
        student_row = fetch_student_by_nickname(
            nick_name=_clean_name(payload.person_id),
            host=mysql_cfg.get("host", "localhost"),
            port=int(mysql_cfg.get("port", 3306)),
            user=mysql_cfg.get("user", "root"),
            password=mysql_cfg.get("password", "root"),
            database=mysql_cfg.get("database", "capstone"),
        )
        student_id = str(student_row.get("student_id", payload.person_id)) if student_row else payload.person_id
    if student_row:
        enroll = int(student_row.get("enrollment_year", datetime.utcnow().year))
        student_year = min(max(datetime.utcnow().year - enroll + 1, 1), 4)
    else:
        student_year = 1

    greeting_text, tts_text = await app_state.greeting_bot.greet(
        student_name=clean_name,
        student_id=student_id,
        student_year=student_year,
    )
    logger.info("Greeted %s (year=%d): %r", payload.person_id, student_year, greeting_text[:60])

    log_event({
        "endpoint": "/greeting",
        "person_id": payload.person_id,
        "display_name": clean_name,
        "student_year": student_year,
        "is_registered": payload.is_registered,
        "vision_confidence": payload.vision_confidence,
        "reply_text": greeting_text,
        "status": "ok",
    })
    return {"status": "ok", "greeting_text": greeting_text}


@router.post("/detection", response_model=ActivateResponse)
async def on_detection(payload: DetectionPayload, request: Request):
    """
    Combined vision + STT event from PI 5.
    Runs the full chatbot pipeline (grammar → RAG → intent routing) for registered persons.
    """
    global _active
    app_state = request.app.state
    cfg = app_state.settings

    pi5_url = f"http://{cfg['server']['pi5_ip']}:{cfg['server']['pi5_port']}"

    # PI 5 goes INACTIVE after posting /detection — server must push active=1
    # after the full pipeline to re-enable it, except on farewell (active=0).

    # Unknown / unregistered person — re-enable PI 5 immediately so it can listen again
    if not payload.is_registered or payload.person_id == "Unknown":
        _active = 0
        await _push_active(1, pi5_url)
        log_event({
            "endpoint": "/detection",
            "person_id": payload.person_id,
            "is_registered": False,
            "stt_raw": payload.stt.text,
            "status": "skipped",
        })
        return ActivateResponse(active=0)

    _active = 1
    _cleanup_expired_sessions(cfg["session"]["session_timeout_seconds"])

    # Session
    sess = _get_or_create_session(payload.person_id)

    # Resolve student info — cached in session to avoid repeated DB calls
    if "student_year" not in sess:
        mysql_cfg = cfg.get("mysql", {})
        if payload.student_id:
            det_student_row = fetch_student_by_id(
                student_id=payload.student_id,
                host=mysql_cfg.get("host", "localhost"),
                port=int(mysql_cfg.get("port", 3306)),
                user=mysql_cfg.get("user", "root"),
                password=mysql_cfg.get("password", "root"),
                database=mysql_cfg.get("database", "capstone"),
            )
            sess["student_db_id"] = payload.student_id
        else:
            det_student_row = fetch_student_by_nickname(
                nick_name=_clean_name(payload.person_id),
                host=mysql_cfg.get("host", "localhost"),
                port=int(mysql_cfg.get("port", 3306)),
                user=mysql_cfg.get("user", "root"),
                password=mysql_cfg.get("password", "root"),
                database=mysql_cfg.get("database", "capstone"),
            )
            sess["student_db_id"] = str(det_student_row.get("student_id", payload.person_id)) if det_student_row else payload.person_id
        if det_student_row:
            enroll = int(det_student_row.get("enrollment_year", datetime.utcnow().year))
            sess["student_year"] = min(max(datetime.utcnow().year - enroll + 1, 1), 4)
        else:
            sess["student_year"] = 1

    display_name = _clean_name(payload.thai_name) if payload.thai_name else _clean_name(payload.person_id)

    t_start = time.perf_counter()

    # 1. Grammar correction
    t0 = time.perf_counter()
    corrected = app_state.grammar_corrector.correct(payload.stt.text)
    t_grammar = (time.perf_counter() - t0) * 1000
    logger.info(
        "Detection from %s — raw=%r  corrected=%r",
        payload.person_id, payload.stt.text, corrected,
    )

    # 2. RAG chatbot
    t0 = time.perf_counter()
    response = await app_state.chatbot.ask_and_store(
        question=corrected,
        session_id=sess["session_id"],
        student_id=sess["student_db_id"],
        student_name=display_name,
        student_year=sess["student_year"],
        history=sess["history"],
    )
    t_llm = (time.perf_counter() - t0) * 1000

    # Update short-term history
    sess["history"].append((corrected, response["reply_text"]))
    if len(sess["history"]) > cfg["session"]["max_history_turns"]:
        sess["history"].pop(0)

    # 3. Intent routing (TTS + ROS2 in parallel)
    from mcp.tts_router import to_tts_ready
    phoneme_text = to_tts_ready(response.get("reply_text", ""))
    t0 = time.perf_counter()
    route_result = await app_state.intent_router.route(response)
    t_tts = (time.perf_counter() - t0) * 1000

    t_total = (time.perf_counter() - t_start) * 1000

    logger.info(
        "⏱  pipeline [%s] grammar=%.0fms  llm=%.0fms  tts=%.0fms  total=%.0fms",
        payload.person_id, t_grammar, t_llm, t_tts, t_total,
    )

    # Re-enable PI 5 for the next interaction — always push active=1 after pipeline.
    if response.get("intent") == "farewell":
        _active = 0
    await _push_active(1, pi5_url)

    log_event({
        "endpoint": "/detection",
        "person_id": payload.person_id,
        "display_name": display_name,
        "is_registered": True,
        "stt_raw": payload.stt.text,
        "corrected": corrected,
        "rag_collection": response.get("rag_collection", ""),
        "reply_text": response.get("reply_text", ""),
        "intent": response.get("intent", ""),
        "destination": response.get("destination"),
        "routed_to": route_result.get("routed_to", []),
        "timing_ms": {
            "grammar": round(t_grammar),
            "llm": round(t_llm),
            "tts": round(t_tts),
            "total": round(t_total),
        },
        "status": "ok",
    })
    return ActivateResponse(active=1)


@router.get("/activate")
async def get_activate() -> ActivateResponse:
    """PI 5 polls this to check if robot is in active (conversation) mode."""
    return ActivateResponse(active=_active)


@router.post("/audio_detection", response_model=ActivateResponse)
async def on_audio_detection(
    request: Request,
    audio: UploadFile = File(..., description="WAV audio file — Thai speech from microphone"),
    person_id: str = Form(...),
    is_registered: bool = Form(...),
    thai_name: Optional[str] = Form(None),
    student_id: Optional[str] = Form(None),
    track_id: Optional[int] = Form(None),
    vision_confidence: Optional[float] = Form(None),
    timestamp: Optional[str] = Form(None),
):
    """
    Server-side STT variant of /detection.
    PI 5 sends raw WAV audio + person metadata as multipart/form-data.
    Server transcribes audio via Typhoon2-Audio sidecar, then runs the
    full chatbot pipeline (grammar → RAG → intent routing).
    """
    from tts.typhoon_audio_tts import transcribe as stt_transcribe

    cfg = request.app.state.settings
    sidecar_url = cfg.get("audio_service", {}).get("base_url", "http://localhost:8001")

    # Step 1 — Transcribe audio via sidecar
    wav_bytes = await audio.read()
    stt_text = await stt_transcribe(wav_bytes, sidecar_url=sidecar_url)
    if not stt_text:
        logger.warning("STT returned empty transcript for %s — treating as empty utterance", person_id)
        stt_text = ""

    logger.info("Audio STT [%s]: %r", person_id, stt_text[:80])

    # Step 2 — Build DetectionPayload and delegate to the existing pipeline
    fake_payload = DetectionPayload(
        timestamp=timestamp or datetime.utcnow().isoformat(),
        person_id=person_id,
        thai_name=thai_name,
        student_id=student_id,
        is_registered=is_registered,
        track_id=track_id,
        bbox=None,
        stt=STTResult(text=stt_text, language="th", duration=0.0),
    )
    return await on_detection(fake_payload, request)
