# Server-Side Design Document
## Offline AI Brain — ROS2 MCP Robot System (v2)

> **Purpose:** This document is a complete implementation blueprint for the server-side pipeline of the campus robot AI brain. It is intended to be fed into a generative AI (e.g., GPT-4, Claude) to scaffold the full codebase.

---

## 1. Project Overview

The server runs on a PC (Ubuntu 22.04, NVIDIA GPU) and acts as the **AI Brain** of a service robot. It receives audio/detection input from a Raspberry Pi 5 (PI 5), processes it through a multi-stage MCP pipeline, and routes the output back to either a TTS engine or ROS2 navigation commands.

**Core responsibilities:**
- Receive detection triggers and microphone events from PI 5
- On greeting: immediately greet the student by name and halt robot roaming
- Perform grammar correction on STT text via LLM (Thai prompts only)
- Run a stateful chatbot conversation using Typhoon Thai LLM
- Summarize and store both raw conversation and embedded summary in Milvus
- Route LLM output to TTS (speech) or ROS2 (navigation/roaming)
- On farewell: speak goodbye AND resume robot roaming
- On navigation intent: speak confirmation AND send destination to ROS2

---

## 2. Data Flow Architecture

```
PI 5 (mic / face detection)
  │
  ├─── POST /greeting  (once per 5 mins — carries student name)
  ├─── POST /detection (.json — face/person detection event)
  │
  ▼
server_receiver  [FastAPI — 0.0.0.0:8000]
  │
  ├─── POST /activate (0/1 — activation signal back to PI 5)
  │
  └─── On /greeting ──► greeting_bot (one-shot LLM)
                              │
                    ┌─────────┴──────────┐
                    │  (asyncio.gather)  │
              POST /thai_tts       POST /navigation
          "สวัสดี คุณ [ชื่อ]"      { cmd: "stop_roaming" }
                    │                    │
              mcp_thaitts            PI 5 (ROS2)
                    │
  ┌─────────────────┘
  │
  └─── On STT input ──► POST /grammar (.json raw STT text)
                              │
                              ▼
                        mcp_grammar ──► LLM (Thai prompt) ──► corrected text (.json)
                              │
                              ▼
                        llm_chatbot ◄──── mcp_summary (pulls /summary .json from Milvus)
                              │
                              ├─── POST /history (.json) ──► milvus_database
                              │                               (raw convo + embedded summary)
                              │                                    │
                              └◄── mcp_summary ◄── /summary ◄─────┘
                              │
                              ▼
                        POST /intg
                              │
                              ▼
                        mcp_intendgate
                              │
              ┌───────────────┼───────────────┐
              │               │               │
           chat/info      farewell         navigate
              │               │               │
        /thai_tts      /thai_tts +       /thai_tts +
        (loop)          /navigation       /navigation
                       (resume           (say confirm
                        roaming)          + send dest)
              │               │               │
              └───────────────┴───────────────┘
                              │
                        mcp_thaitts
                    (phoneme conversion)
                     "สะ-หวัด-ดี" etc.
                              │
                 ┌────────────┴────────────┐
                 │  Option A               │  Option B
                 │  TTS on server          │  Send phoneme text
                 │  → send WAV to PI 5     │  → PI 5 runs TTS
                 └─────────────────────────┘
```

---

## 3. Module Breakdown

---

### 3.1 `server_receiver` — FastAPI Entry Point

**File:** `api/main.py`
**Host:** `0.0.0.0:8000`
**Description:** Single ingress point for all PI 5 messages. Validates and dispatches to internal MCP modules.

**Endpoints:**

| Method | Path | Input | Output | Description |
|--------|------|-------|--------|-------------|
| POST | `/greeting` | `GreetingPayload` | `{ "status": "ok" }` | Triggered once per 5 mins. Fires `greeting_bot`, halts roaming. |
| POST | `/detection` | `DetectionPayload (.json)` | `{ "activate": 0 or 1 }` | Face/person detection event. Returns activation signal. |
| GET  | `/activate` | — | `{ "active": 0 or 1 }` | PI 5 polls this to check if robot should be active. |

