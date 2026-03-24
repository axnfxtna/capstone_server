"""
mcp/intent_router.py  (mcp_intendgate)
========================================
Receives ChatbotResponse and routes to downstream handlers.

Intent routing table:
  chat / info → TTS only
  farewell    → TTS + ROS2 resume_roaming  (parallel)
  navigate    → TTS confirmation + ROS2 destination  (parallel)
"""

import asyncio
import logging
from typing import Dict, Optional

import httpx

from llm.typhoon_client import TyphoonClient
from mcp.tts_router import to_tts_ready, expand_for_tts
from tts.text_sender import send_phoneme_text   # Option B default
# from tts.kanom_than_player import send_wav    # Option A — uncomment to switch

logger = logging.getLogger(__name__)



class IntentRouter:
    def __init__(
        self,
        llm: TyphoonClient,
        pi5_base_url: str = "http://10.100.16.XX:5000",
        tts_mode: str = "pi5",
        tts_engine: str = "khanomtan",   # "typhoon_audio" | "khanomtan"
        audio_sidecar_url: str = "http://localhost:8001",
        http_timeout: float = 5.0,
    ):
        self.llm = llm
        self.pi5_base_url = pi5_base_url.rstrip("/")
        self.tts_mode = tts_mode
        self.tts_engine = tts_engine
        self.audio_sidecar_url = audio_sidecar_url
        self.timeout = http_timeout

    # ------------------------------------------------------------------
    async def route(self, chatbot_response: Dict) -> Dict:
        """
        Route ChatbotResponse to TTS and/or ROS2.
        Returns {"routed_to": [...], "status": "ok"}.
        """
        intent      = chatbot_response.get("intent", "chat")
        reply_text  = chatbot_response.get("reply_text", "")
        destination = chatbot_response.get("destination")

        # Always expand English/numbers to Thai first, then syllabify for non-typhoon engines
        expanded = expand_for_tts(reply_text)
        tts_text = expanded if self.tts_engine == "typhoon_audio" else to_tts_ready(expanded)
        routed_to = []

        if intent in ("chat", "info"):
            asyncio.create_task(self._speak(tts_text))
            routed_to.append("tts")

        elif intent == "farewell":
            asyncio.create_task(self._speak(tts_text))
            asyncio.create_task(self._navigate("resume_roaming"))
            routed_to.extend(["tts", "ros2_resume"])

        elif intent == "navigate":
            asyncio.create_task(self._speak(tts_text))
            asyncio.create_task(self._navigate("go_to", destination=destination))
            routed_to.extend(["tts", "ros2_navigate"])

        return {"routed_to": routed_to, "status": "ok"}

    # ------------------------------------------------------------------
    async def _speak(self, phoneme_text: str) -> None:
        """Forward text to TTS output layer."""
        try:
            if self.tts_engine == "typhoon_audio":
                from tts.typhoon_audio_tts import synthesize_and_send
                await synthesize_and_send(
                    phoneme_text,
                    pi5_base_url=self.pi5_base_url,
                    sidecar_url=self.audio_sidecar_url,
                )
            elif self.tts_mode == "server":
                from tts.khanomtan_engine import synthesize_and_send
                await synthesize_and_send(phoneme_text, pi5_base_url=self.pi5_base_url)
            else:
                await send_phoneme_text(phoneme_text, pi5_base_url=self.pi5_base_url)
        except Exception as exc:
            logger.error("IntentRouter._speak failed: %s", exc)

    async def _navigate(self, cmd: str, destination: Optional[str] = None) -> None:
        """POST navigation command to PI 5."""
        url = f"{self.pi5_base_url}/navigation"
        payload: Dict = {"cmd": cmd}
        if destination:
            payload["destination"] = destination
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(url, json=payload)
        except Exception as exc:
            logger.error("IntentRouter._navigate failed (cmd=%s): %s", cmd, exc)
