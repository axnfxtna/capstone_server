# PI 5 ROS2 Service Design — Navigation & Face Expression
## KhanomTan Robot — Teammate B (ROS2 Side)

**Scope:** This document is the implementation blueprint for the FastAPI service that
Teammate B runs on the Raspberry Pi 5 alongside ROS2. The server (GPU PC) pushes
commands to two endpoints: `/nav_state` and `/face_emotion`.

**Server IP:** `10.100.16.22:8000`
**PI 5 (ROS2) IP:** TBD — update `config/settings.yaml` `pi5_ros2.host` when known.

---

## 1. Overview

```
Server (GPU PC — 10.100.16.22:8000)             PI 5 ROS2 Service (<TBD_IP>:8767)
─────────────────────────────────────           ──────────────────────────────────────
POST /nav_state   ──── { state, dest? } ──────► ROS2 nav command (stop / roam / go_to)
POST /face_emotion ─── { emotion }      ──────► OLED / LCD face expression update
```

The server calls both endpoints independently. They can arrive in any order and
should each be handled without blocking the other.

---

## 2. Endpoint Specifications

### 2.1 `POST /nav_state` — Navigation State

**Purpose:** Set the robot's movement mode. State 2 includes a destination codename
that the PI 5 maps to a ROS2 waypoint name.

#### Request Schema
```json
{
  "state": 0,
  "destination": "e12-1"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `state` | int | always | `0` = stop, `1` = roam, `2` = navigate |
| `destination` | str | only when `state == 2` | Room codename (see §4) |

#### State Meanings
| Value | Label | ROS2 Action |
|-------|-------|-------------|
| `0` | Stop | Cancel current goal; halt all motor movement |
| `1` | Roaming | Resume autonomous roaming / patrol behaviour |
| `2` | Navigate | Cancel roaming; navigate to waypoint mapped from `destination` |

#### Response
```json
{ "status": "ok", "state": 0 }
```
Return **immediately** — do not block until navigation completes.

#### Validation
- If `state == 2` and `destination` is missing or unrecognised → return `400`
  ```json
  { "error": "unknown destination: xyz" }
  ```
- If `state` is not in `{0, 1, 2}` → return `400`

---

### 2.2 `POST /face_emotion` — Face Expression

**Purpose:** Update the robot's face display to reflect its current internal state.

#### Request Schema
```json
{ "emotion": 3 }
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `emotion` | int | always | Emotion code 0–4 (see table below) |

#### Emotion Codes
| Value | Label | Description |
|-------|-------|-------------|
| `0` | Normal | Neutral resting face |
| `1` | Searching | Eyes scanning left/right; robot is looking for someone |
| `2` | Happy | Smile; used on greeting and farewell |
| `3` | Talking | Animated mouth; robot is speaking |
| `4` | Thinking | Furrowed brow / loading indicator; LLM is processing |

#### Response
```json
{ "status": "ok", "emotion": 3 }
```

---

## 3. Server-Side Pipeline — When Each Command Is Sent

The server sends both `/nav_state` and `/face_emotion` at specific pipeline stages.
The table below is the authoritative mapping. Teammate B uses this to verify correct
behaviour during integration testing.

| Pipeline Stage | `/nav_state` state | `/face_emotion` emotion | Notes |
|----------------|--------------------|------------------------|-------|
| Server startup / idle | `1` (roam) | `1` (Searching) | Sent once at startup |
| Unknown / unregistered person detected | *(no change)* | `1` (Searching) | Robot keeps roaming |
| Registered person first detected (greeting) | `0` (stop) | `2` (Happy) | Stop robot; greet with smile |
| Pipeline processing (grammar + LLM running) | *(no change)* | `4` (Thinking) | Show thinking while LLM works |
| TTS speaking — chatbot reply or greeting | *(no change)* | `3` (Talking) | Mouth animates while WAV plays |
| TTS finished (after WAV send confirmed) | *(no change)* | `0` (Normal) | Return to neutral |
| Low-confidence STT fallback ("please repeat") | *(no change)* | `3` (Talking) | Playing "ขอโทษค่ะ ช่วยพูดอีกครั้ง" |
| Navigation intent confirmed | `2` (navigate) + dest | `0` (Normal) | Robot moves; neutral face while navigating |
| Arrival at destination (future) | `0` (stop) | `0` (Normal) | TBD — depends on ROS2 arrival callback |
| Farewell intent | `1` (roam) | `2` (Happy) | Say goodbye; robot resumes roaming |
| Session timeout (600 s idle) | `1` (roam) | `1` (Searching) | Session expired; back to patrol |

