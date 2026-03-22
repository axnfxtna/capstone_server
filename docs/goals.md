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
- [x] Teammate A: PI 5 `/audio_play` confirmed — server sends WAV, PI 5 plays through speaker ✅
- [x] Teammate A: PI 5 `/set_active` confirmed — push-based activation working ✅
- [ ] Teammate B: confirm PI 5 `/navigation` accepts `{ "cmd": str, "destination": str? }`
- [ ] Test end-to-end: ask a navigation question, confirm robot moves

### 2.3 Wire up KhanomTan TTS on server ✅ DONE
- [x] `tts/khanomtan_engine.py` implemented — synthesizes WAV via pythaitts, POSTs to PI 5 `/audio_play`
- [x] `IntentRouter` and `GreetingBot` dispatch on `tts_mode` (`"server"` | `"pi5"`)
- [x] `tts_mode` read from `config/settings.yaml` and injected at startup in `api/main.py`
- [x] `pythaitts TTS` (Coqui-TTS) installed — `TTS_AVAILABLE=True`, `tts.mode: "server"` active
- [x] WAV synthesis confirmed: 105KB in ~633ms (RTF 0.19×, GPU)
- [x] End-to-end: server synthesizes WAV → POST to PI 5 `/audio_play` → `200 OK` ✅

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

## Phase 2.7 — Stability & Quality Pass ✅ DONE (2026-03-21)

- [x] **PI 5 race condition fix** — `_send_event()` in `raspi_main.py`: `_set_inactive()` moved to BEFORE HTTP POST so server's `/set_active` push is never overwritten. Eliminates 60-second INACTIVE timeout.
- [x] **`asyncio.get_event_loop()` → `asyncio.get_running_loop()`** in `raspi_main.py` `_set_inactive()` (Python 3.11 fix)
- [x] **Male pronoun fix** — `enforce_female_particle()` now replaces `ผม` → `ฉัน` and strips English sentences
- [x] **System prompt hardened** — bans `ผม`, bans English sentences, calls student by first name only
- [x] **TTS pre-processor rewritten** — `mcp/tts_router.py` now uses word tokenization + syllabification (no phoneme character substitution). Old approach corrupted คุณ→คุน, อะไร→อะไน, ต้องการ→ต้องกาน etc.
- [x] **Monitor MCP visibility** — `rag_collection` and `phoneme_text` fields added to detection log events; monitor renders them as "RAG" and "TTS text" rows
- [x] **Greeting log fixed** — monitor now shows original Thai greeting text, not phoneme-processed text

---

## LLM Upgrade ✅ DONE (2026-03-22)

- [x] LLM upgraded: `qwen2.5:7b-instruct` → `llama3.1-typhoon2-70b-instruct` (Q5_K_M GGUF)
- [x] Pulled via Ollama: `hf.co/mradermacher/llama3.1-typhoon2-70b-instruct-GGUF:Q5_K_M`
- [x] Running on 4× A100-SXM4-80GB; `config/settings.yaml` `llm.model` updated

## Typhoon2-Audio Sidecar ✅ DONE (2026-03-22)

### Phase A — TTS Replacement ✅
- [x] `audio_service/` sidecar created — Python 3.10, port 8001
- [x] `POST /tts { text }` → WAV bytes (16-bit PCM, 16000 Hz, mono)
- [x] `tts.engine: "typhoon_audio"` in settings — GreetingBot + IntentRouter dispatch to sidecar
- [x] End-to-end confirmed: server → sidecar TTS → PI 5 `/audio_play` ✅
- [x] GLIBC / flash-attn fix: `attn_implementation="eager"`, cached model patched

### Phase B — Server-side STT ✅
- [x] `POST /stt <WAV bytes>` on sidecar → `{"text": str}`
- [x] `POST /audio_detection` on main server — multipart: WAV audio + person metadata
- [x] Pipeline: sidecar STT → same grammar → RAG → intent flow as `/detection`
- [x] `tts/typhoon_audio_tts.transcribe()` — async STT client helper

---

## Phase 2.8 — Prompt Fine-tuning (All Models)

Fine-tune every LLM prompt in the pipeline based on observed real-conversation quality.
Reference: `docs/design.md` sections 3.2–3.6.

### 2.8.1 `greeting_bot` — `mcp/greeting_bot.py` ✅ DONE (2026-03-22)
- [x] Year-based tone mapping injected into prompt (Thai, 4 tiers: encouraging / project / internship / thesis)
- [x] Memory retrieval injected: Milvus `conversation_memory` queried at greeting time, summary injected
- [x] Prompt rules: recall memory if present; warm intro + self-introduction on first contact
- [x] Removed `ros2_cmd` from LLM JSON schema — code always sends `stop_roaming`, no LLM involvement
- [x] Temperature raised 0.5 → 0.7 for greeting variety
- [x] `GreetingBot.__init__` now accepts `memory_manager` and injects it
- [x] `greet()` signature: `(student_name, student_id, student_year)` — year + student_id resolved from MySQL
- [x] `fetch_student_by_nickname()` added to `database/mysql_client.py`
- [x] `/greeting` endpoint does MySQL lookup for year + student_id; caches year in `/detection` session
- [x] **Prompt v2 (2026-03-22)**: time-of-day injection (เช้า/เที่ยง/บ่าย/เย็น/กลางคืน); memory contextual sensitivity — broad references only, no technical details; no double-question rule; year tones sharpened per tier

