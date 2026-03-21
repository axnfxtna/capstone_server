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
import uuid
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Request

from api.routes.monitor import log_event
from api.schemas.receiver import ActivateResponse, DetectionPayload, GreetingPayload

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────────────────────────────
# In-memory state
# ─────────────────────────────────────────────────────────────────────

_active: int = 0
_last_greeting: Dict[str, datetime] = {}     # person_id → last greeted at
_sessions: Dict[str, dict] = {}             # person_id → session state


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

    phoneme_text = await app_state.greeting_bot.greet(_clean_name(payload.person_id))
    logger.info("Greeted %s: %r", payload.person_id, phoneme_text[:60])

    log_event({
        "endpoint": "/greeting",
        "person_id": payload.person_id,
        "is_registered": payload.is_registered,
        "stt_confidence": payload.vision_confidence,
        "reply_text": phoneme_text,
        "status": "ok",
    })
    return {"status": "ok", "phoneme_text": phoneme_text}


@router.post("/detection", response_model=ActivateResponse)
async def on_detection(payload: DetectionPayload, request: Request):
    """
    Combined vision + STT event from PI 5.
    If person is registered and STT confidence is sufficient,
    runs the full chatbot pipeline (grammar → RAG → intent routing).
    """
    global _active
    app_state = request.app.state
    cfg = app_state.settings

    # Unknown / unregistered person
    if not payload.is_registered or payload.person_id == "Unknown":
        log_event({
            "endpoint": "/detection",
            "person_id": payload.person_id,
            "is_registered": False,
            "stt_raw": payload.stt.text,
            "stt_confidence": payload.stt.confidence,
            "status": "skipped",
        })
        return ActivateResponse(active=0)

    _active = 1
    _cleanup_expired_sessions(cfg["session"]["session_timeout_seconds"])

    # STT confidence gate
    if payload.stt.confidence < cfg["thresholds"]["stt_confidence"]:
        logger.info(
            "STT confidence too low (%.2f) for %s — speaking fallback",
            payload.stt.confidence, payload.person_id,
        )
        from mcp.tts_router import to_tts_ready
        fallback = to_tts_ready("ขอโทษค่ะ ช่วยพูดอีกครั้งได้ไหมค่ะ")
        await app_state.intent_router._speak(fallback)
        log_event({
            "endpoint": "/detection",
            "person_id": payload.person_id,
            "is_registered": True,
            "stt_raw": payload.stt.text,
            "stt_confidence": payload.stt.confidence,
            "reply_text": "ขอโทษค่ะ ช่วยพูดอีกครั้งได้ไหมค่ะ",
            "status": "low_confidence_fallback",
            "errors": [f"STT confidence {payload.stt.confidence:.2f} below threshold"],
        })
        return ActivateResponse(active=1)

    # Session
    sess = _get_or_create_session(payload.person_id)

    # 1. Grammar correction
    corrected = app_state.grammar_corrector.correct(
        payload.stt.text,
        confidence=payload.stt.confidence,
    )
    logger.info(
        "Detection from %s — raw=%r  corrected=%r  conf=%.2f",
        payload.person_id, payload.stt.text, corrected, payload.stt.confidence,
    )

    # 2. RAG chatbot
    response = await app_state.chatbot.ask_and_store(
        question=corrected,
        session_id=sess["session_id"],
        student_id=payload.person_id,
        student_name=_clean_name(payload.person_id),
        student_year=1,
        history=sess["history"],
    )

    # Update short-term history
    sess["history"].append((corrected, response["reply_text"]))
    if len(sess["history"]) > cfg["session"]["max_history_turns"]:
        sess["history"].pop(0)

    # 3. Intent routing (TTS + ROS2 in parallel)
    route_result = await app_state.intent_router.route(response)

    log_event({
        "endpoint": "/detection",
        "person_id": payload.person_id,
        "is_registered": True,
        "stt_raw": payload.stt.text,
        "stt_confidence": payload.stt.confidence,
        "corrected": corrected,
        "reply_text": response.get("reply_text", ""),
        "intent": response.get("intent", ""),
        "destination": response.get("destination"),
        "routed_to": route_result.get("routed_to", []),
        "status": "ok",
    })
    return ActivateResponse(active=1)


@router.get("/activate")
async def get_activate() -> ActivateResponse:
    """PI 5 polls this to check if robot is in active (conversation) mode."""
    return ActivateResponse(active=_active)
