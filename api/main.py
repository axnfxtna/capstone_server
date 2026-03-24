"""
api/main.py
============
FastAPI application entry point.
Wires all MCP modules together and mounts all routers.

Run with:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.routes import grammar, monitor, receiver, tts
from database import sqlite_client
from llm.typhoon_client import TyphoonClient
from mcp.grammar_corrector import GrammarCorrector
from mcp.greeting_bot import GreetingBot
from mcp.intent_router import IntentRouter
from mcp.llm_chatbot import LLMChatbot
from mcp.memory_manager import MemoryManager
from vector_db import milvus_client

# ─────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/server.log", encoding="utf-8"),
    ],
    force=True,
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Load settings
# ─────────────────────────────────────────────────────────────────────

_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


def _load_settings() -> dict:
    with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────────────
# Lifespan — startup / shutdown
# ─────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise all services on startup; clean up on shutdown."""
    settings = _load_settings()
    app.state.settings = settings

    # Milvus
    mv = settings["milvus"]
    milvus_client.connect_milvus(host=mv["host"], port=str(mv["port"]))
    milvus_client.ensure_memory_collection(dim=mv["embedding_dim"])
    logger.info("Milvus ready")

    # SQLite
    db_path = settings["sqlite"]["db_path"]
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    await sqlite_client.init_db(db_path)
    logger.info("SQLite ready at %s", db_path)

    # LLM clients — 70B for complex tasks, 8B for fast/simple tasks
    llm_cfg = settings["llm"]
    llm = TyphoonClient(
        base_url=llm_cfg["base_url"],
        model=llm_cfg["model"],
        timeout=llm_cfg["timeout"],
    )
    fast_cfg = settings.get("llm_fast", llm_cfg)
    llm_fast = TyphoonClient(
        base_url=fast_cfg["base_url"],
        model=fast_cfg["model"],
        timeout=fast_cfg["timeout"],
    )
    logger.info("LLM 70B: %s", llm_cfg["model"])
    logger.info("LLM  8B: %s", fast_cfg["model"])

    # PI 5 base URL + TTS config
    srv = settings["server"]
    pi5_url = f"http://{srv['pi5_ip']}:{srv['pi5_port']}"
    tts_cfg = settings.get("tts", {})
    tts_mode   = tts_cfg.get("mode", "pi5")
    tts_engine = tts_cfg.get("engine", "khanomtan")
    audio_cfg  = settings.get("audio_service", {})
    audio_sidecar_url = audio_cfg.get("base_url", "http://localhost:8001")

    # MCP modules — fast LLM for grammar + memory, full LLM for chatbot + greeting
    memory_manager = MemoryManager(
        llm=llm_fast,
        db_path=db_path,
        emb_model=mv["embedding_model"],
        top_k=mv["top_k"],
    )
    grammar_corrector = GrammarCorrector(llm=llm_fast)
    greeting_bot = GreetingBot(
        llm=llm,
        memory_manager=memory_manager,
        pi5_base_url=pi5_url,
        tts_mode=tts_mode,
        tts_engine=tts_engine,
        audio_sidecar_url=audio_sidecar_url,
    )
    chatbot = LLMChatbot(
        llm=llm,
        memory_manager=memory_manager,
        rag_top_k=mv["top_k"],
        mem_top_k=mv["top_k"],
        mysql_cfg=settings.get("mysql", {}),
    )
    intent_router = IntentRouter(
        llm=llm,
        pi5_base_url=pi5_url,
        tts_mode=tts_mode,
        tts_engine=tts_engine,
        audio_sidecar_url=audio_sidecar_url,
    )

    # Attach to app state so routes can access them
    app.state.llm              = llm
    app.state.llm_fast         = llm_fast
    app.state.memory_manager   = memory_manager
    app.state.grammar_corrector = grammar_corrector
    app.state.greeting_bot     = greeting_bot
    app.state.chatbot          = chatbot
    app.state.intent_router    = intent_router

    logger.info("Satu AI Brain server started ✅")
    yield

    # Shutdown — nothing to teardown for now
    logger.info("Server shutting down")


# ─────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Satu AI Brain",
    description="Robot AI server — KMITL RAI campus assistant",
    version="2.0.0",
    lifespan=lifespan,
)

# Mount routers
app.include_router(receiver.router, tags=["PI5 Receiver"])
app.include_router(grammar.router,  tags=["Grammar MCP"])
app.include_router(tts.router,      tags=["TTS MCP"])
app.include_router(monitor.router,  tags=["Monitor"])


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    logger.warning(
        "422 on %s %s — body: %s — errors: %s",
        request.method, request.url.path,
        body.decode("utf-8", errors="replace")[:500],
        exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Satu AI Brain v2"}
