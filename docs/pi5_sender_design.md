# PI 5 Data Sender Design
## KhanomTan Robot — Raspberry Pi 5 Outbound Pipeline

**Scope:** This document covers what PI 5 must **send** to the server — face detection events,
speech events, and greetings. It is intended for Teammate A (PI 5 side).

**Companion doc:** `pi5_design.md` covers what PI 5 must **expose** (audio_play, set_active).

---

## 1. Overview

```
PI 5 (10.26.9.196:8766)                   Server (10.100.16.22:8000)
──────────────────────────────────────     ──────────────────────────────────────────
Face detected (registered person)
  └─ POST /greeting  ────── JSON ────────→ one-shot personalised greeting
                                           ↓
                                           LLM greeting → Typhoon2-Audio TTS → /audio_play

Student speaks
  MODE A (PI 5 STT):
  └─ Typhoon ASR → text
     POST /detection ─── JSON ───────────→ grammar → RAG chatbot → intent router
                                           ↓
                                           reply TTS → /audio_play

  MODE B (Server STT):
  └─ record WAV from mic
     POST /audio_detection ─ multipart ─→ Typhoon2-Audio STT → grammar → RAG → intent
                                           ↓
                                           reply TTS → /audio_play
```

**Current active mode:** Mode B (Server STT via Typhoon2-Audio) is preferred — higher STT quality.
Mode A remains supported for fallback or if PI 5 STT is faster in practice.

---

## 2. Event Types

### 2.1 Greeting Event — `POST /greeting`

Triggered **once per person** when the face is first recognised (subject to cooldown on server side).
Do NOT send on every frame — send only on the first confident detection of a registered person.

**URL:** `http://10.100.16.22:8000/greeting`

**Headers:** `Content-Type: application/json`

**Body:**
```json
{
  "timestamp": "2026-03-22T04:30:00.000Z",
  "person_id": "Palm (Krittin Sakharin)",
  "thai_name": "ปาล์ม",
  "student_id": "65010001",
  "is_registered": true,
  "vision_confidence": 0.92
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `timestamp` | str (ISO 8601) | Yes | Time of detection |
| `person_id` | str | Yes | Face recognition label, e.g. `"Palm (Krittin Sakharin)"` |
| `thai_name` | str \| null | Preferred | Thai display name from student DB — server uses this in LLM prompts |
| `student_id` | str \| null | Preferred | DB student ID — server uses for Milvus memory lookup |
| `is_registered` | bool | Yes | Must be `true` (server skips unregistered) |
| `vision_confidence` | float | Yes | Face recognition confidence score (0.0–1.0) |

**Response:**
```json
{ "status": "ok", "greeting_text": "สวัสดีค่ะ คุณปาล์ม..." }
```
or `{ "status": "cooldown" }` if greeted recently (within ~30 s).

---

### 2.2 Detection Event — Mode A (PI 5 STT) — `POST /detection`

Triggered when a registered person speaks and PI 5 has transcribed the speech locally.

**URL:** `http://10.100.16.22:8000/detection`

**Headers:** `Content-Type: application/json`

**Body:**
```json
{
  "timestamp": "2026-03-22T04:30:05.000Z",
  "person_id": "Palm (Krittin Sakharin)",
  "thai_name": "ปาล์ม",
  "student_id": "65010001",
  "is_registered": true,
  "track_id": 42,
  "bbox": [120, 80, 400, 450],
  "stt": {
    "text": "วันนี้มีวิชาอะไรบ้างครับ",
    "language": "th",
    "duration": 2.3
  }
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `timestamp` | str | Yes | |
| `person_id` | str | Yes | |
| `thai_name` | str \| null | Preferred | |
| `student_id` | str \| null | Preferred | |
| `is_registered` | bool | Yes | |
| `track_id` | int \| null | Optional | Vision tracking ID |
| `bbox` | [x1,y1,x2,y2] \| null | Optional | Face bounding box in pixels |
| `stt.text` | str | Yes | Transcribed Thai text from PI 5 ASR |
| `stt.language` | str | Yes | `"th"` |
| `stt.duration` | float | Yes | Audio duration in seconds |

> **Note:** `stt.confidence` has been removed — Typhoon ASR does not expose a real beam score.
> Do not send it; the server no longer uses it.

**Response:**
```json
{ "active": 1 }
```
`active: 1` means the conversation is still going (PI 5 should listen for next utterance).
`active: 0` means the conversation ended (farewell detected).

---

### 2.3 Detection Event — Mode B (Server STT) — `POST /audio_detection`

Triggered when a registered person speaks. PI 5 sends raw WAV audio; the server transcribes
it using Typhoon2-Audio-8B.

**URL:** `http://10.100.16.22:8000/audio_detection`

