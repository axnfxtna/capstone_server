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

### 2.3 Wire up Satu TTS on server ✅ DONE
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

### Sidecar Bugfix ✅ DONE (2026-03-25)
- [x] **Event-loop blocking** — `audio_service/main.py`: `asyncio.to_thread()` wraps both `synthesize()` and `transcribe()`; GPU calls no longer freeze the uvicorn event loop; concurrent requests and health checks remain responsive during model inference
- [x] **STT client timeout** — `tts/typhoon_audio_tts.py`: `transcribe()` timeout `30s → 90s`; large audio blobs (271KB, 492KB) were timing out exactly at 30s
- [x] **TTS client timeout** — `tts/typhoon_audio_tts.py`: `synthesize_and_send()` timeout `15s → 60s`; 8B model can exceed 15s for longer reply texts

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

## Embedding Model Upgrade — BAAI/bge-m3 ✅ DONE (2026-03-23)

- [x] All configs updated (`settings.yaml`, `configs.yaml`, `milvus_client.py`)
- [x] All 4 collections dropped and re-ingested at 1024-dim
  - `curriculum` 516 chunks, `time_table` 128 rows (scores 0.30→0.60), `uni_info` 4 chunks, `conversation_memory` fresh
  - `chat_history` dropped permanently — legacy, never queried
- [x] `kmitl_map_info_thai.txt` created — 52 KMITL buildings (Zone A–D) extracted from campus map images and ingested into `uni_info`
- [x] `tools/reingest_curriculum.py`, `tools/reingest_uni_info.py`, `tools/drop_old_collections.py` written

## Phase 2.10 — Pipeline Fixes ✅ DONE (2026-03-23)

- [x] `uni_info` RAG routing fixed — keyword block added to `_ROUTE_KEYWORDS` in `llm_chatbot.py`
- [x] Robot self-location injected into system prompt — 12th floor of E-12, Zone D
- [x] `pipeline_test.py` rewritten — 39/39 checks passing, covers all RAG routes + intent types + language rules
- [x] Stage-by-stage `▶`/`✔` logging added to pipeline in `receiver.py`
- [x] Duplicate log line bug fixed — `force=True` in `logging.basicConfig()`

---

## Phase 2.11 — Unknown Person + Session Fixes ✅ DONE (2026-03-23)

### Unknown Person Interaction
- [x] `/greeting` unknown path — calls `greet_stranger()` instead of returning skipped
- [x] `greet_stranger()` added to `GreetingBot` — visitor prompt with robot limitations, 1-sentence cap, `หนู` pronoun enforced
- [x] `/detection` unknown path — full pipeline via `chatbot.ask()` (no memory store), session keyed by `track_id`
- [x] Guest sessions tracked in-memory with 5-min timeout (`guest_session_timeout_seconds: 300`)
- [x] Guest turns logged to SQLite for audit; Milvus memory store skipped
- [x] Stranger greeting cooldown using `__stranger__` key in `_last_greeting`

### Session Persistence (Restart Recovery)
- [x] `get_latest_session(student_id)` added to `sqlite_client.py`
- [x] `_restore_session()` async helper — checks memory first, falls back to SQLite on restart
- [x] `upsert_session()` wired into `/greeting` and `/detection` for registered persons
- [x] `get_turns(session_id)` used to restore history after server restart
- [x] Registered session timeout raised to 30 min (`session_timeout_seconds: 1800`)
- [x] Per-role cleanup: guest sessions expire at 5 min, registered at 30 min

### Bug Fixes
- [x] Navigate intent — TTS now uses LLM `reply_text` directly; destination no longer injected into speech
- [x] `llm_chatbot.py` navigate intent description updated — LLM told not to repeat destination in `reply_text`
- [x] `enforce_female_particle()` extended — replaces `ข้าพเจ้า` → `หนู`, `ดิฉัน` → `หนู`, `ผม` → `หนู`
- [x] Grammar corrector — strips `"Output:"` prefix if LLM mimics few-shot format
- [x] STT noise gate — drops inputs < 3 chars; shows `noise` tag in monitor
- [x] Monitor — STT raw always shown; displays `(empty)` in grey when blank
- [x] `asyncio` import added to `receiver.py`

