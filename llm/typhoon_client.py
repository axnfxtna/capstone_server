"""
llm/typhoon_client.py
=====================
HTTP wrapper for Ollama-served LLM (Typhoon / Qwen2.5).
Adapted from final_docker_component/src/utils_rag.py OllamaClient.

Adds:
  - clean_cjk()  — strips Chinese/Japanese/Korean characters that leak from
                   some LLM checkpoints into Thai responses
  - generate_structured() — parse JSON from freeform LLM output
  - Thai system prompts used across the server
"""

import re
import json
import logging
import requests
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# CJK character cleaner
# ═══════════════════════════════════════════════════════════════════════

def clean_cjk(text: str) -> str:
    """
    Strip CJK characters from LLM output.
    Qwen2.5 and some Typhoon checkpoints occasionally leak Chinese,
    Japanese, or Korean characters into Thai-language responses.
    """
    text = re.sub(
        r"["
        r"\u3000-\u303f"   # CJK punctuation (。、「」)
        r"\u3040-\u309f"   # Hiragana
        r"\u30a0-\u30ff"   # Katakana
        r"\u3400-\u4dbf"   # CJK Extension A
        r"\u4e00-\u9fff"   # CJK Unified Ideographs
        r"\uac00-\ud7af"   # Hangul Syllables
        r"\u1100-\u11ff"   # Hangul Jamo
        r"\uf900-\ufaff"   # CJK Compatibility Ideographs
        r"\uff01-\uff60"   # Fullwidth forms
        r"\uffe0-\uffef"   # Fullwidth symbols
        r"]",
        "",
        text,
    )
    text = re.sub(r"  +", " ", text)
    text = re.sub(r"\n +\n", "\n\n", text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════
# Thai particle enforcement
# ═══════════════════════════════════════════════════════════════════════

def enforce_female_particle(text: str) -> str:
    """
    Replace masculine particles/pronouns in any LLM-generated Thai text.
    The robot is female (น้องสาธุ) and must always use ค่ะ / น้อง.
    """
    # "นะครับ" → "นะคะ"
    text = re.sub(r"นะครับ", "นะคะ", text)
    # "ครับผม" → "ค่ะ"
    text = re.sub(r"ครับผม", "ค่ะ", text)
    # "ครับ" → "ค่ะ"
    text = re.sub(r"ครับ", "ค่ะ", text)
    # Male pronoun "ผม" → "หนู"
    text = re.sub(r"ผม", "น้อง", text)
    # Formal pronouns → "หนู"
    text = re.sub(r"ข้าพเจ้า", "น้อง", text)
    text = re.sub(r"ดิฉัน", "น้อง", text)
    # "ฉัน" → "น้อง"
    text = re.sub(r"ฉัน", "น้อง", text)
    # Strip full English sentences (any run of ASCII words ending with punctuation or newline)
    text = re.sub(r"[A-Za-z][A-Za-z0-9 ,'\-]{8,}[.!?]", "", text)
    text = re.sub(r" {2,}", " ", text).strip()
    return text


# ═══════════════════════════════════════════════════════════════════════
# Thai system prompts
# ═══════════════════════════════════════════════════════════════════════

_THAI_DAYS = {0: "จันทร์", 1: "อังคาร", 2: "พุธ", 3: "พฤหัสบดี",
              4: "ศุกร์", 5: "เสาร์", 6: "อาทิตย์"}


def _current_datetime_str() -> str:
    from datetime import datetime, timezone, timedelta
    tz_thai = timezone(timedelta(hours=7))
    now = datetime.now(tz_thai)
    day_th = _THAI_DAYS[now.weekday()]
    return f"วัน{day_th} เวลา {now.strftime('%H:%M')} น."


def build_chatbot_system_prompt(student_name: str, student_year: int) -> str:
    """
    Dynamic system prompt that personalises the response for a specific student.
    """
    return (
        f"คุณคือ \"น้องสาธุ\" หุ่นยนต์บริการหญิงประจำสถาบันเทคโนโลยีพระจอมเกล้าเจ้าคุณทหารลาดกระบัง "
        f"หรือเรียกสั้นๆ ว่า ลาดกระบัง\n"
        f"คุณกำลังพูดคุยกับ {student_name}\n"
        f"ปัจจุบัน: {_current_datetime_str()}\n\n"
        "ข้อมูลเกี่ยวกับตัวคุณ:\n"
        "- คุณทำงานประจำอยู่ที่ชั้น 12 ของตึก E-12 (ตึกสิบสอง) ในโซน D ของสถาบัน\n"
        "- ตึก E-12 คืออาคารเรียนรวม 12 ชั้น ของคณะวิศวกรรมศาสตร์ มีลานจอดรถอยู่ด้านหน้าและด้านข้างของตึก ทางฝั่งตรงข้ามมีโรงอาหารและร้านสะดวกซื้อ\n\n"
        "ความสามารถของคุณมีเพียง 3 อย่างเท่านั้น:\n"
        "1. ทักทายนักศึกษาและพูดคุยในหัวข้อทั่วไป\n"
        "2. ตอบคำถามเกี่ยวกับสถาบัน เช่น ตารางเรียน หลักสูตร\n"
        "3. นำทางไปยังสถานที่ภายในตึกสิบสอง (E-12 Building) ได้แก่ห้อง A | B | C เท่านั้น\n\n"
        "กฎที่ต้องปฏิบัติเสมอ:\n"
        f"- ถ้าจะเรียกชื่อนักศึกษาให้ใช้ว่า \"{student_name}\" เท่านั้น ห้ามใส่นามสกุลหรือวงเล็บ\n"
        f"- ห้ามขึ้นต้นประโยคด้วยชื่อนักศึกษา (\"{student_name}\") ไม่ว่ากรณีใด\n"
        "- ตอบเป็นภาษาไทยเสมอ ไม่ว่านักศึกษาจะพูดภาษาใด\n"
        "- ตอบสั้น กระชับ ไม่เกิน 2 ประโยค เพราะข้อความจะถูกแปลงเป็นเสียงพูด\n"
        
    )
# "- ถ้าคำถามอยู่นอกเหนือความสามารถ 3 อย่างข้างต้น ให้ตอบว่า \"น้องสาธุไม่มีข้อมูลเรื่องนั้นค่ะ
# ═══════════════════════════════════════════════════════════════════════
# Ollama HTTP client
# ═══════════════════════════════════════════════════════════════════════

class TyphoonClient:
    """
    Thin HTTP wrapper around Ollama's /api/generate and /api/chat endpoints.
    Works with any model served via Ollama (Typhoon, Qwen2.5, Llama, etc.).

    Adapted from final_docker_component/src/utils_rag.py OllamaClient.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "typhoon2-7b-instruct",
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        temperature: float = 0.5,
        max_tokens: int = 512,
    ) -> str:
        """Single-turn prompt → response (no chat history)."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "")
            return clean_cjk(text)
        except Exception as exc:
            logger.error("TyphoonClient.generate error: %s", exc)
            return "รบกวนพูดใหม่อีกครั้งได้มั้ยคะ"

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        """
        Multi-turn chat interface.
        messages = [{"role": "system"|"user"|"assistant", "content": str}, ...]
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
            return clean_cjk(content)
        except Exception as exc:
            logger.error("TyphoonClient.chat error: %s", exc)
            return "รบกวนพูดใหม่อีกทีได้มั้ยคะ"

    # ------------------------------------------------------------------
    def generate_structured(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 512,
    ) -> Optional[Dict[str, Any]]:
        """
        Call LLM and parse JSON from the response.
        Uses Ollama format=json to force valid JSON output.
        Handles responses wrapped in ```json ... ``` markdown blocks.
        Returns None if parsing fails.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            raw = clean_cjk(resp.json().get("response", ""))
        except Exception as exc:
            logger.error("TyphoonClient.generate_structured error: %s", exc)
            return None

        # Strip markdown code fences
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        # Strip trailing commas before closing braces/brackets (common LLM mistake)
        raw = re.sub(r",\s*([}\]])", r"\1", raw)
        # Find the first JSON object in the response
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("generate_structured: no JSON found in response: %r", raw[:200])
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning("generate_structured: JSON parse error: %s", exc)
            return None

    # ------------------------------------------------------------------
    def chat_structured(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 512,
    ) -> Optional[Dict[str, Any]]:
        """chat() variant that parses JSON from the response."""
        raw = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("chat_structured: no JSON found in response: %r", raw[:200])
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning("chat_structured: JSON parse error: %s", exc)
            return None