**Schemas (`api/schemas/receiver.py`):**
```python
class GreetingPayload(BaseModel):
    student_id: str
    student_name: str          # Thai name, e.g. "สมชาย"

class DetectionPayload(BaseModel):
    student_id: Optional[str]
    confidence: float
    timestamp: str
    location: Optional[str]

class ActivateResponse(BaseModel):
    active: int                # 0 or 1
```

**Logic:**
- On `/greeting`: pass `student_name` to `greeting_bot` → simultaneously trigger TTS greeting and ROS2 stop
- On `/detection`: if `confidence` > threshold → set session active → return `activate: 1`
- Rate-limit `/greeting` to once per 5 minutes per `student_id`

---

### 3.2 `greeting_bot` — One-Shot Greeting Handler (NEW)

**File:** `mcp/greeting_bot.py`
**Description:** A dedicated one-shot LLM call triggered only on `/greeting`. Generates a personalized Thai greeting AND a ROS2 stop command. Fires independently from the main chatbot — runs exactly once per session start.

**Output Schema:**
```python
class GreetingBotResponse(BaseModel):
    greeting_text: str     # e.g. "สวัสดี คุณสมชาย มีอะไรให้ช่วยไหมครับ"
    ros2_cmd: str          # always "stop_roaming" for greeting
```

**LLM Prompt (Thai only):**
```
คุณคือหุ่นยนต์บริการในมหาวิทยาลัย ชื่อ "ขนมทาน"
นักศึกษาที่ตรวจพบชื่อ: {student_name}

สร้างข้อความทักทายสั้น ๆ เป็นภาษาไทย และคำสั่ง ROS2 สำหรับหยุดเคลื่อนที่

ตอบกลับเป็น JSON เท่านั้น รูปแบบ:
{
  "greeting_text": "สวัสดี คุณ{student_name} ...",
  "ros2_cmd": "stop_roaming"
}
```

**Flow:**
1. Receive `student_name` from `server_receiver`
2. Call Typhoon LLM with greeting prompt → parse `GreetingBotResponse`
3. Fire **in parallel** using `asyncio.gather()`:
   - POST `greeting_text` → `mcp_thaitts` → `/thai_tts` (phoneme convert → TTS)
   - POST `{ cmd: "stop_roaming" }` → PI 5 `/navigation`

> `greeting_text` passes through `mcp_thaitts` for phoneme conversion before TTS playback, same as all other speech output.

---

### 3.3 `mcp_grammar` — Grammar Correction MCP

**File:** `mcp/grammar_corrector.py`
**Description:** Receives raw STT text, sends to LLM with a Thai grammar-fix prompt, returns corrected Thai text.

**Endpoint (internal):**

| Method | Path | Input | Output |
|--------|------|-------|--------|
| POST | `/grammar` | `{ "raw_text": str, "session_id": str }` | `{ "corrected_text": str }` |

**LLM Prompt (Thai only):**
```
คุณคือผู้ช่วยแก้ไขไวยากรณ์ภาษาไทย
แก้ไขการสะกด ไวยากรณ์ และขอบเขตของคำในข้อความภาษาไทยต่อไปนี้
ตอบกลับเฉพาะข้อความที่แก้ไขแล้วเท่านั้น ห้ามอธิบายเพิ่มเติม

ข้อความ: {raw_text}
```

**Flow:**
1. Receive raw STT text
2. Build Thai grammar correction prompt
3. Call Typhoon LLM via `llm/typhoon_client.py`
4. Return corrected text as JSON
5. If LLM fails → forward raw STT text unchanged (fallback)

---

### 3.4 `llm_chatbot` — Core Conversation Engine

**File:** `mcp/llm_chatbot.py`
**Description:** Central conversation handler. Injects memory context from Milvus, calls Typhoon LLM, returns structured response.

**Endpoint (internal):**

| Method | Path | Input | Output |
|--------|------|-------|--------|
| POST | `/llm` | `{ "text": str, "session_id": str }` | `ChatbotResponse (.json)` |

**Output Schema:**
```python
class ChatbotResponse(BaseModel):
    reply_text: str              # Thai text to speak
    intent: str                  # "chat" | "info" | "navigate" | "farewell"
    destination: Optional[str]   # Only if intent == "navigate"
    confidence: float
```

