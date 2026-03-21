"""
mcp/greeting_bot.py
====================
One-shot greeting handler triggered by POST /greeting.
Generates a personalised Thai greeting AND fires TTS + ROS2 stop in parallel.
"""

import asyncio
import logging
from typing import Optional

import httpx

from llm.typhoon_client import TyphoonClient, enforce_female_particle
from mcp.tts_router import to_tts_ready

logger = logging.getLogger(__name__)

_GREETING_PROMPT = """\
คุณคือหุ่นยนต์บริการในมหาวิทยาลัย ชื่อ "ขนมทาน" ใช้คำลงท้ายว่า "ค่ะ" เสมอ ห้ามใช้ "ครับ"
นักศึกษาที่ตรวจพบชื่อ: {student_name}

สร้างข้อความทักทายสั้น ๆ เป็นภาษาไทย (1 ประโยค) และคำสั่ง ROS2 สำหรับหยุดเคลื่อนที่

ตอบกลับเป็น JSON เท่านั้น รูปแบบ:
{{
  "greeting_text": "สวัสดีค่ะ คุณ {student_name} ...",
  "ros2_cmd": "stop_roaming"
}}
"""


class GreetingBot:
    def __init__(
        self,
        llm: TyphoonClient,
        pi5_base_url: str = "http://10.100.16.XX:5000",
        tts_mode: str = "pi5",
        timeout: float = 5.0,
    ):
        self.llm = llm
        self.pi5_base_url = pi5_base_url.rstrip("/")
        self.tts_mode = tts_mode
        self.timeout = timeout

    # ------------------------------------------------------------------
    async def greet(self, student_name: str) -> str:
        """
        Generate greeting text, then fire TTS phoneme conversion + ROS2 stop
        in parallel.  Returns the phoneme-ready greeting text.
        """
        # 1. Generate greeting via LLM
        prompt = _GREETING_PROMPT.format(student_name=student_name)
        parsed = self.llm.generate_structured(prompt, temperature=0.5, max_tokens=128)

        if parsed and "greeting_text" in parsed:
            greeting_text = enforce_female_particle(parsed["greeting_text"])
        else:
            greeting_text = f"สวัสดีค่ะ คุณ {student_name} มีอะไรให้ช่วยไหมค่ะ"

        # 2. Convert to TTS-ready phoneme text
        phoneme_text = to_tts_ready(greeting_text)

        # 3. Fire TTS send + ROS2 stop in parallel
        await asyncio.gather(
            self._send_tts(phoneme_text),
            self._send_navigation("stop_roaming"),
            return_exceptions=True,
        )

        return phoneme_text

    # ------------------------------------------------------------------
    async def _send_tts(self, phoneme_text: str) -> None:
        """POST phoneme text / WAV to TTS output layer."""
        try:
            if self.tts_mode == "server":
                from tts.khanomtan_engine import synthesize_and_send
                await synthesize_and_send(phoneme_text, pi5_base_url=self.pi5_base_url)
            else:
                url = f"{self.pi5_base_url}/tts_render"
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    await client.post(url, json={"phoneme_text": phoneme_text})
        except Exception as exc:
            logger.error("GreetingBot._send_tts failed: %s", exc)

    async def _send_navigation(self, cmd: str, destination: Optional[str] = None) -> None:
        """POST navigation command to PI 5 /navigation."""
        url = f"{self.pi5_base_url}/navigation"
        payload: dict = {"cmd": cmd}
        if destination:
            payload["destination"] = destination
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(url, json=payload)
        except Exception as exc:
            logger.error("GreetingBot._send_navigation failed: %s", exc)
