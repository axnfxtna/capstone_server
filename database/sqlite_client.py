"""
database/sqlite_client.py
==========================
Raw conversation log store using SQLite (aiosqlite for async).
Stores every conversation turn verbatim for debugging.
Milvus stores only the embedded summary; SQLite has the full raw text.
"""

import aiosqlite
import logging
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Default DB path — overridden by settings.yaml
_DEFAULT_DB = "./database/metadata.db"


async def init_db(db_path: str = _DEFAULT_DB) -> None:
    """Create tables if they don't exist."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                student_id  TEXT,
                user_text   TEXT NOT NULL,
                bot_reply   TEXT NOT NULL,
                intent      TEXT,
                timestamp   TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY,
                student_id   TEXT,
                student_name TEXT,
                created_at   TEXT NOT NULL,
                last_active  TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_session
            ON conversation_log(session_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_student
            ON conversation_log(student_id)
        """)
        await db.commit()
    logger.info("SQLite DB initialised at %s", db_path)


async def log_turn(
    session_id: str,
    user_text: str,
    bot_reply: str,
    intent: str,
    student_id: Optional[str] = None,
    db_path: str = _DEFAULT_DB,
) -> None:
    """Append one conversation turn to the raw log."""
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO conversation_log
                (session_id, student_id, user_text, bot_reply, intent, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, student_id, user_text, bot_reply, intent, ts),
        )
        await db.commit()


async def get_turns(
    session_id: str,
    limit: int = 10,
    db_path: str = _DEFAULT_DB,
) -> List[Dict]:
    """Return the most recent turns for a session (newest first)."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM conversation_log
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in reversed(rows)]


async def get_latest_session(
    student_id: str,
    db_path: str = _DEFAULT_DB,
) -> Optional[Dict]:
    """Return the most recent session row for a student (used for restart recovery)."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM sessions
            WHERE student_id = ?
            ORDER BY last_active DESC
            LIMIT 1
            """,
            (student_id,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def upsert_session(
    session_id: str,
    student_id: Optional[str],
    student_name: Optional[str],
    db_path: str = _DEFAULT_DB,
) -> None:
    """Create or update a session record."""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO sessions (session_id, student_id, student_name, created_at, last_active)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                student_id   = excluded.student_id,
                student_name = excluded.student_name,
                last_active  = excluded.last_active
            """,
            (session_id, student_id, student_name, now, now),
        )
        await db.commit()