### TTS English/Number Expansion
- [x] `expand_for_tts()` added to `tts_router.py` — translates English letters, acronyms, and numbers to Thai words before TTS
- [x] E-12 → อี สิบสอง | KMITL → เค เอ็ม ไอ ที แอล | Zone D → โซน ดี | 3 → สาม
- [x] 4-digit numbers read digit-by-digit (room codes): 1201 → หนึ่ง สอง ศูนย์ หนึ่ง
- [x] `expand_for_tts()` wired into `intent_router.py` and `greeting_bot.py` for all TTS engines
- [x] Fixed `num_to_thaiword` function name (pythainlp 3.1.1)

### Greeting Quality + Fallback Phrases ✅ DONE (2026-03-23)
- [x] **Greeting length cap** — `max_tokens` 128 → 64 for `greet()` and `greet_stranger()`; constraint updated with concrete short example
- [x] **Natural questions** — banned `"มีความสุขไหม"` (robotic); replaced with open-ended examples: `"เป็นยังไงบ้างคะ"`, `"ช่วงนี้เป็นไงบ้างคะ"`, `"วันนี้เหนื่อยไหมคะ"`
- [x] **Stranger greeting shortened** — example trimmed to single clause; capability dump banned in prompt
- [x] **Don't-know fallback** — `"ขอโทษค่ะ ไม่ทราบค่ะ"` → `"น้องสาธุไม่มีข้อมูลเรื่องนั้นค่ะ"`
- [x] **Don't-understand fallback** — `"ขออภัยค่ะ ไม่เข้าใจคำถาม..."` → `"รบกวนพูดใหม่อีกทีได้มั้ยคะ"`
- [x] **Student year off-by-one fixed** — formula `current_year - enrollment_year + 1` → `current_year - enrollment_year`; 65→4th, 66→3rd, 67→2nd, 68→1st all correct
- [x] **ID-prefix year fallback** — derives year from student_id first 2 digits (Thai BE short form) when MySQL has no row

---

## Phase 5 — ROS2 Navigation Integration 🔧 SERVER DONE — Waiting on Teammate B

Server-side nav state sending is complete. The robot can be commanded to stop, navigate, or resume roaming from within the pipeline. The remaining work is entirely on Teammate B's PI5 ROS2 service.

### 5.0 Server-side nav state ✅ DONE (2026-03-25)
- [x] `pi5_ros2` config block in `settings.yaml` — `host: "TBD"`, `port: 8767`
- [x] `_push_nav_state(state, ros2_url, destination?)` helper — POST `{state: int}` to `/nav_state`; skipped silently when host is `"TBD"`; fire-and-forget, never blocks pipeline
- [x] State mapping: 0 = stop/idle, 1 = resume roaming, 2 = navigate to destination
- [x] `/greeting` registered → sends state 0 (stop roaming when student detected)
- [x] navigate intent → sends state 2 + destination
- [x] farewell intent → sends state 1 (resume roaming)
- [x] Student-gone timeout (stage 2) → sends state 1 (resume roaming after silence)

### 5.1 PI 5 `/nav_state` endpoint (Teammate B — fill in `pi5_ros2.host` in `settings.yaml`)
- [ ] Implement `POST /nav_state { "state": int, "destination"?: str }` on PI5 port 8767
- [ ] state 0 → halt autonomous roaming
- [ ] state 1 → resume autonomous roaming
- [ ] state 2 → navigate to `destination` (A / B / C room name → ROS2 nav goal)

### 5.2 ROS2 Nav2 goal publishing (Teammate B)
- [ ] Destination name → map coordinate lookup table (A/B/C → `(x, y, θ)`)
- [ ] Publish `geometry_msgs/PoseStamped` or call Nav2 `NavigateToPose` action
- [ ] Handle goal rejection / unreachable destination

### 5.3 End-to-end navigation test
- [ ] Ask robot `"พาไปห้อง A"` → server routes → PI5 `/nav_state` state=2 → robot moves
- [ ] Farewell → state=1 → robot resumes patrolling
- [ ] Student-gone timeout → state=1 → robot resumes patrolling

---

## Phase 6 — Robot Emotion / Expression Frontend ✅ DONE (2026-03-24)

