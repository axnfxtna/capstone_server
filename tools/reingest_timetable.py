"""
tools/reingest_timetable.py
============================
Re-ingests timetable Excel files with structured, per-(day, time) Thai sentences.

Problem with old ingestion: each MySQL/Milvus record was one raw Excel *row*,
which spans ALL day-columns at once. Searching "วันจันทร์" couldn't distinguish
Monday content from Tuesday content in the same row.

Fix: unpivot each row into one record per non-empty (day, time_slot) cell.
Example output:
  "ตาราง RAI 1 รุ่น 67 วันจันทร์ เวลา 8.00-8.30 วิชา Drawing (Lec) กลุ่ม Matbot"

Run from server/ directory:
  source venv/bin/activate
  python tools/reingest_timetable.py
"""

import os
import sys
import re

# ── path setup ──────────────────────────────────────────────────────────
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER_DIR)

import pandas as pd
import mysql.connector
from pymilvus import (
    Collection, CollectionSchema, DataType, FieldSchema, connections, utility
)
from sentence_transformers import SentenceTransformer

# ── config ───────────────────────────────────────────────────────────────
EXCEL_DIR = os.path.join(
    _SERVER_DIR, "..", "final_docker_component", "dataset", "time_table"
)
MYSQL_CFG = dict(host="localhost", port=3306, user="root", password="root", database="capstone")
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
EMB_MODEL = "BAAI/bge-m3"
EMB_DIM = 1024
COLLECTION_NAME = "time_table"
BATCH_SIZE = 50

# Day header → Thai name
_DAY_TH = {
    "monday":    "จันทร์",
    "tuesday":   "อังคาร",
    "wednesday": "พุธ",
    "thursday":  "พฤหัสบดี",
    "friday":    "ศุกร์",
    "saturday":  "เสาร์",
    "sunday":    "อาทิตย์",
}

# Filename → Thai schedule label
def _label(filename: str) -> str:
    """'Schedule_RAI 1-67.xlsx' → 'RAI 1 รุ่น 67'"""
    name = os.path.splitext(filename)[0]              # "Schedule_RAI 1-67"
    name = re.sub(r"^Schedule_", "", name).strip()    # "RAI 1-67"
    # Replace dash between digits with " รุ่น "
    name = re.sub(r"(\d)\s*-\s*(\d)", r"\1 รุ่น \2", name)
    return name

_SKIP_CELLS = {"gened", "break", "nan", "-", ""}

# Pattern for valid time slots: "8.00 - 8.30" / "8:00 - 8:30" / "8.00 -  8.30"
_TIME_PATTERN = re.compile(r"^\d{1,2}[\.:]\d{2}\s*[-–]\s*\d{1,2}[\.:]\d{2}")


