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

from llm.typhoon_client import TyphoonClient, enforce_female_particle
from mcp.tts_router import to_tts_ready
from tts.text_sender import send_phoneme_text   # Option B default
# from tts.kanom_than_player import send_wav    # Option A — uncomment to switch

logger = logging.getLogger(__name__)

_NAVIGATE_CONFIRM_PROMPT = """\
คุณคือหุ่นยนต์หญิงชื่อขนมทาน ใช้คำลงท้าย "ค่ะ" เสมอ ห้ามใช้ "ครับ"
สร้างประโยคยืนยันสั้น ๆ เป็นภาษาไทย (1 ประโยค) ว่าจะพานักศึกษาไปที่ {destination}
ตัวอย่าง: "ได้เลยค่ะ ตามหนูมาเลยนะค่ะ หนูจะพาไปที่ {destination}"
ตอบกลับเฉพาะประโยคเท่านั้น
"""


class IntentRouter:
    def __init__(
        self,
        llm: TyphoonClient,
        pi5_base_url: str = "http://10.100.16.XX:5000",
        tts_mode: str = "pi5",
        http_timeout: float = 5.0,
    ):
        self.llm = llm
        self.pi5_base_url = pi5_base_url.rstrip("/")
        self.tts_mode = tts_mode
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

        phoneme_text = to_tts_ready(reply_text)
        routed_to = []

        if intent in ("chat", "info"):
            await self._speak(phoneme_text)
            routed_to.append("tts")

        elif intent == "farewell":
            await asyncio.gather(
                self._speak(phoneme_text),
                self._navigate("resume_roaming"),
                return_exceptions=True,
            )
            routed_to.extend(["tts", "ros2_resume"])

        elif intent == "navigate":
            confirm_text = self._build_navigate_confirmation(destination or "ปลายทาง")
            phoneme_confirm = to_tts_ready(confirm_text)
            await asyncio.gather(
                self._speak(phoneme_confirm),
                self._navigate("go_to", destination=destination),
                return_exceptions=True,
            )
            routed_to.extend(["tts", "ros2_navigate"])

        return {"routed_to": routed_to, "status": "ok"}

    # ------------------------------------------------------------------
    def _build_navigate_confirmation(self, destination: str) -> str:
        prompt = _NAVIGATE_CONFIRM_PROMPT.format(destination=destination)
        try:
            text = enforce_female_particle(self.llm.generate(prompt, temperature=0.4, max_tokens=64))
            return text.strip() or f"ได้เลยค่ะ หนูจะพาไปที่ {destination} ค่ะ"
        except Exception:
            return f"ได้เลยค่ะ หนูจะพาไปที่ {destination} ค่ะ"

    # ------------------------------------------------------------------
    async def _speak(self, phoneme_text: str) -> None:
        """Forward phoneme text to TTS output layer."""
        try:
            if self.tts_mode == "server":
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
