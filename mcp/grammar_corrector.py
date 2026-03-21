"""
mcp/grammar_corrector.py  (mcp_grammar)
========================================
Receives raw STT text, corrects Thai grammar via LLM, returns corrected text.
If the LLM call fails, the raw STT text is forwarded unchanged.
"""

import logging
from llm.typhoon_client import TyphoonClient

logger = logging.getLogger(__name__)

_SYSTEM = (
    "คุณคือผู้ช่วยแก้ไขคำพูดภาษาไทยที่ถอดเสียงมาจากระบบ STT\n"
    "หน้าที่: แก้ไขการสะกดผิดและคำที่ฟังไม่ชัด ให้เป็นประโยคภาษาไทยที่ถูกต้อง\n"
    "กฎ:\n"
    "- ตอบกลับเฉพาะข้อความที่แก้ไขแล้วเท่านั้น ห้ามอธิบายเพิ่มเติม\n"
    "- ถ้าข้อความถูกต้องอยู่แล้ว ให้ตอบกลับข้อความเดิมโดยไม่เปลี่ยนแปลง\n"
    "- ห้ามเปลี่ยนความหมายหรือเพิ่มคำที่ไม่มีในต้นฉบับ\n"
    "- ห้ามเปลี่ยนคำลงท้าย เช่น ครับ ค่ะ นะ ให้คงไว้ตามต้นฉบับ\n"
    "- ห้ามตัดทอนประโยคให้สั้นลง ให้คงความยาวและความหมายเดิม"
)


class GrammarCorrector:
    def __init__(self, llm: TyphoonClient):
        self.llm = llm

    def correct(self, raw_text: str, confidence: float = 0.0) -> str:
        """
        Return grammar-corrected Thai text using chat API.
        Falls back to raw_text if LLM fails or returns empty.
        Skips LLM call if STT confidence >= 0.85 (clean input, no over-correction risk).
        """
        if not raw_text or not raw_text.strip():
            return raw_text
        if confidence >= 0.85:
            logger.debug("Grammar skip: STT confidence=%.2f is high, passing through", confidence)
            return raw_text
        try:
            corrected = self.llm.chat(
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": raw_text.strip()},
                ],
                temperature=0.1,
                max_tokens=256,
            )
            corrected = corrected.strip()
            if not corrected:
                logger.warning("GrammarCorrector: empty LLM response, using raw")
                return raw_text
            # If output is suspiciously short compared to input, discard it
            if len(corrected) < len(raw_text.strip()) * 0.5:
                logger.warning(
                    "GrammarCorrector: output too short (%d vs %d chars), using raw",
                    len(corrected), len(raw_text.strip()),
                )
                return raw_text
            return corrected
        except Exception as exc:
            logger.error("GrammarCorrector LLM error: %s — forwarding raw text", exc)
            return raw_text
