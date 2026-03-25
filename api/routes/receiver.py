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

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, File, Form, Request, UploadFile

from api.routes.monitor import log_event
from api.schemas.receiver import ActivateResponse, DetectionPayload, GreetingPayload, STTResult
from database import sqlite_client
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


async def _push_nav_state(state: int, ros2_base_url: str, destination: Optional[str] = None) -> None:
    """
    Push navigation state to Teammate B's ROS2 PI5 service (fire-and-forget).

    state: 0 = stop, 1 = roam, 2 = navigate (destination required)
    Skipped silently if ros2_base_url contains 'TBD' (not yet configured).
    """
    if "TBD" in ros2_base_url:
        logger.debug("_push_nav_state skipped — pi5_ros2.host not configured")
        return
    payload: dict = {"state": state}
    if destination:
        payload["destination"] = destination
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{ros2_base_url}/nav_state", json=payload)
        logger.debug("Pushed nav_state=%d dest=%s to ROS2 PI5", state, destination)
    except Exception as exc:
        logger.warning("_push_nav_state failed: %s", exc)


async def _session_gone_timeout(
    person_id: str,
    ros2_url: str,
    speak_fn,
    reprompt_seconds: int,
    gone_seconds: int,
) -> None:
    """
    Two-stage student-gone timer (fire-and-forget, stored per session).

    Stage 1 — after reprompt_seconds silence:
        Speak "ยังอยู่ที่นี่ไหมคะ" — assume paused, not gone.
    Stage 2 — after gone_seconds more silence:
        Speak farewell, push nav_state=1 (roam), drop session.

    Cancelled cleanly whenever /detection or /greeting resets the timer.
    """
    try:
        await asyncio.sleep(reprompt_seconds)
        if person_id not in _sessions:
            return
        logger.info("Gone timer stage 1 [%s] — sending reprompt", person_id)
        asyncio.create_task(speak_fn("ยังอยู่ที่นี่ไหมคะ"))

        await asyncio.sleep(gone_seconds)
        if person_id not in _sessions:
            return
        logger.info("Gone timer stage 2 [%s] — resuming roaming", person_id)
        asyncio.create_task(speak_fn("ไว้เจอกันใหม่นะคะ"))
        asyncio.create_task(_push_nav_state(1, ros2_url))
        _sessions.pop(person_id, None)
    except asyncio.CancelledError:
        pass  # Reset by new activity or farewell — no action needed


def _reset_gone_timer(
    sess: dict,
    person_id: str,
    ros2_url: str,
    speak_fn,
    reprompt_seconds: int,
    gone_seconds: int,
) -> None:
    """Cancel any running gone-timer for this session and start a fresh one."""
    existing: Optional[asyncio.Task] = sess.get("_gone_timer")
    if existing and not existing.done():
        existing.cancel()
    sess["_gone_timer"] = asyncio.create_task(
        _session_gone_timeout(person_id, ros2_url, speak_fn, reprompt_seconds, gone_seconds)
    )


def _cancel_gone_timer(sess: dict) -> None:
    """Cancel the gone-timer without starting a new one (used on farewell)."""
    existing: Optional[asyncio.Task] = sess.get("_gone_timer")
    if existing and not existing.done():
        existing.cancel()
    sess.pop("_gone_timer", None)


def _clean_name(person_id: str) -> str:
    """Extract display name from person_id like 'Palm (Krittin Sakharin)' → 'Palm'."""
    name = person_id.split("(")[0].strip()
    return name if name else person_id


def _get_or_create_session(person_id: str) -> dict:
    """In-memory only — used for guest sessions and as a fast path."""
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