> **Note:** `*(no change)*` means the server does not send `/nav_state` for that stage —
> the robot stays in its current movement state. The server only sends nav commands
> when the state actually changes.

---

## 4. Room Codename → ROS2 Waypoint Mapping

The server sends a short codename string. Teammate B maintains this lookup table
locally and maps it to the ROS2 `NavigateToPose` waypoint name.

| Codename (server sends) | Waypoint Label | Location Description |
|-------------------------|---------------|---------------------|
| `e12-1` | `A` | *(fill in room name)* |
| `e12-2` | `B` | *(fill in room name)* |
| `e12-3` | `C` | *(fill in room name)* |
| *(add more as needed)* | | |

**How to add a new room:**
1. Add the codename → waypoint pair to the `ROOM_MAP` dict in the service (see §5).
2. Add the waypoint coordinates to the ROS2 nav map.
3. Tell the server team the new codename string so it can be added to the LLM's
   navigation intent parser.

---

## 5. Recommended Implementation

```python
"""
pi5_ros2_service.py — FastAPI service for ROS2 nav + face expression
Run with: uvicorn pi5_ros2_service:app --host 0.0.0.0 --port 8767
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn

app = FastAPI(title="PI5 ROS2 Service")

# ── Room codename lookup ──────────────────────────────────────────────────────
ROOM_MAP: dict[str, str] = {
    "e12-1": "A",
    "e12-2": "B",
    "e12-3": "C",
    # add more rooms here
}

# ── Request schemas ───────────────────────────────────────────────────────────
class NavStateRequest(BaseModel):
    state: int                      # 0=stop, 1=roam, 2=navigate
    destination: Optional[str] = None

class FaceEmotionRequest(BaseModel):
    emotion: int                    # 0=Normal, 1=Searching, 2=Happy, 3=Talking, 4=Thinking

# ── In-memory state ───────────────────────────────────────────────────────────
_current_state: int = 1            # start roaming
_current_emotion: int = 1          # start searching

# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.post("/nav_state")
async def nav_state(req: NavStateRequest):
    global _current_state
    if req.state not in (0, 1, 2):
        raise HTTPException(status_code=400, detail="state must be 0, 1, or 2")

    if req.state == 2:
        if not req.destination:
            raise HTTPException(status_code=400, detail="destination required for state 2")
        waypoint = ROOM_MAP.get(req.destination)
        if waypoint is None:
            raise HTTPException(status_code=400, detail=f"unknown destination: {req.destination}")
        _ros2_navigate_to(waypoint)          # implement this

    elif req.state == 1:
        _ros2_resume_roaming()               # implement this

    elif req.state == 0:
        _ros2_stop()                         # implement this

    _current_state = req.state
    print(f"[nav_state] state={req.state} dest={req.destination}")
    return {"status": "ok", "state": req.state}


@app.post("/face_emotion")
async def face_emotion(req: FaceEmotionRequest):
    global _current_emotion
    if req.emotion not in range(5):
        raise HTTPException(status_code=400, detail="emotion must be 0–4")

    _set_face(req.emotion)                   # implement this — OLED/LCD update
    _current_emotion = req.emotion
    print(f"[face_emotion] emotion={req.emotion}")
    return {"status": "ok", "emotion": req.emotion}


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "nav_state": _current_state,
        "face_emotion": _current_emotion,
    }


# ── ROS2 stubs — Teammate B fills these in ────────────────────────────────────
def _ros2_stop():
    """Cancel current nav goal and halt motors."""
    pass  # TODO

def _ros2_resume_roaming():
    """Re-enable autonomous roaming / patrol."""
    pass  # TODO

def _ros2_navigate_to(waypoint: str):
    """Send NavigateToPose goal for the named waypoint."""
    pass  # TODO

def _set_face(emotion: int):
    """Update OLED/LCD to show the given emotion frame."""
    pass  # TODO


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8767)
```

