# KhanomTan / Satu — Technical Diagram Reference

**Source:** `ch3_design_construction.tex` + `design.md` + `audio_package/`
**Generated:** 2026-03-27

---

## Table of Contents

1. [System Overview — End-to-End Pipeline](#1-system-overview--end-to-end-pipeline)
2. [JetsonVision — Face Detection & Recognition Pipeline](#2-jetsonvision--face-detection--recognition-pipeline)
3. [RaspiReceive — Edge Audio Coordination](#3-raspireceive--edge-audio-coordination)
4. [Audio Sidecar (audio_package) — TTS & STT Service](#4-audio-sidecar-audio_package--tts--stt-service)
5. [capstone_server — AI Inference Pipeline](#5-capstone_server--ai-inference-pipeline)
6. [RAG Query Routing Strategy](#6-rag-query-routing-strategy)
7. [baymax-face-ui — Expression State Machine](#7-baymax-face-ui--expression-state-machine)
8. [Autonomous Navigation State Machine](#8-autonomous-navigation-state-machine)
9. [Robot Interaction State Machine](#9-robot-interaction-state-machine)
10. [Student Session Lifecycle](#10-student-session-lifecycle)
11. [Network Topology](#11-network-topology)
12. [Startup Sequence](#12-startup-sequence)

---

## 1. System Overview — End-to-End Pipeline

Four computational nodes connected over LAN. Hub-and-spoke topology: all edges talk to the AI Brain server except the dedicated Jetson→Pi 5 #1 WebSocket for face events.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PHYSICAL WORLD                                     │
│                     Student approaches robot                                │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ Camera frames
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  NVIDIA Jetson Orin Nano (192.168.1.10)                                              │
│                                                                                      │
│  Camera → SCRFD (640×640) → ByteTracker → Head Pose Gate → ArcFace FP16             │
│                                                       cosine sim vs enrollments.json │
│                                     WebSocket :8765 ──────────────────────────────►  │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                            │ JSON detection events
                                            ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Raspberry Pi 5 #1  (10.26.9.196)  — Audio + Display                                │
│                                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐                    │
│  │  asyncio event loop (raspi_main.py)                         │                    │
│  │                                                             │                    │
│  │  _detection_loop() ◄── WebSocket :8765                      │                    │
│  │  _stt_consumer_loop() ◄── AudioWorker thread                │                    │
│  │  _combiner_loop()  (500ms tick) ────────── HTTP POST ──────────────────────────► │
│  │  _run_activation_server() :8766                             │                    │
│  └─────────────────────────────────────────────────────────────┘                    │
│                                                                                      │
│  AudioWorker: PyAudio 16kHz → SileroVAD(0.73) → STT backend                        │
│  Playback:    /audio_play → aplay (watchdog 30s)                                    │
│  Face UI:     HTTP POST /face_emotion → :7000 → WS :8768 → Electron Canvas          │
└─────────────────────────────┬────────────────────────────────────────────────────────┘
                              │ HTTP POST /detection  or  /audio_detection
                              ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  AI Brain Server  (10.100.16.22)                                                     │
│                                                                                      │
│  FastAPI :8000  ──► [1] Input filter (noise gate, <3 chars)                          │
│                     [2] Grammar correction  (Typhoon2-8B via Ollama)                 │
│                     [3] Intent router  (keyword-based → 6 channels)                  │
│                     [4] RAG retrieval  (Milvus bge-m3 1024-dim, top-3)               │
│                     [5] Memory retrieval  (conversation_memory, filtered by ID)      │
│                     [6] LLM generation  (Typhoon2-70B Q5_K_M via Ollama)             │
│                     [7] TTS synthesis  → Audio Sidecar :8001                         │
│                     [8] Async dispatch ──────────────────────────────────────────►   │
│                                                                                      │
│  ┌──────────────────────┐    ┌─────────────────┐    ┌─────────────────────────────┐ │
│  │ Milvus v2.6  :19530  │    │  MySQL + SQLite  │    │  Ollama (70B + 8B models)   │ │
│  │ 4 collections        │    │  5 tables        │    │  GPU-accelerated            │ │
│  └──────────────────────┘    └─────────────────┘    └─────────────────────────────┘ │
│                                                                                      │
│  Audio Sidecar  (audio_package / main.py)  :8001                                    │
│  POST /tts  →  Typhoon2-Audio-8B  →  WAV bytes (16kHz PCM_16)                       │
│  POST /stt  →  Typhoon2-Audio-8B  →  Thai text                                      │
└───────┬──────────────────────────────────────────────────────────────────────────────┘
        │ Async dispatch (concurrent):
        │  POST /audio_play   → Pi 5 #1 :8766   (WAV bytes)
        │  POST /set_active   → Pi 5 #1 :8766   ({active: 0|1})
        │  POST /face_emotion → Pi 5 #1 :7000   ({emotion: 0-4})
        │  POST /nav_state    → Pi 5 #2 :8767   ({state, destination})  [if navigate]
        ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Raspberry Pi 5 #2  (10.26.9.196) — Navigation                                      │
│                                                                                      │
│  pi5_service.py :8767  →  /navigation_command (ROS2 topic)                          │
│  navigation_manager.py  →  BasicNavigator.goToPose()                                │
│  AMCL + Nav2 DWB  →  iREDCr differential drive  →  robot moves                     │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. JetsonVision — Face Detection & Recognition Pipeline

Runs continuously on Jetson Orin Nano. Each camera frame goes through 7 stages.

```
Camera Frame (any resolution)
          │
          ▼
┌─────────────────────────────────────────────────────────┐
│ [1] Face Detection                                      │
│     Model:  SCRFD (Sample & Computation Redistribution  │
│             Face Detector)                              │
│     Input:  640 × 640 px                               │
│     Conf threshold:  0.5                                │
│     NMS IoU threshold:  0.4                             │
│     Backend:  TensorRT (GPU) / ONNX CPU (--cpu-only)    │
│     Output:  bounding boxes + 5 facial landmarks        │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ [2] Multi-Face Tracking                                 │
│     Algorithm:  ByteTracker                             │
│     Assigns persistent track_id per face                │
│     Maintains continuity through brief occlusions       │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ [3] Head Pose Estimation (Frontal Gate)                 │
│     Input:  5 SCRFD landmark points                     │
│     Computes:  yaw, pitch, roll                         │
│     PASS condition:  |yaw| < 25°  AND  |pitch| < 15°   │
│     FAIL → frame discarded (no recognition attempt)     │
└────────────────────────┬────────────────────────────────┘
                         │ frontal faces only
                         ▼
┌─────────────────────────────────────────────────────────┐
│ [4] Recognition Trigger Gate                            │
│     Cooldown:  5 s per track_id                         │
│     Prevents saturating GPU for same face               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ [5] Face Recognition                                    │
│     Model:  ArcFace (ResNet backbone)                   │
│     Embedding:  512-dim, FP16 precision                 │
│     Matching:  cosine similarity vs data/enrollments.json│
│                                                         │
│     similarity ≥ threshold  →  KNOWN (student ID)      │
│     similarity <  threshold  →  UNKNOWN (temp UUID)    │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          │                             │
          ▼                             ▼
   [6] ZMQ LAN Stream          [7] WebSocket Send
       (optional)               ws://10.26.9.196:8765
       frame forwarding
                                JSON schema:
                                {
                                  status: "detected",
                                  metadata: {
                                    eng_name, thai_name,
                                    student_id,
                                    is_registered,  ← true/false
                                    confidence      ← float
                                  }
                                }
```

**Enrollment** (offline, `enroll_student.py`):
5 angles captured per student → 512-dim embeddings stored in `data/enrollments.json`

---

## 3. RaspiReceive — Edge Audio Coordination

Single `asyncio` event loop on Pi 5 #1. One background thread handles blocking audio I/O.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  AudioWorker Thread  (runs outside asyncio loop)                        │
│                                                                         │
│  PyAudio mic (16 kHz mono)                                              │
│       │                                                                 │
│       ▼ audio frames                                                    │
│  3-second rolling circular buffer                                       │
│       │                                                                 │
│       ▼                                                                 │
│  Tier 1: Pre-emphasis filter  (boosts Thai tonal consonants)            │
│       │                                                                 │
│       ▼                                                                 │
│  Tier 2: SileroVAD  (probability threshold 0.73)                       │
│       │                                                                 │
│       ├── prob < 0.73  → discard chunk (noise gate)                    │
│       │                                                                 │
│       └── prob ≥ 0.73  → append to utterance accumulator               │
│                               │                                         │
│                               ▼ end of speech segment                  │
│                        STT Backend (mode-dependent):                    │
│                          Mode A primary:  scb10x/typhoon-asr-realtime   │
│                                           (NeMo, 0.68-2.0 s/seg)       │
│                          Mode A fallback: distill-whisper-th-small      │
│                                           (faster-whisper INT8)         │
│                          Mode B:          raw WAV → POST /audio_detect. │
│                               │           → server Typhoon2-Audio-8B   │
│                               ▼                                         │
│                        STTChunk { text, confidence, timestamp }         │
│                               │                                         │
│                               └──► output queue                        │
│                                                                         │
│  PAUSED during TTS playback; RESUMED after aplay completes             │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       │ non-blocking queue
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  asyncio Event Loop  (raspi_main.py)                                    │
│                                                                         │
│  ┌──────────────────┐   ┌──────────────────┐   ┌───────────────────┐   │
│  │ _detection_loop  │   │ _stt_consumer    │   │ _combiner_loop    │   │
│  │                  │   │ _loop            │   │  (every 500 ms)   │   │
│  │ WS :8765         │   │                  │   │                   │   │
│  │ confidence ≥0.75 │   │ drains STT queue │   │ 4 gating rules:   │   │
│  │ 15s debounce     │   │ rolling buffer   │   │ 1. consecutive    │   │
│  │ Unknown→Known    │   │                  │   │    detections ≥2  │   │
│  │ upgrade logic    │   │                  │   │    /10s window    │   │
│  │                  │   │                  │   │ 2. unknown hold   │   │
│  │ → detection queue│   │ → STT buffer     │   │    ≤ 10s          │   │
│  └──────────────────┘   └──────────────────┘   │ 3. STT wait ≤60s  │   │
│                                                 │ 4. cooldown 5s    │   │
│                                                 │                   │   │
│                                                 │ → POST /detection │   │
│                                                 │   or /audio_detect│   │
│                                                 └───────────────────┘   │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────┐      │
│  │ _run_activation_server  (FastAPI :8766)                       │      │
│  │                                                               │      │
│  │  POST /audio_play  → WAV queue → aplay (watchdog 30s)        │      │
│  │  POST /set_active  → {active: 0|1} pause/resume AudioWorker  │      │
│  │  GET  /health      → status + queue depth                    │      │
│  │  GET  /active_status                                         │      │
│  └───────────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────────┘
```

### Detection-Speech Combiner Logic (detail)

```
Detection event arrives
          │
          ▼
   is_registered = false?
          │
    YES   │   NO
          │    └──────────────────────────────┐
          ▼                                   │
  Unknown-to-Known hold timer (≤10s)          │
  Wait for same track_id to be recognised     │
          │                                   │
          ▼                                   │
  consecutive detections ≥ 2 within 10s? ◄───┘
          │
    NO    │   YES
    drop  │
          ▼
  Wait for STT result  (up to stt_wait_sec = 60s)
          │
  text available?  → assemble payload
  timeout expired? → dispatch with empty text (greeting fallback)
          │
          ▼
  POST to AI server
          │
          ▼
  cooldown_sec = 5s  (ignore new detections)
```

---

## 4. Audio Sidecar (audio_package) — TTS & STT Service

Runs as independent FastAPI process on GPU server port **8001**.
Separate Python 3.10 venv (model dependency isolation from capstone_server).

```
┌──────────────────────────────────────────────────────────────────────────┐
│  audio_package  (main.py + typhoon_audio.py)                             │
│  FastAPI :8001  |  Model: typhoon-ai/llama3.1-typhoon2-audio-8b-instruct │
│                                                                          │
│  Startup:  _get_model() pre-loads model to CUDA (float16)               │
│            attn_implementation="eager"  ← GLIBC_2.32 constraint         │
│            (flash-attn unavailable on Ubuntu 20.04)                      │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  POST /tts                                                       │    │
│  │                                                                  │    │
│  │  Request:  { "text": "Thai text string" }                        │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  asyncio.to_thread(typhoon_audio.synthesize, text)               │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  model.synthesize_speech(text)                                   │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  result = { "array": float32 np.ndarray,                        │    │
│  │             "sampling_rate": 16000 }                             │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  sf.write(BytesIO, array, 16000, format="WAV", subtype="PCM_16") │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  Response:  raw WAV bytes  (Content-Type: audio/wav)             │    │
│  │             ~105 KB typical,  ~633 ms synthesis time             │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  POST /stt                                                       │    │
│  │                                                                  │    │
│  │  Request:  raw WAV bytes body  (Content-Type: audio/wav)         │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  asyncio.to_thread(typhoon_audio.transcribe, wav_bytes)          │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  write WAV → temp file  (model expects file path)                │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  conversation = [                                                │    │
│  │    { role: "system",  content: "Transcribe accurately..."  },   │    │
│  │    { role: "user",    content: [                                 │    │
│  │        { type: "audio", audio_url: tmp_path },                   │    │
│  │        { type: "text",  text: "Transcribe this audio" }          │    │
│  │    ]}                                                            │    │
│  │  ]                                                               │    │
│  │       │                                                          │    │
│  │       ▼                                                          │    │
│  │  model.generate(conversation,                                    │    │
│  │                 max_new_tokens=256,                               │    │
│  │                 do_sample=False,                                  │    │
│  │                 temperature=1.0)                                  │    │
│  │       │                                                          │    │
│  │       ▼  os.unlink(tmp_path)                                     │    │
│  │  Response:  { "text": "transcribed Thai text" }                  │    │
│  │             ~1-5 s latency  (GPU server)                         │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  GET  /health  →  { "status": "ok", "model": MODEL_ID }                 │
└──────────────────────────────────────────────────────────────────────────┘
```

### STT Backend Comparison

```
┌─────────────────────────────────────────────────────┬──────────────┬──────────────────────┬──────────────────┐
│ Backend                                             │ Speed        │ RAM                  │ Role             │
├─────────────────────────────────────────────────────┼──────────────┼──────────────────────┼──────────────────┤
│ scb10x/typhoon-asr-realtime  (NeMo, on Pi 5)        │ 0.68-2.0 s/s │ ~2.8 GB + 1.7 GB swap│ Mode A primary   │
│ biodatlab/distill-whisper-th-small (faster-whisper) │ 8.5-9.7 s/s  │ ~800 MB – 1 GB       │ Mode A fallback  │
│ Typhoon2-Audio-8B  (this sidecar, server GPU)       │ 1-5 s/s      │ GPU (server)         │ Mode B ★ recommended │
└─────────────────────────────────────────────────────┴──────────────┴──────────────────────┴──────────────────┘
```

---

## 5. capstone_server — AI Inference Pipeline

Each `POST /detection` (or `/audio_detection`) request follows this 8-step pipeline:

```
HTTP POST /detection
{
  person_id, student_id, name_th, nickname,
  is_known, text, confidence, timestamp
}
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ [1] INPUT FILTER (Noise Gate)                                           │
│     len(text) < 3 chars  →  REJECT  (no inference)                     │
│     len(text) ≥ 3 chars  →  PASS                                       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ [2] GRAMMAR CORRECTION  (Typhoon2-8B, fast assistant)                   │
│     BYPASS if STT confidence > 0.85                                     │
│     Corrects spelling + phonetic ASR errors                             │
│     Output length guard: must be 50-150% of input length               │
│       out of range → revert to raw transcription                        │
│     Timeout → revert to raw transcription                               │
│     Typical latency:  800–1,500 ms                                      │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ [3] QUERY ROUTING (Intent Detection)                                    │
│     Keyword-based classifier → one of 6 channels:                      │
│       conversation_memory  │  student_info  │  timetable               │
│       curriculum           │  uni_info      │  general_chat             │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ [4] RAG RETRIEVAL  (Milvus + bge-m3)                                    │
│     bge-m3 embed query (1024-dim)                                       │
│     IVF_FLAT index, nlist=128, nprobe=10                                │
│     cosine similarity, top-k=3                                          │
│     Collections:  curriculum (516) │ time_table (128) │ uni_info (7)   │
│     Typical latency:  50-200 ms                                         │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ [5] MEMORY RETRIEVAL  (Milvus conversation_memory)                      │
│     Filter by student_id  →  top-3 past session summaries              │
│     MySQL query for student academic year + metadata                    │
│     Typical latency:  30-80 ms                                          │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ [6] LLM RESPONSE GENERATION  (Typhoon2-70B, Q5_K_M GGUF via Ollama)    │
│     Persona: female Thai robot assistant (ค่ะ, นะคะ)                   │
│     Address student by nickname only                                    │
│     Max 2 sentences output                                              │
│     Expand: E-12 → อี สิบสอง, room numbers digit-by-digit             │
│     Intent classification output: chat | info | navigate | farewell    │
│     Typical latency:  4,000–12,000 ms                                  │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ [7] TTS SYNTHESIS  (Audio Sidecar :8001)                                │
│     Text normalisation:                                                 │
│       English/acronym expansion                                         │
│       Number-to-Thai conversion                                         │
│       Unicode normalisation                                             │
│       Word/syllable segmentation                                        │
│     POST /tts → Typhoon2-Audio-8B → WAV (16kHz PCM_16)                 │
│     Typical output:  ~105 KB,  ~633 ms                                  │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ [8] ASYNC DISPATCH  (concurrent, non-blocking asyncio)                  │
│                                                                         │
│  ┌─ always ──────────────────────────────────────────────────────────┐  │
│  │  POST /audio_play  → Pi 5 #1 :8766  (WAV bytes)                  │  │
│  │  POST /set_active  → Pi 5 #1 :8766  ({active: 0}) mute mic       │  │
│  │  POST /face_emotion → Pi 5 #1 :7000  ({emotion: 3}) Talking      │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌─ if intent = navigate ────────────────────────────────────────────┐  │
│  │  POST /nav_state  → Pi 5 #2 :8767  ({state:2, destination:...})  │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  [async background]                                                     │
│  SQLite: log full conversation turn                                     │
│  Milvus: store session summary embedding (end of session)               │
│  Pi 5:   POST /set_active {active: 1}  (after playback complete)       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 6. RAG Query Routing Strategy

```
User query text
        │
        ▼
  Keyword-based intent classifier
        │
        ├── scheduling keywords (วัน, เวลา, คาบ) ──────────►  time_table  (Milvus top-3)
        │                                                       + MySQL ExcelTimetableData
        │
        ├── course keywords (วิชา, หน่วยกิต, เรียน) ────────►  curriculum  (Milvus top-3)
        │
        ├── student personal info ─────────────────────────►  MySQL Students table
        │                                                      (academic year, nickname)
        │
        ├── past conversation context ─────────────────────►  conversation_memory
        │                                                      (Milvus top-3, student_id filter)
        │
        ├── open chat / no match ──────────────────────────►  general_chat
        │                                                      (no external retrieval)
        │
        └── default fallback ───────────────────────────────►  uni_info  (Milvus)
                                                               campus map, E-12 zones

        ↓ all channels
  Retrieved chunks → injected into LLM system prompt context window
  → Typhoon2-70B generates Thai response (≤2 sentences)
```

---

## 7. baymax-face-ui — Expression State Machine

Two-process architecture: FastAPI HTTP service (port 7000) + Electron renderer (IPC via WebSocket localhost:8768).

### Expression State Transition

```
                    face detected, identity confirmed
                    ↗
┌───────────────┐      ┌───────────────┐
│  0 - IDLE     │      │  2 - HAPPY    │
│  neutral,     │ ──►  │  wide smile,  │
│  breathing    │      │  sparkles     │
└───────────────┘      └───────┬───────┘
       ▲                       │ STT result ready, POST to server
       │                       ▼
       │               ┌───────────────┐
       │               │  4 - THINKING │
       │               │  drooped lid, │
       │               │  drifting iris│
       │               └───────┬───────┘
       │                       │ TTS audio starts playing
       │                       ▼
       │               ┌───────────────┐
       │               │  3 - TALKING  │
       │  playback ends│  mouth vowel  │
       └── (chat/info) │  animation    │
                       └───────┬───────┘
                               │ playback ends + intent = navigate
                               ▼
                       ┌───────────────┐
                       │  1 - SCANNING │
                       │  eyes sweep   │
                       │  left-right   │
                       └───────┬───────┘
                               │ next interaction begins
                               └──────────────► HAPPY (2)
```

### IPC Architecture

```
raspi_main.py coordinator
        │
        │ HTTP POST (fire-and-forget)
        ▼
pi5_face_service.py  (FastAPI, port 7000)
  POST /face_emotion  { "emotion": 0-4 }
        │
        │ WebSocket IPC
        ▼
localhost:8768
        │
        ▼
Electron main process  (BrowserWindow fullscreen)
        │
        │ canvas state transition
        ▼
HTML5 Canvas  requestAnimationFrame loop
  Interpolated transitions between expression states
```

Emotion transition timeline during interaction:
```
Person detected    STT ready     TTS starts    Playback ends
      │                │              │               │
      ▼                ▼              ▼               ▼
   HAPPY(2)  ──►  THINKING(4) ──►  TALKING(3) ──►  IDLE(0)
                                                  or SCANNING(1)
                                                  if navigate intent
```

---

## 8. Autonomous Navigation State Machine

`navigation_manager.py` ROS2 node on Pi 5 #2, wrapping Nav2 `BasicNavigator`.

```
                          HTTP POST /nav_state {state:1}
                         ┌──────────────────────────────┐
                         │                              │
              {state:0}  ▼                              │
      ┌──────────────  IDLE  ──────────────┐           │
      │                │                   │           │
      │         {state:2,                  │    ┌──────┴──────────┐
      │          destination}              │    │    ROAMING      │
      │                │                   │    │ patrol waypoints│
      │                ▼                   │    │ loop            │
      │          NAVIGATING  ──────────────┘    └─────────────────┘
      │               │  │                        {state:0}
      │               │  │ no progress 15s
      │               │  └──────────────────────────────────────────┐
      │               │                                              │
      │               │ goal reached                                 ▼
      │               ▼                                     spin 360° (AMCL
      │          SUCCEEDED  ──────────────► IDLE            re-localisation)
      │                                                              │
      │          FAILED  ◄──── 2 retries exhausted ─────────────────┘
      │               │                                    retry
      └───────────────┘
             IDLE

```

**Stuck detection:** displacement < 0.05 m over 15s → spin 360° → retry (max 2 retries)

### Navigation Stack Layers

```
HTTP POST /nav_state
        │
        ▼
pi5_service.py  (FastAPI :8767)
        │
        │ ROS2 topic /navigation_command (std_msgs/String)
        ▼
navigation_manager.py  (ROS2 node)
        │
        │ goals.yaml lookup: e12-1→A, e12-2→B, e12-3→C
        │ BasicNavigator.goToPose(x, y, yaw)
        ▼
Nav2 Stack:
  Global planner  →  collision-free path
  DWB local controller  →  velocity commands
    max_vel_x: 0.3 m/s
    trans_stopped_velocity: 0.25 m/s  (must be < max_vel_x)
        │
        ▼
AMCL  (Adaptive Monte Carlo Localisation)
  pre-built 2D occupancy grid map
  particle filter
  odometry input: /odom  (laser scan matcher, primary)
                  /odom_encoder  (wheel encoders, secondary)
        │
        ▼
iREDCr differential drive  (/dev/ttyiREDCr)
  Publishes /odom_encoder
RPLiDAR A1  →  ros2_laser_scan_matcher  →  /odom
```

---

## 9. Robot Interaction State Machine

From RaspiReceive coordinator perspective (Pi 5 #1):

```
                        face detected, gate passes
                       (consecutive ≥2, within 10s)
                        ┌──────────────────────────┐
                        │                          │
              ┌─────────┴──────┐          ┌────────┴───────┐
              │    INACTIVE    │          │     ACTIVE      │
              │  mic disabled  │ ◄──────  │  mic enabled    │
              │  standby /     │          │  combiner loop  │
              │  TTS playback  │          │  matching events│
              └────────────────┘          └────────────────┘
                        ▲                          │
                        │  POST /set_active=0      │ payload dispatched
                        │  (mute during response)  │
                        │                          ▼
                        │                  server response received
                        │                  WAV audio playing
                        │
                        └─ POST /set_active=1  (after playback ends)
                           OR  activate_timeout_sec=60 fallback
```

---

## 10. Student Session Lifecycle

Managed on AI Brain server (capstone_server):

```
No student present
        │
        │ face detection event received (is_known=true)
        ▼
Session created (student_id)
  ├─ load student metadata from MySQL
  ├─ load top-3 conversation summaries from Milvus (student_id filter)
  └─ initialise empty short-term history buffer

        │ greeting cooldown check (300s per student_id)
        ▼
POST /greeting
  ├─ 70B LLM generates personalised Thai greeting
  ├─ stop_roaming nav command → Pi 5 #2
  └─ session history initialised with greeting (bot-only entry)

        │ subsequent detection events → /detection
        ▼
Session active
  ├─ grammar correction → RAG retrieval → LLM → TTS → dispatch
  ├─ short-term history updated after each exchange
  └─ session timeout reset on each interaction

        │ no detection events for timeout period
        │   registered student: 30 minutes
        │   guest (unknown):      5 minutes
        ▼
Session closed
  ├─ 8B LLM summarises session transcript
  ├─ summary embedded (bge-m3) → stored in Milvus conversation_memory
  │   tagged with student_id
  └─ SQLite: full conversation log written (audit)
```

---

## 11. Network Topology

```
                    ┌──────────────────────────────────────┐
                    │     LAN  (10.x.x.x / 192.168.x.x)   │
                    └──────────────────────────────────────┘

┌──────────────────────────────┐          ┌──────────────────────────────┐
│  GPU AI Brain Server         │          │  Jetson Orin Nano            │
│  10.100.16.22                │          │  192.168.1.10                │
│                              │          │                              │
│  :8000  FastAPI (main)       │          │  :9999  TCP shutdown         │
│  :8001  Audio Sidecar        │          │         listener             │
│  :19530 Milvus (gRPC)        │          │                              │
│  MySQL / SQLite (internal)   │          │  Camera → SCRFD → ArcFace   │
│  Ollama (70B + 8B, internal) │          │  WebSocket client ────────► │
└──────────┬───────────────────┘          └─────────────────────────────┘
           │                                         │
           │  HTTP POST                              │ ws://10.26.9.196:8765
           │  /detection  /audio_detection           │
           │  /greeting                              ▼
           │                          ┌──────────────────────────────────┐
           │◄─────────────────────────│  Raspberry Pi 5  #1              │
           │                          │  10.26.9.196                     │
           │                          │                                  │
           │  POST /audio_play :8766  │  :8765 WS  Jetson receiver       │
           │  POST /set_active :8766  │  :8766 HTTP  RaspiReceive API    │
           │  POST /face_emotion:7000 │  :7000 HTTP  Face emotion API    │
           │  POST /nav_state  :8767  │  :8768 WS   Face IPC (internal)  │
           └─────────────────────────►│                                  │
                                      │  aplay speaker (USB/3.5mm)       │
                                      │  HDMI display (Electron face)    │
                                      └──────────────────────────────────┘
                                                       │
                                       HTTP POST :8767 │
                                                       ▼
                                      ┌──────────────────────────────────┐
                                      │  Raspberry Pi 5  #2              │
                                      │  10.26.9.196                     │
                                      │                                  │
                                      │  :8767 HTTP  nav bridge          │
                                      │  ROS2 Jazzy + Nav2 + AMCL        │
                                      │  RPLiDAR A1  (2D 360°)           │
                                      │  iREDCr differential drive       │
                                      └──────────────────────────────────┘
```

---

## 12. Startup Sequence

Deterministic startup — each layer depends on the previous being ready:

```
Step 1  ─  GPU AI Brain Server
           ├─ docker compose up  (Milvus v2.6 + MySQL)
           ├─ ollama serve  (Typhoon2-70B Q5_K_M + Typhoon2-8B loaded)
           ├─ uvicorn main:app  (audio_package :8001)
           │    └─ pre-loads Typhoon2-Audio-8B to CUDA
           └─ uvicorn app:app   (capstone_server :8000)

Step 2  ─  Raspberry Pi 5 #2  (navigation)
           ├─ ros2 launch ired_bringup bringup.launch.py
           │    └─ URDF, IMU, RPLiDAR, iREDCr serial, scan_matcher
           ├─ ros2 launch ired_navigation navigation.launch.py
           │    └─ AMCL, costmaps, DWB planner
           └─ python3 pi5_service.py  (:8767)

Step 3  ─  Raspberry Pi 5 #1  (audio + display)
           ├─ systemctl --user start baymax-face-api   (:7000)
           ├─ systemctl --user start baymax-face-ui    (Electron fullscreen)
           └─ systemctl --user start baymax-robot
                └─ raspi_main.py  (:8766 HTTP  +  :8765 WS listener)

Step 4  ─  Jetson Orin Nano
           └─ python3 visual_jetson_async.py --ws-enabled
                └─ connects to ws://10.26.9.196:8765
                   begins emitting detection events
```

### Graceful Shutdown

```
Jetson:   SIGTERM → close WebSocket → release camera → exit
Pi 5 #1:  SIGTERM → cancel asyncio tasks → flush audio queue → exit
Pi 5 #2:  SIGTERM → ROS2 lifecycle → Nav2 cancels active goals → exit
Server:   stop services in reverse order of startup

Remote Jetson shutdown:
  pi_shutdown_sender.py → TCP :9999 on Jetson shutdown listener
```

---

*Diagrams generated from: `design.md`, `ch3_design_construction.tex`, `audio_package/main.py`, `audio_package/typhoon_audio.py`*
*Last updated: 2026-03-27*