**Headers:** `Content-Type: multipart/form-data`

**Form fields:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `audio` | File (WAV bytes) | Yes | Raw WAV audio from microphone |
| `person_id` | str | Yes | |
| `is_registered` | bool | Yes | |
| `thai_name` | str \| null | Preferred | |
| `student_id` | str \| null | Preferred | |
| `track_id` | int \| null | Optional | |
| `vision_confidence` | float \| null | Optional | |
| `timestamp` | str \| null | Optional | Defaults to server time if omitted |

**WAV audio requirements:**
| Property | Value |
|----------|-------|
| Format | PCM WAV (standard RIFF header) |
| Sample rate | Any (16000 Hz recommended) |
| Channels | Mono preferred |
| Duration | Capture full utterance (end on silence) |

**Response:** same as `/detection` — `{ "active": 1 }` or `{ "active": 0 }`

---

## 3. Python Reference Implementation

### 3.1 Sending a Greeting (both modes)

```python
import requests
from datetime import datetime, timezone

SERVER = "http://10.100.16.22:8000"

def send_greeting(person_id: str, thai_name: str, student_id: str, vision_conf: float):
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "person_id": person_id,
        "thai_name": thai_name,
        "student_id": student_id,
        "is_registered": True,
        "vision_confidence": vision_conf,
    }
    try:
        resp = requests.post(f"{SERVER}/greeting", json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[greeting] send failed: {e}")
        return None
```

---

### 3.2 Mode A — Send JSON Detection (PI 5 STT)

```python
def send_detection(person_id: str, thai_name: str, student_id: str,
                   stt_text: str, duration: float, track_id: int = None):
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "person_id": person_id,
        "thai_name": thai_name,
        "student_id": student_id,
        "is_registered": True,
        "track_id": track_id,
        "stt": {
            "text": stt_text,
            "language": "th",
            "duration": duration,
        },
    }
    try:
        resp = requests.post(f"{SERVER}/detection", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()  # {"active": 0 or 1}
    except Exception as e:
        print(f"[detection] send failed: {e}")
        return {"active": 0}
```

---

### 3.3 Mode B — Send WAV Audio (Server STT)

```python
def send_audio_detection(person_id: str, thai_name: str, student_id: str,
                         wav_bytes: bytes, vision_conf: float = None):
    """
    Send raw WAV audio to server for STT + chatbot pipeline.
    wav_bytes: raw WAV bytes recorded from microphone (PCM format).
    """
    fields = {
        "person_id": (None, person_id),
        "is_registered": (None, "true"),
        "thai_name": (None, thai_name or ""),
        "student_id": (None, student_id or ""),
        "timestamp": (None, datetime.now(timezone.utc).isoformat()),
    }
    if vision_conf is not None:
        fields["vision_confidence"] = (None, str(vision_conf))

    files = {
        "audio": ("speech.wav", wav_bytes, "audio/wav"),
    }

    try:
        resp = requests.post(
            f"{SERVER}/audio_detection",
            data={k: v[1] for k, v in fields.items()},
            files=files,
            timeout=60,   # STT on large audio can take up to ~30s
        )
        resp.raise_for_status()
        return resp.json()  # {"active": 0 or 1}
    except Exception as e:
        print(f"[audio_detection] send failed: {e}")
        return {"active": 0}
```

---

### 3.4 Recording WAV from Microphone (sounddevice)

