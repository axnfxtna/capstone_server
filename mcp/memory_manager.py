"""
mcp/memory_manager.py  (mcp_summary)
======================================
Memory retrieval and summarisation.

Write flow (/history):
  1. Log raw turn to SQLite (always, for debugging)
  2. Ask LLM to summarise the turn into 1-2 Thai sentences
  3. Embed the summary with all-MiniLM-L6-v2
  4. Insert into Milvus conversation_memory collection

Read flow (/summary):
  1. Embed the query
  2. Search Milvus conversation_memory filtered by student_id
  3. Return top-k summary strings concatenated as context
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from database import sqlite_client
from llm.typhoon_client import TyphoonClient
from vector_db import milvus_client

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Summary prompt
# ═══════════════════════════════════════════════════════════════════════

_SUMMARY_PROMPT = """\
สรุปการสนทนานี้เป็นภาษาไทย 1-2 ประโยค โดยเน้น:
- คำสำคัญ: ชื่อตึก ชื่อวิชา ชื่อสถานที่ หรือหัวข้อโปรเจกต์
- สถานะล่าสุด: นักศึกษาต้องการอะไร และผลลัพธ์สุดท้ายคืออะไร

กฎสำคัญ: ห้ามตัดชื่อเฉพาะทิ้ง และห้ามอธิบายเพิ่มเติม

นักศึกษา: {user_text}
หุ่นยนต์: {bot_reply}
"""


class MemoryManager:
    def __init__(
        self,
        llm: TyphoonClient,
        db_path: str = "./database/metadata.db",
        emb_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        top_k: int = 3,
    ):
        self.llm = llm
        self.db_path = db_path
        self.emb_model = emb_model
        self.top_k = top_k

    # ------------------------------------------------------------------
    async def store(
        self,
        session_id: str,
        student_id: Optional[str],
        user_text: str,
        bot_reply: str,
        intent: str,
    ) -> None:
        """
        Persist one conversation turn:
          - Always writes raw text to SQLite
          - Generates LLM summary → embeds → writes to Milvus
        """
        ts = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%dT%H:%M:%S+07:00")

        # 1. Raw log in SQLite (fire-and-forget, never blocks the response)
        try:
            await sqlite_client.log_turn(
                session_id=session_id,
                user_text=user_text,
                bot_reply=bot_reply,
                intent=intent,
                student_id=student_id,
                db_path=self.db_path,
            )
        except Exception as exc:
            logger.error("SQLite log_turn failed: %s", exc)

        # 2. Summarise turn with LLM
        summary_prompt = _SUMMARY_PROMPT.format(
            user_text=user_text, bot_reply=bot_reply
        )
        try:
            summary_text = self.llm.generate(
                summary_prompt, temperature=0.3, max_tokens=256
            )
        except Exception as exc:
            logger.error("Summary LLM call failed: %s — using raw user_text", exc)
            summary_text = f"นักศึกษาถาม: {user_text[:200]}"

        # 3. Embed + insert into Milvus
        try:
            milvus_client.insert_memory(
                student_id=student_id or "unknown",
                session_id=session_id,
                raw_user_text=user_text,
                raw_bot_reply=bot_reply,
                summary_text=summary_text,
                intent=intent,
                timestamp=ts,
                emb_model=self.emb_model,
            )
        except Exception as exc:
            logger.error("Milvus insert_memory failed: %s", exc)

    # ------------------------------------------------------------------
    def retrieve(
        self,
        query: str,
        student_id: str,
    ) -> str:
        """
        Retrieve top-k relevant memory summaries for a student.
        Returns a single Thai string to inject into the LLM context.
        """
        try:
            hits = milvus_client.search_memory(
                query=query,
                student_id=student_id,
                top_k=self.top_k,
                emb_model=self.emb_model,
            )
        except Exception as exc:
            logger.error("Milvus search_memory failed: %s", exc)
            return ""

        if not hits:
            return ""

        lines = []
        for h in hits:
            ts = h.get("timestamp", "")[:10]
            summary = h.get("summary_text", "")
            if summary:
                lines.append(f"[{ts}] {summary}")
        return "\n".join(lines)
