# Baymax Face UI — Server-Side Integration Guide

This document describes the full API surface of the `baymax-face-ui` frontend running on the Raspberry Pi 5. It is intended for the server/backend team to understand what commands can be sent to drive the robot's face.

---

## System Architecture

```
  [Your Server]
       │
       │  HTTP POST /face_emotion  (port 7000)
       ▼
  ┌─────────────────────────────────┐
  │  FastAPI  (pi5_face_service.py) │   ← Primary external interface
  └────────────────┬────────────────┘
                   │  HTTP POST /set-expression  (port 8768, localhost only)
                   ▼
  ┌─────────────────────────────────┐
  │  Electron Main Process          │   ← IPC bridge (internal)
  └────────────────┬────────────────┘
                   │  Electron IPC (ipcMain → ipcRenderer)
                   ▼
  ┌─────────────────────────────────┐
  │  Face Renderer  (index.html)    │   ← Fullscreen display
  └─────────────────────────────────┘
```

The server should only communicate with the **FastAPI service on port 7000**. Everything below that is internal.

---

## Primary API — FastAPI (Port 7000)

Base URL: `http://<pi5-ip>:7000`

### `POST /face_emotion`

Sets the robot's facial expression by emotion code.

**Request body (JSON):**
```json
{ "emotion": <int> }
```

**Emotion codes:**

| Code | Expression | Visual Description |
|------|------------|--------------------|
| `0`  | `idle`     | Neutral resting face, subtle breathing animation, gentle eye drift |
| `1`  | `scanning` | Eyes sweep left–right, focused squint, mouth syncs with scan direction |
| `2`  | `happy`    | Wide smile, squinting eyes, sparkle particles around eyes |
| `3`  | `talking`  | Mouth cycles through vowel shapes (a/e/i/o/u) at 200 ms intervals |
| `4`  | `thinking` | One eyelid droops, iris floats slowly, small pursed mouth |

**Success response:**
```json
{ "status": "ok", "emotion": 3, "expression": "talking" }
```

**Error response (invalid code):**
```json
{ "detail": "Invalid emotion code. Use 0-4." }
```

**Example (curl):**
```bash
curl -X POST http://<pi5-ip>:7000/face_emotion \
     -H "Content-Type: application/json" \
     -d '{"emotion": 2}'
```

**Example (Python requests):**
```python
import requests

PI5_URL = "http://<pi5-ip>:7000"

def set_face(emotion_code: int):
    r = requests.post(f"{PI5_URL}/face_emotion", json={"emotion": emotion_code})
    r.raise_for_status()
    return r.json()

set_face(3)  # start talking
# ... TTS audio plays ...
set_face(0)  # back to idle
```

---

### `GET /health`

Returns the current state of the face service.

**Response:**
```json
{ "status": "ok", "face_emotion": 3, "expression": "talking" }
```

**Example:**
```bash
curl http://<pi5-ip>:7000/health
```

---

## Extended Expressions (Internal IPC Bridge — Port 8768)

The internal IPC bridge on `localhost:8768` supports **7 expressions** (a superset of the 5 via the public API). The server can call this endpoint directly **only from the Pi itself** (e.g. from `raspi_main.py` or another local process):

**Endpoint:** `POST http://127.0.0.1:8768/set-expression`

```json
{ "expression": "<name>" }
```

| Expression   | Trigger via `/face_emotion`? | Notes |
|--------------|:---------------------------:|-------|
| `idle`       | ✅ (code 0)                 | Default resting state |
| `scanning`   | ✅ (code 1)                 | Active search/detection |
| `happy`      | ✅ (code 2)                 | Greeting, positive response |
| `talking`    | ✅ (code 3)                 | While TTS audio is playing |
| `thinking`   | ✅ (code 4)                 | Processing / waiting for response |
| `surprised`  | ❌ (internal only)          | Wide eyes, O-shaped mouth |
| `warning`    | ❌ (internal only)          | Red glow + battery overlay |

> **Recommendation:** Expose `surprised` and `warning` via `/face_emotion` codes 5 and 6 when ready, by editing `pi5_face_service.py`.

---

## Recommended Usage Patterns

### Pattern 1 — Detection + Greeting Flow

```
Person detected (unknown)  →  POST /face_emotion {"emotion": 1}  (scanning)
Person identified          →  POST /face_emotion {"emotion": 2}  (happy)
TTS greeting starts        →  POST /face_emotion {"emotion": 3}  (talking)
TTS greeting ends          →  POST /face_emotion {"emotion": 0}  (idle)
```

### Pattern 2 — Query / LLM Response Flow

```
Keyword detected / wake word  →  emotion 4  (thinking — "processing your request")
LLM response ready            →  emotion 3  (talking — while TTS plays)
TTS ends                      →  emotion 0  (idle)
```

### Pattern 3 — No one present

```
Activation timeout  →  emotion 0  (idle — ambient state)
```

---

## Extending the API

### Adding a new emotion code

Edit `/home/natcha/Desktop/baymax-face-ui/src/pi5_face_service.py`:

```python
EMOTION_MAP = {
    0: "idle",
    1: "scanning",
    2: "happy",
    3: "talking",
    4: "thinking",
    5: "surprised",   # ← add here
    6: "warning",     # ← add here
}
```

No restart needed if you apply a hot-reload; otherwise `systemctl --user restart baymax-face-api`.

### Adding a new expression to the renderer

New expressions must be implemented in `/home/natcha/Desktop/baymax-face-ui/src/index.html`:

1. Add a case to `EyeController` — `set<Name>()` method
2. Add a case to `MouthController` — `set<Name>()` method
3. Register it in `RobotFace.setExpression()` switch block
4. Add the expression name to the valid set in `scripts/set_face.js`

---

## Eye Direction Control (Local / IPC only)

Eye gaze direction is currently only controllable via Electron IPC (not exposed over HTTP). To use it from a local process on the Pi, send a message via `scripts/set_face.js` or extend the FastAPI service.

**Valid directions:**

```
up  |  up-right  |  right
    |            |
 ───┼────────────┼───
    |            |
down| down-right | left
```

Full set: `up`, `down`, `left`, `right`, `up-left`, `up-right`, `down-left`, `down-right`, `center`

> **Suggestion:** Add `POST /face_direction {"direction": "left"}` endpoint to `pi5_face_service.py` to enable gaze control from the server (e.g. to look toward a detected person's position).

---

## Developer Screen (Runtime Debug)

A hidden developer overlay is accessible on the physical display by **tapping both eyes 5 times within 3 seconds** (touch screen) or via IPC `toggle-devscreen`.

It shows:
- Live expression name
- Battery percentage
- WiFi / Motors / ROS status chips
- Teleop D-pad for manual robot movement
- ROS map placeholder

These status values can be updated from the server via the IPC bridge if needed in future.

---

## Service Management

```bash
# Check status of all face services
systemctl --user status baymax-face-api baymax-face-ui

# Restart after code changes
systemctl --user restart baymax-face-api

# View live logs
journalctl --user -u baymax-face-api -f
journalctl --user -u baymax-face-ui -f
```

---

## Quick Reference

| What you want              | Call                                          |
|----------------------------|-----------------------------------------------|
| Robot is idle / standby    | `POST /face_emotion {"emotion": 0}`           |
| Robot is searching         | `POST /face_emotion {"emotion": 1}`           |
| Robot greets / is happy    | `POST /face_emotion {"emotion": 2}`           |
| Robot is speaking (TTS)    | `POST /face_emotion {"emotion": 3}`           |
| Robot is thinking / loading| `POST /face_emotion {"emotion": 4}`           |
| Check service is alive     | `GET  /health`                                |