**LLM Prompt (Thai only):**
```
คุณคือหุ่นยนต์บริการในมหาวิทยาลัย ชื่อ "ขนมทาน"
คุณช่วยเหลือนักศึกษาในวิทยาเขต ตอบเป็นภาษาไทยเสมอ
พูดสุภาพ กระชับ และเป็นมิตร

ข้อมูลที่จำได้เกี่ยวกับนักศึกษาคนนี้:
{memory_summary}

ตอบกลับเป็น JSON เท่านั้น รูปแบบ:
{
  "reply_text": "...",
  "intent": "chat | info | navigate | farewell",
  "destination": "ชื่อสถานที่ หรือ null",
  "confidence": 0.0-1.0
}
```

**Flow:**
1. Receive corrected text + `session_id`
2. Call `mcp_summary` `/summary` → retrieve relevant memory from Milvus
3. Build full context (system prompt + short-term session history + memory + user input)
4. Call Typhoon LLM → parse `ChatbotResponse`
5. POST to `/history` → `mcp_summary` (store raw convo + embedded summary)
6. Return `ChatbotResponse` to `mcp_intendgate`

---

### 3.5 `mcp_summary` — Memory Retrieval & Summarization

**File:** `mcp/memory_manager.py`
**Description:** Interfaces with Milvus and SQLite. On write, stores the **raw conversation** in SQLite for debugging AND stores an **LLM-generated embedded summary** in Milvus for semantic retrieval.

**Endpoints (internal):**

| Method | Path | Input | Output |
|--------|------|-------|--------|
| POST | `/history` | `HistoryPayload (.json)` | `{ "status": "stored" }` |
| POST | `/summary` | `{ "session_id": str, "query": str }` | `{ "summary": str }` |

**`HistoryPayload` Schema:**
```python
class HistoryPayload(BaseModel):
    session_id: str
    student_id: Optional[str]
    user_text: str              # raw corrected STT text
    bot_reply: str              # full LLM reply text
    intent: str
    timestamp: str
```

**Write Flow (`/history`):**
1. Receive `HistoryPayload`
2. **Store raw conversation in SQLite as-is** — preserves `user_text` + `bot_reply` verbatim for debugging and inspection
3. Call LLM to summarize the turn into 1–2 Thai sentences (see prompt below)
4. Embed `summary_text` using `bge-m3`
5. Store in Milvus: `{ student_id, session_id, raw_user_text, raw_bot_reply, summary_text, embedding, intent, timestamp }`

**Summary Prompt (Thai only):**
```
สรุปการสนทนาต่อไปนี้เป็น 1-2 ประโยคภาษาไทย
เน้นสิ่งที่นักศึกษาต้องการและสิ่งที่หุ่นยนต์ตอบ

นักศึกษา: {user_text}
หุ่นยนต์: {bot_reply}

ตอบกลับเฉพาะบทสรุปเท่านั้น
```

**Read Flow (`/summary`):**
1. Embed the incoming query using `bge-m3`
2. Search Milvus for top-3 most similar `summary_text` entries filtered by `student_id`
3. Concatenate and return as a single Thai context string for `llm_chatbot`

---

### 3.6 `mcp_intendgate` — Intent Router (UPDATED)

**File:** `mcp/intent_router.py`
**Description:** Receives `ChatbotResponse` and routes to downstream handlers. `farewell` and `navigate` intents trigger **dual outputs** (TTS + ROS2) fired in parallel.

**Endpoint (internal):**

| Method | Path | Input | Output |
|--------|------|-------|--------|
| POST | `/intg` | `ChatbotResponse (.json)` | `{ "routed_to": List[str], "status": "ok" }` |

**Routing Logic:**

| Intent | TTS Action | ROS2 Action |
|--------|-----------|-------------|
| `chat` | POST `reply_text` → `/thai_tts` | None |
| `info` | POST `reply_text` → `/thai_tts` | None |
| `farewell` | POST `reply_text` → `/thai_tts` (say goodbye) | POST `{ cmd: "resume_roaming" }` → PI 5 `/navigation` |
| `navigate` | POST confirmation text → `/thai_tts` (e.g. "ได้เลยครับ ตามผมมา ผมจะพาคุณไปที่ {destination}") | POST `{ destination: str }` → PI 5 `/navigation` |

