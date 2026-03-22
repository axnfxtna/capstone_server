# Server-Side Design Document
## KhanomTan AI Brain — ROS2 Campus Robot (v3, 2026-03-22)

> **Purpose:** Accurate reference document for the current implemented state of the server-side pipeline. Reflects Phase 2.8.1 — all schemas, prompts, and logic match the live codebase.

---

## 1. Project Overview

The server runs on a PC (Ubuntu 22.04, NVIDIA GPU) and acts as the **AI Brain** of a service robot named **ขนมทาน** (KhanomTan). It receives audio/vision events from a Raspberry Pi 5 (PI 5), processes them through a multi-stage MCP pipeline, and routes output back to TTS playback or ROS2 navigation commands.

**Core responsibilities:**
- Receive face-detection and STT events from PI 5 via HTTP POST
- On greeting: generate personalised Thai greeting (year-aware + memory recall) and halt roaming
- Perform Thai grammar correction on raw STT text via LLM
- Run a stateful RAG chatbot conversation (5 routing paths + conversation memory)
- Summarise and persist each turn to SQLite (raw) + Milvus (embedded summary)
- Route LLM output to TTS speech and/or ROS2 navigation
- On farewell: speak goodbye AND resume robot roaming
- On navigation intent: speak confirmation AND send destination to ROS2

**Environment:**

| Item | Value |
|------|-------|
| Server IP | `10.100.16.22` |
| PI 5 IP | `10.26.9.196` |
| PI 5 port | `8766` |
| LLM | `qwen2.5:7b-instruct` via Ollama at `localhost:11434` |
| Embedding | `sentence-transformers/all-MiniLM-L6-v2` (dim 384) |
| Milvus | `localhost:19530` (Docker) |
| MySQL | `localhost:3306` — DB: `capstone` |
| Python | `3.8.10` (venv at `./venv`) |

---

## 2. Data Flow Architecture

```
PI 5 (face camera + microphone)
  │
  ├── POST /greeting   ← first contact with a registered person
  │       GreetingPayload { person_id, thai_name?, student_id?, is_registered, vision_confidence }
  │
  └── POST /detection  ← every event with STT transcription
          DetectionPayload { person_id, thai_name?, student_id?, is_registered, stt: { text, language, duration }, ... }

                    ▼
          ┌─────────────────────────────────┐
          │  FastAPI — api/main.py          │
          │  0.0.0.0:8000                   │
          └─────────────────────────────────┘
                    │
          ┌─────────┴─────────┐
          │                   │
      /greeting          /detection
          │                   │
          ▼                   ▼
    greeting_bot      Grammar Correction (LLM)
    (LLM one-shot)          │
          │            RAG Chatbot (LLM)
          │            ┌─────────────────────────────────┐
          │            │  Route → one of 5 collections:  │
          │            │   chat_history  → Milvus memory │
          │            │   mysql_students → MySQL DB      │
          │            │   time_table    → Milvus RAG     │
          │            │   curriculum    → Milvus RAG     │
          │            │   uni_info      → Milvus RAG     │
          │            └─────────────────────────────────┘
          │                   │
          │            Memory Write (async, fire-and-forget)
          │            ├── SQLite raw log
          │            └── Milvus embedded summary
          │                   │
          │            Intent Router
          │            ├── chat/info  → TTS only
          │            ├── farewell   → TTS + ROS2 resume_roaming (parallel)
          │            └── navigate   → TTS confirmation + ROS2 go_to (parallel)
          │                   │
          └───────────────────┘
                    │
          ┌─────────┴──────────────┐
          │ TTS Output (mode=server)│
          │ khanomtan_engine.py     │
          │ WAV → POST PI5/audio_play│
          └─────────────────────────┘
                    │
                    ▼
          PI 5 plays audio + executes ROS2 commands
```

**Activation flow:**
- PI 5 goes INACTIVE immediately after POSTing `/detection`
- Server POSTs `{ "active": 1 }` to PI 5 `/set_active` after pipeline completes
- On farewell: server marks local state `_active = 0` but still pushes `active=1` so robot is ready for next person

---

## 3. Module Breakdown

---

### 3.1 `server_receiver` — FastAPI Entry Point

**Files:** `api/main.py`, `api/routes/receiver.py`
**Host:** `0.0.0.0:8000`

**Public Endpoints:**