async def _restore_session(person_id: str, student_id: str, db_path: str) -> dict:
    """
    Get or create a session for a registered person.
    If the session is already in memory (normal case), return it immediately.
    If not (server restarted mid-conversation), look up the latest session from
    SQLite by student_id and restore the conversation history from conversation_log.
    """
    if person_id in _sessions:
        sess = _sessions[person_id]
        sess["last_active"] = datetime.utcnow()
        return sess

    # Server restarted — try to recover session from SQLite
    existing = await sqlite_client.get_latest_session(student_id, db_path)
    if existing:
        session_id = existing["session_id"]
        turns = await sqlite_client.get_turns(session_id, limit=10, db_path=db_path)
        history = [(t["user_text"], t["bot_reply"]) for t in turns]
        logger.info(
            "Restored session %s for %s — %d turns recovered from SQLite",
            session_id, person_id, len(history),
        )
    else:
        session_id = str(uuid.uuid4())
        history = []

    _sessions[person_id] = {
        "session_id":  session_id,
        "person_id":   person_id,
        "history":     history,
        "created_at":  datetime.utcnow(),
        "last_active": datetime.utcnow(),
    }
    return _sessions[person_id]


def _cleanup_expired_sessions(timeout_seconds: int, guest_timeout_seconds: int) -> None:
    """Remove sessions idle longer than their respective timeout."""
    now = datetime.utcnow()
    expired = []
    for pid, sess in list(_sessions.items()):
        is_guest = pid.startswith("guest_") or pid == "unknown_guest"
        limit = guest_timeout_seconds if is_guest else timeout_seconds
        if (now - sess["last_active"]).total_seconds() > limit:
            expired.append(pid)
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

    # Unknown / unregistered person — greet as visitor, no name/memory lookup
    if not payload.is_registered or payload.person_id == "Unknown":
        last = _last_greeting.get("__stranger__")
        if last and (datetime.utcnow() - last).total_seconds() < cooldown:
            logger.info("Stranger greeting skipped (cooldown)")
            return {"status": "cooldown"}
        _last_greeting["__stranger__"] = datetime.utcnow()
        _active = 1
        greeting_text = await app_state.greeting_bot.greet_stranger()
        logger.info("Stranger greeted: %r", greeting_text[:60])
        log_event({
            "endpoint": "/greeting",
            "person_id": payload.person_id,
            "display_name": "ผู้มาเยือน",
            "is_registered": False,
            "reply_text": greeting_text,
            "status": "stranger",
        })
        return {"status": "stranger", "greeting_text": greeting_text}

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
        student_year = min(max(datetime.utcnow().year - enroll, 1), 4)
    else:
        # Derive from student_id prefix: first 2 digits = Thai Buddhist year (e.g. 65 → 2565 BE → 2022 CE)
        try:
            prefix = int(str(student_id)[:2])
            enroll_ce = prefix + 1957   # 65→2022, 66→2023, 67→2024, 68→2025
            student_year = min(max(datetime.utcnow().year - enroll_ce, 1), 4)
        except Exception:
            student_year = 1

    # Create/restore session at greeting time so session_id is stable for the whole conversation
    db_path = app_state.settings.get("sqlite", {}).get("db_path", "./database/metadata.db")
    sess = await _restore_session(payload.person_id, student_id, db_path)
    await sqlite_client.upsert_session(
        session_id=sess["session_id"],
        student_id=student_id,
        student_name=clean_name,
        db_path=db_path,
    )

    ros2_cfg = app_state.settings.get("pi5_ros2", {})
    ros2_url = f"http://{ros2_cfg.get('host', 'TBD')}:{ros2_cfg.get('port', 8767)}"

    greeting_text, tts_text = await app_state.greeting_bot.greet(
        student_name=clean_name,
        student_id=student_id,
        student_year=student_year,
    )
    # Stop robot roaming when greeting a registered person (fire-and-forget)
    asyncio.create_task(_push_nav_state(0, ros2_url))
    # Start student-gone timer — robot resumes roaming if student goes quiet
    sess_cfg = app_state.settings.get("session", {})
    _reset_gone_timer(
        sess, payload.person_id, ros2_url,
        speak_fn=app_state.greeting_bot._send_tts,
        reprompt_seconds=sess_cfg.get("student_gone_reprompt_seconds", 15),
        gone_seconds=sess_cfg.get("student_gone_roam_seconds", 15),
    )
    # Seed session history with greeting so the first /detection reply has context
    sess["history"].append(("", greeting_text))
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

    pi5_url  = f"http://{cfg['server']['pi5_ip']}:{cfg['server']['pi5_port']}"
    ros2_url = f"http://{cfg.get('pi5_ros2', {}).get('host', 'TBD')}:{cfg.get('pi5_ros2', {}).get('port', 8767)}"

    # PI 5 goes INACTIVE after posting /detection — server must push active=1
    # after the full pipeline to re-enable it, except on farewell (active=0).

    # Noise gate — drop empty or near-empty STT (likely mic noise, not real speech)
    _MIN_STT_CHARS = 3
    if not payload.stt.text or len(payload.stt.text.strip()) < _MIN_STT_CHARS:
        await _push_active(1, pi5_url)
        log_event({
            "endpoint": "/detection",
            "person_id": payload.person_id,
            "is_registered": payload.is_registered,
            "stt_raw": payload.stt.text,
            "status": "noise",
        })
        logger.debug("STT noise gate triggered for %s — text=%r", payload.person_id, payload.stt.text)
        return ActivateResponse(active=1)

    _active = 1
    _cleanup_expired_sessions(
        cfg["session"]["session_timeout_seconds"],
        cfg["session"]["guest_session_timeout_seconds"],
    )
    db_path = cfg.get("sqlite", {}).get("db_path", "./database/metadata.db")

    # Unknown / unregistered person — run full pipeline but skip MySQL, memory store
    if not payload.is_registered or payload.person_id == "Unknown":
        guest_key = f"guest_{payload.track_id}" if payload.track_id else "unknown_guest"
        sess = _get_or_create_session(guest_key)
        display_name = "ผู้มาเยือน"

        t_start = time.perf_counter()
        corrected = payload.stt.text

        logger.info("▶ [1/2 llm     ] %s — asking chatbot", display_name)
        # ask() only — skip memory store for guests
        response = app_state.chatbot.ask(
            question=corrected,
            session_id=sess["session_id"],
            student_id="guest",
            student_name=display_name,
            student_year=1,
            history=sess["history"],
            routing_hint=payload.stt.text,
        )
        sub = response.get("timing_ms", {})
        logger.info(
            "✔ [1/2 llm     ] rag=%dms memory=%dms llm=%dms — rag=%s intent=%s reply=%r",
            sub.get("rag", 0), sub.get("memory", 0), sub.get("llm", 0),
            response.get("rag_collection", "?"), response.get("intent", "?"),
            response.get("reply_text", "")[:60],
        )

        sess["history"].append((corrected, response["reply_text"]))
        if len(sess["history"]) > cfg["session"]["max_history_turns"]:
            sess["history"].pop(0)

        # Log turn to SQLite for audit only (no Milvus memory store)
        asyncio.create_task(sqlite_client.log_turn(
            session_id=sess["session_id"],
            user_text=corrected,
            bot_reply=response["reply_text"],
            intent=response.get("intent", "chat"),
            student_id="guest",
            db_path=db_path,
        ))

        logger.info("▶ [2/2 tts     ] %s — sending to PI 5 (fire-and-forget)", display_name)
        route_result = await app_state.intent_router.route(response)
        logger.info("✔ [2/2 tts     ] dispatched → %s", route_result.get("routed_to", []))

        t_total = (time.perf_counter() - t_start) * 1000
        await _push_active(1, pi5_url)
        log_event({
            "endpoint": "/detection",
            "person_id": payload.person_id,
            "display_name": display_name,
            "is_registered": False,
            "stt_raw": payload.stt.text,
            "corrected": corrected,
            "rag_collection": response.get("rag_collection", ""),
            "reply_text": response.get("reply_text", ""),
            "intent": response.get("intent", ""),
            "destination": response.get("destination"),
            "routed_to": route_result.get("routed_to", []),
            "timing_ms": {**sub, "total": round(t_total)},
            "status": "guest",
        })
        return ActivateResponse(active=1)

    # Session — restore from SQLite if server restarted mid-conversation
    sess = await _restore_session(payload.person_id, payload.student_id or payload.person_id, db_path)

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
            sess["student_year"] = min(max(datetime.utcnow().year - enroll, 1), 4)
        else:
            try:
                prefix = int(str(payload.person_id)[:2])
                enroll_ce = prefix + 1957
                sess["student_year"] = min(max(datetime.utcnow().year - enroll_ce, 1), 4)
            except Exception:
                sess["student_year"] = 1

        # Persist session to SQLite so it survives server restarts
        asyncio.create_task(sqlite_client.upsert_session(
            session_id=sess["session_id"],
            student_id=sess["student_db_id"],
            student_name=_clean_name(payload.thai_name) if payload.thai_name else _clean_name(payload.person_id),
            db_path=db_path,
        ))

    display_name = _clean_name(payload.thai_name) if payload.thai_name else _clean_name(payload.person_id)

    t_start = time.perf_counter()

    corrected = payload.stt.text

    # 1. RAG chatbot (includes RAG fetch + memory retrieve + LLM inference)
    logger.info("▶ [1/2 llm     ] %s — asking chatbot", display_name)
    response = await app_state.chatbot.ask_and_store(
        question=corrected,
        session_id=sess["session_id"],
        student_id=sess["student_db_id"],
        student_name=display_name,
        student_year=sess["student_year"],
        history=sess["history"],
        routing_hint=payload.stt.text,
    )
    sub = response.get("timing_ms", {})
    logger.info(
        "✔ [1/2 llm     ] rag=%dms memory=%dms llm=%dms — rag=%s intent=%s reply=%r",
        sub.get("rag", 0), sub.get("memory", 0), sub.get("llm", 0),
        response.get("rag_collection", "?"), response.get("intent", "?"),
        response.get("reply_text", "")[:60],
    )

    # Update short-term history
    sess["history"].append((corrected, response["reply_text"]))
    if len(sess["history"]) > cfg["session"]["max_history_turns"]:
        sess["history"].pop(0)

    # 2. Intent routing — TTS dispatched fire-and-forget
    logger.info("▶ [2/2 tts     ] %s — sending to PI 5 (fire-and-forget)", display_name)
    route_result = await app_state.intent_router.route(response)
    logger.info("✔ [2/2 tts     ] dispatched → %s", route_result.get("routed_to", []))

    t_total = (time.perf_counter() - t_start) * 1000
    logger.info(
        "⏱  pipeline [%s] rag=%dms  memory=%dms  llm=%dms  total=%.0fms",
        payload.person_id,
        sub.get("rag", 0), sub.get("memory", 0), sub.get("llm", 0), t_total,
    )

    # Push ROS2 navigation state based on intent (fire-and-forget)
    intent = response.get("intent", "")
    sess_cfg = cfg.get("session", {})
    if intent == "navigate":
        asyncio.create_task(_push_nav_state(2, ros2_url, destination=response.get("destination")))
    elif intent == "farewell":
        asyncio.create_task(_push_nav_state(1, ros2_url))

    # Student-gone timer — reset on every turn, cancel on farewell
    if intent == "farewell":
        _cancel_gone_timer(sess)
    else:
        _reset_gone_timer(
            sess, payload.person_id, ros2_url,
            speak_fn=app_state.greeting_bot._send_tts,
            reprompt_seconds=sess_cfg.get("student_gone_reprompt_seconds", 15),
            gone_seconds=sess_cfg.get("student_gone_roam_seconds", 15),
        )

    # Re-enable PI 5 for the next interaction — always push active=1 after pipeline.
    if intent == "farewell":
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
        "timing_ms": {**sub, "total": round(t_total)},
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
    logger.info("▶ [0/3 stt     ] %s — transcribing %d bytes via sidecar", person_id, len(wav_bytes))
    stt_text = await stt_transcribe(wav_bytes, sidecar_url=sidecar_url)
    if not stt_text:
        logger.warning("STT returned empty transcript for %s — treating as empty utterance", person_id)
        stt_text = ""

    logger.info("✔ [0/3 stt     ] %s — %r", person_id, stt_text[:80])

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
