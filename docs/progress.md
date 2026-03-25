# Server Progress Log

## Project: Satu AI Brain — Server Side
**Stack:** FastAPI · Ollama (llama3.1-typhoon2-70b-instruct Q5_K_M + typhoon2-8b Q5_K_M) · Typhoon2-Audio sidecar · Milvus · SQLite · MySQL · Python 3.8.10 (main) + 3.10 (audio sidecar)

---

## Current Status: ROS2 Nav Integration + Pipeline Polish ✅ DONE (2026-03-25)

All accuracy targets met. Pipeline is ready for pilot deployment.
Next: Add bathroom data to `uni_info` (data gap) → Phase 5 ROS2 navigation (depends on Teammate B).

| Metric | Result | Target |
|--------|--------|--------|
| Intent Accuracy | 90.9% | ≥ 90% ✅ |
| Macro F1 | 0.93 | — |
| Slot F1 (navigate destination) | 1.00 | ≥ 0.85 ✅ |
| RAG Route Accuracy | 100% | — |
| Language Compliance | 100% | 100% ✅ |
| OOS Rejection Rate | 100% | ≥ 80% ✅ |
| Task Success Rate (TSR) | 0.91 | ≥ 0.80 ✅ |

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
- [x] Embedding model: `BAAI/bge-m3` (1024-dim, multilingual) on CUDA

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
- [x] `to_tts_ready()` bypassed for `tts_engine == "typhoon_audio"` — Typhoon2-Audio reads raw Thai natively; syllabification only applied for satu/pi5 paths
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
- [x] **`phoneme_text` dead code removed** — `from mcp.tts_router import to_tts_ready` import + assignment deleted from `receiver.py` (2026-03-23)

### Embedding Upgrade — BAAI/bge-m3 ✅ DONE (2026-03-23)
- [x] `config/settings.yaml` → `embedding_model: BAAI/bge-m3`, `embedding_dim: 1024`
- [x] `database/configs/configs.yaml` → all collection dims updated to 1024
- [x] `vector_db/milvus_client.py` → `MEMORY_DIM = 1024`, all default model args updated
- [x] `tools/reingest_timetable.py` re-run — 128 entities at 1024-dim; scores 0.30 → 0.60
- [x] `tools/reingest_curriculum.py` written + run — 516 entities at 1024-dim
- [x] `tools/reingest_uni_info.py` written + run — 4 entities at 1024-dim (2 docx + new map txt)
- [x] `tools/drop_old_collections.py` written + run — dropped conversation_memory + chat_history (recreated at 1024-dim on restart)
- [x] `final_docker_component/dataset/uni_info/kmitl_map_info_thai.txt` created — all 52 buildings across Zone A–D ingested
- [x] PyMuPDF + python-docx installed in venv

### Phase 2.11 — Unknown Person + Session Fixes + Prompt Tuning v2 ✅ DONE (2026-03-23)

#### Unknown Person Interaction
- [x] `/greeting` unknown path — calls `greet_stranger()` instead of returning `skipped`
- [x] `greet_stranger()` added to `GreetingBot` — visitor prompt, 1-sentence cap, `หนู` pronoun, no name/memory
- [x] `/detection` unknown path — full `chatbot.ask()` pipeline (no memory store), session keyed by `track_id`
- [x] Guest sessions tracked in-memory with 5-min timeout (`guest_session_timeout_seconds: 300`)
- [x] Guest turns logged to SQLite for audit; Milvus memory store skipped
- [x] Stranger greeting cooldown via `__stranger__` key in `_last_greeting`

#### Session Persistence (Restart Recovery)
- [x] `get_latest_session(student_id)` added to `sqlite_client.py`
- [x] `_restore_session()` helper — checks in-memory first, falls back to SQLite on restart
- [x] `upsert_session()` wired into `/greeting` and `/detection` for registered persons
- [x] `get_turns(session_id)` restores history after server restart
- [x] Registered timeout raised to 30 min; per-role cleanup: guest 5 min / registered 30 min

