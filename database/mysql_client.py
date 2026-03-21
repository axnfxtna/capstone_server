"""
database/mysql_client.py
========================
Thin wrapper around MySQL for timetable text lookup and student roster.
The time_table Milvus collection stores only IDs + embeddings;
the actual row_text lives in MySQL ExcelTimetableData.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_conn = None


def _get_conn(host: str, port: int, user: str, password: str, database: str):
    """Return a live MySQL connection, reconnecting if needed."""
    global _conn
    try:
        if _conn is not None and _conn.is_connected():
            return _conn
    except Exception:
        pass

    import mysql.connector
    _conn = mysql.connector.connect(
        host=host, port=port, user=user, password=password, database=database
    )
    logger.info("MySQL connected at %s:%s/%s", host, port, database)
    return _conn


def fetch_timetable_rows(
    row_ids: List[int],
    host: str = "localhost",
    port: int = 3306,
    user: str = "root",
    password: str = "root",
    database: str = "capstone",
) -> Dict[int, str]:
    """
    Fetch row_text from ExcelTimetableData for the given row_ids.
    Returns {row_id: row_text}.  Empty dict on failure.
    """
    if not row_ids:
        return {}
    try:
        conn = _get_conn(host, port, user, password, database)
        cursor = conn.cursor()
        placeholders = ",".join(["%s"] * len(row_ids))
        cursor.execute(
            f"SELECT row_id, row_text FROM ExcelTimetableData WHERE row_id IN ({placeholders})",
            row_ids,
        )
        result = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.close()
        return result
    except Exception as exc:
        logger.error("MySQL timetable fetch error: %s", exc)
        global _conn
        _conn = None   # force reconnect next call
        return {}


def fetch_student_context(
    host: str = "localhost",
    port: int = 3306,
    user: str = "root",
    password: str = "root",
    database: str = "capstone",
) -> str:
    """
    Fetch student roster and academic year data from MySQL.
    Returns a formatted Thai context string for injection into the LLM prompt.
    Mirrors final_docker_component/src/pipelines/rag_pipeline.py _fetch_student_context().
    """
    try:
        conn = _get_conn(host, port, user, password, database)
        cursor = conn.cursor(dictionary=True)
        parts = []

        try:
            cursor.execute("SELECT * FROM Students LIMIT 50")
            students = cursor.fetchall()
            if students:
                parts.append("=== ข้อมูลนักศึกษา ===")
                for s in students:
                    parts.append(
                        f"รหัส: {s.get('student_id')}, "
                        f"ชื่อ: {s.get('first_name')} {s.get('last_name')} "
                        f"(ชื่อเล่น: {s.get('nick_name')}), "
                        f"อีเมล: {s.get('student_email')}"
                    )
        except Exception as exc:
            logger.warning("Students table query failed: %s", exc)
            parts.append("(ไม่สามารถดึงข้อมูลนักศึกษาได้)")

        try:
            cursor.execute("SELECT * FROM Academic_Year")
            years = cursor.fetchall()
            if years:
                parts.append("\n=== ปีการศึกษา ===")
                for a in years:
                    parts.append(
                        f"RAI รุ่น {a.get('RAI_Gen')}, "
                        f"KMITL รุ่น {a.get('KMITL_Gen')}, "
                        f"ปี: {a.get('year_start')}-{a.get('year_end')}"
                    )
        except Exception as exc:
            logger.warning("Academic_Year table query failed: %s", exc)

        cursor.close()
        return "\n".join(parts)
    except Exception as exc:
        logger.error("MySQL fetch_student_context error: %s", exc)
        global _conn
        _conn = None
        return ""