def _cell_text(val) -> str:
    """Normalise a cell value to a clean string, or '' if it should be skipped."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    text = str(val).strip().replace("\n", " ").replace("\r", " ")
    text = re.sub(r" {2,}", " ", text)
    if text.lower() in _SKIP_CELLS:
        return ""
    return text


def parse_excel(path: str, label: str) -> list:
    """
    Parse one timetable xlsx and return a list of structured Thai sentences.

    Actual Excel layout (verified from all 5 files):
      Row 0: Title row  ("1st year Robotics and AI Engineering Timetable...")
      Row 1: Day headers — col 0 is "Time", col 1+ have "Monday", "Tuesday"...
              (merged cells: "Monday" appears once, then blanks for each section under it)
      Row 2: Section sub-headers — "Sec 1" / "RAI 1" etc. (optional, skip)
      Row 3+: Data rows — col 0 is time slot ("8.00-8.30"), col 1+ are cells

    RAI 1-67 has a different curriculum-list format (no "Time" column) — skipped.
    """
    df = pd.read_excel(path, header=None)
    sentences = []

    if df.shape[0] < 3 or df.shape[1] < 2:
        print(f"  ⚠  {os.path.basename(path)}: too small, skipping")
        return sentences

    # Find the day-header row: look for the row where col 0 == "Time"
    header_row = None
    for r in range(min(5, df.shape[0])):
        if _cell_text(df.iloc[r, 0]).lower() == "time":
            header_row = r
            break

    if header_row is None:
        print(f"  ⚠  {os.path.basename(path)}: no 'Time' header found — skipping")
        return sentences

    # Build col_index → day name map from header_row
    col_day = {}
    current_day = None
    for col_idx in range(1, df.shape[1]):
        val = _cell_text(df.iloc[header_row, col_idx])
        if val:
            day_key = val.lower()
            current_day = _DAY_TH.get(day_key, val)
        if current_day:
            col_day[col_idx] = current_day

    # Data rows start 2 rows after header (skip section sub-header row)
    data_start = header_row + 2

    for row_idx in range(data_start, df.shape[0]):
        time_val = _cell_text(df.iloc[row_idx, 0])
        if not time_val or not _TIME_PATTERN.match(time_val):
            continue

        for col_idx in range(1, df.shape[1]):
            day = col_day.get(col_idx)
            if not day:
                continue
            cell = _cell_text(df.iloc[row_idx, col_idx])
            if not cell:
                continue

            sentence = f"ตาราง {label} วัน{day} เวลา {time_val} วิชา {cell}"
            sentences.append(sentence)

    return sentences


# ── Milvus helpers ────────────────────────────────────────────────────────

def drop_and_create_collection() -> Collection:
    if utility.has_collection(COLLECTION_NAME):
        utility.drop_collection(COLLECTION_NAME)
        print(f"  Dropped existing '{COLLECTION_NAME}' collection")

    fields = [
        FieldSchema("id",        DataType.VARCHAR,      is_primary=True, auto_id=False, max_length=32),
        FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=EMB_DIM),
    ]
    schema = CollectionSchema(fields, description="Timetable schedule (structured)")
    col = Collection(COLLECTION_NAME, schema)
    col.create_index(
        "embedding",
        {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}},
    )
    col.load()
    print(f"  Created '{COLLECTION_NAME}' collection (dim={EMB_DIM})")
    return col


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Timetable re-ingestion")
    print("=" * 60)

    # 1. Connect
    print("\n[1/5] Connecting...")
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    print("  Milvus connected")

    db = mysql.connector.connect(**MYSQL_CFG)
    cursor = db.cursor()
    print("  MySQL connected")

    # 2. Drop + recreate Milvus collection
    print("\n[2/5] Resetting Milvus collection...")
    col = drop_and_create_collection()

    # 3. Clear MySQL table
    print("\n[3/5] Clearing MySQL ExcelTimetableData...")
    cursor.execute("DELETE FROM ExcelTimetableData")
    cursor.execute("ALTER TABLE ExcelTimetableData AUTO_INCREMENT = 1")
    db.commit()
    print("  Table cleared")

    # 4. Parse Excel files and collect sentences
    print("\n[4/5] Parsing Excel files...")
    excel_dir = os.path.abspath(EXCEL_DIR)
    if not os.path.exists(excel_dir):
        print(f"  ❌ Excel directory not found: {excel_dir}")
        sys.exit(1)

    all_sentences = []
    for filename in sorted(os.listdir(excel_dir)):
        if not filename.endswith(".xlsx"):
            continue
        label = _label(filename)
        path = os.path.join(excel_dir, filename)
        sentences = parse_excel(path, label)
        print(f"  {filename} ({label}): {len(sentences)} records")
        all_sentences.extend(sentences)

    print(f"  Total structured records: {len(all_sentences)}")

    # 5. Embed + insert in batches
    print(f"\n[5/5] Embedding and inserting (batch size={BATCH_SIZE})...")
    model = SentenceTransformer(EMB_MODEL)

    inserted = 0
    for i in range(0, len(all_sentences), BATCH_SIZE):
        batch = all_sentences[i : i + BATCH_SIZE]

        # Insert into MySQL, collect row_ids
        row_ids = []
        for text in batch:
            cursor.execute(
                "INSERT INTO ExcelTimetableData (row_text) VALUES (%s)", (text,)
            )
            row_ids.append(str(cursor.lastrowid))
        db.commit()

        # Embed batch
        embeddings = model.encode(batch, normalize_embeddings=True).tolist()

        # Insert into Milvus
        col.insert([row_ids, embeddings])

        inserted += len(batch)
        print(f"  Inserted {inserted}/{len(all_sentences)}", end="\r")

    col.flush()
    db.close()

    print(f"\n  Done — {inserted} records inserted")
    print("\n" + "=" * 60)
    print("Verification — sample search: 'เรียนวันจันทร์กี่โมง'")
    print("=" * 60)

    # Quick smoke test
    from vector_db import milvus_client
    hits = milvus_client.search_collection("เรียนวันจันทร์กี่โมง", COLLECTION_NAME, top_k=5)
    from database.mysql_client import fetch_timetable_rows
    row_ids_int = [int(h["id"]) for h in hits if h.get("id")]
    rows = fetch_timetable_rows(row_ids_int)
    for i, h in enumerate(hits, 1):
        text = rows.get(int(h["id"]), "")
        print(f"  [{i}] score={h['score']:.4f}  {text[:90]}")


if __name__ == "__main__":
    main()