#### TTS English/Number Expansion
- [x] `expand_for_tts()` added to `mcp/tts_router.py` — translates English letters, acronyms, numbers to Thai before TTS
- [x] E-12 → อี สิบสอง | KMITL → เค เอ็ม ไอ ที แอล | Zone D → โซน ดี | 3 → สาม
- [x] 4-digit numbers read digit-by-digit (room codes): 1201 → หนึ่ง สอง ศูนย์ หนึ่ง
- [x] `expand_for_tts()` wired into `intent_router.py` and `greeting_bot.py` for all TTS engines
- [x] Fixed: `num_to_word` → `num_to_thaiword` (correct function name in pythainlp 3.1.1)

#### Prompt Tuning v2
- [x] **Day/time awareness** — `_current_datetime_str()` added to `typhoon_client.py` (UTC+7); injected into `build_chatbot_system_prompt()` and `_get_time_of_day()` in `greeting_bot.py`
- [x] **ตึกสิบสอง** — replaced all instances of `ตึกโหล` in prompts and system prompt (easier TTS pronunciation)
- [x] **Over-helping fix** — system prompt rule changed to `"น้องสาธุไม่มีข้อมูลเรื่องนั้นค่ะ"`, no suggestions or elaboration allowed
- [x] **ปี 4 tone** — changed from thesis-focused formal tone to `"คุยแบบเป็นกันเองและให้กำลังใจที่ใกล้เรียนจบ"`
- [x] **Greeting context seeding** — greeting turn injected into session history as `("", greeting_text)` so chatbot knows what it just said on first user reply
- [x] **Navigate TTS fix** — `intent_router.py` uses LLM `reply_text` directly for TTS; destination no longer spoken again
- [x] **Grammar corrector "Output:" prefix** — strips `"Output:"` if LLM mimics few-shot format
- [x] **STT noise gate** — drops inputs < 3 chars; shows `noise` tag in monitor; `(empty)` displayed in grey when blank
- [x] **`enforce_female_particle()` extended** — replaces `ข้าพเจ้า` → `หนู`, `ดิฉัน` → `หนู`, `ผม` → `หนู`

#### Greeting Quality Fixes (2026-03-23)
- [x] **Greeting length cap** — `max_tokens` reduced 128 → 64 for both `greet()` and `greet_stranger()`; constraint updated with concrete short example
- [x] **Natural greeting questions** — banned `"มีความสุขไหม"` pattern (feels robotic); prompt now gives open-ended examples: `"เป็นยังไงบ้างคะ"`, `"ช่วงนี้เป็นไงบ้างคะ"`, `"วันนี้เหนื่อยไหมคะ"`
- [x] **Stranger greeting shortened** — example in `_STRANGER_GREETING_PROMPT` trimmed to `"สวัสดีตอน[เวลา]ค่ะ หนูชื่อน้องสาธุ มีอะไรให้ช่วยไหมค่ะ"`; capability dump banned

#### Fallback Phrases
- [x] **Don't-know response** — changed from `"ขอโทษค่ะ ไม่ทราบค่ะ"` → `"น้องสาธุไม่มีข้อมูลเรื่องนั้นค่ะ"` (honest, no apology)
- [x] **Don't-understand / JSON-parse fallback** — changed from `"ขออภัยค่ะ ไม่เข้าใจคำถาม..."` → `"รบกวนพูดใหม่อีกทีได้มั้ยคะ"` (natural request)
- [x] Applied consistently in: `SYSTEM_PROMPT`, `build_chatbot_system_prompt()`, `_FALLBACK_RESPONSE`, `TyphoonClient.generate()`, `TyphoonClient.chat()`

#### Student Year Calculation Fix
- [x] **Off-by-one removed** — formula was `current_year - enrollment_year + 1`; corrected to `current_year - enrollment_year` (65→4th, 66→3rd, 67→2nd, 68→1st all now correct)
- [x] **ID-prefix fallback** — when MySQL has no row, derives year from student_id first 2 digits (Thai Buddhist year short form: 65 = 2565 BE = 2022 CE → year 4)
- [x] Fixed in both `/greeting` handler and `/detection` session init

