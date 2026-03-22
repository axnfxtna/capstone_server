"""
tts/khanomtan_engine.py
========================
Server-side Thai TTS using KhanomTan v1.0 (pythaitts / Coqui-TTS).
Synthesizes WAV on server GPU → sends bytes to PI 5 /audio_play (Option A).

Model:   wannaphong/KhanomTan-TTS-v1.0
Speaker: Tsyncone  (Thai female voice, TSync-1 corpus)
Install: pip install pythaitts TTS
"""
import asyncio
import logging
import os
import re
import tempfile

from tts.kanom_than_player import send_wav   # handles POST to PI 5

logger = logging.getLogger(__name__)

DEFAULT_SPEAKER  = "Tsyncone"
DEFAULT_LANGUAGE = "th-th"

try:
    from pythaitts import TTS as _ThaiTTS
    _tts = _ThaiTTS(pretrained="khanomtan")
    TTS_AVAILABLE = True
    logger.info("KhanomTan TTS v1.0 engine loaded (speaker=%s)", DEFAULT_SPEAKER)
except Exception as exc:
    TTS_AVAILABLE = False
    logger.warning("pythaitts KhanomTan unavailable (%s) — server-side TTS disabled", exc)


def _clean_text(text: str) -> str:
    """Normalize Thai text before synthesis (matches docker TTSPipeline behaviour)."""
    try:
        from pythainlp.util import normalize
        text = normalize(text)
    except ImportError:
        pass
    return re.sub(r"\s+", " ", text).strip()


def _synthesize_blocking(
    text: str,
    speaker: str = DEFAULT_SPEAKER,
    language: str = DEFAULT_LANGUAGE,
) -> bytes:
    if not TTS_AVAILABLE:
        raise RuntimeError("pythaitts KhanomTan not available — run: pip install pythaitts TTS")

    cleaned = _clean_text(text)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        requested_path = f.name

    try:
        result_path = _tts.tts(
            cleaned,
            speaker_idx=speaker,
            language_idx=language,
            filename=requested_path,
        )
        # Some pythaitts versions return the actual written path
        audio_path = result_path if (result_path and result_path != requested_path) else requested_path

        if not os.path.exists(audio_path):
            raise RuntimeError(f"KhanomTan produced no WAV at {audio_path}")

        with open(audio_path, "rb") as f:
            return f.read()
    finally:
        for p in (requested_path, result_path if result_path else None):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


async def synthesize_and_send(
    text: str,
    pi5_base_url: str,
    speaker: str = DEFAULT_SPEAKER,
    language: str = DEFAULT_LANGUAGE,
) -> None:
    """Synthesize Thai text to WAV on server GPU and POST bytes to PI 5 /audio_play."""
    loop = asyncio.get_event_loop()
    wav_bytes = await loop.run_in_executor(
        None, _synthesize_blocking, text, speaker, language
    )
    await send_wav(wav_bytes, pi5_base_url=pi5_base_url)
