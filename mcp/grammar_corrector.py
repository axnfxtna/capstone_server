"""
mcp/grammar_corrector.py  (mcp_grammar)
========================================
Receives raw STT text, corrects Thai grammar via LLM, returns corrected text.
If the LLM call fails, the raw STT text is forwarded unchanged.
"""

import logging
from llm.typhoon_client import TyphoonClient

logger = logging.getLogger(__name__)

# Skip LLM correction for very short inputs — low chance of meaningful STT errors,
# high chance of over-correction (added particles, changed tone)
_MIN_CHARS = 15

_SYSTEM = (
    "คุณคือ STT Text Normalizer สำหรับระบบหุ่นยนต์ภาษาไทย\n"
    "หน้าที่ของคุณคือทำความสะอาดข้อความที่ถอดเสียงมาจาก STT เท่านั้น\n"
    "ไม่ใช่นักเขียน ไม่ใช่ผู้แก้ไขภาษา — แค่แก้คำที่ STT ฟังผิดหรือสะกดผิด\n\n"
    "กฎสำคัญ:\n"
    "- รักษารูปแบบและโทนของต้นฉบับ 100% (ภาษาพูดให้คงเป็นภาษาพูด)\n"
    "- ถ้าต้นฉบับไม่มีคำลงท้าย (ครับ/ค่ะ/นะ) ห้ามเติม\n"
    "- ถ้าต้นฉบับมีคำลงท้าย ให้คงไว้ตามเดิม ห้ามเปลี่ยน\n"
    "- ห้ามเปลี่ยนความหมาย เพิ่มคำ หรือตัดทอนประโยค\n"
    "- ห้ามตอบคำถาม ห้ามเพิ่มข้อมูลใดๆ — ถ้า input เป็นคำถาม ให้คืนคำถามนั้นกลับไปโดยแก้เฉพาะตัวสะกด\n"
    "- ตอบกลับเฉพาะข้อความที่แก้ไขแล้วเท่านั้น ห้ามอธิบาย\n\n"
    "ตัวอย่าง:\n"
    "Input:  ไปไหนมา\n"
    "Output: ไปไหนมา\n\n"
    "Input:  ขอบคุณครับ\n"
    "Output: ขอบคุณครับ\n\n"
    "Input:  วันนี้มีวิชาอาไรบ้าง\n"
    "Output: วันนี้มีวิชาอะไรบ้าง\n\n"
    "Input:  หลักสูตร RAI มีวิชาอะไรบ้าง\n"
    "Output: หลักสูตร RAI มีวิชาอะไรบ้าง\n\n"
    "Input:  เราพบกันล่าสุดเมือไหร่นะ\n"
    "Output: เราพบกันล่าสุดเมื่อไหร่นะ"
)


class GrammarCorrector:
    def __init__(self, llm: TyphoonClient):
        self.llm = llm

    def correct(self, raw_text: str) -> str:
        """
        Return grammar-corrected Thai text using chat API.
        Falls back to raw_text if LLM fails or returns empty.
        """
        if not raw_text or not raw_text.strip():
            return raw_text
        if len(raw_text.strip()) < _MIN_CHARS:
            logger.debug("GrammarCorrector: short input (%d chars), skipping LLM", len(raw_text.strip()))
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
            # Discard if output is suspiciously short (truncated) or much longer (hallucinated)
            raw_len = len(raw_text.strip())
            if len(corrected) < raw_len * 0.5:
                logger.warning(
                    "GrammarCorrector: output too short (%d vs %d chars), using raw",
                    len(corrected), raw_len,
                )
                return raw_text
            if len(corrected) > raw_len * 1.5:
                logger.warning(
                    "GrammarCorrector: output too long (%d vs %d chars), LLM likely hallucinated — using raw",
                    len(corrected), raw_len,
                )
                return raw_text
            return corrected
        except Exception as exc:
            logger.error("GrammarCorrector LLM error: %s — forwarding raw text", exc)
            return raw_text
