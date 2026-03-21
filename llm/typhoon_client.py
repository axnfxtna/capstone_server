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
    Replace masculine particle ครับ with ค่ะ in any LLM-generated Thai text.
    The robot is female (ขนมทาน) and must always use ค่ะ.
    Handles: ครับ, ครับผม, นะครับ — replaces with ค่ะ equivalents.
    """
    # "นะครับ" → "นะค่ะ"
    text = re.sub(r"นะครับ", "นะค่ะ", text)
    # standalone "ครับผม" → "ค่ะ"
    text = re.sub(r"ครับผม", "ค่ะ", text)
    # standalone "ครับ" → "ค่ะ"
    text = re.sub(r"ครับ", "ค่ะ", text)
    return text


# ═══════════════════════════════════════════════════════════════════════
# Thai system prompts
# ═══════════════════════════════════════════════════════════════════════

# Base system prompt — used by llm_chatbot for general conversation
SYSTEM_PROMPT = (
    "คุณคือหุ่นยนต์บริการชื่อ \"ขนมทาน\" ของสถาบันเทคโนโลยีพระจอมเกล้าเจ้าคุณทหารลาดกระบัง (KMITL)\n"
    "คุณช่วยเหลือนักศึกษาหลักสูตร Robotics and AI Engineering (RAI)\n\n"
    "กฎสำคัญที่ต้องปฏิบัติตามอย่างเคร่งครัด:\n"
    "1. ตอบเป็นภาษาไทยเท่านั้น สามารถใช้คำภาษาอังกฤษได้เฉพาะชื่อเฉพาะ เช่น KMITL, RAI, email\n"
    "2. ห้ามใช้ตัวอักษรภาษาจีน ภาษาเกาหลี หรือภาษาญี่ปุ่นโดยเด็ดขาด\n"
    "3. ตอบสั้น กระชับ และเป็นมิตร ใช้ภาษาสุภาพ ลงท้ายด้วย \"ค่ะ\" เสมอ ห้ามใช้ \"ครับ\"\n"
    "4. ถ้าไม่มีข้อมูลให้บอกตรงๆ ว่าไม่ทราบ\n"
    "5. ใช้คำว่า \"ห้องปฏิบัติการ\" หรือ \"แลป\" แทนคำว่า lab"
)


def build_chatbot_system_prompt(student_name: str, student_year: int) -> str:
    """
    Dynamic system prompt that personalises the response for a specific student.
    Adapted from final_docker_component/src/utils_rag.py build_dynamic_system_prompt().
    """
    year_label = {1: "น้องปี 1", 2: "น้องปี 2", 3: "น้องปี 3", 4: "พี่ปี 4"}.get(
        student_year, "คุณ"
    )
    return (
        f"คุณคือหุ่นยนต์บริการชื่อ \"ขนมทาน\" ของ KMITL\n"
        f"คุณกำลังพูดคุยกับ {student_name} ({year_label})\n\n"
        "กฎสำคัญ:\n"
        f"1. เรียกนักศึกษาว่า \"{year_label}\" หรือ \"{student_name}\"\n"
        "2. ตอบเป็นภาษาไทยเสมอ ลงท้ายด้วย \"ค่ะ\" เสมอ ห้ามใช้ \"ครับ\" โดยเด็ดขาด\n"
        "3. ตอบสั้น กระชับ 1-3 ประโยค\n"
        "4. ห้ามใช้อักษรจีน เกาหลี หรือญี่ปุ่น\n"
        "5. ถ้าไม่รู้ให้บอกตรงๆ ว่าไม่ทราบ"
    )


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
            return "ขออภัยค่ะ ไม่สามารถประมวลผลได้ในขณะนี้"

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
            return "ขออภัยค่ะ ไม่เข้าใจ"

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
