# Server Progress Log

## Project: KhanomTan AI Brain — Server Side
**Stack:** FastAPI · Ollama (qwen2.5:7b-instruct) · Milvus · SQLite · Python 3.8.10

---

## Current Status: Phase 2.6 Complete ✅

The full server pipeline runs end-to-end with live PI 5 traffic.
All 5 RAG routes work correctly with real data. Session timeout, grammar confidence
gate, and server-side TTS infrastructure added in Phase 2 completion pass (2026-03-21).
Output stages (TTS, ROS2) fire correctly but PI 5 receiver endpoints not yet confirmed
by teammates.

---

## Completed

### Infrastructure
- [x] FastAPI server on `0.0.0.0:8000`
- [x] Milvus connected at `localhost:19530` (Docker container, run by root)
- [x] SQLite raw conversation log at `./database/metadata.db`
- [x] All services initialised cleanly at startup
- [x] Settings centralised in `config/settings.yaml`
- [x] `/monitor` dashboard — live pipeline event viewer (auto-refresh 3s)
- [x] `/events` JSON endpoint — last 50 pipeline events
- [x] 422 validation error logger — logs raw body of malformed requests

### Payload Integration
- [x] `/detection` — 200 OK, full pipeline runs with live PI 5 traffic
- [x] `/greeting` — 200 OK, correct `GreetingPayload` schema (no stt, has `vision_confidence`)
- [x] `/greeting` skips unregistered and Unknown persons
- [x] `person_id` cleaned before use: `"Palm (Krittin Sakharin)"` → `"Palm"` via `_clean_name()`

### Processing Pipeline (per `/detection` event)
- [x] Registered person filter (`is_registered` + `person_id != "Unknown"`)
- [x] STT confidence gate (threshold: 0.6) — low-confidence speaks fallback TTS instead of silent skip
- [x] Session management — per `person_id`, in-memory, UUID session ID
- [x] **Session timeout** — idle sessions expired after 600s; history dropped, logged (Phase 2.6)
- [x] Grammar correction via LLM — uses chat API (not generate) to prevent prompt token leakage
- [x] Grammar corrector length guard — falls back to raw if output < 50% of input length
- [x] **Grammar high-confidence skip** — STT conf ≥ 0.85 bypasses LLM corrector entirely (Phase 2.5)
- [x] RAG chatbot (`llm_chatbot.py`) — builds context from Milvus + conversation history
- [x] RAG routing: `chat_history` / `mysql_students` / `time_table` / `curriculum` / `uni_info`
- [x] `time_table` keywords: `วันไหน`, `เวลาไหน`, `กี่โมง`, `คาบเรียน`, `วันเรียน`, `class`, `schedule`
- [x] `mysql_students` route: queries MySQL `Students` + `Academic_Year` tables
- [x] `chat_history` route: uses `memory_summary` + `history_str` context
- [x] `_THAI_TO_ENG` augmentation: `คอร์ส`, `สอน`, `เนื้อหา`
- [x] Memory write — stores conversation turn to SQLite + Milvus (async, fire-and-forget)
- [x] Intent routing — `chat` / `info` / `navigate` / `farewell` with dual-output logic
- [x] Embedding model: `sentence-transformers/all-MiniLM-L6-v2` on CUDA

### Language / Prompt
- [x] All chatbot replies use `ค่ะ` (female particle), `ครับ` explicitly prohibited in all prompts
- [x] `enforce_female_particle()` post-processor in `llm/typhoon_client.py` — replaces any ครับ → ค่ะ
- [x] Grammar corrector prompt: preserves polite particles, no truncation

### time_table Data — Re-ingested ✅
- [x] Old ingestion stored raw Excel rows (unusable) — replaced with per-(day, time_slot) sentences
- [x] Re-ingestion script: `tools/reingest_timetable.py`
- [x] Old data backed up to MySQL `ExcelTimetableData_backup` (229 rows)
- [x] 128 clean records from 4 files (RAI 1-65, 1-66, 2-65, 2-66)
      Note: RAI 1-67 skipped — curriculum list, not a timetable grid
- [x] Verified: "วิชา Programming เรียนวันไหน" → returns correct slots ✅

### Output (Server → PI 5)
- [x] TTS Option B — POST to PI 5 `/tts_render` with `{ "phoneme_text": str }` (active mode)
- [x] Navigation — POST to PI 5 `/navigation` with `{ "cmd": str, "destination"?: str }`
- [x] `tts_mode` config switch in `settings.yaml` (`"pi5"` | `"server"`) propagated to all modules
- [ ] TTS delivery confirmed end-to-end (depends on Teammate A)
- [ ] ROS2 navigation confirmed end-to-end (depends on Teammate B)