---

### Phase 2.10 — Pipeline Fixes & Test Suite ✅ DONE (2026-03-23)
- [x] **`uni_info` RAG routing fixed** — `mcp/llm_chatbot.py`: `uni_info` keyword block added to `_ROUTE_KEYWORDS` (was missing entirely; location queries defaulted to `chat_history`)
  - Keywords: `อยู่ที่ไหน`, `อยู่ไหน`, `ตึก`, `อาคาร`, `แผนที่`, `โซน`, `zone`, `E-12`, `HM`, `ECC`, campus facilities
- [x] **Robot self-location added to system prompt** — `llm/typhoon_client.py` `build_chatbot_system_prompt()`: states robot works on 12th floor of E-12, Zone D
- [x] **`pipeline_test.py` rewritten** — updated for current schema (no `stt.confidence`, no `phoneme_text`); 39 checks covering greeting, cooldown, all RAG routes, intent types, language rules; all 39/39 pass
- [x] **Stage-by-stage pipeline logging** — `api/routes/receiver.py`: `▶`/`✔` log lines at start + end of each stage (grammar / llm / tts); audio_detection logs STT byte count + transcript
- [x] **Duplicate log lines fixed** — `api/main.py`: `force=True` added to `logging.basicConfig()` — prevents uvicorn's double-import from registering two FileHandlers

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
- [x] Satu TTS v1.0 reads standard Thai natively; character substitution not needed
- [x] Result: อะไร → อะ ไร  |  คุณ → คุณ  |  ต้องการ → ต้อง การ  (spaces only, no corruption)

---

## Phase 6 — Robot Emotion / Expression Frontend ✅ DONE (2026-03-24)

### Architecture Decision
After initial implementation on the server side, all emotion state transitions were moved to the PI5.
The PI5 owns both audio output (speaker) and face display, so it has full visibility into the emotion lifecycle.
The server pipeline has zero face calls — no latency impact, no coupling.

### Server-side deliverables
- [x] `face/face_client.py` — async `set_face(emotion, url)` helper with constants (IDLE/SCANNING/HAPPY/TALKING/THINKING); kept for future use (e.g. navigation scanning callback)
- [x] `face/__init__.py` — package marker
- [x] `config/settings.yaml` — `pi5_face_port: 7000` added
- [x] `tools/test_face_emotions.py` — unit tests for face_client helper + live PI5 connectivity test
- [x] `tools/watch_face.py` — real-time face state monitor; polls `/health` every 500ms, prints on change
- [x] `docs/face_integration_notes.md` — PI5 implementation guide (emotion codes, transition table, timing notes)

### Test results
- [x] Unit tests: 9/9 pass (constants, error swallowing, correct HTTP body, all 5 codes)
- [x] Live PI5 test: 8/8 pass — all emotion codes (0–4) accepted, invalid code rejected with 4xx
- [x] Confirmed via `watch_face.py`: `talking (3)` visible on robot screen during TTS

### PI5 responsibilities (documented in `docs/face_integration_notes.md`)
- [x] Before POSTing `/greeting` → send `happy (2)`
- [x] Before POSTing `/detection` → send `thinking (4)`
- [x] When audio playback starts (in audio player) → send `talking (3)`
- [x] When audio playback ends → send `idle (0)`
- [ ] When navigate TTS ends and robot starts moving → send `scanning (1)` (depends on Teammate B ROS2)
- [ ] When robot arrives at destination (ROS2 callback, TBD) → send `idle (0)`

---

