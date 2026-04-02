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

from llm.typhoon_client import TyphoonClient, enforce_female_particle, _current_datetime_str
from mcp.tts_router import to_tts_ready, expand_for_tts

logger = logging.getLogger(__name__)

# Year-based tone descriptions in Thai
# _YEAR_TONE = {
#     ปี 1: "เน้นการต้อนรับสู่รั้วมหาวิทยาลัย ใช้คำที่ดูใจดีเหมือนพี่สาวดูแลน้อง",
#     ปี 2: "เน้นความกระตือรือร้น ถามไถ่เรื่องความท้าทายในวิชาที่เริ่มหนักขึ้น",
#     ปี 3: "เน้นความเอาใจใส่ ถามเรื่องความพร้อมหรือความตื่นเต้นในการหาที่ฝึกงาน",
#     ปี 4: "คุยแบบเป็นกันเองและให้กำลังใจที่ใกล้เรียนจบ",
# }

_TIME_OF_DAY = {
    "เช้า":      range(6, 12),
    "เที่ยง":    range(12, 13),
    "บ่าย":      range(13, 18),
    "เย็น":      range(18, 21),
    "กลางคืน":  range(21, 24),
}


def _get_time_of_day() -> str:
    from datetime import datetime, timezone, timedelta
    tz_thai = timezone(timedelta(hours=7))
    hour = datetime.now(tz_thai).hour
    for label, hours in _TIME_OF_DAY.items():
        if hour in hours:
            return label
    return "เช้า"  # midnight–5am fallback

_STRANGER_GREETING_PROMPT = """\
คุณคือ "น้องสาธุ" หุ่นยนต์บริการหญิงของ KMITL ที่ตึก E-12 ชั้น 12
ช่วงเวลา: {time_of_day}

กฎที่ต้องปฏิบัติเสมอ:
- ใช้ "น้องสาธุ" แทนตัวเอง(เป็นผู้พูด) และใช้ "ค่ะ" ลงท้ายเสมอ
- ตอบ 1 ประโยคเท่านั้น ห้ามเกิน 1 ประโยค
- ทักทายตามช่วงเวลา + แนะนำชื่อตัวเอง รวมในประโยคเดียวสั้นๆ
- ห้ามถามชื่อ ห้ามถามคำถามซ้อนคำถาม ห้ามบอกความสามารถของตัวเองในการทักทาย

ตัวอย่างที่ถูกต้อง (สั้นแบบนี้เท่านั้น — น้องสาธุเป็นผู้พูด ไม่ใช่ผู้ถูกส่งสาร):
{{"greeting_text": "สวัสดีตอน{time_of_day}ค่ะ น้องสาธุยินดีต้อนรับค่ะ มีอะไรให้ช่วยไหมคะ"}}
หรือ: {{"greeting_text": "สวัสดีตอน{time_of_day}ค่ะ น้องสาธุช่วยอะไรได้บ้างค่ะ"}}

ตอบกลับเป็น JSON เท่านั้น:
{{"greeting_text": "..."}}
"""

_GREETING_PROMPT = """
คุณคือ "น้องสาธุ" หุ่นยนต์บริการหญิงประจำสถาบันเทคโนโลยีพระจอมเกล้าเจ้าคุณทหารลาดกระบัง หรือเรียกสั้นๆ ว่า ลาดกระบัง
คุณกำลังพูดคุยกับ {student_name}
ปัจจุบัน: {current_datetime}
ความจำจากบทสนทนาครั้งก่อน: {memory_summary}
สถานการณ์ปัจจุบัน: คุณกำลังทักทายนักศึกษาที่คุณเพิ่งเจอ

กฎที่ต้องปฏิบัติเสมอ:
- ตอบ 1 ประโยคสั้นๆ เท่านั้น ห้ามเกิน 1 ประโยคโดยเด็ดขาด ห้ามต่อประโยคด้วยคำเชื่อม
- ถ้ามีความจำ ให้ทักทาย + กล่าวถึงเรื่องนั้นกว้างๆ ในประโยคเดียว
- ถ้าไม่มีความจำ ให้ถามแบบเปิดกว้างเหมือนเพื่อนคุยกัน เช่น "เป็นยังไงบ้างคะ" "ช่วงนี้เป็นไงบ้างคะ" 
- ให้เรียกชื่อนักศึกษาว่า "{student_name}" เท่านั้น ห้ามใช้นามสกุลหรือวงเล็บ
- ห้ามพูดถึงความสามารถของตัวเอง

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
        pi5_ros2_base_url: str = "http://10.26.3.203:8767",
        tts_mode: str = "pi5",
        tts_engine: str = "khanomtan",   # "typhoon_audio" | "khanomtan"
        audio_sidecar_url: str = "http://localhost:8001",
        timeout: float = 5.0,
    ):
        self.llm = llm
        self.memory_manager = memory_manager
        self.pi5_base_url = pi5_base_url.rstrip("/")
        self.pi5_ros2_base_url = pi5_ros2_base_url.rstrip("/")
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

        # 2. Generate greeting via LLM
        prompt = _GREETING_PROMPT.format(
            student_name=student_name,
            current_datetime=_current_datetime_str(),
            memory_summary=memory_summary,
        )
        parsed = self.llm.generate_structured(prompt, temperature=0.7, max_tokens=64)

        if parsed and "greeting_text" in parsed:
            greeting_text = enforce_female_particle(parsed["greeting_text"])
        else:
            greeting_text = f"สวัสดีตอน{_get_time_of_day()}ค่ะ {student_name} วันนี้เป็นยังไงบ้างคะ"

        # 2. Expand English/numbers then syllabify for non-typhoon engines
        expanded = expand_for_tts(greeting_text)
        tts_text = expanded if self.tts_engine == "typhoon_audio" else to_tts_ready(expanded)

        # 3. Fire TTS — non-blocking, response returns immediately
        asyncio.create_task(self._send_tts(tts_text))

        # Return (original_text, tts_text) so caller can log the clean Thai
        return greeting_text, tts_text

    # ------------------------------------------------------------------
    async def greet_stranger(self) -> str:
        """
        Generate a visitor greeting (no name/year/memory), then fire TTS + ROS2 stop.
        Returns greeting_text.
        """
        prompt = _STRANGER_GREETING_PROMPT.format(time_of_day=_get_time_of_day())
        parsed = self.llm.generate_structured(prompt, temperature=0.7, max_tokens=64)

        if parsed and "greeting_text" in parsed:
            greeting_text = enforce_female_particle(parsed["greeting_text"])
        else:
            greeting_text = "สวัสดีค่ะ มีอะไรให้น้องสาธุช่วยไหมคะ"

        expanded = expand_for_tts(greeting_text)
        tts_text = expanded if self.tts_engine == "typhoon_audio" else to_tts_ready(expanded)

        asyncio.create_task(self._send_tts(tts_text))
        return greeting_text

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
            logger.error("GreetingBot._send_tts failed: %r", exc)


