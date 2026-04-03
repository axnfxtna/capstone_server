"""
llm/typhoon_client.py
=====================
HTTP wrapper for OpenAI-compatible LLM endpoints (vLLM / Ollama /v1).

Both vLLM and Ollama expose an OpenAI-compatible API at /v1/chat/completions,
so this client works with either backend — just point base_url at the right
server in config/settings.yaml.

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

_THAI_MONTHS = {
    1: "มกราคม", 2: "กุมภาพันธ์", 3: "มีนาคม", 4: "เมษายน",
    5: "พฤษภาคม", 6: "มิถุนายน", 7: "กรกฎาคม", 8: "สิงหาคม",
    9: "กันยายน", 10: "ตุลาคม", 11: "พฤศจิกายน", 12: "ธันวาคม",
}


def _current_datetime_str() -> str:
    from datetime import datetime, timezone, timedelta
    from mcp.tts_router import thai_time_str
    tz_thai = timezone(timedelta(hours=7))
    now = datetime.now(tz_thai)
    day_th = _THAI_DAYS[now.weekday()]
    month_th = _THAI_MONTHS[now.month]
    year_be = now.year + 543          # Buddhist Era (พ.ศ.)
    time_th = thai_time_str(now.hour, now.minute)
    return f"วัน{day_th}ที่ {now.day} {month_th} พ.ศ. {year_be} เวลา{time_th}"


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
        "ข้อ 1. ทักทายและสนทนาทั่วไป\n"
        "ข้อ 2. ตอบคำถามเกี่ยวกับสถาบัน เช่น ตารางเรียน หลักสูตร\n"
        "ข้อ 3. นำทางภายในตึกสิบสอง (E-12) ได้แก่ห้อง A | B | C เท่านั้น\n\n"
        "กฎที่ต้องปฏิบัติเสมอ:\n"
        f"- ถ้าจะเรียกชื่อนักศึกษาให้ใช้ว่า \"{student_name}\" เท่านั้น ห้ามใส่นามสกุลหรือวงเล็บ\n"
        f"- ห้ามขึ้นต้นประโยคด้วยชื่อนักศึกษา (\"{student_name}\") ไม่ว่ากรณีใด\n"
        "- ตอบเป็นภาษาไทยเสมอ ไม่ว่านักศึกษาจะพูดภาษาใด\n"
        "- ตอบสั้น กระชับ ไม่เกิน 2 ประโยค เพราะข้อความจะถูกแปลงเป็นเสียงพูด\n"
        "- เมื่อสนทนาทั่วไป ให้ตอบเป็นกันเองตามธรรมชาติ ห้ามปฏิเสธ\n"
        "- เมื่อถามว่าช่วยอะไรได้บ้าง ให้บอกว่าช่วยได้ 3 อย่าง: (1) สนทนาทั่วไป (2) ข้อมูลสถาบัน (3) นำทางในตึก\n"
        "- เมื่อคำขออยู่นอกขอบเขต 3 อย่างนั้น (เช่น แปลภาษา ทำการบ้าน โทรหาคน) ให้ปฏิเสธสั้นๆเป็นกันเอง\n"
    )
# "- ถ้าคำถามอยู่นอกเหนือความสามารถ 3 อย่างข้างต้น ให้ตอบว่า \"น้องสาธุไม่มีข้อมูลเรื่องนั้นค่ะ
# ═══════════════════════════════════════════════════════════════════════
# OpenAI-compatible client (vLLM / Ollama /v1)
# ═══════════════════════════════════════════════════════════════════════

class TyphoonClient:
    """
    HTTP wrapper around any OpenAI-compatible /v1/chat/completions endpoint.

    Works with:
      - vLLM  (recommended for 70B — set base_url to http://localhost:8000/v1)
      - Ollama /v1 (for 8B fast model — set base_url to http://localhost:11434/v1)

    vLLM accepts top_k and repetition_penalty as extensions to the OpenAI spec.
    Ollama /v1 accepts top_k and ignores unknown params gracefully.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        model: str = "scb10x/llama3.1-typhoon2-70b-instruct",
        timeout: int = 60,
        temperature: float = 0.7,
        max_tokens: int = 150,
        top_k: int = 30,
        top_p: float = 0.85,
        repeat_penalty: float = 1.15,
        num_ctx: int = 4096,  # kept for config compatibility; set at vLLM startup, not per-request
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_k = top_k
        self.top_p = top_p
        self.repeat_penalty = repeat_penalty
        self.num_ctx = num_ctx

    def _call(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        top_k: Optional[int] = None,
        json_mode: bool = False,
    ) -> Optional[str]:
        """Core POST to /v1/chat/completions. Returns content string or None on error."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": self.top_p,
            "top_k": top_k if top_k is not None else self.top_k,
            "repetition_penalty": self.repeat_penalty,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return clean_cjk(content)
        except Exception as exc:
            logger.error("TyphoonClient._call error: %s", exc)
            return None

    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        temperature: float = None,
        max_tokens: int = None,
    ) -> str:
        """Single-turn prompt → response (no chat history)."""
        temperature = temperature if temperature is not None else self.temperature
        max_tokens  = max_tokens  if max_tokens  is not None else self.max_tokens
        result = self._call(
            [{"role": "user", "content": prompt}],
            temperature, max_tokens,
        )
        return result if result is not None else "รบกวนพูดใหม่อีกครั้งได้มั้ยคะ"

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
    ) -> str:
        """
        Multi-turn chat interface.
        messages = [{"role": "system"|"user"|"assistant", "content": str}, ...]
        """
        temperature = temperature if temperature is not None else self.temperature
        max_tokens  = max_tokens  if max_tokens  is not None else self.max_tokens
        result = self._call(messages, temperature, max_tokens)
        return result if result is not None else "รบกวนพูดใหม่อีกทีได้มั้ยคะ"

    # ------------------------------------------------------------------
    def generate_structured(
        self,
        prompt: str,
        temperature: float = None,
        max_tokens: int = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Call LLM and parse JSON from the response.
        Uses response_format=json_object to enforce valid JSON output.
        Handles responses wrapped in ```json ... ``` markdown blocks.
        Returns None if parsing fails.
        """
        temperature = temperature if temperature is not None else max(self.temperature - 0.4, 0.1)
        max_tokens  = max_tokens  if max_tokens  is not None else self.max_tokens
        # Tighter top_k for JSON generation to reduce hallucinated keys
        top_k = min(self.top_k, 20)
        try:
            raw = self._call(
                [{"role": "user", "content": prompt}],
                temperature, max_tokens,
                top_k=top_k,
                json_mode=True,
            )
            if raw is None:
                return None
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
        raw = self._call(messages, temperature, max_tokens, json_mode=True)
        if raw is None:
            return None
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
