# Server Progress Log

## Project: KhanomTan AI Brain — Server Side
**Stack:** FastAPI · Ollama (llama3.1-typhoon2-70b-instruct Q5_K_M + typhoon2-8b Q5_K_M) · Typhoon2-Audio sidecar · Milvus · SQLite · MySQL · Python 3.8.10 (main) + 3.10 (audio sidecar)

---

## Current Status: Phase 2.9 Bug Fixes ✅ — Pending: Server Restart + Embedding Model Upgrade (BAAI/bge-m3)

Phase 2.9 fixes applied (2026-03-22). Server restart required for all changes to take effect.
Bug fixes: ปาล์มค่ะ prefix removed, grammar corrector hallucination guarded, TTS fire-and-forget, monitor TTS text row removed.
Next: restart server, verify fixes, then upgrade Milvus embedding model from `all-MiniLM-L6-v2` to `BAAI/bge-m3` (1024-dim, Thai-aware).

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
- [x] `thai_name` field added to both payloads — used as display name in LLM prompts when present (2026-03-22)
- [x] `student_id` field added to both payloads — used for MySQL/Milvus lookup when present (2026-03-22)
- [x] `stt.confidence` removed from `STTResult` — Typhoon ASR does not expose a real per-utterance score (2026-03-22)

### Processing Pipeline (per `/detection` event)
- [x] Registered person filter (`is_registered` + `person_id != "Unknown"`)
- [x] STT confidence gate — **removed** (2026-03-22): Typhoon ASR has no real confidence score
- [x] Session management — per `person_id`, in-memory, UUID session ID; year + student_db_id cached in session
- [x] **Session timeout** — idle sessions expired after 600s; history dropped, logged (Phase 2.6)
- [x] Grammar correction via LLM — uses chat API (not generate) to prevent prompt token leakage
- [x] Grammar corrector length guard — falls back to raw if output < 50% of input length
- [x] **Grammar high-confidence skip** — **removed** (2026-03-22): confidence was always 0.80, skip never fired
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
- [x] `enforce_female_particle()` also replaces male pronoun `ผม` → `ฉัน` and strips English sentences (Phase 2.7)
- [x] `build_chatbot_system_prompt()` explicitly bans `ผม`, bans English sentences, calls student by first name only (Phase 2.7)
- [x] Grammar corrector prompt: preserves polite particles, no truncation

### time_table Data — Re-ingested ✅
- [x] Old ingestion stored raw Excel rows (unusable) — replaced with per-(day, time_slot) sentences
- [x] Re-ingestion script: `tools/reingest_timetable.py`
- [x] Old data backed up to MySQL `ExcelTimetableData_backup` (229 rows)
- [x] 128 clean records from 4 files (RAI 1-65, 1-66, 2-65, 2-66)
      Note: RAI 1-67 skipped — curriculum list, not a timetable grid
- [x] Verified: "วิชา Programming เรียนวันไหน" → returns correct slots ✅

### Output (Server → PI 5)
- [x] TTS Option A — POST WAV bytes to PI 5 `/audio_play` (server GPU synthesis, active mode)
- [x] Navigation — POST to PI 5 `/navigation` with `{ "cmd": str, "destination"?: str }`
- [x] `tts_mode` config switch in `settings.yaml` (`"pi5"` | `"server"`) propagated to all modules
- [x] Push-based activation: server POSTs `{ "active": 1 }` to PI 5 `/set_active` after pipeline completes
- [x] `config/settings.yaml` `pi5_port` corrected to `8766` (was 5000)
- [x] TTS WAV delivery confirmed end-to-end — `audio_play 200 OK` in server logs ✅
- [ ] ROS2 navigation confirmed end-to-end (depends on Teammate B)

### Monitor Enhancements (Phase 2.7)
- [x] `rag_collection` logged per detection event — visible in monitor as "RAG" row
- [x] `phoneme_text` logged per detection event — visible in monitor as "TTS text" row
- [x] Greeting endpoint now logs original Thai text (not phoneme-processed text)
- [x] `llm_chatbot.ask()` returns `rag_collection` in response dict
- [x] `greeting_bot.greet()` returns `(greeting_text, tts_text)` tuple