```python
import sounddevice as sd
import soundfile as sf
import io

SAMPLE_RATE = 16000
MAX_DURATION = 10       # seconds — hard cap
SILENCE_THRESHOLD = 0.01
SILENCE_DURATION = 1.0  # seconds of silence to stop recording

def record_utterance() -> bytes:
    """
    Record audio from microphone until silence is detected.
    Returns WAV bytes (PCM, 16000 Hz, mono).
    """
    print("[mic] listening...")
    audio = sd.rec(
        int(MAX_DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype='int16',
    )
    sd.wait()

    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format='WAV', subtype='PCM_16')
    buf.seek(0)
    return buf.read()
```

---

## 4. Full Detection Loop — Mode B (Recommended)

```python
import time

_greeted = {}           # person_id → last greeted timestamp
GREET_COOLDOWN = 30     # seconds

def detection_loop():
    """
    Main loop — run continuously while robot is active.
    Face recognition and VAD (voice activity detection) run in separate threads.
    This shows the data-sender logic only.
    """
    while True:
        # ── Step 1: Face detection ────────────────────────────────────────
        result = get_face_detection()   # your existing face recognition code
        if result is None:
            time.sleep(0.1)
            continue

        person_id = result["person_id"]
        thai_name = result.get("thai_name")
        student_id = result.get("student_id")
        vision_conf = result.get("confidence", 0.0)

        if not result["is_registered"] or person_id == "Unknown":
            time.sleep(0.1)
            continue

        # ── Step 2: Greeting (once per person, with cooldown) ─────────────
        now = time.time()
        if person_id not in _greeted or now - _greeted[person_id] > GREET_COOLDOWN:
            _greeted[person_id] = now
            send_greeting(person_id, thai_name, student_id, vision_conf)

        # ── Step 3: Speech capture ────────────────────────────────────────
        wav_bytes = record_utterance()   # VAD-triggered recording
        if not wav_bytes:
            continue

        # ── Step 4: Send to server (Mode B — server STT) ─────────────────
        result = send_audio_detection(
            person_id=person_id,
            thai_name=thai_name,
            student_id=student_id,
            wav_bytes=wav_bytes,
            vision_conf=vision_conf,
        )

        if result.get("active") == 0:
            print("[detection_loop] conversation ended (farewell)")
            # robot resumes roaming — handled by server posting /navigation
```

---

## 5. Choosing Between Mode A and Mode B

| Criteria | Mode A (PI 5 STT) | Mode B (Server STT) |
|----------|-------------------|---------------------|
| STT model | Typhoon ASR on PI 5 | Typhoon2-Audio-8B on GPU |
| Audio quality | depends on PI 5 model | superior (larger model) |
| Latency | lower (no WAV transfer) | +100–500 ms for transfer |
| Bandwidth | low (text only) | ~50–200 KB per utterance |
| Implementation | existing PI 5 STT code | record WAV → multipart POST |
| Recommended | fallback / testing | **preferred for production** |

Both modes feed into the identical server pipeline (grammar → RAG → intent). The only
difference is where STT happens.

---

## 6. Network & Timing

| Stage | Duration |
|-------|----------|
| Greeting LLM generation | ~1–3 s |
| Typhoon2-Audio TTS | ~0.5–2 s |
| WAV transfer PI 5 → Server (200 KB) | ~10 ms (LAN) |
| Server STT (Typhoon2-Audio-8B) | ~1–5 s depending on audio length |
| Grammar corrector LLM | ~0.5–1 s |
| RAG chatbot LLM | ~2–5 s |
| Reply WAV → PI 5 `/audio_play` | ~10 ms |
| **Total (Mode B, greeting+reply)** | **~5–15 s** |

---

## 7. Endpoint Summary

| Method | Path | Payload | Purpose |
|--------|------|---------|---------|
| POST | `/greeting` | JSON `GreetingPayload` | First face contact — fire one-shot greeting |
| POST | `/detection` | JSON `DetectionPayload` | PI 5 STT result → full pipeline |
| POST | `/audio_detection` | multipart WAV + fields | Raw audio → server STT → full pipeline |
| GET | `/health` | — | Check server is alive |

---

## 8. Dependencies (PI 5 side for Mode B)

```bash
pip install requests sounddevice soundfile
# libsndfile needed for soundfile:
sudo apt-get install libsndfile1 portaudio19-dev
```

Mode A has no extra dependencies (text-only POST using `requests`).
