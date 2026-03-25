# PI 5 Service Design — Audio & Activation
## Satu Robot — Raspberry Pi 5 Side

**Scope:** This document covers only the two routes the PI 5 must expose to receive
commands from the server: `/audio_play` (WAV playback) and `/set_active` (push-based
activation state). It is intended for Teammate A.

**Config file:** `config/pi5.yaml` — copy this to the PI 5 service directory and load
it at startup. All hardcoded values (port, IPs, audio settings) come from there.

---

## 1. Overview

```
Server (GPU PC — 10.100.16.22:8000)          PI 5 (10.26.9.196:5000)
─────────────────────────────────────        ──────────────────────────────────
POST /audio_play  ──── WAV bytes ─────────→  play through speaker
POST /set_active  ──── {active: 0|1} ────→  update robot mode flag
GET  /health      ←──── poll ────────────── (PI 5 checks server is up)
```

The server no longer polls `/activate` on itself — it pushes state directly to PI 5
via `/set_active` each time the activation state changes. This eliminates polling lag.

---

## 2. Routes PI 5 Must Expose

### 2.1 `POST /audio_play` — WAV Playback

**Purpose:** Receive synthesized WAV bytes from the server and play them through the
speaker immediately. Serialised — one utterance at a time via a queue.

#### Request
```
POST /audio_play
Content-Type: audio/wav
Body: <raw WAV bytes>
```

#### Response
```json
{ "status": "queued", "queue_depth": 0 }
```
Return **immediately** (do not block until playback finishes).
The server has a 15 s timeout — if the response takes longer, it logs an error.

#### WAV format (from server)
| Property | Value |
|----------|-------|
| Format | PCM WAV (standard RIFF header) |
| Sample rate | 16 000 Hz |
| Channels | Mono (1) |
| Bit depth | 16-bit signed int |
| Typical size | 80 – 200 KB per utterance |
| Typical duration | 2 – 6 seconds |

#### Implementation — recommended approach

```python
import io
import queue
import threading
import tempfile, os
import sounddevice as sd
import soundfile as sf
from flask import Flask, request, jsonify

app = Flask(__name__)
_play_queue = queue.Queue()   # thread-safe playback queue


def _player_thread():
    """Dedicated thread — dequeues and plays WAV files one at a time."""
    while True:
        wav_bytes = _play_queue.get()
        try:
            buf = io.BytesIO(wav_bytes)
            data, samplerate = sf.read(buf, dtype='int16')
            sd.play(data, samplerate)
            sd.wait()          # blocks until this utterance finishes
        except Exception as e:
            print(f"[audio_play] playback error: {e}")
        finally:
            _play_queue.task_done()


# Start player thread once at startup
threading.Thread(target=_player_thread, daemon=True).start()


@app.route('/audio_play', methods=['POST'])
def audio_play():
    wav_bytes = request.data
    if not wav_bytes:
        return jsonify({"error": "empty body"}), 400

    # Validate it looks like a WAV (RIFF header)
    if len(wav_bytes) < 44 or wav_bytes[:4] != b'RIFF':
        return jsonify({"error": "not a valid WAV"}), 400

    _play_queue.put(wav_bytes)
    return jsonify({"status": "queued", "queue_depth": _play_queue.qsize()})
```

**Why a queue?**
The server can fire overlapping requests in rare cases (e.g. greeting + low-confidence
fallback racing). A queue ensures utterances play in order without clipping each other.

**Alternative — `aplay` subprocess (simpler, no Python audio deps):**
```python
import subprocess, tempfile, os

@app.route('/audio_play', methods=['POST'])
def audio_play():
    wav_bytes = request.data
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        f.write(wav_bytes)
        path = f.name
    # --quiet: suppress aplay output; runs async so response is immediate
    subprocess.Popen(['aplay', '--quiet', path],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({"status": "queued"})
```
Note: `aplay` uses the system ALSA device and handles 16 kHz PCM natively.
This does not serialise playback — multiple fast calls may overlap.
Use the `sounddevice` approach above if overlap is a problem.

---

### 2.2 `POST /set_active` — Push-Based Activation State

**Purpose:** Server pushes activation state to PI 5 whenever it changes, replacing the
original polling model (`GET /activate` on server). PI 5 stores the flag locally and
uses it to decide whether the robot should be in conversation mode.

