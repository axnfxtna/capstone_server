"""
tts/kanom_than_player.py — Option A: Server-Side TTS WAV sender
=================================================================
POSTs WAV bytes to PI 5 /audio_play after GPU TTS inference on server.

Pros: Fast GPU inference (~100-300ms)
Cons: WAV payload is large (100-500 KB), adds ~50-200ms transfer time
"""

import logging
import httpx

logger = logging.getLogger(__name__)


async def send_wav(
    wav_bytes: bytes,
    pi5_base_url: str = "http://10.100.16.XX:5000",
    timeout: float = 15.0,
) -> None:
    """POST WAV bytes to PI 5 /audio_play for immediate speaker playback."""
    url = f"{pi5_base_url.rstrip('/')}/audio_play"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            resp.raise_for_status()
        logger.debug("WAV sent to PI 5: %d bytes", len(wav_bytes))
    except Exception as exc:
        logger.error("send_wav failed: %s", exc)
        raise