### Robot Rename — สาธุ / Satu (2026-03-24)
- [x] Thai name: สาธุ — robot calls itself น้องสาธุ or น้อง
- [x] English name: Satu (not KhanomTan)
- [x] All prompts updated: `mcp/greeting_bot.py`, `llm/typhoon_client.py`, `mcp/llm_chatbot.py`
- [x] All labels updated: `api/main.py`, `api/routes/monitor.py`, `vector_db/milvus_client.py`
- [x] All docs updated: `progress.md`, `goals.md`, `design.md`, `pi5_*.md`, `teammate_b_design.md`
- [x] Tools updated: `tools/benchmark.py`, `tools/eval_accuracy.py`, `tools/scp_to_pi5.sh`
- [x] Configs updated: `config/settings.yaml`, `config/pi5.yaml`
- [x] `SYSTEM_PROMPT` constant removed from `llm/typhoon_client.py` — was imported but never used; `build_chatbot_system_prompt()` is sole identity prompt
- [x] `enforce_female_particle()` pronouns updated: ผม/ดิฉัน/ข้าพเจ้า → น้อง (was หนู)
- [ ] `tts/khanomtan_engine.py` filename kept — refers to pythaitts library model, not robot name

---

### Prompt Tuning v2 — Bug Fixes ✅ DONE (2026-03-25)

- [x] **`ฉัน` pronoun fix** — `enforce_female_particle()` in `llm/typhoon_client.py`: added `ฉัน` → `น้อง` replacement
- [x] **Grammar corrector English bypass** — `mcp/grammar_corrector.py`: code-level early exit before LLM if no Thai characters detected (`[\u0e00-\u0e7f]`); English/non-Thai STT input returned unchanged instantly
- [x] **Navigate false positive guard** — `mcp/llm_chatbot.py` `_CHATBOT_PROMPT_TEMPLATE`: navigate intent description now requires explicit destination (A/B/C) to be present; falls back to `info` if unclear
- [x] **Repetitive replies** — deemed acceptable behaviour; no change made
- [x] **`_current_datetime_str()` crash in greeting prompt** — `mcp/greeting_bot.py`: invalid `{_current_datetime_str()}` format placeholder replaced with `{current_datetime}`; `_current_datetime_str` imported from `typhoon_client` and passed as kwarg to `.format()`
- [x] **All datetime → Thai time UTC+7** — `database/sqlite_client.py`, `mcp/memory_manager.py`, `api/routes/monitor.py`, `tools/benchmark.py`, `tools/eval_accuracy.py`: all `datetime.utcnow()` and `datetime.now()` replaced with `datetime.now(timezone(timedelta(hours=7)))`; timestamp strings now use `+07:00` suffix
- [x] **Grammar corrector fully skipped** — `api/routes/receiver.py`: both registered and guest paths now set `corrected = payload.stt.text` directly; 8B model was hallucinating (answering questions, translating English → Thai); grammar LLM call removed entirely
- [x] **Timetable RAG routing fix** — `mcp/llm_chatbot.py`: added `routing_hint: Optional[str]` param to `ask()` and `ask_and_store()`; `receiver.py` passes raw `payload.stt.text` as `routing_hint`; grammar corrector was rewriting `"วันไหน"` to synonyms causing keyword miss
- [x] **Greeting fire-and-forget** — `mcp/greeting_bot.py`: `await asyncio.gather()` → `asyncio.create_task()` for both TTS and navigation; HTTP response no longer blocks on PI5 TTS (was blocking up to 10s)
- [x] **`_YEAR_TONE` NameError crash** — `mcp/greeting_bot.py`: removed dangling `year_tone = _YEAR_TONE.get(...)` line (dict was intentionally deleted but reference remained, causing every registered greeting to 500)
- [x] **TTS pronunciation fix for สาธุ** — `mcp/tts_router.py`: `_THAI_SUBSTITUTION` dict added (`น้องสาธุ` → `น้อง สา ทุ`, `สาธุ` → `สา ทุ`); applied in `to_tts_ready()` before syllabification
- [x] **TTS English word expansions** — `mcp/tts_router.py` `_ENG_WORD`: added `programming`, `drawing`, `introduction`, `physics`, `to`

### OOS Fix + Eval Script Fixes ✅ DONE (2026-03-25)