### TTS — Server-Side Infrastructure (Phase 2.3)
- [x] `tts/khanomtan_engine.py` created — synthesizes WAV via pythaitts, POSTs to PI 5 `/audio_play`
- [x] `IntentRouter` and `GreetingBot` dispatch on `tts_mode` (`"server"` → GPU WAV, `"pi5"` → text)
- [x] `pythainlp 3.1.1` installed; Thai syllabification working in `mcp/tts_router.py`
- [ ] `coqui-tts` (`pip install coqui-tts`) not yet installed — `TTS_AVAILABLE=False`, server mode blocked
      → Once installed: change `tts.mode: "server"` in `settings.yaml` to activate
- [ ] `walle-tts` Docker container (VachanaTTS on port 5002) — defined but not running
      → Start with: `sudo docker-compose --profile interactive up walle-tts`
      → Note: container uses VachanaTTS, not KhanomTan — endpoint is `POST /speak {"text": "..."}`

---

## Known Issues

| Issue | Severity | Status |
|-------|----------|--------|
| Grammar corrector still occasionally over-corrects informal Thai | Medium | Mitigated: length guard + prompt rules + high-conf skip (≥0.85) |
| Session state lost on server restart (in-memory only) | Low | Phase 3 item (Redis) |
| `time_table` search scores moderate (~0.28-0.33) — English embedder on Thai text | Low | Acceptable; multilingual embedder would improve |
| `coqui-tts` not installed — server-side KhanomTan synthesis disabled | Low | `pip install coqui-tts` then set `tts.mode: "server"` |
| `walle-tts` container (VachanaTTS) not running | Low | `sudo docker-compose --profile interactive up walle-tts` |

---

## TTS Architecture

```
tts.mode = "pi5"  (current / default)
  └─ text_sender.py → POST PI5:5000/tts_render { phoneme_text }
     PI 5 does TTS locally on ARM CPU

tts.mode = "server"  (blocked: needs coqui-tts)
  └─ khanomtan_engine.py → pythaitts TTS(pretrained="khanomtan") on GPU
     → kanom_than_player.py → POST PI5:5000/audio_play <wav bytes>

walle-tts container  (not running, alternative path)
  └─ VachanaTTS on localhost:5002 → POST /speak { text } → returns WAV path
     Note: uses VachanaTTS, not KhanomTan — different model
```

---

## Docker Services (final_docker_component)

| Container | Port | Status | Purpose |
|-----------|------|--------|---------|
| `milvus` | 19530 | ✅ Running | Vector DB |
| `mysql` | 3306 | ✅ Running | Relational DB (Students, timetable) |
| `ollama` | 11434 | ✅ Running | LLM (qwen2.5:7b-instruct) |
| `etcd` | 2379 | ✅ Running | Milvus dependency |
| `minio` | 9002/9003 | ✅ Running | Milvus object storage |
| `walle-tts` | 5002 | ⬜ Not running | VachanaTTS — `--profile interactive` |
| `walle-thaireader` | — | ⬜ Not running | Thai phoneme post-processor — `--profile interactive` |
| `walle-stt` | — | ⬜ Not running | Speech-to-text — `--profile interactive` |
| `walle-mcp` | — | ⬜ Not running | MCP stack (superseded by this server) |

---

## Milvus Collections

| Collection | Entities | Status |
|-----------|----------|--------|
| `curriculum` | 516 | ✅ RAG working |
| `time_table` | 128 | ✅ Re-ingested with structured Thai sentences |
| `uni_info` | 7 | ✅ RAG working |
| `conversation_memory` | 11+ | ✅ Stores/retrieves session summaries |
| `student_face_images` | 0 | N/A (PI 5 side) |

---

## Key Packages (venv)

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.124.4 | HTTP framework |
| httpx | 0.28.1 | Async HTTP client (PI 5 calls) |
| pymilvus | 2.6.10 | Vector DB client |
| pythainlp | 3.1.1 | Thai NLP / syllabification |
| pythaitts | 0.4.2 | Thai TTS wrapper (khanomtan) |
| torch | 2.4.1 | GPU tensors |
| vachanatts | 0.0.7 | VachanaTTS (installed as pythaitts dep) |

---

## Team Responsibilities

| Component | Owner |
|-----------|-------|
| Server (this repo) | You |
| TTS playback on PI 5 (`/tts_render`) | Teammate A |
| ROS2 / Navigation on PI 5 (`/navigation`) | Teammate B |
| KhanomTan TTS model integration | Teammate A |

---

## Environment