| Method | Path | Input | Output | Description |
|--------|------|-------|--------|-------------|
| POST | `/greeting` | `GreetingPayload` | `{ "status": "ok" }` | First contact with registered person. Rate-limited by `greeting_cooldown_seconds`. |
| POST | `/detection` | `DetectionPayload` | `{ "active": 0\|1 }` | Vision + STT event. Runs full pipeline. |
| GET | `/activate` | — | `{ "active": 0\|1 }` | PI 5 polls activation state (fallback to push). |
| GET | `/health` | — | `{ "status": "ok" }` | Server liveness check. |
| GET | `/monitor` | — | HTML | Live pipeline dashboard (auto-refresh 3s). |
| GET | `/events` | — | JSON | Last 50 pipeline events. |
| POST | `/grammar` | `{ "raw_text": str }` | `{ "corrected_text": str }` | Direct grammar correction (testing). |
| POST | `/thai_tts` | `{ "text": str }` | `{ "phoneme_text": str }` | Direct TTS text prep (testing). |

**Payload Schemas (`api/schemas/receiver.py`):**

```python
class STTResult(BaseModel):
    text: str
    language: str
    duration: float
    # NOTE: confidence removed — Typhoon ASR does not expose a real per-utterance score

class DetectionPayload(BaseModel):
    timestamp: str
    person_id: str              # face recognition label, e.g. "Palm (Krittin Sakharin)"
    thai_name: Optional[str]    # Thai display name from PI 5 — used in LLM prompts
    student_id: Optional[str]   # DB student_id from PI 5 — used for MySQL/Milvus lookup
    is_registered: bool
    track_id: Optional[int]
    bbox: Optional[List[float]]
    stt: STTResult

class GreetingPayload(BaseModel):
    timestamp: str
    person_id: str
    thai_name: Optional[str]
    student_id: Optional[str]
    is_registered: bool
    vision_confidence: float

class ActivateResponse(BaseModel):
    active: int                 # 0 or 1
```

**Key Logic in `receiver.py`:**

- `_clean_name(person_id)` — extracts nickname from `"Palm (Krittin Sakharin)"` → `"Palm"`
- `display_name` = `thai_name` if provided, else `_clean_name(person_id)`
- Student year + `student_db_id` resolved from MySQL:
  - If `student_id` in payload → `fetch_student_by_id()`
  - Else → `fetch_student_by_nickname()` using cleaned name
- In `/detection`, year + student_db_id cached in `sess` dict on first event per session
- Rate-limit: `/greeting` skips if called within `greeting_cooldown_seconds` for the same `person_id`
- Push activation: `_push_active(1, pi5_url)` POSTs to PI 5 `/set_active` after each detection pipeline

---

### 3.2 `greeting_bot` — Personalised One-Shot Greeting

**File:** `mcp/greeting_bot.py`

**Purpose:** Triggered once per session start via `/greeting`. Generates a personalised Thai greeting that is year-aware and can recall the student's previous conversation from Milvus memory. Fires TTS + ROS2 stop in parallel.

**Year-Tone Map (`_YEAR_TONE`):**

| Year | Thai Tone Instruction |
|------|----------------------|
| 1 | โทนการพูด: ให้กำลังใจ อบอุ่น เป็นกันเอง เหมือนรุ่นพี่คอยดูแลน้องใหม่ |
| 2 | โทนการพูด: สนับสนุนด้านโปรเจกต์และการเรียน สนใจความคืบหน้า |
| 3 | โทนการพูด: เน้นเรื่องฝึกงานและประสบการณ์ทำงาน ให้กำลังใจในช่วงนี้ |
| 4 | โทนการพูด: เน้นโปรเจกต์จบการศึกษา ให้กำลังใจและแสดงความเชื่อมั่น |

**LLM Prompt (`_GREETING_PROMPT`):**
```
คุณคือหุ่นยนต์บริการหญิงชื่อ "ขนมทาน" ของ KMITL ใช้คำลงท้าย "ค่ะ" เสมอ ห้ามใช้ "ครับ"

นักศึกษาที่พบ: คุณ {student_name} (ปีที่ {student_year})
{year_tone}

ประวัติการสนทนาล่าสุด:
{memory_summary}

กฎ:
- ถ้ามีประวัติการสนทนา ให้อ้างถึงเรื่องนั้นสั้น ๆ ในการทักทาย เพื่อแสดงว่าจำได้
- ถ้าไม่มีประวัติ (ไม่มีประวัติการสนทนา) ให้ทักทายตามปกติและแนะนำตัวสั้น ๆ
- ตอบเป็นภาษาไทยเท่านั้น 1 ประโยค

ตอบกลับเป็น JSON เท่านั้น รูปแบบ:
{
  "greeting_text": "..."
}
```