Baymax face UI running on PI5 (port 7000). Server provides `face/face_client.py` helper.
All emotion transitions are owned by the PI5 — see `docs/face_integration_notes.md` for the full PI5 implementation guide.

### 6.1 Emotion state model ✅
- [x] 5 states defined: `idle(0)` / `scanning(1)` / `happy(2)` / `talking(3)` / `thinking(4)`
- [x] Mapping:
  - Greeting about to fire → `happy (2)`
  - STT ready, POSTing to server → `thinking (4)`
  - Audio playback starts → `talking (3)`
  - Audio playback ends → `idle (0)`
  - Navigate TTS ends, robot moving → `scanning (1)`

### 6.2 Face API ✅
- [x] PI5 face service: `POST http://localhost:7000/face_emotion {"emotion": <int>}`
- [x] Health check: `GET http://localhost:7000/health`
- [x] All 5 codes tested end-to-end from server (live test: 8/8 pass)

### 6.3 Server-side helpers ✅
- [x] `face/face_client.py` — async helper for future server-side use (e.g. nav callback)
- [x] `tools/watch_face.py` — real-time face state monitor for debugging
- [x] `tools/test_face_emotions.py` — unit + live connectivity tests (17/17 pass)

### 6.4 Remaining (PI5 side)
- [x] Trigger `happy (2)` before POSTing `/greeting`
- [x] Trigger `thinking (4)` before POSTing `/detection`
- [x] Trigger `talking (3)` when audio playback starts in audio player
- [x] Trigger `idle (0)` when audio playback ends
- [ ] Trigger `scanning (1)` when navigate TTS ends and robot starts moving (depends on Teammate B ROS2)
- [ ] Trigger `idle (0)` on ROS2 Nav2 goal completion (depends on Teammate B)

---

## Prompt Tuning v2 ✅ DONE (2026-03-23)

### Chatbot system prompt (`llm/typhoon_client.py`)
- [x] Day/time awareness injected every turn — `วันจันทร์ เวลา 16:13 น.` (UTC+7 corrected)
- [x] `ตึกโหล` → `ตึกสิบสอง` throughout all prompts (easier TTS pronunciation)
- [x] Over-helping fix — unknown answer now replies `"ขอโทษค่ะ ไม่ทราบค่ะ"` with no extra suggestions
- [x] `enforce_female_particle()` extended — `ข้าพเจ้า` → `หนู`, `ดิฉัน` → `หนู`, `ผม` → `หนู`

### Greeting bot (`mcp/greeting_bot.py`)
- [x] ปี 4 tone changed — `คุยแบบเป็นกันเองและให้กำลังใจที่ใกล้เรียนจบ` (removed formal ว่าที่บัณฑิต)
- [x] Greeting length capped — `ห้ามเกิน 1 ประโยคโดยเด็ดขาด` + example anchored in prompt
- [x] `ห้ามพูดถึงความสามารถของตัวเองในการทักทาย` — stops robot from padding with capability description
- [x] UTC+7 fix applied to `_get_time_of_day()` (was reading UTC, now reads Thai time)
- [x] Stranger greeting prompt tightened — `หนู` pronoun enforced, 1-sentence cap, robot limitations included
- [x] Greeting seeded into session history — `sess["history"].append(("", greeting_text))` so chatbot has context for first reply
- [x] `llm_chatbot.py` history formatter — skips empty user turn so greeting renders as bot-only opening line

### Grammar corrector (`mcp/grammar_corrector.py`)
- [x] Strips `"Output:"` prefix if LLM mimics few-shot format

### TTS (`mcp/tts_router.py`)
- [x] `expand_for_tts()` — English letters/acronyms/numbers → Thai words before TTS
- [x] 4-digit numbers read digit-by-digit (room codes): `1201` → `หนึ่ง สอง ศูนย์ หนึ่ง`
- [x] Fixed `num_to_thaiword` function name (pythainlp 3.1.1)
- [x] Wired into `intent_router.py` and `greeting_bot.py` for all TTS engines

### Navigate intent fix (`mcp/intent_router.py`)
- [x] TTS now uses LLM `reply_text` directly — destination no longer spoken by intent_router
- [x] `llm_chatbot.py` navigate description updated — LLM told not to repeat destination in reply

