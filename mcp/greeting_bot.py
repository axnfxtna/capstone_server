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

# Year-based tone descriptions in Thai
_YEAR_TONE = {
    1: "เน้นการต้อนรับสู่รั้วมหาวิทยาลัย ใช้คำที่ดูใจดีเหมือนพี่สาวดูแลน้อง",
    2: "เน้นความกระตือรือร้น ถามไถ่เรื่องความท้าทายในวิชาที่เริ่มหนักขึ้น",
    3: "เน้นความเอาใจใส่ ถามเรื่องความพร้อมหรือความตื่นเต้นในการหาที่ฝึกงาน",
    4: "เน้นความภาคภูมิใจและให้เกียรติ ให้กำลังใจในฐานะว่าที่บัณฑิต",
}

_TIME_OF_DAY = {
    "เช้า":      range(6, 12),
    "เที่ยง":    range(12, 13),
    "บ่าย":      range(13, 18),
    "เย็น":      range(18, 21),
    "กลางคืน":  range(21, 24),
}


def _get_time_of_day() -> str:
    from datetime import datetime
    hour = datetime.now().hour
    for label, hours in _TIME_OF_DAY.items():
        if hour in hours:
            return label
    return "เช้า"  # midnight–5am fallback

_GREETING_PROMPT = """\
คุณคือ "ขนมทาน" หุ่นยนต์บริการหญิงของ KMITL (บุคลิก: สุภาพ ร่าเริง ช่างสังเกต)
กฎการพูด: ใช้ "ค่ะ" เสมอ ตอบ 1 ประโยคสั้นๆ เพื่อ TTS ที่รวดเร็ว

นักศึกษา: {student_name} (ปี {student_year})
โทนการพูด: {year_tone}
ช่วงเวลา: {time_of_day}
ความจำจากครั้งก่อน: {memory_summary}

Constraint:
- ถ้ามีความจำ ให้ทักทาย + พูดถึงเรื่องนั้นในเชิงบวกและกว้างๆ ห้ามลงรายละเอียดเชิงเทคนิค
- ถ้าไม่มีความจำ ให้ทักทายตามช่วงเวลาและแนะนำตัวสั้นๆ
- ห้ามถามคำถามซ้อนคำถาม ให้เลือกถามอย่างใดอย่างหนึ่ง
- ห้ามใช้นามสกุลหรือวงเล็บ

ตอบกลับเป็น JSON เท่านั้น:
{{
  "greeting_text": "..."
}}
"""


class GreetingBot:
    def __init__(
        self,
        llm: TyphoonClient,
        memory_manager=None,
        pi5_base_url: str = "http://10.100.16.XX:5000",
        tts_mode: str = "pi5",
        tts_engine: str = "khanomtan",   # "typhoon_audio" | "khanomtan"
        audio_sidecar_url: str = "http://localhost:8001",
        timeout: float = 5.0,
    ):
        self.llm = llm
        self.memory_manager = memory_manager
        self.pi5_base_url = pi5_base_url.rstrip("/")
        self.tts_mode = tts_mode
        self.tts_engine = tts_engine
        self.audio_sidecar_url = audio_sidecar_url
        self.timeout = timeout

    # ------------------------------------------------------------------
    async def greet(
        self,
        student_name: str,
        student_id: Optional[str] = None,
        student_year: int = 1,
    ) -> str:
        """
        Generate greeting text, then fire TTS + ROS2 stop in parallel.
        Returns (greeting_text, tts_text).
        """
        # 1. Fetch memory summary for returning students
        memory_summary = "(ไม่มีประวัติการสนทนา)"
        if self.memory_manager and student_id:
            try:
                recalled = self.memory_manager.retrieve(
                    query="การสนทนาครั้งล่าสุด", student_id=student_id
                )
                if recalled:
                    memory_summary = recalled
            except Exception as exc:
                logger.warning("Memory retrieve failed at greeting: %s", exc)

        year_tone = _YEAR_TONE.get(student_year, _YEAR_TONE[1])

        # 2. Generate greeting via LLM
        prompt = _GREETING_PROMPT.format(
            student_name=student_name,
            student_year=student_year,
            year_tone=year_tone,
            time_of_day=_get_time_of_day(),
            memory_summary=memory_summary,
        )
        parsed = self.llm.generate_structured(prompt, temperature=0.7, max_tokens=128)

        if parsed and "greeting_text" in parsed:
            greeting_text = enforce_female_particle(parsed["greeting_text"])
        else:
            greeting_text = f"สวัสดีค่ะ คุณ {student_name} ดีใจที่ได้พบค่ะ มีอะไรให้ช่วยไหมค่ะ"

        # 2. Convert to TTS-ready text (clean/normalize only, no phoneme rules)
        tts_text = to_tts_ready(greeting_text)

        # 3. Fire TTS + ROS2 stop in parallel
        await asyncio.gather(
            self._send_tts(greeting_text),
            self._send_navigation("stop_roaming"),
            return_exceptions=True,
        )

        # Return (original_text, tts_text) so caller can log the clean Thai
        return greeting_text, tts_text

    # ------------------------------------------------------------------
    async def _send_tts(self, text: str) -> None:
        """Send text to TTS output layer."""
        try:
            if self.tts_engine == "typhoon_audio":
                from tts.typhoon_audio_tts import synthesize_and_send
                await synthesize_and_send(
                    text,
                    pi5_base_url=self.pi5_base_url,
                    sidecar_url=self.audio_sidecar_url,
                )
            elif self.tts_mode == "server":
                from tts.khanomtan_engine import synthesize_and_send
                await synthesize_and_send(to_tts_ready(text), pi5_base_url=self.pi5_base_url)
            else:
                url = f"{self.pi5_base_url}/tts_render"
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    await client.post(url, json={"phoneme_text": to_tts_ready(text)})
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