**Parameters:** `temperature=0.7`, `max_tokens=128`

**Flow:**
1. Fetch Milvus memory: `memory_manager.retrieve(query="การสนทนาครั้งล่าสุด", student_id=student_id)` → inject as `memory_summary` (or `"(ไม่มีประวัติการสนทนา)"` on first contact)
2. Look up student year from MySQL
3. Call Typhoon LLM → parse `greeting_text`
4. Apply `enforce_female_particle()` post-processor
5. Convert with `to_tts_ready()`
6. Fire **in parallel** via `asyncio.gather()`:
   - POST text to TTS layer
   - POST `{ "cmd": "stop_roaming" }` to PI 5 `/navigation`

> Note: `ros2_cmd` was removed from the LLM JSON schema — the robot always sends `stop_roaming` on greeting; the LLM does not decide this.

---

### 3.3 `mcp_grammar` — Grammar Correction

**File:** `mcp/grammar_corrector.py`

**Purpose:** Receives raw Typhoon ASR transcription and corrects Thai spelling/transcription errors via LLM. Always runs (no confidence-based skip — ASR does not expose a real per-utterance score).

**System Prompt:**
```
คุณคือผู้ช่วยแก้ไขคำพูดภาษาไทยที่ถอดเสียงมาจากระบบ STT
หน้าที่: แก้ไขการสะกดผิดและคำที่ฟังไม่ชัด ให้เป็นประโยคภาษาไทยที่ถูกต้อง
กฎ:
- ตอบกลับเฉพาะข้อความที่แก้ไขแล้วเท่านั้น ห้ามอธิบายเพิ่มเติม
- ถ้าข้อความถูกต้องอยู่แล้ว ให้ตอบกลับข้อความเดิมโดยไม่เปลี่ยนแปลง
- ห้ามเปลี่ยนความหมายหรือเพิ่มคำที่ไม่มีในต้นฉบับ
- ห้ามเปลี่ยนคำลงท้าย เช่น ครับ ค่ะ นะ ให้คงไว้ตามต้นฉบับ
- ห้ามตัดทอนประโยคให้สั้นลง ให้คงความยาวและความหมายเดิม
```

**Parameters:** `temperature=0.1`, `max_tokens=256` (via `llm.chat()` to prevent prompt leakage)

**Fallbacks:**
- LLM error → return raw text unchanged
- Output < 50% of input length → discard LLM output, return raw text (length guard)

---

### 3.4 `llm_chatbot` — Core RAG Conversation Engine

**File:** `mcp/llm_chatbot.py`

**Purpose:** Central conversation handler. Routes the question to one of 5 data sources, injects memory + RAG context, calls Typhoon LLM, returns structured response.

#### RAG Routing (`_route_query`)

| Route | Trigger Keywords | Data Source |
|-------|-----------------|-------------|
| `chat_history` | ครั้งที่แล้ว, เมื่อกี้, ก่อนหน้า, ถามอะไร, คุยอะไร, ประวัติ, history, previous | Milvus `conversation_memory` (memory summary only, no external RAG) |
| `mysql_students` | นักศึกษา, ชื่อ, อีเมล, นศ, รหัสนักศึกษา, สมาชิก, ใครบ้าง, คนไหน, รุ่น, student, email | MySQL `Students` + `Academic_Year` tables |
| `time_table` | ตารางเรียน, ตารางสอบ, ตาราง, เวลาเรียน, คาบเรียน, วันเรียน, วันไหน, เวลาไหน, กี่โมง, exam, schedule, class, สอบ, timetable | Milvus `time_table` |
| `curriculum` | วิชา, หลักสูตร, หน่วยกิต, รายวิชา, คอร์ส, เนื้อหา, เรียน, course, credit, subject, curriculum | Milvus `curriculum` |
| `uni_info` | (default / fallback) | Milvus `uni_info` |

> Note: `time_table` routing also includes a `_THAI_TO_ENG` augmentation step that appends English synonyms before embedding (e.g. คอร์ส → course, สอน → teach).

#### Dynamic System Prompt (`build_chatbot_system_prompt`)

Built from `SYSTEM_PROMPT` base (in `llm/typhoon_client.py`) + personalisation:

```
[Base rules: Thai-only, ค่ะ not ครับ, ฉัน not ผม, 1–3 sentences, no CJK, no standalone English sentences]

เรียกนักศึกษาว่า "{student_name}" เท่านั้น
นักศึกษาอยู่ปีที่ {student_year} ({year_label})
โทนการพูด: {year tone description}
```

Year label map: `{1: "น้องปี 1", 2: "น้องปี 2", 3: "น้องปี 3", 4: "พี่ปี 4"}`

#### Prompt Template (`_CHATBOT_PROMPT_TEMPLATE`)

```
{system_prompt}

ข้อมูลที่จำได้เกี่ยวกับนักศึกษาคนนี้:
{memory_summary}

ข้อมูลอ้างอิงจากระบบ:
{rag_context}

บทสนทนาล่าสุด:
{history}

คำถามปัจจุบัน: {question}

ตอบกลับเป็น JSON เท่านั้น รูปแบบ:
{
  "reply_text": "ข้อความตอบกลับภาษาไทย",
  "intent": "chat | info | navigate | farewell",
  "destination": "ชื่อสถานที่ หรือ null",
  "confidence": 0.0
}
```

**Parameters:** `temperature=0.7`, `max_tokens=512`

**Output Schema:**
```json
{
  "reply_text": "ห้องสมุดอยู่ที่อาคาร E ชั้น 2 ค่ะ",
  "intent": "info",
  "destination": null,
  "confidence": 0.9
}
```

> Note: `confidence` field is returned by LLM but not used by any downstream code. Candidate for removal in Phase 2.8.3.

**Fallback Response (on LLM error):**
```json
{
  "reply_text": "ขออภัยค่ะ ไม่เข้าใจคำถาม ช่วยพูดอีกครั้งได้ไหมค่ะ",
  "intent": "chat",
  "destination": null,
  "confidence": 0.3
}
```

**Memory Store (async, non-blocking):**
After every successful `/detection` turn, `ask_and_store()` calls `memory_manager.store()` without awaiting — does not block the response to PI 5.

---

### 3.5 `mcp_summary` — Memory Persistence & Retrieval

**File:** `mcp/memory_manager.py`

**Purpose:** Dual-write per conversation turn — raw to SQLite for debugging, LLM summary + embedding to Milvus for semantic recall.

#### Write Flow (`store()`)

1. `sqlite_client.log_turn()` — write raw `user_text`, `bot_reply`, `intent`, `timestamp` to `conversation_log` table
2. LLM call → generate 1–2 sentence Thai summary

**Summary Prompt:**
```
สรุปการสนทนาต่อไปนี้เป็น 1-2 ประโยคภาษาไทย
เน้นสิ่งที่นักศึกษาต้องการและสิ่งที่หุ่นยนต์ตอบ

นักศึกษา: {user_text}
หุ่นยนต์: {bot_reply}

ตอบกลับเฉพาะบทสรุปเท่านั้น ห้ามอธิบายเพิ่มเติม
```

**Parameters:** `temperature=0.3`, `max_tokens=256` (via `llm.generate()`)

3. Embed `summary_text` with `sentence-transformers/all-MiniLM-L6-v2`
4. Insert into Milvus `conversation_memory`

#### Read Flow (`retrieve()`)

1. Embed the query string
2. `milvus_client.search_memory()` → top-k cosine-similar summaries filtered by `student_id`
3. Return as newline-joined Thai string: `"[2026-03-21] นักศึกษาถามเรื่อง..."`
4. Empty string returned on error or no hits

---

### 3.6 `mcp_intendgate` — Intent Router

**File:** `mcp/intent_router.py`

**Purpose:** Receives chatbot response dict, routes to TTS and/or ROS2 based on `intent` field.

**Routing Table:**

| Intent | TTS | ROS2 |
|--------|-----|------|
| `chat` | Speak `reply_text` | None |
| `info` | Speak `reply_text` | None |
| `farewell` | Speak `reply_text` | POST `{ "cmd": "resume_roaming" }` to PI 5 `/navigation` |
| `navigate` | Speak LLM-generated confirmation | POST `{ "cmd": "go_to", "destination": str }` to PI 5 `/navigation` |

For `farewell` and `navigate`: TTS + ROS2 calls fired **in parallel** via `asyncio.gather()`.