- [x] **`destination` string "null" normalization** — `mcp/llm_chatbot.py` `ask()`: `"null"` / `"none"` / `""` → `None`; was the root cause of 2 OOS eval failures (LLM outputs string literal `"null"` which Python evaluates as truthy)
- [x] **`info` intent scope tightened** — `_CHATBOT_PROMPT_TEMPLATE`: `info` now explicitly restricted to KMITL university questions only; homework/translation/calling banned from `info`
- [x] **`chat` covers OOS** — `_CHATBOT_PROMPT_TEMPLATE`: `chat` intent now explicitly covers out-of-scope requests with a polite decline
- [x] **System prompt OOS rule hardened** — `llm/typhoon_client.py`: removed "สามารถเสนอแนะหรืออธิบายเพิ่มเติม"; now strictly "น้องสาธุไม่มีข้อมูลเรื่องนั้นค่ะ" with no exceptions
- [x] **`eval_accuracy.py` navigate fixtures updated** — replaced unreachable destinations (ห้องสมุด/ห้องน้ำ/โรงอาหาร/1201) with valid A/B/C rooms matching current robot capability
- [x] **`eval_accuracy.py` `fetch_event` bug fixed** — events are stored newest-first (`appendleft`); `evts[after_index:]` was returning oldest events; fixed to `evts[:len(evts)-after_index]`
- [x] **`eval_accuracy.py` OOS stale event fix** — `pre_count` snapshot now taken before each OOS POST; passed as `after_index` to `fetch_event`
- [x] **`eval_accuracy.py` timestamp fix** — `datetime.utcnow()` → Thai time UTC+7

---

### ROS2 Nav State + Student-Gone Timeout ✅ DONE (2026-03-25)

#### ROS2 Nav State Sending
- [x] **`pi5_ros2` config block** — `config/settings.yaml`: `host: "TBD"` (Teammate B fills in) + `port: 8767`
- [x] **`_push_nav_state()` helper** — `api/routes/receiver.py`: async POST `{state: int, destination?: str}` to `{ros2_base_url}/nav_state`; skipped silently when host is `"TBD"`; errors caught and logged at WARNING level (non-blocking)
- [x] **Navigate intent** → `_push_nav_state(2, ros2_url, destination)` fire-and-forget after LLM response
- [x] **Farewell intent** → `_push_nav_state(1, ros2_url)` fire-and-forget + gone timer cancelled
- [x] **`/greeting` registered path** → `_push_nav_state(0, ros2_url)` fire-and-forget (stop roaming when student detected)
- [x] **State mapping**: 0 = stop/idle, 1 = resume roaming, 2 = navigate to destination

#### Student-Gone Inactivity Timeout
- [x] **Split timeout** — `config/settings.yaml`: `student_gone_reprompt_seconds: 15` + `student_gone_roam_seconds: 15` (replaces single `student_gone_timeout_seconds: 30`)
- [x] **`_session_gone_timeout()` coroutine** — `api/routes/receiver.py`:
  - Stage 1: sleep 15s → if session still active → fire-and-forget TTS `"ยังอยู่ที่นี่ไหมคะ"`
  - Stage 2: sleep 15s more → if session still active → TTS `"ไว้เจอกันใหม่นะคะ"` + `_push_nav_state(1)` + `_sessions.pop()`
  - Coroutine cancelled cleanly on `asyncio.CancelledError`
- [x] **`_reset_gone_timer()` / `_cancel_gone_timer()` helpers** — cancel and restart the coroutine on every new voice input; called in `/detection` after each pipeline run
- [x] **Timer started on `/greeting`** — restarted on every `/detection` event; cancelled on farewell intent
- [x] **Research backing**: Pepper robot (5s vision), Alexa (8s per reprompt), commercial kiosks (30–60s); 15+15s split matches HRI best practice — active farewell before silent session drop

