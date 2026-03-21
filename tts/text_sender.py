"""
tts/text_sender.py — Option B: PI 5-Side TTS
==============================================
POSTs phoneme-ready Thai text to PI 5 /tts_render.
PI 5 runs its own lightweight TTS model and plays the audio locally.

Pros: ~5ms payload (tiny JSON)
Cons: PI 5 must run TTS on ARM CPU
"""

import logging
import httpx

logger = logging.getLogger(__name__)


async def send_phoneme_text(
    phoneme_text: str,
    pi5_base_url: str = "http://10.100.16.XX:5000",
    timeout: float = 10.0,
) -> None:
    """Send phoneme text to PI 5 /tts_render for local TTS playback."""
    url = f"{pi5_base_url.rstrip('/')}/tts_render"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json={"phoneme_text": phoneme_text})
            resp.raise_for_status()
        logger.debug("TTS text sent to PI 5: %r", phoneme_text[:60])
    except Exception as exc:
        logger.error("send_phoneme_text failed: %s", exc)
        raise