### PI 5 Race Condition Fix (Phase 2.7)
- [x] `raspi_main.py` `_send_event()`: `_set_inactive()` moved to BEFORE `run_in_executor` — eliminates race where server's `/set_active` push arrived during HTTP call and was overwritten
- [x] `raspi_main.py` `_set_inactive()`: `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (Python 3.11 deprecation fix)
- [x] Fixed file sent to PI 5 operator

### TTS — Server-Side Infrastructure (Phase 2.3)
- [x] `tts/khanomtan_engine.py` created — synthesizes WAV via pythaitts, POSTs to PI 5 `/audio_play`
- [x] `IntentRouter` and `GreetingBot` dispatch on `tts_mode` (`"server"` → GPU WAV, `"pi5"` → text)
- [x] `pythainlp 3.1.1` installed; Thai syllabification working in `mcp/tts_router.py`
- [x] `pythaitts TTS` (Coqui-TTS) — install: `pip install pythaitts TTS` — `tts.mode` switched to `"server"` (2026-03-21)
- [x] `khanomtan_engine.py` aligned with docker `TTSPipeline`: `_clean_text()` preprocessing, result-path edge-case handling, file-existence guard (2026-03-21)
- [ ] `walle-tts` Docker container (VachanaTTS on port 5002) — defined but not running (superseded; server-mode active)
      → `sudo docker-compose --profile interactive up walle-tts` if fallback needed

### LLM Model Upgrade (2026-03-22)
- [x] LLM upgraded from `qwen2.5:7b-instruct` → `llama3.1-typhoon2-70b-instruct` (Q5_K_M GGUF via HuggingFace)
- [x] Loaded via Ollama: `ollama pull hf.co/mradermacher/llama3.1-typhoon2-70b-instruct-GGUF:Q5_K_M`
- [x] Runs on 4× A100-SXM4-80GB (320 GB VRAM total); Q5_K_M GGUF is ~46 GB
- [x] `config/settings.yaml` `llm.model` updated accordingly

### Typhoon2-Audio Sidecar — Phase A TTS (2026-03-22)
- [x] `audio_service/` created — separate Python 3.10 FastAPI service on port 8001
- [x] Uses pyenv Python 3.10.14 venv (`audio_service/venv310/`)
- [x] `audio_service/typhoon_audio.py` — loads `typhoon-ai/llama3.1-typhoon2-audio-8b-instruct`, exposes `synthesize()` and `transcribe()`
- [x] `audio_service/main.py` — FastAPI sidecar; `POST /tts` → WAV bytes, `POST /stt` → `{"text": str}`, `GET /health`
- [x] `audio_service/requirements.txt` — `transformers==4.45.2`, `fairseq==0.12.2`, flash-attn commented out (GLIBC_2.32 incompatible with Ubuntu 20.04)
- [x] `audio_service/start.sh` — activates venv310 and starts uvicorn on port 8001
- [x] GLIBC fix: `attn_implementation="eager"` in `AutoModel.from_pretrained()`
- [x] Cached model patch 1: `flash_attention_2` → `eager` in `modeling_typhoon2audio.py`
- [x] Cached model patch 2: `attention_mask=None` in `predict()` (2D mask incompatible with transformers 4.45.2 LlamaAttention)
- [x] `synthesize_speech()` returns `{"array": float32_ndarray, "sampling_rate": 16000}` — fixed extraction in `typhoon_audio.py`
- [x] `tts/typhoon_audio_tts.py` added — `synthesize_and_send()` and `transcribe()` helpers for main server
- [x] `tts_engine: "typhoon_audio"` in `settings.yaml`; `GreetingBot` and `IntentRouter` dispatch to sidecar
- [x] TTS confirmed end-to-end: valid 16-bit PCM WAV at 16000 Hz, 200 OK ✅

### Typhoon2-Audio Sidecar — Phase B STT (2026-03-22)
- [x] `POST /stt` endpoint on sidecar — accepts raw WAV bytes, returns `{"text": str}`
- [x] `typhoon_audio.transcribe()` — writes WAV to temp file, calls `model.generate()` with audio conversation
- [x] `tts/typhoon_audio_tts.py` `transcribe()` — async helper: POSTs WAV to sidecar, returns text
- [x] `POST /audio_detection` added to main server `api/routes/receiver.py`
  - Accepts multipart/form-data: `audio` (WAV UploadFile) + person metadata fields
  - Calls sidecar STT → builds DetectionPayload → delegates to `on_detection()` pipeline
  - Same grammar → RAG → intent routing flow as `/detection`
- [x] `config/settings.yaml` `audio_service.stt_enabled` — STT sidecar available

### Memory Summary & TTS Router (Phase 2.8.4 + TTS bypass, 2026-03-22)
- [x] `_SUMMARY_PROMPT` updated — entity preservation rule added (building names, subjects, project topics must not be dropped); outcome-focused structure for better Milvus retrieval relevance
- [x] `to_tts_ready()` bypassed for `tts_engine == "typhoon_audio"` — Typhoon2-Audio reads raw Thai natively; syllabification only applied for khanomtan/pi5 paths
- [x] Bypass applied in both `intent_router.route()` and `greeting_bot._send_tts()`

### Dual-Model LLM Architecture (2026-03-22)
- [x] Pulled `hf.co/mradermacher/llama3.1-typhoon2-8b-instruct-GGUF:Q5_K_M` via Ollama (5.7 GB)
- [x] `config/settings.yaml` — added `llm_fast` block (8B, timeout 15s, temperature 0.3)
- [x] `api/main.py` — two `TyphoonClient` instances: `llm` (70B) and `llm_fast` (8B)
- [x] `GrammarCorrector` and `MemoryManager` now use `llm_fast` (8B) — faster simple tasks
- [x] `LLMChatbot` and `GreetingBot` keep `llm` (70B) — best quality for conversation
- [x] Falls back to `llm` config if `llm_fast` missing (safe graceful degradation)
- [x] Both model names logged at server startup

### Database Verification (Phase 2.9, 2026-03-22)
- [x] **SQLite** — `conversation_log` 244 turns, `sessions` table healthy, all indexes present
- [x] **MySQL** — `Students` 4 rows, `Academic_Year` 4 rows, `ExcelTimetableData` 229 rows, `ChatHistory` 301 rows, `Face_Recognition_Data` empty (PI 5 side)
- [x] **Milvus** — 6 collections healthy:
  - `curriculum` 516 entities, `time_table` 229 entities, `uni_info` 7 entities
  - `conversation_memory` 244 entities, `chat_history` 240 entities, `student_face_images` 0
- [x] Memory search verified: `student_id="65011356"` returns 75 correctly-keyed entries (score ≥ 0.80)
- [x] Old Milvus entries (168) use `"Palm (Krittin Sakharin)"` format — silently skipped by filter, new writes correct
- [x] **`enrollment_year` bug fixed** — `Students` table uses `enrollment_year` (e.g. 2022), not `year`; both `/greeting` and `/detection` handlers now compute `student_year = min(current_year - enrollment_year + 1, 1→4)`
- [x] Verified: Palm (enrolled 2022, current 2026) → `student_year=4` → correct "ว่าที่บัณฑิต" tone in greeting

### Bug Fixes (Phase 2.9.1, 2026-03-22)
- [x] **"ปาล์มค่ะ" prefix bug fixed** — `build_chatbot_system_prompt()` in `llm/typhoon_client.py`:
  - Removed `"ลงท้ายด้วย ค่ะ เสมอ"` rule (LLM was appending ค่ะ after the student name mid-sentence); `enforce_female_particle()` handles this in post-processing
  - Added explicit rule: `ห้ามขึ้นต้นประโยคด้วยชื่อนักศึกษา ไม่ว่ากรณีใด`
- [x] **Grammar corrector hallucination guard** — `mcp/grammar_corrector.py`:
  - Added upper-length check: if output > 1.5× input length → discard as hallucinated, use raw
  - Added system prompt rule: `ห้ามตอบคำถาม ห้ามเพิ่มข้อมูลใดๆ` + counter-example (question input → question output unchanged)
- [x] **TTS fire-and-forget** — `mcp/intent_router.py`: all `_speak()` and `_navigate()` calls switched from `await` to `asyncio.create_task()` — pipeline no longer blocks on PI 5 TTS (was blocking ~18s per request when PI 5 is slow/unreachable)
- [x] **Monitor TTS text row removed** — `api/routes/monitor.py`: `phoneme_text` row removed from HTML dashboard; `api/routes/receiver.py`: `phoneme_text` key removed from `log_event()` call
- [ ] **Cleanup**: `phoneme_text` variable still computed in `receiver.py:272` but no longer used — safe to remove

### Prompt Fine-tuning (Phase 2.8.2–2.8.5, 2026-03-22)
- [x] **RAG routing fixed** — `_last_route` shared-state bug removed; default fallback changed from `uni_info` → `chat_history`; casual/greeting/identity keywords added to `chat_history` route
- [x] **Display name fix** — `_clean_name()` now applied to `thai_name` too; "ปาล์ม (กฤติน สาครินทร์)" → "ปาล์ม" everywhere
- [x] **`llm_chatbot` system prompt rewritten** — capabilities-first structure, scope limited to university Q&A + navigation in E-12 Building only, 2-sentence cap for TTS, out-of-scope denial rule
- [x] **Intent descriptions added** — moved above JSON schema as a clear 4-way decision guide (chat / info / navigate / farewell)
- [x] **`confidence` field removed** from chatbot JSON output — never used downstream
- [x] **`grammar_corrector` reframed** as STT Text Normalizer; preserve-style rules replace negative constraints; few-shot examples added; skip LLM for inputs < 15 chars
- [x] **Navigation confirmation LLM call dropped** — `intent_router` now uses fixed template `"ได้เลยค่ะ ตามหนูมาเลยนะค่ะ หนูจะพาไปที่ {destination} ค่ะ"` (saves one LLM call per navigate)
- [x] **Greeting prompt rewritten** — time-of-day injection (เช้า/เที่ยง/บ่าย/เย็น/กลางคืน); memory contextual sensitivity (broad tone, no technical details); no double questions rule; year tones sharpened per tier
- [x] **Pipeline timing instrumentation** added to `/detection` — grammar / llm / tts / total ms logged and visible in monitor as colour-coded bar

### Greeting Personalisation & Payload Schema Update (Phase 2.8.1, 2026-03-22)
- [x] `_GREETING_PROMPT` rewritten — year-tone in Thai (4 tiers), memory injection, removed `ros2_cmd` field
- [x] `_YEAR_TONE` map: ปี 1 = ให้กำลังใจ/อบอุ่น, ปี 2 = โปรเจกต์, ปี 3 = ฝึกงาน, ปี 4 = โปรเจกต์จบ
- [x] `GreetingBot.greet()` fetches Milvus memory at greeting time; injects as "ประวัติการสนทนาล่าสุด"
- [x] `GreetingBot.__init__` accepts `memory_manager`; `main.py` injects it at startup
- [x] `fetch_student_by_nickname()` and `fetch_student_by_id()` added to `database/mysql_client.py`
- [x] `/greeting` resolves year + student_id from MySQL (prefers `payload.student_id` if present)
- [x] `/detection` caches `student_year` + `student_db_id` in session on first event (no repeated MySQL calls)
- [x] `thai_name` + `student_id` added to `DetectionPayload` and `GreetingPayload` schemas
- [x] `stt.confidence` removed from `STTResult` schema — not a real value from Typhoon ASR
- [x] STT confidence gate removed from `/detection` handler
- [x] Grammar high-confidence skip removed from `GrammarCorrector.correct()` (never fired: 0.80 < 0.85)
- [x] `display_name` uses `payload.thai_name` when provided, falls back to `_clean_name(person_id)`
- [x] Greeting temperature raised 0.5 → 0.7 for variety

### TTS Pre-processor Rewrite (Phase 2.7)
- [x] Old `mcp/tts_router.py` phoneme rules caused character corruption: คุณ→คุน, อะไร→อะไน, ต้องการ→ต้องกาน, เลย→เย, สามารถ→สามาด etc.
- [x] Compared old vs new on 10 test cases — old approach corrupted 6/7 phrases; new produces clean output
- [x] Replaced with syllabify-only approach: `word_tokenize` + `thai_syllables` — inserts spaces, never changes characters
- [x] KhanomTan TTS v1.0 reads standard Thai natively; character substitution not needed
- [x] Result: อะไร → อะ ไร  |  คุณ → คุณ  |  ต้องการ → ต้อง การ  (spaces only, no corruption)

---

## Known Issues

| Issue | Severity | Status |
|-------|----------|--------|
| Grammar corrector still occasionally over-corrects informal Thai | Medium | Mitigated: length guard + prompt rules + high-conf skip (≥0.85) |
| Session state lost on server restart (in-memory only) | Low | Phase 3 item (Redis) |
| `time_table` search scores moderate (~0.28-0.33) — English embedder on Thai text | Low | Planned fix: upgrade to BAAI/bge-m3 (next move) |
| TTS venv dependency pins (numba/numpy/protobuf conflicts) | Resolved | numba upgraded to 0.58.1; numpy pinned to 1.24.4; protobuf 5.29.6 |
| `walle-tts` container (VachanaTTS) not running | Low | `sudo docker-compose --profile interactive up walle-tts` |
| STT confidence field removed | Resolved (2026-03-22) | Typhoon ASR has no real per-utterance beam score; `stt.confidence` removed from payload schema; gate and grammar skip removed |
| PI 5 60-second INACTIVE timeout | Resolved (Phase 2.7) | Race condition in `_send_event()` — `_set_inactive()` now called BEFORE `run_in_executor` |

---

## TTS Architecture

```
tts.engine = "typhoon_audio"  (current / active ✅)
  └─ GreetingBot / IntentRouter → tts/typhoon_audio_tts.synthesize_and_send()
     → POST audio_service:8001/tts { text }
     → Typhoon2-Audio-8B synthesizes WAV (16-bit PCM, 16000 Hz)
     → POST PI5:8766/audio_play <wav bytes>