### Pipeline gates (`api/routes/receiver.py`)
- [x] STT noise gate — drops inputs < 3 chars
- [x] Monitor shows `(empty)` in grey when STT raw is blank
- [x] `noise` status tag added to monitor

---

## Robot Rename — สาธุ / Satu ✅ DONE (2026-03-24)

- [x] Thai name: สาธุ — self-reference: น้องสาธุ / น้อง; English: Satu
- [x] All prompts, configs, docs, tools updated
- [x] `SYSTEM_PROMPT` constant removed — `build_chatbot_system_prompt()` is sole identity prompt
- [x] `enforce_female_particle()` updated: ผม/ดิฉัน/ข้าพเจ้า → น้อง

---

## Prompt Tuning v2 ✅ DONE (2026-03-25)

Second pass on all LLM prompts, informed by real conversation logs.

**Critical**
- [x] **Grammar corrector answers questions** — fully skipped in `receiver.py`; 8B model hallucinations (answers questions, translates English → Thai) made it harmful; `corrected = payload.stt.text` set directly
- [x] **Grammar corrector translates English → Thai** — resolved by full skip above; code-level bypass also added to `grammar_corrector.py` for non-Thai inputs
- [x] **`ฉัน` pronoun slipping through** — `enforce_female_particle()` in `llm/typhoon_client.py`: `ฉัน` → `น้อง` added
- [x] **`_YEAR_TONE` NameError crash** — `greeting_bot.py`: dangling `year_tone = _YEAR_TONE.get(...)` removed; every registered greeting was returning 500
- [x] **`_current_datetime_str()` invalid format syntax** — `greeting_bot.py`: placeholder fixed; `current_datetime` passed as `.format()` kwarg

**Medium**
- [x] **Navigate false positives on garbled STT** — `_CHATBOT_PROMPT_TEMPLATE` navigate description updated: only emit `navigate` when destination is explicitly stated (A/B/C); falls back to `info` otherwise
- [x] **Timetable RAG routing miss** — grammar corrector was rewriting `วันไหน` before routing; fixed by passing raw `payload.stt.text` as `routing_hint` to `llm_chatbot.ask()`
- [x] **Greeting endpoint blocking** — `greeting_bot.py`: `asyncio.create_task()` for TTS + navigation; HTTP response no longer waits for PI5 (was blocking up to 10s)
- [x] **Repetitive replies** — deemed acceptable; no change made

**TTS**
- [x] **สาธุ mispronounced** — `tts_router.py` `_THAI_SUBSTITUTION`: `น้องสาธุ` → `น้อง สา ทุ`, `สาธุ` → `สา ทุ`
- [x] **All datetime → Thai time UTC+7** — 5 files updated: `sqlite_client.py`, `memory_manager.py`, `monitor.py`, `benchmark.py`, `eval_accuracy.py`

**Minor**
- [ ] **Out-of-scope math phrasing** — `"น้องสาธุไม่มีข้อมูลเรื่องนั้นค่ะ"` sounds like a database miss for math. Low priority.
- [ ] **ห้องน้ำ data gap** — robot has no bathroom location data; replies "ไม่มีข้อมูล" for a basic student question. Add to `uni_info` dataset.

### eval_accuracy.py result (2026-03-25) — all targets met ✅
| Metric | Result | Target |
|--------|--------|--------|
| Intent Accuracy | 90.9% | ≥ 90% ✅ |
| Slot F1 (destination) | 1.00 | ≥ 0.85 ✅ |
| Language Compliance | 100% | 100% ✅ |
| OOS Rejection Rate | 100% | ≥ 80% ✅ |
| TSR | 0.91 | ≥ 0.80 ✅ |

### pipeline_test.py result: 35/36 pass (1 check updated for new behavior; all core logic passes)

---

## ROS2 Nav State + Student-Gone Timeout ✅ DONE (2026-03-25)

### Nav state sending
- [x] `_push_nav_state(state, ros2_url, destination?)` — fire-and-forget; skipped when `pi5_ros2.host = "TBD"`
- [x] Integrated into `/greeting` (state 0), navigate intent (state 2), farewell intent (state 1), gone timeout stage 2 (state 1)
- [x] `pi5_ros2` config block in `settings.yaml` — Teammate B fills in `host`