**Navigation Confirmation Prompt (`_NAVIGATE_CONFIRM_PROMPT`):**
```
คุณคือหุ่นยนต์หญิงชื่อขนมทาน ใช้คำลงท้าย "ค่ะ" เสมอ ห้ามใช้ "ครับ"
สร้างประโยคยืนยันสั้น ๆ เป็นภาษาไทย (1 ประโยค) ว่าจะพานักศึกษาไปที่ {destination}
ตัวอย่าง: "ได้เลยค่ะ ตามหนูมาเลยนะค่ะ หนูจะพาไปที่ {destination}"
ตอบกลับเฉพาะประโยคเท่านั้น
```

**TTS dispatch** (`_speak()`): calls either `khanomtan_engine.synthesize_and_send()` (mode=server) or POSTs phoneme text to PI 5 `/tts_render` (mode=pi5).

---

### 3.7 `mcp_thaitts` — Thai TTS Text Preprocessor

**File:** `mcp/tts_router.py`

**Purpose:** Text normalisation only — converts Thai text to syllable-spaced format that KhanomTan TTS v1.0 reads cleanly. Does **not** run the TTS model.

**Function:** `to_tts_ready(text: str) -> str`

**Processing Steps:**
1. `pythainlp.util.normalize()` — unicode normalisation (no character substitution)
2. Expand `ๆ` (mai yamok): regex `([\u0e00-\u0e45\u0e47-\u0e7f]+)\s*ๆ` → repeat preceding word
3. `_syllabify_thai()`: tokenise Thai runs with `word_tokenize` then split each word with `thai_syllables` — insert spaces between syllables
4. Collapse whitespace

**Examples:**
```
อะไร      → อะ ไร
คุณ       → คุณ         (unchanged — single syllable)
ต้องการ   → ต้อง การ
มหาวิทยาลัย → มะ หา วิท ยา ลัย
```

> Note: The old approach joined syllables with `-` and performed character substitution, which corrupted text. Current approach inserts spaces only — KhanomTan reads standard Thai natively.

---

### 3.8 TTS Output — Two Architecture Options

The speaker is physically on PI 5. `tts.mode` in `settings.yaml` controls which option is active.

#### Option A — Server-Side TTS (Active, `tts.mode: "server"`)

```
to_tts_ready(text)
    │
    ▼
khanomtan_engine.synthesize_and_send()
    ├── _clean_text()         — normalize + collapse whitespace
    ├── _tts.predict(text)    — KhanomTan v1.0 GPU inference (pythaitts)
    │   Model: wannaphong/KhanomTan-TTS-v1.0
    │   Speaker: Tsyncone (Thai female, TSync-1 corpus)
    │   Language: th-th
    │   RTF: ~0.19× on GPU (~633ms for a 3s utterance)
    ├── Read WAV bytes from temp file
    └── send_wav(wav_bytes, pi5_base_url)
            └── POST WAV bytes → PI 5 /audio_play
```

**Latency:** ~633ms TTS inference + ~50ms network (105 KB WAV)

#### Option B — PI 5-Side TTS (`tts.mode: "pi5"`)

```
to_tts_ready(text)
    │
    ▼
POST { "phoneme_text": str } → PI 5 /tts_render
    │
    ▼
PI 5 runs TTS locally on ARM CPU
```

**Latency:** ~5ms network + PI 5 ARM inference time (300–800ms estimated)

---

## 4. File & Directory Structure