---

## 6. Server-Side Changes Required

The server must be updated to call both PI 5 endpoints. These calls are added to
`api/routes/receiver.py` alongside the existing `_push_active()` helper.

### 6.1 New config keys (`config/settings.yaml`)

```yaml
pi5_ros2:
  host: "TBD"      # ← Teammate B fills this in when PI 5 IP is known
  port: 8767
```

### 6.2 New helpers in `receiver.py`

```python
_PI5_ROS2_BASE = f"http://{cfg['pi5_ros2']['host']}:{cfg['pi5_ros2']['port']}"

async def _push_nav_state(state: int, destination: str | None = None) -> None:
    payload = {"state": state}
    if destination:
        payload["destination"] = destination
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{_PI5_ROS2_BASE}/nav_state", json=payload)
    except Exception as exc:
        logger.warning("_push_nav_state failed: %s", exc)

async def _push_face_emotion(emotion: int) -> None:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{_PI5_ROS2_BASE}/face_emotion", json={"emotion": emotion})
    except Exception as exc:
        logger.warning("_push_face_emotion failed: %s", exc)
```

### 6.3 Call sites in the `/detection` pipeline

| Location in pipeline | Calls to add |
|---------------------|------|
| Registered person found, session created | `_push_nav_state(0)` + `_push_face_emotion(2)` |
| Before grammar + LLM call | `_push_face_emotion(4)` |
| Before sending TTS WAV | `_push_face_emotion(3)` |
| After TTS confirmed (`200 OK`) | `_push_face_emotion(0)` |
| Low-conf STT fallback TTS | `_push_face_emotion(3)` |
| `navigate` intent detected | `_push_nav_state(2, destination)` + `_push_face_emotion(0)` |
| `farewell` intent detected | `_push_nav_state(1)` + `_push_face_emotion(2)` |
| Session timeout | `_push_nav_state(1)` + `_push_face_emotion(1)` |

---

## 7. Emotion Sequence — Full Conversation Example

```
[Robot roaming]                      nav=1, face=1 (Searching)
[Palm detected]                      nav=0, face=2 (Happy)        ← stop + smile
[Greeting TTS playing]               nav=0, face=3 (Talking)
[Greeting done]                      nav=0, face=0 (Normal)
[STT received, LLM processing]              face=4 (Thinking)
[Chatbot reply ready, TTS starts]           face=3 (Talking)
[TTS done]                           nav=0, face=0 (Normal)
[Palm: "พาไปห้อง e12-1"]
[LLM: navigate intent]               nav=2 dest=e12-1, face=0 (Normal)
[Robot navigating ...]
[Palm: "ลาก่อน"]
[LLM: farewell intent]               nav=1, face=2 (Happy)        ← goodbye + smile
[Session ends, back to roaming]      nav=1, face=1 (Searching)
```

---

## 8. Dependencies (PI 5 venv)

```bash
pip install fastapi uvicorn pydantic
# ROS2 packages installed via apt (ros-humble-nav2-msgs etc.)
```

---

## 9. Startup

```bash
# Activate ROS2 environment first
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash

# Then run the FastAPI service
uvicorn pi5_ros2_service:app --host 0.0.0.0 --port 8767
```

---

## 10. Endpoint Summary

| Method | Path | Called by | Purpose |
|--------|------|-----------|---------|
| POST | `/nav_state` | Server | Set robot movement state (0=stop / 1=roam / 2=navigate) |
| POST | `/face_emotion` | Server | Set face expression (0=Normal … 4=Thinking) |
| GET | `/health` | Server / debug | Read back current nav state and emotion |