### Student-gone inactivity timeout
- [x] Split 15 + 15s: reprompt after 15s silence → roam after 15s more
- [x] `_session_gone_timeout()` coroutine: stage 1 TTS `"ยังอยู่ที่นี่ไหมคะ"`, stage 2 TTS `"ไว้เจอกันใหม่นะคะ"` + nav state 1 + session drop
- [x] `_reset_gone_timer()` / `_cancel_gone_timer()` called on every detection + farewell
- [x] Commercial robot research: Pepper 5s vision / Alexa 8s / kiosk 30–60s — 15+15s within HRI norms

### Farewell phrase consistency
- [x] `"ไว้เจอกันใหม่นะคะ"` added to farewell intent description in `_CHATBOT_PROMPT_TEMPLATE` — LLM now uses this phrase
- [x] Same phrase used in gone-timeout stage 2 — consistent experience whether student says farewell or walks away

### OOS eval fixes
- [x] `destination` string `"null"` normalized to `None` in `llm_chatbot.ask()` — was root cause of 2 eval failures
- [x] `info` intent scope tightened; `chat` intent explicitly covers OOS with polite decline
- [x] Navigate fixtures updated to valid A/B/C rooms; `fetch_event` slice direction fixed

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

## Phase 4 — Real-World Test Suite & Technical Report

### 4.1 Timing instrumentation ✅ DONE (2026-03-25, refactored)
- [x] `time.perf_counter()` wraps each stage in `receiver.py` `/detection` handler
- [x] Per-stage ms logged and visible in server console + `/events` monitor
- [x] Stage progress logs: `▶`/`✔` lines show each stage starting and finishing in real time
- [x] **Timing refactored (2026-03-25)** — grammar stage removed (skipped entirely); TTS removed (fire-and-forget); replaced with sub-timing inside `llm_chatbot.ask()`:
  - `rag` ms — Milvus vector search
  - `memory` ms — conversation memory retrieval
  - `llm` ms — LLM inference
  - `total` ms — full pipeline wall time
- [x] Monitor bars updated: `rag` / `memory` / `llm` colour-coded bars (replaces old grammar/tts)

---

### 4.2 Greeting Scenarios
Test that the robot greets correctly for every person type and situation.

| # | Scenario | Expected behaviour |
|---|----------|--------------------|
| G1 | Registered student — first ever visit (no memory) | Short warm greeting by name, open-ended question |
| G2 | Registered student — returning (has Milvus memory) | Greeting references a past topic naturally, 1 sentence |
| G3 | Unknown visitor / stranger | "สวัสดีตอน[เวลา]ค่ะ หนูชื่อน้องสาธุ มีอะไรให้ช่วยไหมค่ะ" — no name, no capability dump |
| G4 | Same registered student within cooldown window | `cooldown` status returned, no duplicate greeting |
| G5 | Same stranger within cooldown window | `cooldown` status returned |
| G6 | ปี 1 student | Welcoming, friendly elder-sister tone |
| G7 | ปี 2 student | Encouraging, asks about challenges in harder subjects |
| G8 | ปี 3 student | Caring tone, asks about internship readiness |
| G9 | ปี 4 student | Casual, encouraging tone about graduating soon |
| G10 | PI 5 sends `student_id` directly (bypasses nickname lookup) | Same quality greeting without MySQL nickname fallback |

---

### 4.3 University Information Queries (uni_info RAG)
Student asks about places, buildings, or facilities on campus.

| # | Input | Expected behaviour |
|---|-------|--------------------|
| U1 | "ห้องน้ำอยู่ที่ไหนคะ" | Answers with floor/location info from uni_info |
| U2 | "โรงอาหารอยู่ตึกไหน" | Returns correct building name |
| U3 | "ตึก E-12 อยู่โซนไหน" | "โซน D" from uni_info |
| U4 | "ห้องสมุดอยู่ที่ไหน" | Correct location, concise ≤ 2 sentences |
| U5 | "มีสระว่ายน้ำไหม" | Answers from campus facilities data |
| U6 | "HM building คืออะไร" | Returns faculty/building description |
| U7 | "ECC อยู่ที่ไหน" | Returns correct zone/location |
| U8 | "ที่จอดรถอยู่ตรงไหน" | Returns parking info if in dataset, otherwise `"น้องสาธุไม่มีข้อมูลเรื่องนั้นค่ะ"` |