#### Farewell Phrase
- [x] **`"ไว้เจอกันใหม่นะคะ"`** — added as explicit example to `farewell` intent description in `_CHATBOT_PROMPT_TEMPLATE` (`mcp/llm_chatbot.py`)
- [x] **Same phrase used in timeout** — `_session_gone_timeout()` sends `"ไว้เจอกันใหม่นะคะ"` as the final TTS before session drop; consistent user experience

#### Pipeline Timing Refactor
- [x] **Sub-timing in `llm_chatbot.ask()`** — `mcp/llm_chatbot.py`: `import time` added; `t_rag`, `t_memory`, `t_llm` measured around each operation; returned as `"timing_ms": {"rag": int, "memory": int, "llm": int}` in response dict
- [x] **`receiver.py` timing cleaned up** — removed `t_grammar` and `t_tts` variables (grammar skipped, TTS fire-and-forget); timing log now: `"⏱  pipeline [%s] rag=%dms  memory=%dms  llm=%dms  total=%.0fms"`
- [x] **Log stages renamed** — `[1/3 grammar]` / `[2/3 llm]` / `[3/3 tts]` → `[1/2 llm]` / `[2/2 tts]`
- [x] **Monitor timing bars updated** — `api/routes/monitor.py`: replaced `grammar` + `tts` bars with `rag` / `memory` / `llm` bars (max scales: rag=2000ms, memory=1000ms, llm=10000ms)

---

## Known Issues

| Issue | Severity | Status |
|-------|----------|--------|
| Grammar corrector still occasionally over-corrects informal Thai | Medium | ✅ Resolved — grammar corrector fully skipped in receiver.py (2026-03-25); 8B model was hallucinating |
| Session state lost on server restart (in-memory only) | Low | ✅ Resolved — `_restore_session()` restores from SQLite on restart (Phase 2.11) |
| `time_table` search scores moderate (~0.28-0.33) — English embedder on Thai text | Resolved | ✅ Upgraded to BAAI/bge-m3; scores now ~0.60 |
| TTS venv dependency pins (numba/numpy/protobuf conflicts) | Resolved | numba upgraded to 0.58.1; numpy pinned to 1.24.4; protobuf 5.29.6 |
| `walle-tts` container (VachanaTTS) not running | Low | `sudo docker-compose --profile interactive up walle-tts` |
| STT confidence field removed | Resolved (2026-03-22) | Typhoon ASR has no real per-utterance beam score; `stt.confidence` removed from payload schema |
| PI 5 60-second INACTIVE timeout | Resolved (Phase 2.7) | Race condition in `_send_event()` — `_set_inactive()` now called BEFORE `run_in_executor` |
| Duplicate log lines in server.log | Resolved (2026-03-23) | `force=True` added to `logging.basicConfig()` in `api/main.py` |

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

### STT Research — Typhoon ASR Realtime (evaluated 2026-03-25)
- Typhoon ASR Realtime: OpenAI Whisper-style model fine-tuned for Thai, 114M parameters
- Benchmarks: 19× faster than Whisper, CER 6.62%, WER 12.52%, supports streaming
- **Not suitable for PI5** — 114M parameters ≈ ~456 MB (fp32) / ~228 MB (fp16) runtime RAM; exceeds PI5 available headroom when combined with face display + ROS2 processes
- Current PI5 STT (Typhoon ASR standard) remains in use

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

| Collection | Entities | Dim | Status |
|-----------|----------|-----|--------|
| `curriculum` | 516 | 1024 | ✅ Re-ingested bge-m3; RAG working |
| `time_table` | 128 | 1024 | ✅ Re-ingested bge-m3; scores ~0.60 |
| `uni_info` | 4 | 1024 | ✅ Re-ingested bge-m3; routing fixed; includes full KMITL map (52 buildings) |
| `conversation_memory` | rebuilds | 1024 | ✅ Recreated fresh at 1024-dim; auto-fills from new conversations |
| `student_face_images` | 0 | — | N/A (PI 5 side) |
| `chat_history` | — | — | Dropped — legacy collection, never queried by this server |

---

