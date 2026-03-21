"""
tts/khanomtan_engine.py
========================
Server-side Thai TTS using KhanomTan (pythaitts / Coqui-TTS).
Synthesizes WAV on server GPU → sends bytes to PI 5 /audio_play (Option A).

Requires: pip install pythaitts coqui-tts
"""
import asyncio
import logging
import os
import tempfile

from tts.kanom_than_player import send_wav   # already handles POST to PI 5

logger = logging.getLogger(__name__)

try:
    from pythaitts import TTS as _ThaiTTS
    _tts = _ThaiTTS(pretrained="khanomtan")
    TTS_AVAILABLE = True
    logger.info("KhanomTan TTS engine loaded")
except Exception as exc:
    TTS_AVAILABLE = False
    logger.warning("pythaitts KhanomTan unavailable (%s) — server-side TTS disabled", exc)


def _synthesize_blocking(text: str, speaker: str = "Tsyncone") -> bytes:
    if not TTS_AVAILABLE:
        raise RuntimeError("pythaitts KhanomTan not available")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        _tts.tts(text, speaker_idx=speaker, language_idx="th-th", filename=path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


async def synthesize_and_send(text: str, pi5_base_url: str) -> None:
    """Synthesize Thai text to WAV and POST bytes to PI 5 /audio_play."""
    loop = asyncio.get_event_loop()
    wav_bytes = await loop.run_in_executor(None, _synthesize_blocking, text)
    await send_wav(wav_bytes, pi5_base_url=pi5_base_url)