---

### 4.4 Curriculum Queries (curriculum RAG)
Student asks about courses, credits, or programme structure.

| # | Input | Expected behaviour |
|---|-------|--------------------|
| C1 | "หลักสูตร RAI มีกี่หน่วยกิต" | Returns total credits from curriculum data |
| C2 | "วิชา AI มีอะไรบ้าง" | Lists AI-related subjects |
| C3 | "วิชา Programming เรียนปีไหน" | Returns correct year from curriculum |
| C4 | "หน่วยกิตวิชาเลือกคือกี่หน่วย" | Returns elective credit info |
| C5 | "คณะนี้มีวิชา robotics ไหม" | Confirms and describes relevant courses |
| C6 | "เรียนกี่ปี" | Returns programme duration |
| C7 | "วิชาบังคับมีกี่วิชา" | Returns count from curriculum data |

---

### 4.5 Timetable Queries (time_table RAG + MySQL)
Student asks about their class schedule or exam timetable.

| # | Input | Expected behaviour |
|---|-------|--------------------|
| T1 | "วันจันทร์มีวิชาอะไรบ้าง" | Returns Monday schedule correctly |
| T2 | "วิชา Programming เรียนวันไหน กี่โมง" | Returns day + time from timetable |
| T3 | "ตารางสอบมีไหม" | Returns exam schedule if in dataset |
| T4 | "คาบบ่ายวันศุกร์มีอะไร" | Returns afternoon Friday classes |
| T5 | "เรียนกี่โมงถึงกี่โมง" | Returns time range for a given day |
| T6 | "วันนี้[วันจริง]มีเรียนไหม" | Day-awareness test — uses injected current day correctly |

---

### 4.6 Navigation Requests (navigate intent)
Student asks the robot to take them somewhere in E-12 building.

| # | Input | Expected behaviour |
|---|-------|--------------------|
| N1 | "พาฉันไปห้องสมุดหน่อย" | intent=navigate, short invitation reply ("ตามหนูมาเลยค่ะ"), PI 5 `/navigation` called |
| N2 | "อยากไปห้อง 1201" | Room code spoken digit-by-digit in TTS, navigation command sent |
| N3 | "ไปห้องน้ำได้ไหม" | Same navigate flow |
| N4 | "พาไปที่ชั้น 12 ได้มั้ย" | Navigate to floor |
| N5 | "ไปที่ไหนก็ได้" | Robot asks for clarification or picks a sensible default |
| N6 | Navigation to place outside E-12 (e.g. "พาไปโรงพยาบาล") | Politely declines, explains it can only navigate inside E-12 |
| N7 | Farewell after navigation ("ขอบคุณ ไปเองได้แล้ว") | intent=farewell, `resume_roaming` command sent to PI 5 |

---

### 4.7 Casual Conversation (chat intent)
Student chats casually — no RAG needed, robot uses memory + persona.

| # | Input | Expected behaviour |
|---|-------|--------------------|
| CH1 | "สวัสดีค่ะ" | Friendly reciprocal greeting, no RAG call |
| CH2 | "วันนี้เหนื่อยมาก" | Empathetic short reply, no over-advising |
| CH3 | "น้องสาธุคือใคร" | Short self-introduction (name + location + 2 capabilities only) |
| CH4 | "ทำอะไรได้บ้าง" | Lists only 2 capabilities: Q&A and E-12 navigation |
| CH5 | "ขอบคุณนะ" | Warm short reply using ค่ะ |
| CH6 | "ครั้งที่แล้วเราคุยอะไรกัน" | Retrieves Milvus memory and references it naturally |
| CH7 | "เป็นยังไงบ้าง" | Short casual reply in persona |
| CH8 | Multi-turn follow-up "แล้ว...ล่ะ" | Uses session history context, doesn't lose track of topic |

---

### 4.8 Out-of-Scope Requests
Robot must decline gracefully without being rude.

