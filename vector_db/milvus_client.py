"""
vector_db/milvus_client.py
===========================
Milvus connection, collection setup, insert, and search.

Two responsibilities:
  1. conversation_memory collection — new, for session memory R/W
  2. RAG search — searches existing collections (curriculum, uni_info,
     time_table) shared with the final_docker_component system

Adapted from:
  final_docker_component/src/utils_database.py  (connection logic)
  final_docker_component/src/pipelines/rag_pipeline.py  (search logic)
"""

import logging
import os
from typing import Dict, List, Optional

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Singleton embedder (loaded once)
# ═══════════════════════════════════════════════════════════════════════

_embedder: Optional[SentenceTransformer] = None
_EMB_DIM = 1024


def get_embedder(model_name: str = "BAAI/bge-m3") -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        logger.info("Loading embedding model: %s", model_name)
        _embedder = SentenceTransformer(model_name)
    return _embedder


def embed(text: str, model_name: str = "BAAI/bge-m3") -> List[float]:
    emb = get_embedder(model_name)
    vec = emb.encode([text], normalize_embeddings=True)[0]
    return vec.tolist()


# ═══════════════════════════════════════════════════════════════════════
# Connection
# ═══════════════════════════════════════════════════════════════════════

def connect_milvus(
    host: str = "localhost",
    port: str = "19530",
    alias: str = "default",
    uri: Optional[str] = None,
) -> None:
    """Connect to Milvus. Idempotent — safe to call multiple times."""
    if connections.has_connection(alias):
        return
    if uri:
        connections.connect(alias=alias, uri=uri)
    else:
        connections.connect(alias=alias, host=host, port=port)
    logger.info("Milvus connected at %s:%s", host, port)


# ═══════════════════════════════════════════════════════════════════════
# conversation_memory collection setup
# ═══════════════════════════════════════════════════════════════════════

MEMORY_COLLECTION = "conversation_memory"
MEMORY_DIM = 1024  # BAAI/bge-m3


def ensure_memory_collection(dim: int = MEMORY_DIM) -> Collection:
    """
    Create conversation_memory collection if it doesn't exist.
    Schema matches design.md Section 7 (adapted to 384-dim for all-MiniLM).
    """
    if utility.has_collection(MEMORY_COLLECTION):
        col = Collection(MEMORY_COLLECTION)
        col.load()
        return col

    fields = [
        FieldSchema("id",            DataType.INT64,         is_primary=True, auto_id=True),
        FieldSchema("student_id",    DataType.VARCHAR,        max_length=64),
        FieldSchema("session_id",    DataType.VARCHAR,        max_length=64),
        FieldSchema("raw_user_text", DataType.VARCHAR,        max_length=2048),
        FieldSchema("raw_bot_reply", DataType.VARCHAR,        max_length=2048),
        FieldSchema("summary_text",  DataType.VARCHAR,        max_length=1024),
        FieldSchema("intent",        DataType.VARCHAR,        max_length=32),
        FieldSchema("timestamp",     DataType.VARCHAR,        max_length=32),
        FieldSchema("embedding",     DataType.FLOAT_VECTOR,   dim=dim),
    ]
    schema = CollectionSchema(fields, description="Conversation memory for KhanomTan robot")
    col = Collection(MEMORY_COLLECTION, schema)
    col.create_index(
        "embedding",
        {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}},
    )
    col.load()
    logger.info("Created collection: %s (dim=%d)", MEMORY_COLLECTION, dim)
    return col


def insert_memory(
    student_id: str,
    session_id: str,
    raw_user_text: str,
    raw_bot_reply: str,
    summary_text: str,
    intent: str,
    timestamp: str,
    emb_model: str = "BAAI/bge-m3",
) -> None:
    """Embed summary_text and insert into conversation_memory."""
    col = ensure_memory_collection()
    vec = embed(summary_text, emb_model)
    col.insert([
        [student_id],
        [session_id],
        [raw_user_text[:2047]],
        [raw_bot_reply[:2047]],
        [summary_text[:1023]],
        [intent],
        [timestamp],
        [vec],
    ])
    col.flush()


def search_memory(
    query: str,
    student_id: str,
    top_k: int = 3,
    emb_model: str = "BAAI/bge-m3",
) -> List[Dict]:
    """
    Search conversation_memory for semantically similar past turns
    filtered by student_id.
    """
    col = ensure_memory_collection()
    vec = embed(query, emb_model)
    expr = f'student_id == "{student_id}"' if student_id else ""
    results = col.search(
        data=[vec],
        anns_field="embedding",
        param={"metric_type": "COSINE", "params": {"nprobe": 10}},
        limit=top_k,
        expr=expr if expr else None,
        output_fields=["student_id", "session_id", "summary_text", "intent", "timestamp"],
    )
    hits = []
    for r in results:
        for hit in r:
            hits.append({
                "score":        hit.score,
                "summary_text": hit.entity.get("summary_text", ""),
                "intent":       hit.entity.get("intent", ""),
                "timestamp":    hit.entity.get("timestamp", ""),
            })
    return hits


# ═══════════════════════════════════════════════════════════════════════
# Generic RAG search — reused from rag_pipeline.py logic
# Searches existing dataset collections (curriculum, uni_info, time_table)
# ═══════════════════════════════════════════════════════════════════════

def search_collection(
    query: str,
    collection_name: str,
    top_k: int = 5,
    emb_model: str = "BAAI/bge-m3",
) -> List[Dict]:
    """
    Generic Milvus search against any existing collection.
    Adapted from RAGQueryPipeline._search_milvus() in rag_pipeline.py.

    Handles collections with different vector field names and dimensions.
    """
    if not utility.has_collection(collection_name):
        logger.warning("Collection %s not found", collection_name)
        return []

    col = Collection(collection_name)
    col.load()

    # Detect vector field and its dimension
    schema = col.schema
    vector_field = None
    query_dim = len(embed(query, emb_model))

    # Prefer doc_embedding > embedding > first vector field
    for preference in ("doc_embedding", "embedding"):
        for f in schema.fields:
            if f.name == preference and f.dtype == DataType.FLOAT_VECTOR:
                vector_field = f.name
                query_dim = f.params.get("dim", query_dim)
                break
        if vector_field:
            break
    if not vector_field:
        for f in schema.fields:
            if f.dtype == DataType.FLOAT_VECTOR:
                vector_field = f.name
                query_dim = f.params.get("dim", query_dim)
                break

    if not vector_field:
        logger.warning("No vector field found in %s", collection_name)
        return []

    # Embed and pad/truncate to match collection dimension
    raw_vec = embed(query, emb_model)
    if len(raw_vec) < query_dim:
        raw_vec = raw_vec + [0.0] * (query_dim - len(raw_vec))
    elif len(raw_vec) > query_dim:
        raw_vec = raw_vec[:query_dim]

    # Output all non-vector fields
    output_fields = [
        f.name for f in schema.fields
        if f.dtype not in (DataType.FLOAT_VECTOR, DataType.BINARY_VECTOR)
        and not f.is_primary
    ]

    try:
        results = col.search(
            data=[raw_vec],
            anns_field=vector_field,
            param={"metric_type": "COSINE", "params": {"nprobe": 10}},
            limit=top_k,
            output_fields=output_fields,
        )
    except Exception as exc:
        logger.error("Milvus search error in %s: %s", collection_name, exc)
        return []

    hits = []
    for r in results:
        for hit in r:
            entry = {"score": hit.score, "id": hit.id}
            for field in output_fields:
                entry[field] = hit.entity.get(field, "")
            hits.append(entry)
    return hits
