"""
tts/typhoon_audio_tts.py
=========================
TTS client that calls the Typhoon2-Audio sidecar service (audio_service:8001).
Drop-in replacement for khanomtan_engine.synthesize_and_send().
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_SIDECAR_URL = "http://localhost:8001"


async def synthesize_and_send(
    text: str,
    pi5_base_url: str,
    sidecar_url: str = _DEFAULT_SIDECAR_URL,
    timeout: float = 15.0,
) -> None:
    """
    1. POST text to audio sidecar /tts → receive WAV bytes
    2. POST WAV bytes to PI 5 /audio_play
    """
    # Step 1 — synthesize
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{sidecar_url}/tts",
                json={"text": text},
            )
            resp.raise_for_status()
            wav_bytes = resp.content
    except Exception as exc:
        logger.error("Typhoon2-Audio TTS synthesis failed: %s", exc)
        return

    # Step 2 — play on PI 5
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.post(
                f"{pi5_base_url}/audio_play",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
    except Exception as exc:
        logger.error("PI 5 audio_play delivery failed: %s", exc)


async def transcribe(
    wav_bytes: bytes,
    sidecar_url: str = _DEFAULT_SIDECAR_URL,
    timeout: float = 30.0,
) -> str:
    """
    POST WAV bytes to audio sidecar /stt → return transcribed text.
    Falls back to empty string on error.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{sidecar_url}/stt",
                content=wav_bytes,
                headers={"Content-Type": "audio/wav"},
            )
            resp.raise_for_status()
            return resp.json().get("text", "")
    except Exception as exc:
        logger.error("Typhoon2-Audio STT transcription failed: %s", exc)
        return ""
