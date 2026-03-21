"""
api/routes/tts.py
==================
POST /thai_tts — converts raw Thai text to phoneme-ready syllable string.
Used internally by greeting_bot and intent_router; also exposed as HTTP
so other services can call it if needed.
"""

from fastapi import APIRouter
from api.schemas.chatbot import TTSRequest, TTSResponse
from mcp.tts_router import to_tts_ready

router = APIRouter()


@router.post("/thai_tts", response_model=TTSResponse)
async def thai_tts(payload: TTSRequest) -> TTSResponse:
    """Convert raw Thai text to syllable-spaced TTS-ready string."""
    phoneme_text = to_tts_ready(payload.text)
    return TTSResponse(phoneme_text=phoneme_text)