```
server/
├── api/
│   ├── main.py                    # FastAPI app, startup wiring, lifespan
│   ├── routes/
│   │   ├── receiver.py            # /greeting, /detection, /activate
│   │   ├── grammar.py             # /grammar (direct correction endpoint)
│   │   ├── tts.py                 # /thai_tts (direct TTS prep endpoint)
│   │   └── monitor.py             # /monitor (HTML dashboard), /events (JSON)
│   └── schemas/
│       └── receiver.py            # DetectionPayload, GreetingPayload, STTResult, ActivateResponse
├── mcp/
│   ├── greeting_bot.py            # Year-aware + memory-recall greeting LLM
│   ├── grammar_corrector.py       # Raw STT → corrected Thai via LLM chat API
│   ├── llm_chatbot.py             # 5-route RAG chatbot with session history
│   ├── memory_manager.py          # SQLite raw log + Milvus embedded summary
│   ├── intent_router.py           # Intent → TTS + optional ROS2 dispatch
│   └── tts_router.py              # Thai syllabification preprocessor
├── llm/
│   └── typhoon_client.py          # Ollama HTTP client, female particle enforcer, CJK cleaner
├── tts/
│   ├── khanomtan_engine.py        # Option A: KhanomTan GPU TTS → WAV → PI 5
│   ├── kanom_than_player.py       # send_wav() helper — POST WAV to PI 5 /audio_play
│   ├── text_sender.py             # Option B: POST phoneme text to PI 5 /tts_render
│   └── vits_engine.py             # Stub (future VITS integration)
├── vector_db/
│   └── milvus_client.py           # Milvus connect, ensure_collection, insert/search memory + RAG
├── database/
│   ├── sqlite_client.py           # Async SQLite: init_db, log_turn, get_turns, upsert_session
│   ├── mysql_client.py            # MySQL: timetable fetch, student lookup by id/nickname
│   └── metadata.db                # SQLite raw conversation log (auto-created)
├── config/
│   ├── settings.yaml              # Main server config (see Section 5)
│   └── pi5.yaml                   # PI 5 service config (host, port, audio, activation mode)
├── tools/
│   ├── reingest_timetable.py      # Re-ingest timetable xlsx → Milvus time_table collection
│   └── pipeline_test.py           # Full smoke test for all endpoints + intents
├── docs/
│   ├── design.md                  # This document
│   ├── goals.md                   # Phase goals and task checklist
│   ├── progress.md                # Implementation log
│   ├── pi5_design.md              # PI 5 side design (Teammate A/B interface)
│   └── teammate_b_design.md       # ROS2/navigation teammate B interface
└── logs/
    └── server.log                 # Rotating server log (INFO level)
```

---

## 5. Configuration — `config/settings.yaml`

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  pi5_ip: "10.26.9.196"
  pi5_port: 8766

llm:
  provider: "ollama"
  base_url: "http://localhost:11434"
  model: "qwen2.5:7b-instruct"
  timeout: 30
  temperature: 0.7
  max_tokens: 512

milvus:
  host: "localhost"
  port: 19530
  memory_collection: "conversation_memory"
  curriculum_collection: "curriculum"
  uni_info_collection: "uni_info"
  time_table_collection: "time_table"
  embedding_model: "sentence-transformers/all-MiniLM-L6-v2"
  embedding_dim: 384
  top_k: 3

tts:
  mode: "server"          # "server" = Option A (KhanomTan GPU → WAV → PI 5)
  speaker: "Tsyncone"     # Thai female voice (TSync-1 corpus)
  language: "th-th"

session:
  greeting_cooldown_seconds: 300
  session_timeout_seconds: 600
  max_history_turns: 5

thresholds:
  detection_confidence: 0.75
  # stt_confidence removed — Typhoon ASR does not expose a real per-utterance score

sqlite:
  db_path: "./database/metadata.db"

mysql:
  host: "localhost"
  port: 3306
  user: "root"
  password: "root"
  database: "capstone"