#### Request
```json
POST /set_active
Content-Type: application/json

{ "active": 1 }    // 1 = conversation mode on, 0 = conversation mode off
```

#### Response
```json
{ "status": "ok", "active": 1 }
```

#### Implementation
```python
_active_flag = 0     # module-level state

@app.route('/set_active', methods=['POST'])
def set_active():
    global _active_flag
    body = request.get_json(force=True)
    val = body.get("active", 0)
    if val not in (0, 1):
        return jsonify({"error": "active must be 0 or 1"}), 400
    _active_flag = val
    print(f"[set_active] active = {_active_flag}")
    return jsonify({"status": "ok", "active": _active_flag})


@app.route('/active_status', methods=['GET'])
def active_status():
    """Optional read-back — useful for debugging from a browser."""
    return jsonify({"active": _active_flag})
```

#### When the server sends `/set_active`
| Event | `active` value | Reason |
|-------|---------------|--------|
| Registered person detected | `1` | Conversation starts |
| Farewell intent detected | `0` | Conversation ends, robot resumes roaming |
| Session timeout (600 s idle) | `0` | Sent on next `/detection` after expiry |
| Unregistered / Unknown person | `0` | No conversation |

---

## 3. Required Server-Side Change — Push Instead of Poll

The current server has PI 5 polling `GET /activate`. To switch to push, the server
needs to call `POST /set_active` on PI 5 whenever `_active` changes.

Add a helper to `api/routes/receiver.py`:

```python
async def _push_active(state: int, pi5_base_url: str) -> None:
    """Push activation state to PI 5 /set_active."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{pi5_base_url}/set_active",
                json={"active": state},
            )
    except Exception as exc:
        logger.warning("_push_active failed: %s", exc)
```

Call this wherever `_active` is set, e.g.:
```python
_active = 1
await _push_active(1, pi5_base_url)
```

> Until this is wired in, the existing `GET /activate` poll endpoint on the server
> remains as a fallback — PI 5 can still poll it if push is not yet implemented.

---

## 4. Health Check

```python
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "active": _active_flag,
        "queue_depth": _play_queue.qsize(),
    })
```

Server pings `GET http://PI5:5000/health` before sending WAV to detect if PI 5 is
reachable. If unreachable, server logs error and skips TTS send (robot stays silent
rather than crashing).

---

## 5. Dependencies (PI 5 venv)

```bash
pip install flask sounddevice soundfile
# soundfile needs libsndfile:
sudo apt-get install libsndfile1
```

Or if using `aplay` only:
```bash
pip install flask
# aplay is part of alsa-utils (usually pre-installed on Raspberry Pi OS):
sudo apt-get install alsa-utils
```

---

## 6. Startup

```bash
# Run PI 5 service (adjust port if needed)
python pi5_service.py
# or with gunicorn for production:
gunicorn --bind 0.0.0.0:5000 --workers 1 pi5_service:app
```

**Use 1 worker only** — the playback queue and `_active_flag` are in-process state.
Multiple workers would each have their own copy.

---

## 7. Endpoint Summary

| Method | Path | Called by | Purpose |
|--------|------|-----------|---------|
| POST | `/audio_play` | Server | Receive WAV bytes → play through speaker |
| POST | `/set_active` | Server | Update activation state (push model) |
| GET | `/active_status` | Debug / Server | Read back current active flag |
| GET | `/health` | Server | Liveness check before sending WAV |
| POST | `/tts_render` | Server (legacy) | Phoneme text → PI 5 TTS (Option B, superseded) |
| POST | `/navigation` | Server | ROS2 navigation commands (Teammate B) |

---

## 8. Latency Budget (Option A — Server WAV)

| Stage | Where | Target |
|-------|-------|--------|
| Satu GPU synthesis | Server | ~600 ms |
| WAV transfer (100 KB over LAN) | Network | ~10 ms |
| `/audio_play` response (queue) | PI 5 | < 5 ms |
| Audio playback | PI 5 speaker | real-time |
| **Total server → first sound** | | **~620 ms** |

> Measured synthesis: **633 ms** for a 6-word phrase (RTF 0.19×, GPU).
> Network transfer is negligible on a local LAN (100 KB ≈ 10 ms at 100 Mbps).