**Navigate confirmation text prompt (Thai only):**
```
สร้างประโยคยืนยันสั้น ๆ เป็นภาษาไทยว่าหุ่นยนต์จะพานักศึกษาไปที่ {destination}
ตัวอย่าง: "ได้เลยครับ ตามผมมาเลย ผมจะพาคุณไปที่ {destination}"
ตอบกลับเฉพาะประโยคเท่านั้น
```

> For `farewell` and `navigate`: TTS call + ROS2 call are fired **in parallel** using `asyncio.gather()` to minimize latency.

---

### 3.7 `mcp_thaitts` — Thai Phoneme Preprocessor (UPDATED)

**File:** `mcp/tts_router.py`
**Description:** Receives Thai text and converts it to a **phoneme-ready / syllable-broken string** that the TTS engine (KhanomTan) can read accurately. This is a **text normalization step only** — it does not run the TTS model.

**Endpoint (internal):**

| Method | Path | Input | Output |
|--------|------|-------|--------|
| POST | `/thai_tts` | `{ "text": str, "session_id": str }` | `{ "phoneme_text": str }` |

**What this module does:**
- Splits Thai text into syllables and formats them for TTS pronunciation
- Example: `"สวัสดี"` → `"สะ-หวัด-ดี"`
- Example: `"มหาวิทยาลัย"` → `"มะ-หา-วิด-ทะ-ยา-ลัย"`
- Handles tone marks, vowel shortening, consonant clusters

**Implementation using `pythainlp`:**
```python
from pythainlp.tokenize import syllable_tokenize

def to_phoneme_ready(text: str) -> str:
    syllables = syllable_tokenize(text)
    return "-".join(syllables)
```

After conversion, `phoneme_text` is forwarded to the TTS output layer (Section 3.8).

---

### 3.8 TTS Output — Two Architecture Options

The speaker is physically on PI 5. Both options are documented. Choose based on latency testing.

---

#### Option A — Server-Side TTS (Send WAV to PI 5)

**Flow:**
```
mcp_thaitts (phoneme_text)
    │
    ▼
TTS Model on PC (KhanomTan / VITS) — GPU inference
    │  → WAV bytes
    ▼
POST WAV binary → PI 5 /audio_play
    │
    ▼
PI 5 plays WAV via speaker
```

**Files:**
- `tts/vits_engine.py` — loads KhanomTan model, runs GPU inference, returns WAV bytes
- `tts/kanom_than_player.py` — POSTs WAV bytes to PI 5 `/audio_play`

**PI 5 must expose:**
```
POST /audio_play   — receives WAV bytes, plays immediately via speaker
```

**Pros:** PI 5 stays lightweight; fast GPU inference on server.
**Cons:** WAV payload is large (~100–500 KB); adds ~50–200ms network transfer time.

---

#### Option B — PI 5-Side TTS (Send Phoneme Text to PI 5)

**Flow:**
```
mcp_thaitts (phoneme_text)
    │
    ▼
POST { "phoneme_text": str } → PI 5 /tts_render
    │
    ▼
PI 5 runs lightweight TTS model (CPU/ARM)
    │
    ▼
PI 5 plays audio via speaker
```

**Files:**
- `tts/text_sender.py` — POSTs phoneme text to PI 5 `/tts_render`

**PI 5 must expose:**
```
POST /tts_render   — receives phoneme_text, runs TTS locally, plays audio
```

**Pros:** Tiny JSON payload; no WAV transfer overhead.
**Cons:** PI 5 must run TTS model on ARM CPU; inference may be slower.

---

#### Estimated Latency Comparison

| Step | Option A (Server TTS) | Option B (PI 5 TTS) |
|------|-----------------------|---------------------|
| TTS inference | ~100–300ms (GPU) | ~300–800ms (ARM CPU) |
| Network transfer | ~50–200ms (WAV binary) | ~5ms (text JSON) |
| **Total estimated** | **~150–500ms** | **~305–805ms** |

> **Recommendation:** Option A is likely faster due to GPU inference. Test both end-to-end before deciding. Set `tts.mode` in `settings.yaml` to switch between them.

---

