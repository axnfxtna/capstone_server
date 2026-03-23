"""
tools/reingest_uni_info.py
===========================
Re-ingests uni_info .docx files into Milvus using BAAI/bge-m3 (1024-dim).

Drops and recreates the 'uni_info' collection with a simplified schema
(doc_embedding only — our server only searches text, not images).
.jpg files in the dataset directory are skipped.

Run from server/ directory:
  source venv/bin/activate
  python tools/reingest_uni_info.py
"""

import os
import sys

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER_DIR)

from docx import Document
from pymilvus import (
    Collection, CollectionSchema, DataType, FieldSchema, connections, utility
)
from sentence_transformers import SentenceTransformer

# ── config ────────────────────────────────────────────────────────────────
DOCX_DIR = os.path.join(
    _SERVER_DIR, "..", "final_docker_component", "dataset", "uni_info"
)
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
EMB_MODEL = "BAAI/bge-m3"
EMB_DIM = 1024
COLLECTION_NAME = "uni_info"
CHUNK_SIZE = 2000
OVERLAP = 200


# ── text helpers ──────────────────────────────────────────────────────────

def docx_to_text(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def txt_to_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for sep in ("\n\n", ". ", "\n"):
                bp = text.rfind(sep, start + chunk_size // 2, end)
                if bp != -1:
                    end = bp + 1
                    break
        chunk = text[start:end].strip()
        if chunk and len(chunk) >= 20:
            if len(chunk) > 65000:
                chunk = chunk[:65000]
            chunks.append(chunk)
        start = end - overlap
    return chunks


# ── Milvus helpers ────────────────────────────────────────────────────────

def drop_and_create_collection() -> Collection:
    if utility.has_collection(COLLECTION_NAME):
        utility.drop_collection(COLLECTION_NAME)
        print(f"  Dropped existing '{COLLECTION_NAME}' collection")

    fields = [
        FieldSchema("id",            DataType.INT64,        is_primary=True, auto_id=True),
        FieldSchema("file_path",     DataType.VARCHAR,      max_length=255),
        FieldSchema("file_type",     DataType.VARCHAR,      max_length=50),
        FieldSchema("text_content",  DataType.VARCHAR,      max_length=65535),
        FieldSchema("doc_embedding", DataType.FLOAT_VECTOR, dim=EMB_DIM),
    ]
    schema = CollectionSchema(fields, description="University info docs (bge-m3 1024-dim)")
    col = Collection(COLLECTION_NAME, schema)
    col.create_index(
        "doc_embedding",
        {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}},
    )
    col.load()
    print(f"  Created '{COLLECTION_NAME}' collection (dim={EMB_DIM})")
    return col


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("uni_info re-ingestion  (BAAI/bge-m3  1024-dim)")
    print("=" * 60)

    print("\n[1/4] Connecting to Milvus...")
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    print("  Milvus connected")

    print("\n[2/4] Resetting Milvus collection...")
    col = drop_and_create_collection()

    docx_dir = os.path.abspath(DOCX_DIR)
    if not os.path.exists(docx_dir):
        print(f"  ❌ Directory not found: {docx_dir}")
        sys.exit(1)

    print(f"\n[3/4] Loading embedding model ({EMB_MODEL})...")
    model = SentenceTransformer(EMB_MODEL)

    print("\n[4/4] Parsing .docx files and inserting...")
    total = 0
    for filename in sorted(os.listdir(docx_dir)):
        if filename.endswith(".docx"):
            full_text = docx_to_text(os.path.join(docx_dir, filename))
        elif filename.endswith(".txt"):
            full_text = txt_to_text(os.path.join(docx_dir, filename))
        else:
            print(f"  Skipping: {filename}")
            continue
        chunks = chunk_text(full_text)
        print(f"  {filename}: {len(full_text)} chars → {len(chunks)} chunk(s)")

        for chunk in chunks:
            emb = model.encode(chunk, normalize_embeddings=True).tolist()
            col.insert([[filename], ["docx"], [chunk], [emb]])
            total += 1

    col.flush()
    print(f"\n  Done — {total} chunks inserted into '{COLLECTION_NAME}'")

    # Smoke test
    print("\n" + "=" * 60)
    print("Verification — sample search: 'ตึก E-12 อยู่ที่ไหน'")
    print("=" * 60)
    from vector_db import milvus_client
    milvus_client.connect_milvus(host=MILVUS_HOST, port=MILVUS_PORT)
    hits = milvus_client.search_collection("ตึก E-12 อยู่ที่ไหน", COLLECTION_NAME, top_k=3)
    for i, h in enumerate(hits, 1):
        text = str(h.get("text_content", ""))[:100]
        print(f"  [{i}] score={h['score']:.4f}  {text}")


if __name__ == "__main__":
    main()