| Item | Detail |
|------|--------|
| Server IP | `10.100.16.22` |
| PI 5 IP | `10.26.9.196` |
| LLM | `qwen2.5:7b-instruct` via Ollama at `localhost:11434` |
| Embedding | `sentence-transformers/all-MiniLM-L6-v2` (dim 384) |
| Milvus | `localhost:19530` (Docker, run by root) |
| Python | 3.8.10 (venv at `./venv`) |
| Monitor | `http://10.100.16.22:8000/monitor` or SSH tunnel `http://localhost:8080/monitor` |

---

## Phase Roadmap

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Core pipeline (FastAPI, RAG, Milvus, sessions, TTS/ROS2 fire) | ✅ Done |
| 2.3 | Server-side TTS infrastructure (khanomtan_engine, tts_mode switch) | ✅ Infrastructure done; blocked on coqui-tts install |
| 2.5 | Grammar high-confidence skip (STT conf ≥ 0.85 bypasses LLM) | ✅ Done |
| 2.6 | Session timeout & cleanup (600s idle expiry) | ✅ Done |
| 3 | Session persistence across restarts (Redis) | ⬜ Not started |
| 3 | Multilingual embedding model (Thai-aware) | ⬜ Not started |
| 4 | Performance benchmarking & technical report | ⬜ Not started |

---

## Phase 4 — Performance Benchmarking & Technical Report

Goal: measure accuracy and latency for every stage of the pipeline, produce a
technical report with tables and charts suitable for the capstone submission.

### 4.1 — Component Accuracy (Confidence)

| Component | Metric | Method |
|-----------|--------|--------|
| **STT** | Word Error Rate (WER) | Feed N known Thai sentences, compare transcript vs ground truth |
| **Grammar Corrector** | Correction accuracy | Hand-labelled set of noisy STT outputs; score corrected vs expected |
| **RAG Retrieval** | Hit rate @ top-3 | Known Q→collection pairs; check if correct collection retrieved |
| **RAG Answer** | Relevance score | Human rating 1–5 on N question/answer pairs |
| **Intent Router** | Intent accuracy | Labelled test set; compare predicted intent vs expected |
| **TTS** | — | Subjective MOS (Mean Opinion Score) listening test |

### 4.2 — Component Latency (Efficiency)

Measure wall-clock time for each stage in a single `/detection` request end-to-end.
All timings in milliseconds, averaged over N=50 requests.

| Stage | What to time | Target |
|-------|-------------|--------|
| STT (PI 5) | PI 5 ASR inference | < 2000 ms |
| Grammar Corrector | LLM chat call | < 1000 ms (or 0 ms if skipped) |
| RAG — Embedding | `sentence-transformers` encode | < 100 ms |
| RAG — Milvus Search | Vector search across all collections | < 200 ms |
| RAG — LLM Answer | Ollama generate/chat call | < 5000 ms |
| Intent Router | Intent classification + TTS dispatch | < 100 ms |
| TTS (Option B) | POST to PI 5 `/tts_render` | < 500 ms |
| **End-to-end** | PI 5 sends payload → robot speaks | < 8000 ms |

### 4.3 — Implementation Plan

**Step 1 — Add timing instrumentation to `receiver.py`**

Wrap each pipeline stage with `time.perf_counter()` and log timing to a structured
JSON log or append to SQLite. Example fields:
```
{ "session_id", "stage", "duration_ms", "timestamp", "person_id" }
```

**Step 2 — Build a benchmark test harness (`tools/benchmark.py`)**

- Replay a fixed set of N=50 `DetectionPayload` JSON fixtures through the live server
- Fixtures should cover: high-conf STT, low-conf STT, each RAG route (chat/student/timetable/curriculum/info), each intent (chat/info/navigate/farewell)
- Collect per-stage timings from the structured log
- Output a summary table (mean / p50 / p95 / p99 latency per stage)

**Step 3 — Accuracy evaluation (`tools/eval_accuracy.py`)**

- Grammar corrector: feed 20 noisy STT strings, compare corrected output vs hand-labelled expected
- RAG routing: feed 20 questions with known correct collection, count hits
- Intent router: feed 20 chatbot responses with known intent, count correct predictions

**Step 4 — Technical Report**

Produce `docs/technical_report.md` (or PDF export) with:
- System architecture diagram description
- Per-component accuracy table (from Step 3)
- End-to-end latency breakdown table + bar chart (from Step 2)
- Known limitations and future improvements
- Comparison: grammar skip vs no-skip latency delta
- Comparison: TTS Option A (server GPU) vs Option B (PI 5 ARM) latency delta (once Option A is active)