## Key Packages (venv)

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.124.4 | HTTP framework |
| httpx | 0.28.1 | Async HTTP client (PI 5 calls) |
| pymilvus | 2.6.10 | Vector DB client |
| pythainlp | 3.1.1 | Thai NLP / syllabification |
| pythaitts | 0.4.2 | Thai TTS wrapper (satu) |
| torch | 2.4.1 | GPU tensors |
| vachanatts | 0.0.7 | VachanaTTS (installed as pythaitts dep) |

---

## Team Responsibilities

| Component | Owner |
|-----------|-------|
| Server (this repo) | You |
| TTS playback on PI 5 (`/tts_render`) | Teammate A |
| ROS2 / Navigation on PI 5 (`/navigation`) | Teammate B |
| Satu TTS model integration | Teammate A |

---

## Environment

| Item | Detail |
|------|--------|
| Server IP | `10.100.16.22` |
| PI 5 IP | `10.26.9.196` |
| LLM | `llama3.1-typhoon2-70b-instruct` (Q5_K_M GGUF) via Ollama at `localhost:11434` |
| Embedding | `BAAI/bge-m3` (dim 1024, multilingual, Thai-aware) |
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
| Phase A TTS | Typhoon2-Audio sidecar TTS replacing satu — WAV at 16kHz, confirmed end-to-end | ✅ Done |
| Phase B STT | Typhoon2-Audio sidecar STT — `/audio_detection` endpoint on main server | ✅ Done |
| 2.8.1 | Greeting personalisation — year-tone + memory recall + thai_name/student_id payload | ✅ Done |
| 2.8.2–5 | Prompt fine-tuning — grammar, chatbot, greeting, intent router | ✅ Done |
| Dual-LLM | Typhoon2-8B for grammar + memory; 70B for chatbot + greeting | ✅ Done |
| 2.9 | Database verification — SQLite / MySQL / Milvus all confirmed healthy; enrollment_year bug fixed | ✅ Done |
| 2.9.1 | Bug fixes — prefix, grammar hallucination guard, TTS fire-and-forget, monitor cleanup | ✅ Done |
| Embed upgrade | BAAI/bge-m3 (1024-dim) — all 4 collections re-ingested, scores ~0.30→0.60 | ✅ Done |
| 2.10 | uni_info routing fix, robot location in prompt, pipeline test 39/39, stage logging | ✅ Done |
| 2.11 | Greeting naturalness, fallback phrases, student year fix, routing fixes | ✅ Done |
| 3 | Session persistence across restarts (Redis) | ⬜ Not started |
| 4 | Performance benchmarking & accuracy evaluation | 🔄 In progress |

---

## Phase 2.11 — Prompt Tuning v2 & Routing Fixes

### Changes

**Greeting naturalness**
- Banned robotic question "มีความสุขไหมคะ" explicitly in prompt
- Added open-ended examples: "เป็นยังไงบ้างคะ", "ช่วงนี้เป็นไงบ้างคะ", "วันนี้เหนื่อยไหมคะ"
- Reduced `max_tokens` 128 → 64 for both `greet()` and `greet_stranger()`
- Fallback text updated to natural Thai phrases (no more literal template strings)

**Fallback phrases**
- HTTP error fallback: `"รบกวนพูดใหม่อีกทีได้มั้ยคะ"` (both `generate()` and `chat()`)
- Chatbot JSON parse fallback: same phrase
- SYSTEM_PROMPT rule 4: `"น้องสาธุไม่มีข้อมูลเรื่องนั้นค่ะ"` on unknown — no guessing

**Student year calculation fix**
- Removed erroneous `+1` in both `/greeting` and `/detection` handlers
- Formula: `student_year = min(max(utcnow().year - enroll_ce, 1), 4)` where `enroll_ce = prefix + 1957`
- ID-prefix fallback added when MySQL has no matching student row
- Result: 65→4th ✅, 66→3rd ✅, 67→2nd ✅, 68→1st ✅