### 2.8.2 `mcp_grammar` — `mcp/grammar_corrector.py` ✅ DONE (2026-03-22)
- [x] Reframed as STT Text Normalizer — not a language editor
- [x] Preserve-style rules replace negative constraints (positive framing more effective)
- [x] Few-shot examples added: no-particle input preserved, typo-only correction shown
- [x] Skip LLM entirely for inputs < 15 chars — avoids over-correction on short casual utterances

### 2.8.3 `llm_chatbot` — `mcp/llm_chatbot.py` + `llm/typhoon_client.py` ✅ DONE (2026-03-22)
- [x] System prompt rewritten — capabilities-first, scope-limited to university Q&A + E-12 navigation
- [x] 2-sentence cap (down from 1-3) for cleaner TTS output
- [x] Out-of-scope denial rule added
- [x] Intent descriptions moved above JSON schema as clear 4-way guide
- [x] `confidence` field removed from JSON output — never used downstream
- [x] RAG default fallback changed from `uni_info` → `chat_history`
- [x] `_last_route` shared-state bug removed — no more cross-student routing contamination
- [x] Casual/greeting/identity keywords added to `chat_history` route

### 2.8.4 `mcp_summary` — `mcp/memory_manager.py` ✅ DONE (2026-03-22)
- [x] Summary prompt updated — entity preservation (building names, subjects, project topics kept in summary)
- [x] Outcome-focused structure: keyword + final state, so Milvus vector search hits relevant past turns
- [x] `to_tts_ready()` bypassed for `typhoon_audio` engine in `intent_router` and `greeting_bot`

### 2.8.5 `mcp_intendgate` — `mcp/intent_router.py` ✅ DONE (2026-03-22)
- [x] Navigation confirmation LLM call dropped — replaced with fixed template (saves one LLM call per navigate)
- [x] Intent classification handled by chatbot LLM in single call (Option A)

---

## Phase 2.9 — Database & Memory Pipeline Testing ✅ DONE (2026-03-22)

### 2.9.1 Write path — conversation turn storage ✅
- [x] SQLite `conversation_log`: 244 turns confirmed, all fields present
- [x] Milvus `conversation_memory`: 244 entities; 75 with correct numeric `student_id`
- [x] `student_id` partition key now uses real student_id resolved from MySQL (not raw person_id)
- [x] Old 168 Milvus entries use legacy `"Palm (Krittin Sakharin)"` format — filtered out cleanly by search expr

### 2.9.2 Read path — memory retrieval ✅
- [x] `memory_manager.retrieve()` returns top-k summaries filtered by `student_id`
- [x] Verified: search for `student_id="65011356"` returns semantically relevant hits (score ≥ 0.80)
- [x] Memory injected into `_CHATBOT_PROMPT_TEMPLATE` under `ข้อมูลที่จำได้`

### 2.9.3 Bug fixed — `enrollment_year` column ✅
- [x] `Students` table has `enrollment_year` (absolute year, e.g. 2022), not `year`
- [x] Both `/greeting` and `/detection` handlers updated: `student_year = min(current_year - enrollment_year + 1, 1→4)`
- [x] All 4 students: enrolled 2022, current 2026 → `student_year = 4` (ว่าที่บัณฑิต tone)

### 2.9.4 Dual-Model LLM Architecture ✅
- [x] Pulled `typhoon2-8b-instruct Q5_K_M` (5.7 GB) via Ollama
- [x] `llm_fast` block added to `config/settings.yaml`
- [x] `api/main.py` instantiates two `TyphoonClient` instances
- [x] `GrammarCorrector` + `MemoryManager` → `llm_fast` (8B)
- [x] `LLMChatbot` + `GreetingBot` → `llm` (70B)

---

## Embedding Model Upgrade — BAAI/bge-m3 🔜 NEXT

Upgrade from `sentence-transformers/all-MiniLM-L6-v2` (384-dim, English-only) to `BAAI/bge-m3`
(1024-dim, multilingual, state-of-the-art Thai support). Hardware (4× A100-SXM4-80GB) handles it easily.

### Why bge-m3 over paraphrase-multilingual-MiniLM-L12-v2
- bge-m3 supports 100+ languages with significantly better Thai semantic quality
- 1024-dim vs 384-dim — richer representation for Thai words and phrases
- Current RAG scores for `time_table` queries are ~0.28-0.33 — bge-m3 expected to improve noticeably
- All dataset files are available locally for re-ingestion

