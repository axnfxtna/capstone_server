"""
mcp/llm_chatbot.py  (llm_chatbot)
===================================
Core conversation engine.

Flow per turn:
  1. Route the question to the right dataset collection (curriculum /
     uni_info / time_table) — adapted from rag_pipeline.py route_query()
  2. Retrieve top-k RAG context from the relevant Milvus collection
  3. Retrieve top-k conversation memory summary from memory_manager
  4. Build full LLM context (system prompt + memories + RAG + history + question)
  5. Call Typhoon LLM → parse ChatbotResponse JSON
  6. Store turn in memory_manager (async)
  7. Return ChatbotResponse

Dataset collections searched (from final_docker_component):
  - curriculum   → questions about courses, credits, subject names
  - time_table   → schedule, exam timetable questions
  - uni_info     → campus location, building, facilities (default)
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple

from llm.typhoon_client import TyphoonClient, SYSTEM_PROMPT, build_chatbot_system_prompt, enforce_female_particle
from mcp.memory_manager import MemoryManager
from vector_db import milvus_client
from database.mysql_client import fetch_timetable_rows, fetch_student_context

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Query routing — adapted from utils_rag.py route_query()
# ═══════════════════════════════════════════════════════════════════════

# Routing priority: chat_history → mysql_students → time_table → curriculum → default
# time_table must come before curriculum because "เรียน" appears in both.
_ROUTE_KEYWORDS: Dict[str, List[str]] = {
    "chat_history": [
        # Explicit memory references
        "ครั้งที่แล้ว", "เมื่อกี้", "ก่อนหน้า", "ถามอะไร",
        "คุยอะไร", "ประวัติ", "history", "previous",
        # Identity / intro — robot should answer from its own context
        "คุณคือ", "แนะนำตัว", "ชื่ออะไร", "คือใคร", "คุณเป็น",
        "ทำอะไร", "ทำอะไรได้",
        # Conversational / emotional / greetings
        "สวัสดี", "หวัดดี", "ยินดี", "ดีใจ", "เป็นยังไง",
        "สบายดี", "เป็นไง", "โอเค", "โอ้เค", "ขอบคุณ", "ขอบใจ",
        "มู้ด", "รู้สึก", "อารมณ์",
    ],
    "mysql_students": [
        "นักศึกษา", "อีเมล", "นศ", "รหัสนักศึกษา",
        "สมาชิก", "ใครบ้าง", "คนไหน", "รุ่น",
        "student", "email",
    ],
    "time_table": [
        "ตารางเรียน", "ตารางสอบ", "ตาราง",
        "เวลาเรียน", "คาบเรียน", "วันเรียน",
        "วันไหน", "เวลาไหน", "กี่โมง",
        "exam", "schedule", "class", "สอบ", "timetable",
    ],
    "curriculum": [
        "วิชา", "หลักสูตร", "หน่วยกิต", "รายวิชา", "คอร์ส", "เนื้อหา",
        "เรียน", "course", "credit", "subject", "curriculum",
    ],
}

# English augmentation for curriculum queries (mirrors utils_rag.py)
_THAI_TO_ENG: Dict[str, str] = {
    "วิชา": "courses subjects",
    "หลักสูตร": "curriculum program",
    "เรียน": "study learn",
    "หน่วยกิต": "credits",
    "รายวิชา": "course list",
    "คอร์ส": "courses",
    "สอน": "teaching",
    "เนื้อหา": "content",
    "ปี": "year",
}


def _route_query(question: str) -> str:
    q_lower = question.lower()
    for collection, keywords in _ROUTE_KEYWORDS.items():
        if any(kw in q_lower for kw in keywords):
            return collection
    # Default: treat unmatched questions as casual conversation
    # (uses memory + session history context rather than hitting a wrong DB collection)
    return "chat_history"


def _augment_for_curriculum(question: str) -> str:
    extra = []
    for th, en in _THAI_TO_ENG.items():
        if th in question:
            extra.append(en)
    return question + " " + " ".join(extra) if extra else question


def _build_rag_context(
    question: str,
    collection: str,
    top_k: int = 5,
    mysql_cfg: Optional[dict] = None,
) -> str:
    """Retrieve relevant context for the given route and format as a Thai string.

    Routes:
      chat_history   — hint string; actual memory is already in memory_summary/history
      mysql_students — fetch Students + Academic_Year from MySQL
      time_table     — Milvus IDs → MySQL ExcelTimetableData row_text
      curriculum     — dual Thai+English Milvus search
      uni_info       — standard Milvus search
    """
    cfg = mysql_cfg or {}
    mysql_kwargs = dict(
        host=cfg.get("host", "localhost"),
        port=int(cfg.get("port", 3306)),
        user=cfg.get("user", "root"),
        password=cfg.get("password", "root"),
        database=cfg.get("database", "capstone"),
    )

    # ── chat_history ──────────────────────────────────────────────────
    if collection == "chat_history":
        # Long-term memory is already injected via memory_summary; recent
        # session turns via history_str. Return a directing hint only.
        return "(ดูข้อมูลการสนทนาจาก 'ข้อมูลที่จำได้' และ 'บทสนทนาล่าสุด' ด้านบน)"

    # ── mysql_students ────────────────────────────────────────────────
    if collection == "mysql_students":
        context = fetch_student_context(**mysql_kwargs)
        return context or "(ไม่พบข้อมูลนักศึกษาในฐานข้อมูล)"

    # ── Milvus-backed routes ──────────────────────────────────────────
    try:
        if collection == "curriculum":
            # Dual search: Thai + English augmented
            hits1 = milvus_client.search_collection(question, collection, top_k)
            hits2 = milvus_client.search_collection(
                _augment_for_curriculum(question), collection, top_k
            )
            seen = set()
            hits = []
            for h in hits1 + hits2:
                if h["id"] not in seen:
                    seen.add(h["id"])
                    hits.append(h)
            hits = sorted(hits, key=lambda x: x["score"], reverse=True)[:top_k]
        else:
            hits = milvus_client.search_collection(question, collection, top_k)
    except Exception as exc:
        logger.error("RAG search failed for %s: %s", collection, exc)
        return ""

    if not hits:
        return ""

    # time_table: IDs in Milvus are the MySQL row_ids — fetch text from MySQL
    if collection == "time_table":
        row_ids = []
        for h in hits:
            try:
                row_ids.append(int(h["id"]))
            except (ValueError, TypeError):
                pass
        if not row_ids:
            return ""
        rows = fetch_timetable_rows(row_ids, **mysql_kwargs)
        lines = []
        for i, h in enumerate(hits, 1):
            text = rows.get(int(h["id"]), "")
            if text:
                lines.append(f"[{i}] {text}")
        return "\n".join(lines)

    # All other collections: text fields are stored directly in Milvus
    lines = []
    for i, h in enumerate(hits, 1):
        parts = []
        for k, v in h.items():
            if k not in ("score", "id") and v:
                parts.append(f"{k}: {v}")
        if parts:
            lines.append(f"[{i}] " + " | ".join(parts))
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Chatbot response schema
# ═══════════════════════════════════════════════════════════════════════

_CHATBOT_PROMPT_TEMPLATE = """\
{system_prompt}