**Routing bug fixes**
- `time_table` keywords: added all 7 Thai day names (`วันจันทร์`…`วันอาทิตย์`) — "วันจันทร์มีวิชาอะไร" now routes correctly
- `mysql_students` keywords: removed `"นศ"` which was a substring of `วันศุกร์` (Thai string "น"+"ศ" collision) — replaced by keeping only `"นักศึกษา"`

**Routing verified (4/4 routes passing):**
- chat_history → สวัสดีค่ะ ✅
- curriculum → หลักสูตร RAI ✅
- time_table → วันจันทร์มีวิชาอะไร ✅ (was broken, now fixed)
- uni_info → ห้องน้ำอยู่ที่ไหน ✅

---

## Phase 4 — Performance Benchmarking & Accuracy Evaluation

### 4.1 — Evaluation Tools Built

Two tools written and run against the live server:

**`tools/eval_accuracy.py`** — measures:
- Intent Accuracy + Macro F1 (per class breakdown)
- Slot F1 — navigation destination extraction
- Language Compliance (L1–L8 rules: Thai only, female particle ค่ะ, correct persona, no hallucination, etc.)
- OOS (Out-of-Scope) Rejection Rate
- TSR (Task Success Rate) — composite of intent + language + slot

**`tools/benchmark.py`** — measures per-stage TTFR latency:
- Stages: grammar_ms, llm_ms, tts_ms, total_ms
- 16 fixtures balanced across all 4 RAG routes
- Reports mean / p50 / p95 / p99
- Saves to `docs/benchmark_report.txt`

### 4.2 — First Eval Run Results (post routing fixes)

| Metric | Result | Target | Status |
|--------|--------|--------|--------|
| Intent Accuracy | 91.3% | ≥ 90% | ✅ |
| Macro F1 | 0.92 | ≥ 0.85 | ✅ |
| Slot F1 (nav destination) | 0.75 | ≥ 0.80 | ⚠️ (by design — destination mapped to A/B/C by nav system) |
| Language Compliance | 22/23 ≈ 96% | ≥ 95% | ✅ (ฉัน pronoun accepted) |
| OOS Rejection Rate | 100% | ≥ 95% | ✅ |
| TSR | 0.85 | ≥ 0.80 | ✅ |

### 4.3 — Latency Benchmark Results

Hardware: 4× A100 GPU, 70B model via Ollama

| Stage | p50 | p95 | Target p50 | Status |
|-------|-----|-----|-----------|--------|
| grammar | ~150ms | ~300ms | ≤ 200ms | ✅ |
| llm | ~5500ms | ~18000ms | ≤ 3000ms | ❌ hardware-bound |
| tts | ~400ms | ~700ms | ≤ 800ms | ✅ |
| total | ~6000ms | ~19000ms | ≤ 3000ms | ❌ hardware-bound |

**Note:** First-call latency ~19s is Ollama cold-start (KV cache cold). Warm p50 ~6s.
The 3s target is aspirational — 70B model on shared A100s is the bottleneck.
Smaller model (8B) would hit target but sacrifices Thai quality.

### 4.4 — Known Metrics Planned (not yet collected)

| Metric | Tool needed | Status |
|--------|-------------|--------|
| CER (Thai STT) | Real audio recordings from lobby | ⬜ Pending hardware |
| RAGAS Faithfulness | Ground-truth QA pairs | ⬜ Pending manual annotation |
| RAGAS Context Recall | Same as above | ⬜ Pending manual annotation |
| MOS (TTS quality) | Listening test with raters | ⬜ Pending |

### 4.5 — Next Steps

- [ ] Collect real lobby audio → compute CER against Typhoon2-Audio STT
- [ ] Write 20 ground-truth QA pairs per collection → run RAGAS
- [ ] Write `docs/technical_report.md` for capstone submission
- [ ] Phase 5: ROS2 Navigation integration (blocked on Teammate B's `/navigation` endpoint)
- [ ] Phase 6: Robot Emotion Frontend (blocked on hardware/display decision)