### Scope of change
| Collection | Dim change | Action |
|---|---|---|
| `curriculum` | 384 → 1024 | Drop + re-embed 516 PDF chunks |
| `time_table` | 384 → 1024 | Drop + re-embed via `reingest_timetable.py` (update EMB_MODEL + EMB_DIM) |
| `uni_info` | 768 → 1024 | Drop + re-embed 7 docs (currently padded/mismatched anyway) |
| `conversation_memory` | 384 → 1024 | Drop + recreate (rebuilds from new conversations) |
| `chat_history` | 384 → 1024 | Drop + recreate (legacy collection, not actively written by server) |

### Implementation steps
1. **`config/settings.yaml`** — update `embedding_model` + `embedding_dim`:
   ```yaml
   embedding_model: "BAAI/bge-m3"
   embedding_dim: 1024
   ```
2. **`database/configs/configs.yaml`** — update `models.text_embedding.name` + `dim`
3. **`vector_db/milvus_client.py`** — update `MEMORY_DIM = 1024`; `ensure_memory_collection(dim=1024)`
4. **`tools/reingest_timetable.py`** — change `EMB_MODEL` + `EMB_DIM = 1024`; re-run
5. **Write `tools/reingest_curriculum.py`** — reads PDFs from `database/dataset/curriculum/`, chunks, embeds, inserts into `curriculum` (drop + recreate at 1024-dim)
6. **Write `tools/reingest_uni_info.py`** — re-embeds the 7 uni_info docs at 1024-dim
7. **Drop `conversation_memory` + `chat_history`** — recreated at 1024-dim on server restart
8. **Restart server** — `ensure_memory_collection()` recreates at 1024-dim automatically

### Dataset files available
- `database/dataset/curriculum/` — 4 PDFs (RAI Curriculum gen 61, 63, 68; AI Engineering gen 68)
- `database/dataset/time_table/` — 5 XLSXs (RAI 1-65, 1-66, 1-67, 2-65, 2-66)
- `database/dataset/uni_info/` — need to verify content

---

## Phase 3 — Polish (When Everything Works)

- [ ] Replace in-memory session state with Redis (for restart resilience)
- [ ] Upgrade Python to 3.9+ — enables pythainlp 4.x / 5.x features
- [ ] Add `/events` pagination (currently capped at last 50 events)
- [ ] Tune chatbot system prompt based on real conversation quality
- [ ] `greeting_cooldown_seconds` tuning based on real usage patterns
- [ ] Load test: multiple students detected simultaneously
- [ ] Confirm Teammate B `/navigation` endpoint end-to-end

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
| `/detection` | POST | `DetectionPayload` (JSON) | Vision + PI 5 STT event — runs full pipeline |
| `/audio_detection` | POST | multipart/form-data | Vision + raw WAV audio — server-side STT then full pipeline |
| `/greeting` | POST | `GreetingPayload` (JSON) | First contact with registered person |
| `/activate` | GET | — | Poll activation state (`{ "active": 0|1 }`) |
| `/health` | GET | — | Server health check |
| `/monitor` | GET | — | Live pipeline dashboard (auto-refresh 3s) |
| `/events` | GET | — | Last 50 pipeline events (JSON) |

### DetectionPayload schema (PI 5 → Server)

```json
{
  "timestamp": "str",
  "person_id": "str",              // face recognition label e.g. "Palm (Krittin Sakharin)"
  "thai_name": "str | null",       // Thai display name from PI 5 (preferred for LLM prompts)
  "student_id": "str | null",      // DB student_id from PI 5 (preferred for memory/MySQL lookup)
  "is_registered": true,
  "track_id": "int | null",
  "bbox": "[x1,y1,x2,y2] | null",
  "stt": {
    "text": "str",
    "language": "str",
    "duration": "float"
  }
}
```

### GreetingPayload schema (PI 5 → Server)

```json
{
  "timestamp": "str",
  "person_id": "str",
  "thai_name": "str | null",
  "student_id": "str | null",
  "is_registered": true,
  "vision_confidence": "float"
}
```

> **Note:** `stt.confidence` was removed — Typhoon ASR does not expose a real per-utterance beam score (was always hardcoded 0.80). STT confidence gate and grammar high-confidence skip have been removed accordingly.

### /audio_detection multipart schema (PI 5 → Server)

```
Content-Type: multipart/form-data

Fields:
  audio               UploadFile   WAV audio bytes (Thai speech from microphone)
  person_id           str          face recognition label e.g. "Palm (Krittin Sakharin)"
  is_registered       bool         true if face is registered
  thai_name           str | null   Thai display name (optional, preferred for LLM prompts)
  student_id          str | null   DB student_id (optional, preferred for memory/MySQL lookup)
  track_id            int | null   vision tracking ID (optional)
  vision_confidence   float | null face recognition confidence (optional)
  timestamp           str | null   ISO timestamp (optional, defaults to server time)
```

> PI 5 should send WAV in PCM format at the microphone's native sample rate.
> The Typhoon2-Audio model accepts any sample rate — no resampling needed.