ข้อมูลที่จำได้เกี่ยวกับนักศึกษาคนนี้:
{memory_summary}

ข้อมูลอ้างอิงจากระบบ:
{rag_context}

บทสนทนาล่าสุด:
{history}

คำถามปัจจุบัน: {question}

intent ให้เลือกตามนี้:
- chat     = สนทนาทั่วไป ทักทาย ถามเรื่องตัวเอง
- info     = ถามข้อมูลมหาวิทยาลัย ตารางเรียน หลักสูตร
- navigate = ต้องการไปยังสถานที่ในตึกโหล
- farewell = กล่าวลา ไม่ต้องการความช่วยเหลือแล้ว

ตอบกลับเป็น JSON เท่านั้น รูปแบบ:
{{
  "reply_text": "ข้อความตอบกลับภาษาไทย",
  "intent": "chat | info | navigate | farewell",
  "destination": "ชื่อสถานที่ภาษาไทย หรือ null"
}}
"""

_FALLBACK_RESPONSE = {
    "reply_text": "ขออภัยค่ะ ไม่เข้าใจคำถาม ช่วยพูดอีกครั้งได้ไหมค่ะ",
    "intent": "chat",
    "destination": None,
    "confidence": 0.3,
}


class LLMChatbot:
    def __init__(
        self,
        llm: TyphoonClient,
        memory_manager: MemoryManager,
        rag_top_k: int = 5,
        mem_top_k: int = 3,
        mysql_cfg: Optional[dict] = None,
    ):
        self.llm = llm
        self.memory = memory_manager
        self.rag_top_k = rag_top_k
        self.mem_top_k = mem_top_k
        self.mysql_cfg = mysql_cfg or {}

    # ------------------------------------------------------------------
    def ask(
        self,
        question: str,
        session_id: str,
        student_id: str,
        student_name: str,
        student_year: int,
        history: List[Tuple[str, str]],  # [(user, bot), ...]  last N turns
    ) -> Dict:
        """
        Full RAG + memory chatbot turn.
        Returns a dict matching ChatbotResponse schema.
        """
        # 1. Route query
        collection = _route_query(question)

        # 2. RAG context from dataset
        rag_context = _build_rag_context(question, collection, self.rag_top_k, self.mysql_cfg)

        # 3. Conversation memory
        memory_summary = self.memory.retrieve(question, student_id)

        # 4. Format short-term history
        history_lines = []
        for q, a in history[-5:]:
            history_lines.append(f"นักศึกษา: {q}")
            history_lines.append(f"ขนมทาน: {a}")
        history_str = "\n".join(history_lines) if history_lines else "(ยังไม่มีประวัติการสนทนา)"

        # 5. Build system prompt
        system_prompt = build_chatbot_system_prompt(student_name, student_year)

        # 6. Build full prompt
        prompt = _CHATBOT_PROMPT_TEMPLATE.format(
            system_prompt=system_prompt,
            memory_summary=memory_summary or "(ไม่มีข้อมูลที่จำได้)",
            rag_context=rag_context or "(ไม่มีข้อมูลอ้างอิง)",
            history=history_str,
            question=question,
        )

        # 7. Call LLM
        parsed = self.llm.generate_structured(prompt, temperature=0.7, max_tokens=512)
        if not parsed or "reply_text" not in parsed:
            logger.warning("LLMChatbot: LLM returned no valid JSON — using fallback")
            return dict(_FALLBACK_RESPONSE)

        # Validate intent
        valid_intents = {"chat", "info", "navigate", "farewell"}
        if parsed.get("intent") not in valid_intents:
            parsed["intent"] = "chat"

        reply_text = enforce_female_particle(
            parsed.get("reply_text", _FALLBACK_RESPONSE["reply_text"])
        )
        return {
            "reply_text":     reply_text,
            "intent":         parsed.get("intent", "chat"),
            "destination":    parsed.get("destination"),
            "rag_collection": collection,
        }

    # ------------------------------------------------------------------
    async def ask_and_store(
        self,
        question: str,
        session_id: str,
        student_id: str,
        student_name: str,
        student_year: int,
        history: List[Tuple[str, str]],
    ) -> Dict:
        """
        ask() + store the turn in memory.  Fire-and-forget the store.
        """
        response = self.ask(
            question, session_id, student_id, student_name, student_year, history
        )
        # Store asynchronously — do not await; don't block the response
        asyncio.create_task(
            self.memory.store(
                session_id=session_id,
                student_id=student_id,
                user_text=question,
                bot_reply=response["reply_text"],
                intent=response["intent"],
            )
        )
        return response