tts.engine = "khanomtan"  (fallback)
  └─ khanomtan_engine.py → pythaitts TTS(pretrained="khanomtan") on GPU
     → POST PI5:8766/audio_play <wav bytes>

tts.mode = "pi5"  (PI 5-side TTS)
  └─ POST PI5:8766/tts_render { phoneme_text }
     PI 5 does TTS locally on ARM CPU
```

## STT Architecture

```
Option A — PI 5 STT (original)
  PI 5 Typhoon ASR → { "stt": { "text": str } } → POST server:8000/detection

Option B — Server STT via Typhoon2-Audio (new ✅)
  PI 5 → multipart WAV + metadata → POST server:8000/audio_detection
  → audio_service:8001/stt → Typhoon2-Audio-8B transcribes
  → same grammar → RAG → intent pipeline as /detection
```

---

## Docker Services (final_docker_component)

| Container | Port | Status | Purpose |
|-----------|------|--------|---------|
| `milvus` | 19530 | ✅ Running | Vector DB |
| `mysql` | 3306 | ✅ Running | Relational DB (Students, timetable) |
| `ollama` | 11434 | ✅ Running | LLM (llama3.1-typhoon2-70b-instruct Q5_K_M GGUF) |
| `etcd` | 2379 | ✅ Running | Milvus dependency |
| `minio` | 9002/9003 | ✅ Running | Milvus object storage |
| `audio_service` | 8001 | ✅ Running | Typhoon2-Audio sidecar (TTS + STT) — Python 3.10 venv |
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
| `conversation_memory` | 244 | ✅ Stores/retrieves session summaries; 75 entries with correct student_id |
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
| LLM | `llama3.1-typhoon2-70b-instruct` (Q5_K_M GGUF) via Ollama at `localhost:11434` |
| Embedding | `sentence-transformers/all-MiniLM-L6-v2` (dim 384) — upgrading to `BAAI/bge-m3` (1024) |
| Milvus | `localhost:19530` (Docker, run by root) |
| Python | 3.8.10 (main server venv `./venv`) · 3.10.14 (audio sidecar `audio_service/venv310/`) |
| Monitor | `http://10.100.16.22:8000/monitor` or SSH tunnel `http://localhost:8080/monitor` |