| # | Input | Expected behaviour |
|---|-------|--------------------|
| OS1 | "ช่วยทำการบ้านให้หน่อย" | Polite decline, states scope |
| OS2 | "ขอ wifi password หน่อย" | Polite decline |
| OS3 | "ช่วยโทรหาอาจารย์ได้ไหม" | Polite decline |
| OS4 | "แปลภาษาอังกฤษให้หน่อย" | Polite decline |
| OS5 | "รู้จัก ChatGPT ไหม" | Short casual answer, doesn't go off-topic |
| OS6 | "ไปข้างนอกตึกได้ไหม" | Explains navigation is E-12 only |

---

### 4.9 Language & Persona Compliance
Every response must pass all of these regardless of input.

| # | Rule | Check method |
|---|------|--------------|
| L1 | All replies use `ค่ะ`, never `ครับ` | `assert "ครับ" not in reply` |
| L2 | No CJK characters in reply | regex check |
| L3 | No English sentences (only proper nouns allowed) | regex check |
| L4 | Reply does not start with student's name | `assert not reply.startswith(student_name)` |
| L5 | Reply ≤ 2 sentences | count Thai sentence-end particles |
| L6 | No `ผม`, `ดิฉัน`, `ข้าพเจ้า` in reply | string check |
| L7 | Robot refers to itself as `หนู` or `น้องสาธุ` | string check |
| L8 | Room codes spoken digit-by-digit in TTS text | check `tts_text` field in response |

---

### 4.10 Edge Cases & Failure Modes

| # | Scenario | Expected behaviour |
|---|----------|--------------------|
| E1 | STT returns < 3 chars (noise) | Dropped silently, `status: noise` in monitor |
| E2 | STT returns empty string | Dropped, `(empty)` shown in monitor |
| E3 | LLM returns malformed JSON | `_FALLBACK_RESPONSE` used: `"รบกวนพูดใหม่อีกทีได้มั้ยคะ"` |
| E4 | Milvus unavailable during RAG search | Empty context passed to LLM, no crash |
| E5 | MySQL unavailable during student lookup | Year defaults to ID-prefix derivation, no crash |
| E6 | PI 5 TTS endpoint times out | `asyncio.create_task` swallows error, pipeline still returns response |
| E7 | Student speaks English ("where is the library") | Robot answers in Thai regardless |
| E8 | Very short reply ("โอเค", "ค่ะ") | Treated as casual chat, no RAG, graceful short response |
| E9 | Same question twice in a row | Second answer shows session history context |
| E10 | Student session idle > 30 min | Session expired and cleaned up; next event starts fresh |
| E11 | Guest session idle > 5 min | Guest session expired; new guest session on next event |
| E12 | Server restart mid-conversation | `_restore_session()` rebuilds history from SQLite |

---

### 4.11 Latency Benchmark (`tools/benchmark.py`)

Replay all scenario fixtures above through the live server and measure pipeline timing.

**Metric: TTFR (Time to First Response)**
```
TTFR = time from end of user utterance → start of robot audio output
```
Perceptual thresholds for a physical robot: < 500ms feels instant, < 1.5s acceptable, > 2s feels broken.

**Per-stage latency breakdown** (already instrumented):
```
grammar_ms  |  llm_ms  |  tts_ms  |  total_ms
```

| Stage | Target | Notes |
|-------|--------|-------|
| Grammar correction | < 200ms | 8B model; skipped for < 15 chars |
| RAG + LLM (chat_history route) | < 2s | No Milvus call |
| RAG + LLM (Milvus route) | < 3s | Vector search + 70B model |
| TTS synthesis | < 800ms | RTF 0.19x confirmed |
| **TTFR total (p50)** | **< 3s** | Acceptable for lobby robot |
| **TTFR total (p95)** | **< 5s** | Tail latency bound |

**Benchmark runs:**
- [ ] Run all G/U/C/T/N/CH/OS/E fixtures (sections 4.2–4.10)
- [ ] Report mean / p50 / p95 / p99 per stage and per route
- [ ] Compare registered (MySQL lookup) vs unknown (no DB call) latency
- [ ] Compare Milvus routes vs chat_history (no Milvus) latency

---

### 4.12 Accuracy Evaluation (`tools/eval_accuracy.py`)

