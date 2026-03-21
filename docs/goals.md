# Task Goals — Server Side

**Philosophy: make the whole pipeline work end-to-end first, then improve each component.**

---

## Phase 1 — Pipeline Complete ✅ DONE

All Phase 1 items are complete. The full pipeline runs with live PI 5 traffic.

- [x] `/greeting` endpoint working (correct schema, skips Unknown/unregistered)
- [x] `/detection` endpoint working (full grammar → RAG → intent routing)
- [x] RAG data verified: `curriculum` (516), `uni_info` (7), `time_table` (128, re-ingested) all work
- [x] Memory verified: `conversation_memory` stores and retrieves summaries
- [x] `person_id` name cleaned before passing to LLM
- [x] Interface contract documented below (teammate coordination ongoing)

---

## Phase 2 — Component Improvements ✅ DONE

### 2.1 Fix `time_table` RAG data ✅
- [x] Root cause: old ingestion stored raw Excel rows spanning all day-columns — unusable
- [x] Re-ingestion script: `tools/reingest_timetable.py`
- [x] 128 structured Thai sentences ingested from 4 timetable Excel files
- [x] Old data backed up to `ExcelTimetableData_backup` (MySQL)
- [x] Verified: schedule queries now return correct day/time/subject results

### 2.2 Confirm teammate endpoints are live
- [ ] Teammate A: confirm PI 5 `/tts_render` accepts `{ "phoneme_text": str }` and plays audio
- [ ] Teammate B: confirm PI 5 `/navigation` accepts `{ "cmd": str, "destination": str? }`
- [ ] Test end-to-end: ask a navigation question, confirm robot moves

### 2.3 Wire up KhanomTan TTS on server ✅ (infrastructure done, blocked on dependency)
- [x] `tts/khanomtan_engine.py` implemented — synthesizes WAV via pythaitts, POSTs to PI 5 `/audio_play`
- [x] `IntentRouter` and `GreetingBot` dispatch on `tts_mode` (`"server"` | `"pi5"`)
- [x] `tts_mode` read from `config/settings.yaml` and injected at startup in `api/main.py`
- [ ] `coqui-tts` not installed — `TTS_AVAILABLE=False`, server mode blocked
      → Fix: `pip install coqui-tts`, then set `tts.mode: "server"` in `settings.yaml`
- Note: correct class name is `TTS` (not `PyThaiTTS`) from `pythaitts 0.4.2`
- Note: `walle-tts` Docker container uses VachanaTTS (not KhanomTan) — different model

### 2.4 Low-confidence STT fallback response ✅
- [x] STT confidence gate calls `intent_router._speak()` with phoneme-converted fallback
- [x] Student hears `"ขอโทษค่ะ ช่วยพูดอีกครั้งได้ไหมค่ะ"` instead of silence
- [x] Monitor log event: `status: "low_confidence_fallback"`

### 2.5 Grammar corrector high-confidence skip ✅
- [x] `correct()` takes `confidence: float = 0.0` parameter
- [x] STT confidence ≥ 0.85 bypasses LLM call entirely — passes text through unchanged
- [x] Logged at DEBUG level: `"Grammar skip: STT confidence=X.XX is high, passing through"`
- [x] `receiver.py` passes `payload.stt.confidence` into `correct()` call
- [x] Log line updated to show `conf=%.2f`

### 2.6 Session timeout and cleanup ✅
- [x] `_cleanup_expired_sessions(timeout_seconds)` added to `receiver.py`
- [x] Called on every `/detection` event after `_active = 1`
- [x] Uses `cfg["session"]["session_timeout_seconds"]` (600s from settings)
- [x] Expired sessions logged with session_id and history turn count
- [x] No Milvus flush needed — history already stored fire-and-forget via `ask_and_store()`

---

## Phase 3 — Polish (When Everything Works)

- [ ] Replace in-memory session state with Redis (for restart resilience)
- [ ] Upgrade Python to 3.9+ — enables pythainlp 4.x / 5.x features
- [ ] Swap embedding model to `paraphrase-multilingual-MiniLM-L12-v2` for Thai-aware search
      → Requires re-ingesting all Milvus collections after switch
- [ ] Add `/events` pagination (currently capped at last 50 events)
- [ ] Tune chatbot system prompt based on real conversation quality
- [ ] `greeting_cooldown_seconds` tuning based on real usage patterns
- [ ] Load test: multiple students detected simultaneously

---

## Phase 4 — Performance Benchmarking & Technical Report

### 4.1 Timing instrumentation
- [ ] Add `time.perf_counter()` around each stage in `receiver.py` `/detection` handler
- [ ] Log per-stage durations as structured JSON: `{ session_id, stage, duration_ms, timestamp }`
- [ ] Stages to instrument: grammar correction, RAG embed, Milvus search, LLM answer, intent route, TTS send

### 4.2 Benchmark harness (`tools/benchmark.py`)
- [ ] Replay 50 fixed `DetectionPayload` fixtures through live server
- [ ] Cover all RAG routes (chat_history / mysql_students / time_table / curriculum / uni_info)
- [ ] Cover all intents (chat / info / navigate / farewell)
- [ ] Cover high-conf (grammar skip) vs low-conf (full LLM) requests
- [ ] Output mean / p50 / p95 / p99 latency per stage

### 4.3 Accuracy evaluation (`tools/eval_accuracy.py`)
- [ ] Grammar corrector: 20 noisy STT strings → score corrected vs hand-labelled expected
- [ ] RAG routing: 20 questions with known correct collection → count hits
- [ ] Intent router: 20 chatbot responses with known intent → count correct predictions

### 4.4 Technical report (`docs/technical_report.md`)
- [ ] System architecture description
- [ ] Per-component accuracy table (from 4.3)
- [ ] End-to-end latency breakdown table (from 4.2)
- [ ] Grammar skip latency delta: skipped vs full LLM correction
- [ ] TTS Option A vs B latency delta (once Option A active)
- [ ] Known limitations and proposed improvements

---

## Interface Contracts (for teammates)

### Server → PI 5 (what PI 5 must expose)

| Endpoint | Method | Payload | Purpose |
|----------|--------|---------|---------|
| `/tts_render` | POST | `{ "phoneme_text": str }` | PI 5 runs TTS locally and plays audio (Option B, active) |
| `/audio_play` | POST | WAV bytes (`audio/wav`) | PI 5 plays WAV from server (Option A, pending coqui-tts) |
| `/navigation` | POST | `{ "cmd": str, "destination": str? }` | ROS2 command |

**`cmd` values:**
- `"stop_roaming"` — halt robot movement (on greeting)
- `"resume_roaming"` — resume autonomous roaming (on farewell)
- `"go_to"` + `"destination": "ห้องสมุด"` — navigate to named location

### PI 5 → Server (what server exposes)

| Endpoint | Method | Payload | Purpose |
|----------|--------|---------|---------|
| `/detection` | POST | `DetectionPayload` | Vision + STT event — runs full pipeline |
| `/greeting` | POST | `GreetingPayload` | First contact with registered person |
| `/activate` | GET | — | Poll activation state (`{ "active": 0|1 }`) |
| `/health` | GET | — | Server health check |
| `/monitor` | GET | — | Live pipeline dashboard (auto-refresh 3s) |
| `/events` | GET | — | Last 50 pipeline events (JSON) |