---

## Phase Roadmap

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Core pipeline (FastAPI, RAG, Milvus, sessions, TTS/ROS2 fire) | ✅ Done |
| 2.3 | Server-side TTS infrastructure (khanomtan_engine, tts_mode switch) | ✅ Done |
| 2.5 | Grammar high-confidence skip (STT conf ≥ 0.85 bypasses LLM) | ✅ Done |
| 2.6 | Session timeout & cleanup (600s idle expiry) | ✅ Done |
| 2.7 | PI 5 race condition fix, LLM persona, TTS pre-processor rewrite, monitor MCP visibility | ✅ Done |
| LLM upgrade | qwen2.5:7b → llama3.1-typhoon2-70b-instruct (Q5_K_M, 4× A100) | ✅ Done |
| Phase A TTS | Typhoon2-Audio sidecar TTS replacing khanomtan — WAV at 16kHz, confirmed end-to-end | ✅ Done |
| Phase B STT | Typhoon2-Audio sidecar STT — `/audio_detection` endpoint on main server | ✅ Done |
| 2.8.1 | Greeting personalisation — year-tone + memory recall + thai_name/student_id payload | ✅ Done |
| 2.8.2–5 | Prompt fine-tuning — grammar, chatbot, greeting, intent router | ✅ Done |
| Dual-LLM | Typhoon2-8B for grammar + memory; 70B for chatbot + greeting | ✅ Done |
| 2.9 | Database verification — SQLite / MySQL / Milvus all confirmed healthy; enrollment_year bug fixed | ✅ Done |
| 2.9.1 | Bug fixes — prefix, grammar hallucination guard, TTS fire-and-forget, monitor cleanup | ✅ Done (restart pending) |
| Embed upgrade | Swap embedding from all-MiniLM-L6-v2 → BAAI/bge-m3 (1024-dim, Thai-aware) | 🔜 Next |
| 3 | Session persistence across restarts (Redis) | ⬜ Not started |
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