```

---

## 6. Key Data Schemas

### `DetectionPayload` (PI 5 → Server)
```json
{
  "timestamp": "2026-03-22T10:00:00Z",
  "person_id": "Palm (Krittin Sakharin)",
  "thai_name": "ปาล์ม",
  "student_id": "66070501001",
  "is_registered": true,
  "track_id": 1,
  "bbox": [120, 80, 340, 420],
  "stt": {
    "text": "ห้องสมุดอยู่ที่ไหนครับ",
    "language": "th",
    "duration": 2.1
  }
}
```

### `GreetingPayload` (PI 5 → Server)
```json
{
  "timestamp": "2026-03-22T10:00:00Z",
  "person_id": "Palm (Krittin Sakharin)",
  "thai_name": "ปาล์ม",
  "student_id": "66070501001",
  "is_registered": true,
  "vision_confidence": 0.92
}
```

### `ChatbotResponse` (internal — LLM output)
```json
{
  "reply_text": "ห้องสมุดอยู่ที่อาคาร E ชั้น 2 ค่ะ",
  "intent": "info",
  "destination": null,
  "confidence": 0.9
}
```

### `GreetingLLMOutput` (internal — greeting LLM output)
```json
{
  "greeting_text": "สวัสดีค่ะ คุณปาล์ม ครั้งที่แล้วถามเรื่องตารางเรียนไว้นะค่ะ มีอะไรเพิ่มเติมไหมค่ะ"
}
```

### Navigation command (Server → PI 5)
```json
{ "cmd": "stop_roaming" }
{ "cmd": "resume_roaming" }
{ "cmd": "go_to", "destination": "ห้องสมุด" }
```

---

## 7. Milvus Collections

### `conversation_memory`

| Field | Type | Notes |
|-------|------|-------|
| `id` | INT64 (PK, auto) | Auto-generated |
| `student_id` | VARCHAR(64) | From PI 5 payload or MySQL lookup |
| `session_id` | VARCHAR(64) | UUID per session |
| `raw_user_text` | VARCHAR(2048) | Grammar-corrected STT text |
| `raw_bot_reply` | VARCHAR(2048) | Full LLM reply text |
| `summary_text` | VARCHAR(1024) | LLM-generated 1–2 sentence Thai summary |
| `intent` | VARCHAR(32) | chat / info / navigate / farewell |
| `timestamp` | VARCHAR(32) | ISO UTC e.g. `"2026-03-22T10:00:00Z"` |
| `embedding` | FLOAT_VECTOR(384) | all-MiniLM-L6-v2 embedding of `summary_text` |

**Index:** `IVF_FLAT`, metric: `COSINE`, `nlist=128`, `nprobe=10`

### RAG Collections

| Collection | Entities | Embedding Model | Dim |
|-----------|----------|----------------|-----|
| `curriculum` | 516 | all-MiniLM-L6-v2 | 384 |
| `time_table` | 128 | all-MiniLM-L6-v2 | 384 |
| `uni_info` | 7 | all-MiniLM-L6-v2 | 384 |

> `time_table` was re-ingested with structured Thai sentences per (day, time_slot, course) using `tools/reingest_timetable.py`.

---

## 8. Session State Management

Sessions are stored in-memory per process (lost on restart). Keyed by `person_id`.

```python
_sessions[person_id] = {
    "session_id":    str(uuid.uuid4()),  # stable for the session lifetime
    "person_id":     str,
    "history":       List[Tuple[str, str]],  # last N (user, bot) turns
    "created_at":    datetime,
    "last_active":   datetime,
    # Populated on first /detection:
    "student_year":  int,    # 1–4, from MySQL
    "student_db_id": str,    # real student_id for Milvus partitioning
}
```

- `max_history_turns: 5` — oldest turn dropped when limit exceeded
- `session_timeout_seconds: 600` — idle sessions cleaned up on every `/detection` event
- On expiry: session dict dropped; history already stored in Milvus turn-by-turn (no flush needed)
- `_last_greeting[person_id]` — tracks last greeting time separately for cooldown

---

## 9. LLM Client & Post-Processing

**File:** `llm/typhoon_client.py`

**Methods:**
- `generate(prompt, temperature, max_tokens)` — calls Ollama `/api/generate`, returns raw text
- `chat(messages, temperature, max_tokens)` — calls Ollama `/api/chat`, returns assistant text
- `generate_structured(prompt, ...)` → `Optional[Dict]` — parses JSON from generate response (strips markdown fences, fixes trailing commas)
- `chat_structured(messages, ...)` → `Optional[Dict]` — parses JSON from chat response

**`enforce_female_particle(text)`** post-processor (applied to all LLM output):
- `ครับ` → `ค่ะ`
- `ผม` → `ฉัน`
- Strips standalone English sentences (lines > 20 chars containing mostly ASCII with no Thai)

**`clean_cjk(text)`:**
- Strips Hiragana, Katakana, CJK Unified, Hangul, and fullwidth character ranges

**Base `SYSTEM_PROMPT`** (injected into all chatbot calls):
```
คุณคือหุ่นยนต์บริการชื่อ "ขนมทาน" ของสถาบันเทคโนโลยีพระจอมเกล้าเจ้าคุณทหารลาดกระบัง (KMITL)
คุณช่วยเหลือนักศึกษาหลักสูตร Robotics and AI Engineering (RAI)

