"""
tools/reingest_curriculum.py
=============================
Re-ingests curriculum PDFs into Milvus using BAAI/bge-m3 (1024-dim).

Drops and recreates the 'curriculum' collection, then embeds all PDF
chunks from the dataset directory.

Run from server/ directory:
  source venv/bin/activate
  python tools/reingest_curriculum.py
"""

import os
import sys

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER_DIR)

import fitz  # PyMuPDF
from pymilvus import (
    Collection, CollectionSchema, DataType, FieldSchema, connections, utility
)
from sentence_transformers import SentenceTransformer

# ── config ────────────────────────────────────────────────────────────────
PDF_DIR = os.path.join(
    _SERVER_DIR, "..", "final_docker_component", "dataset", "curriculum"
)
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
EMB_MODEL = "BAAI/bge-m3"
EMB_DIM = 1024
COLLECTION_NAME = "curriculum"
CHUNK_SIZE = 2000
OVERLAP = 200
BATCH_SIZE = 50


# ── text helpers ──────────────────────────────────────────────────────────

def pdf_to_text(path: str) -> str:
    doc = fitz.open(path)
    return " ".join(page.get_text() for page in doc)


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
        FieldSchema("id",           DataType.INT64,       is_primary=True, auto_id=True),
        FieldSchema("doc_name",     DataType.VARCHAR,     max_length=200),
        FieldSchema("text_content", DataType.VARCHAR,     max_length=65535),
        FieldSchema("embedding",    DataType.FLOAT_VECTOR, dim=EMB_DIM),
    ]
    schema = CollectionSchema(fields, description="Curriculum PDFs (bge-m3 1024-dim)")
    col = Collection(COLLECTION_NAME, schema)
    col.create_index(
        "embedding",
        {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}},
    )
    col.load()
    print(f"  Created '{COLLECTION_NAME}' collection (dim={EMB_DIM})")
    return col


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Curriculum re-ingestion  (BAAI/bge-m3  1024-dim)")
    print("=" * 60)

    # 1. Connect
    print("\n[1/4] Connecting to Milvus...")
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    print("  Milvus connected")

    # 2. Drop + recreate
    print("\n[2/4] Resetting Milvus collection...")
    col = drop_and_create_collection()

    # 3. Parse PDFs
    print("\n[3/4] Parsing PDFs...")
    pdf_dir = os.path.abspath(PDF_DIR)
    if not os.path.exists(pdf_dir):
        print(f"  ❌ PDF directory not found: {pdf_dir}")
        sys.exit(1)

    batch_names, batch_texts, batch_embeddings = [], [], []
    total_chunks = 0

    print(f"\n[4/4] Embedding and inserting (model={EMB_MODEL}, batch={BATCH_SIZE})...")
    model = SentenceTransformer(EMB_MODEL)

    for filename in sorted(os.listdir(pdf_dir)):
        if not filename.endswith(".pdf"):
            continue
        path = os.path.join(pdf_dir, filename)
        full_text = pdf_to_text(path)
        chunks = chunk_text(full_text)
        print(f"  {filename}: {len(full_text)} chars → {len(chunks)} chunks")

        for chunk in chunks:
            emb = model.encode(chunk, normalize_embeddings=True).tolist()
            batch_names.append(filename)
            batch_texts.append(chunk)
            batch_embeddings.append(emb)
            total_chunks += 1

            if len(batch_names) >= BATCH_SIZE:
                col.insert([batch_names, batch_texts, batch_embeddings])
                batch_names, batch_texts, batch_embeddings = [], [], []
                print(f"  Inserted {total_chunks} chunks so far...", end="\r")

    if batch_names:
        col.insert([batch_names, batch_texts, batch_embeddings])

    col.flush()
    print(f"\n  Done — {total_chunks} chunks from {COLLECTION_NAME}")

    # Smoke test
    print("\n" + "=" * 60)
    print("Verification — sample search: 'วิชาบังคับหลักสูตร'")
    print("=" * 60)
    from vector_db import milvus_client
    milvus_client.connect_milvus(host=MILVUS_HOST, port=MILVUS_PORT)
    hits = milvus_client.search_collection("วิชาบังคับหลักสูตร", COLLECTION_NAME, top_k=3)
    for i, h in enumerate(hits, 1):
        text = str(h.get("text_content", ""))[:100]
        print(f"  [{i}] score={h['score']:.4f}  {text}")


if __name__ == "__main__":
    main()