## 4. File & Directory Structure

```
server/
├── api/
│   ├── main.py                    # FastAPI app, mounts all routers
│   ├── routes/
│   │   ├── receiver.py            # /greeting, /detection, /activate
│   │   ├── grammar.py             # /grammar
│   │   ├── tts.py                 # /thai_tts
│   │   └── navigation.py          # outbound nav + roaming commands to PI 5
│   └── schemas/
│       ├── receiver.py            # GreetingPayload, DetectionPayload
│       ├── chatbot.py             # ChatbotResponse
│       ├── greeting_bot.py        # GreetingBotResponse
│       └── history.py             # HistoryPayload
├── mcp/
│   ├── greeting_bot.py            # One-shot greeting LLM + parallel dispatch
│   ├── grammar_corrector.py       # mcp_grammar
│   ├── llm_chatbot.py             # Core conversation LLM
│   ├── memory_manager.py          # mcp_summary (Milvus R/W + SQLite raw log)
│   ├── intent_router.py           # mcp_intendgate (dual-output routing)
│   └── tts_router.py              # mcp_thaitts (phoneme preprocessing)
├── llm/
│   └── typhoon_client.py          # HTTP client for Ollama Typhoon
├── tts/
│   ├── vits_engine.py             # Option A: TTS inference on server (WAV output)
│   ├── kanom_than_player.py       # Option A: POSTs WAV to PI 5 /audio_play
│   └── text_sender.py             # Option B: POSTs phoneme text to PI 5 /tts_render
├── vector_db/
│   └── milvus_client.py           # Milvus connect, insert, search
├── database/
│   ├── sqlite_client.py           # Raw conversation log (for debugging)
│   └── metadata.db
├── config/
│   └── settings.yaml
├── logs/
│   └── server.log
└── requirements.txt
```

---

## 5. Configuration — `config/settings.yaml`

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  pi5_ip: "10.100.16.XX"
  pi5_port: 5000

llm:
  provider: "ollama"
  base_url: "http://localhost:11434"
  model: "typhoon:7b-instruct"
  timeout: 30

milvus:
  host: "localhost"
  port: 19530
  collection: "conversation_memory"
  embedding_model: "BAAI/bge-m3"
  top_k: 3

tts:
  mode: "server"              # "server" = Option A | "pi5" = Option B
  model_path: "./models/vits_thai.pth"
  cache_dir: "./cache/tts"
  max_cache: 500

session:
  greeting_cooldown_seconds: 300
  session_timeout_seconds: 600

thresholds:
  detection_confidence: 0.75
  stt_confidence: 0.6
```

---

## 6. Key Data Schemas

### `ChatbotResponse`
```json
{
  "reply_text": "ได้เลยครับ ตามผมมา ผมจะพาคุณไปที่ห้องสมุด",
  "intent": "navigate",
  "destination": "ห้องสมุด",
  "confidence": 0.95
}
```

### `GreetingBotResponse`
```json
{
  "greeting_text": "สวัสดี คุณสมชาย มีอะไรให้ช่วยไหมครับ",
  "ros2_cmd": "stop_roaming"
}
```

### `HistoryPayload`
```json
{
  "session_id": "sess_abc123",
  "student_id": "6401234567",
  "user_text": "หอสมุดอยู่ที่ไหน",
  "bot_reply": "หอสมุดอยู่ที่อาคาร 3 ครับ",
  "intent": "navigate",
  "timestamp": "2025-03-20T10:01:00Z"
}
```

> SQLite stores the full raw payload above. Milvus additionally stores `summary_text` (LLM-generated) and `embedding` (bge-m3 vector of the summary).

---

## 7. Milvus Collection Schema

**Collection:** `conversation_memory`

| Field | Type | Description |
|-------|------|-------------|
| `id` | INT64 (PK, auto) | Primary key |
| `student_id` | VARCHAR(64) | Student identifier (partition key) |
| `session_id` | VARCHAR(64) | Conversation session |
| `raw_user_text` | VARCHAR(2048) | Raw corrected STT input (for debugging) |
| `raw_bot_reply` | VARCHAR(2048) | Full bot reply text (for debugging) |
| `summary_text` | VARCHAR(1024) | LLM-generated Thai summary of the turn |
| `embedding` | FLOAT_VECTOR(1024) | bge-m3 embedding of `summary_text` |
| `intent` | VARCHAR(32) | Intent label |
| `timestamp` | VARCHAR(32) | ISO timestamp |

**Index:** IVF_FLAT on `embedding`, metric: COSINE
**Partition key:** `student_id`

---

## 8. Inter-Service Communication Pattern

All MCP modules run as **sub-routers within the same FastAPI process**, calling each other as Python functions — not HTTP — unless noted below.

**External HTTP calls only:**
- Outbound to Ollama/vLLM for LLM inference
- Outbound to PI 5: `/navigation`, `/activate`, `/audio_play` (Option A), `/tts_render` (Option B)

**Parallel dispatch** via `asyncio.gather()`:
- `greeting_bot`: TTS + ROS2 stop fired in parallel
- `farewell` intent: TTS + ROS2 resume fired in parallel
- `navigate` intent: TTS confirmation + ROS2 destination fired in parallel

---

## 9. Session State Management

```python
class SessionState:
    session_id: str
    student_id: Optional[str]
    student_name: Optional[str]
    active: bool
    last_greeting: datetime
    conversation_history: List[dict]   # last 5 turns (short-term only)
    created_at: datetime