กฎสำคัญ:
1. ตอบเป็นภาษาไทยเท่านั้น (ชื่อเฉพาะ เช่น KMITL, RAI, email ได้)
2. ห้ามใช้อักษรจีน เกาหลี หรือญี่ปุ่น
3. ตอบสั้น กระชับ เป็นมิตร ลงท้ายด้วย "ค่ะ" ห้ามใช้ "ครับ"
4. ถ้าไม่มีข้อมูลให้บอกตรงๆ
5. ใช้ "ห้องปฏิบัติการ" หรือ "แลป" แทน lab
```

---

## 10. Error Handling & Fallbacks

| Failure Point | Fallback Behaviour |
|---------------|--------------------|
| LLM timeout / error (chatbot) | Return canned Thai reply: `"ขออภัยค่ะ ไม่เข้าใจคำถาม ช่วยพูดอีกครั้งได้ไหมค่ะ"`, intent=`chat` |
| LLM error (grammar corrector) | Forward raw STT text unchanged |
| LLM output too short (< 50% input length) | Grammar: discard LLM output, use raw text |
| Milvus search fails | Proceed with empty memory context / empty RAG context |
| Milvus write fails | Raw log still saved in SQLite; error logged |
| MySQL lookup fails | `student_year` defaults to 1, `student_db_id` defaults to `person_id` |
| Memory retrieve fails at greeting | Use `"(ไม่มีประวัติการสนทนา)"` — LLM gives generic greeting |
| TTS synthesis fails (Option A) | Error logged; PI 5 does not receive audio |
| PI 5 unreachable (navigation/TTS) | Error logged; pipeline continues |
| LLM JSON parse fails | `generate_structured()` returns `None`; caller uses fallback response |
| Greeting LLM fails | Fallback: `"สวัสดีค่ะ คุณ {student_name} ดีใจที่ได้พบค่ะ มีอะไรให้ช่วยไหมค่ะ"` |

---

## 11. Inter-Service Communication

All MCP modules run as **Python objects within the same FastAPI process** — no internal HTTP. External HTTP only:

| Call | Destination | Method | Payload |
|------|------------|--------|---------|
| LLM inference | `localhost:11434` (Ollama) | POST | `/api/generate` or `/api/chat` |
| TTS playback (Option A) | PI 5 `/audio_play` | POST | WAV bytes |
| TTS text (Option B) | PI 5 `/tts_render` | POST | `{ "phoneme_text": str }` |
| Navigation | PI 5 `/navigation` | POST | `{ "cmd": str, "destination"?: str }` |
| Activation push | PI 5 `/set_active` | POST | `{ "active": 0\|1 }` |

**Parallel dispatch** via `asyncio.gather()`:
- `greeting_bot`: TTS speak + ROS2 stop_roaming
- `farewell` intent: TTS speak + ROS2 resume_roaming
- `navigate` intent: TTS confirmation + ROS2 go_to

---

## 12. MySQL Database

**Database:** `capstone` on `localhost:3306`

**Tables used:**

| Table | Used For | Key Columns |
|-------|----------|-------------|
| `Students` | Student name/year lookup by nick_name or student_id | `student_id`, `first_name`, `last_name`, `nick_name`, `student_email`, `year` |
| `Academic_Year` | Joined in student context queries | — |
| `ExcelTimetableData` | Timetable text retrieval by row_id from Milvus hits | `row_id`, `row_text` |
| `ExcelTimetableData_backup` | Backup of old raw Excel rows (229 rows) | — |

**Helper functions (`database/mysql_client.py`):**
- `fetch_student_by_id(student_id, ...)` — lookup by real student_id (from PI 5 payload)
- `fetch_student_by_nickname(nick_name, ...)` — lookup by face-recognition nickname (fallback)
- `fetch_timetable_rows(row_ids, ...)` — fetch timetable text rows by ID list
- `fetch_student_context(...)` — full student info for chatbot RAG

---

## 13. Dependencies (Key Packages)

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.124.4 | HTTP framework |
| uvicorn | — | ASGI server |
| httpx | 0.28.1 | Async HTTP client (PI 5 calls, Ollama) |
| pymilvus | 2.6.10 | Vector DB client |
| sentence-transformers | — | Embedding model (all-MiniLM-L6-v2, dim 384) |
| pythainlp | 3.1.1 | Thai NLP: word tokenise, syllabification, normalise |
| pythaitts | 0.4.2 | KhanomTan TTS wrapper (Coqui-TTS backend) |
| torch | 2.4.1 | GPU tensors for TTS inference |
| aiosqlite | — | Async SQLite for conversation log |
| mysql-connector-python | — | MySQL client for student/timetable data |
| pyyaml | — | settings.yaml loading |
| pydantic | 2.x | Request/response schema validation |

---

## 14. Quick-Start

```bash
# 1. Activate venv
source venv/bin/activate

# 2. Start Docker services (Milvus, MySQL, Ollama)
sudo docker-compose up -d

# 3. Pull LLM model
ollama pull qwen2.5:7b-instruct

# 4. Start server
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 5. Monitor dashboard
# http://10.100.16.22:8000/monitor
# or SSH tunnel: http://localhost:8080/monitor
```

---

*Design Document v3 — reflects Phase 2.8.1 implementation. Last updated 2026-03-22.*