#### A. STT — Character Error Rate (CER)
**CER is the correct metric for Thai** (no word boundaries; WER is tokenizer-dependent and misleading).
```
CER = (substitutions + deletions + insertions) / total_reference_characters
```
- [ ] Record 30 real utterances from the robot's microphone in E-12 lobby conditions
- [ ] Manually transcribe each as ground truth
- [ ] Compute CER against Typhoon2-Audio sidecar output
- [ ] **Target: CER < 20%** in ambient lobby noise (< 15% in quiet)
- [ ] Also report WER (using PyThaiNLP `deepcut` tokenizer) for cross-model comparison

#### B. Intent Classification — Accuracy + F1 per class
```
Intent Accuracy = correct_predictions / total_utterances
F1 per class = 2 × (Precision × Recall) / (Precision + Recall)
```
- [ ] Label 50 test utterances with ground-truth intent (chat / info / navigate / farewell)
- [ ] Run through live server, compare `intent` field in response
- [ ] Report accuracy + F1 per class (navigate and farewell are rare → F1 matters more than accuracy)
- [ ] **Target: Intent Accuracy > 90%, F1 per class > 0.85**

#### C. Slot Filling — Destination F1 (navigate intent only)
```
Slot F1 = 2 × (Precision × Recall) / (Precision + Recall)
Precision = correctly extracted destinations / all predicted destinations
Recall    = correctly extracted destinations / all true destinations
```
- [ ] Test 15 navigation requests with known Thai destination ground truth
- [ ] Compare `destination` field in response vs expected
- [ ] **Target: Slot F1 > 0.85**

#### D. RAG Quality — RAGAS Metrics
Standard RAG evaluation framework. Scores in [0, 1], higher is better.

**Faithfulness** — are all claims in the answer supported by retrieved context? (hallucination guard)
```
Faithfulness = claims_in_answer_supported_by_context / total_claims_in_answer
```

**Context Recall** — does the retrieved context contain enough info to answer the question?
```
Context Recall = ground_truth_claims_found_in_context / total_ground_truth_claims
```

- [ ] Build 30 QA pairs with ground-truth answers for uni_info + curriculum + time_table routes
- [ ] Run through RAG pipeline, collect (question, context, answer) triples
- [ ] Score with RAGAS library or LLM-as-judge equivalent
- [ ] **Target: Faithfulness > 0.80, Context Recall > 0.75**

#### E. Task Success Rate (TSR) — Primary End-to-End Metric
**TSR is the most important metric for a service robot.**
```
TSR = tasks_completed_successfully / total_tasks_attempted
```
A "task" = one user interaction episode; success = user's stated goal was fulfilled.

- [ ] Run 30 end-to-end scripted interactions covering all scenario groups (4.2–4.9)
- [ ] Score each: 1 (fully successful), 0.5 (partial), 0 (failed/abandoned)
- [ ] **Target: TSR > 0.80** for pilot deployment (> 0.90 for unsupervised operation)

#### F. Language Compliance — Automated Checks
Run these assertions across all 50+ test responses:

| Check | Rule | Target |
|-------|------|--------|
| L1 | `"ครับ" not in reply` | 100% |
| L2 | No CJK characters (regex) | 100% |
| L3 | No full English sentences (regex) | 100% |
| L4 | Reply does not start with student name | 100% |
| L5 | Reply ≤ 2 Thai sentences | 100% |
| L6 | No `ผม`, `ดิฉัน`, `ข้าพเจ้า` | 100% |
| L7 | TTS text: room codes are digit-by-digit | 100% |
| L8 | Fallback text is `รบกวนพูดใหม่อีกทีได้มั้ยคะ` (not old phrase) | 100% |

---

### 4.13 Technical Report (`docs/technical_report.md`)
- [ ] System architecture overview (pipeline stages, models, data stores)
- [ ] Evaluation methodology — how each metric was measured
- [ ] STT performance table: CER / WER under quiet vs. lobby noise conditions
- [ ] Intent classification table: Accuracy + F1 per class
- [ ] RAG quality table: Faithfulness + Context Recall per collection
- [ ] Latency breakdown table: per-stage mean/p50/p95 per route
- [ ] Task Success Rate: score distribution across scenario groups
- [ ] Language compliance: pass rate per rule
- [ ] Failure analysis: top-3 failure modes observed
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