```

- New session created on `/greeting` or `/detection` above confidence threshold
- Session expires after `session_timeout_seconds` of inactivity
- On expiry: flush remaining turns to Milvus via `mcp_summary`

---

## 10. Error Handling & Fallbacks

| Failure Point | Fallback Behavior |
|---------------|-------------------|
| LLM timeout / error | Return canned Thai reply: `"ขออภัยครับ ไม่เข้าใจ"` |
| Grammar correction fails | Forward raw STT text unchanged to `llm_chatbot` |
| Milvus search fails | Proceed with no memory context injected |
| Milvus write fails | Raw log still saved in SQLite; retry Milvus write async |
| TTS inference fails (Option A) | Send pre-recorded fallback WAV to PI 5 |
| PI 5 TTS fails (Option B) | Log error; attempt one retry |
| STT confidence < threshold | Fire TTS: `"ขอโทษครับ ช่วยพูดอีกครั้งได้ไหม"` — do not forward to chatbot |
| PI 5 unreachable (navigation) | Log error, skip ROS2 command, continue TTS only |

---

## 11. Implementation Order (Recommended)

1. `config/settings.yaml` — define all constants first
2. `database/sqlite_client.py` — raw conversation log store
3. `vector_db/milvus_client.py` — Milvus connect + collection setup
4. `llm/typhoon_client.py` — Ollama HTTP wrapper, validate Thai prompts
5. `mcp/memory_manager.py` — test Milvus R/W with both raw fields + embedded summary
6. `mcp/grammar_corrector.py` — test Thai grammar prompt
7. `mcp/greeting_bot.py` — one-shot greeting with parallel TTS + ROS2 stop
8. `mcp/llm_chatbot.py` — full conversation round-trip with memory
9. `mcp/intent_router.py` — test all 4 intent branches including dual-output
10. `mcp/tts_router.py` — Thai phoneme conversion with `pythainlp`
11. `tts/vits_engine.py` or `tts/text_sender.py` — TTS Option A or B
12. `api/main.py` + all routes — wire everything into FastAPI
13. End-to-end test with PI 5 simulator (mock detection + mock STT input)

---

## 12. Dependencies (`requirements.txt`)

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
pydantic>=2.0.0
httpx>=0.27.0
pymilvus>=2.4.0
sentence-transformers>=2.7.0
pythainlp>=5.0.0          # Thai NLP: syllabification, phoneme conversion
torch>=2.2.0
torchaudio>=2.2.0
sounddevice>=0.4.6
pyyaml>=6.0
aiosqlite>=0.20.0
python-multipart>=0.0.9
```

---

## 13. Quick-Start Instructions

```bash
# 1. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start Milvus (Docker)
docker-compose up -d

# 4. Pull and start Typhoon LLM
ollama pull typhoon:7b-instruct
ollama serve

# 5. Start the FastAPI server
cd server
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

---

*End of Design Document v2 — ready for generative AI implementation.*
