# Face UI Integration — PI 5 Implementation Guide

This document describes the agreed emotion state contract between the server pipeline and the Baymax face UI.
**The PI 5 is responsible for all face emotion transitions.** The server does not call the face service.

---

## Architecture

```
PI 5 (Raspberry Pi 10.26.9.196)
  ├── raspi_main.py          ← detection + audio logic
  ├── baymax-face-api        ← face service on port 7000
  └── speaker / audio player ← plays WAV received from server
```

The PI 5 both triggers pipeline events (sending to the server) and receives results back (WAV audio). Since it owns both the input and output, it has full visibility into the emotion lifecycle and manages all face transitions locally.

---

## Emotion Codes

| Code | Expression | When to use |
|------|-----------|-------------|
| `0` | `idle` | Default resting state — no active interaction |
| `1` | `scanning` | Robot is navigating to a destination |
| `2` | `happy` | Person detected / greeting fired |
| `3` | `talking` | Robot is speaking (audio playback in progress) |
| `4` | `thinking` | Waiting for server LLM response |

API call:
```python
POST http://localhost:7000/face_emotion
{ "emotion": <int> }
```

---

## Required Emotion Transitions (per interaction type)

### Greeting (registered student or unknown visitor)
```
PI5 detects person
  → POST /face_emotion {"emotion": 2}   (happy)
  → POST server /greeting
  ← server responds, WAV arrives at /audio_play
  → POST /face_emotion {"emotion": 3}   (talking)   ← when audio playback starts
  → POST /face_emotion {"emotion": 0}   (idle)       ← when audio playback ends
```

### Query — chat / info / farewell
```
PI5 receives speech (STT)
  → POST /face_emotion {"emotion": 4}   (thinking)  ← before sending to server
  → POST server /detection
  ← server responds, WAV arrives at /audio_play
  → POST /face_emotion {"emotion": 3}   (talking)   ← when audio playback starts
  → POST /face_emotion {"emotion": 0}   (idle)       ← when audio playback ends
```

### Navigate intent
```
PI5 receives speech (STT)
  → POST /face_emotion {"emotion": 4}   (thinking)  ← before sending to server
  → POST server /detection
  ← server responds, WAV arrives at /audio_play
  → POST /face_emotion {"emotion": 3}   (talking)   ← when audio playback starts
  → POST /face_emotion {"emotion": 1}   (scanning)  ← when audio ends, robot starts moving
  → POST /face_emotion {"emotion": 0}   (idle)       ← when robot arrives at destination
```

> **Note:** The "arrived at destination" trigger for `idle` depends on ROS2 Nav2 goal completion callback (Teammate B). Until that is implemented, setting `idle` on a timeout or leaving `scanning` until the next interaction are both acceptable.

---

## Key Implementation Notes

### 1. Talking → Idle timing
Set `talking (3)` when your audio player **starts** playback (not when the WAV is received).
Set `idle (0)` when playback **ends** — this is accurate because the PI 5 speaker and face display are on the same hardware.

The server does **not** send any face emotion — it only sends WAV bytes to `/audio_play`. Timing is fully owned by the PI 5 audio player.

### 2. Thinking before sending to server
Set `thinking (4)` immediately when the STT result is ready and you are about to POST to the server. This gives visual feedback during the LLM processing time (~3–15s).

### 3. Happy on greeting
Set `happy (2)` as soon as a registered person (or visitor) is identified, before POSTing to `/greeting`. This makes the robot look responsive even while the LLM generates the greeting text.

### 4. Fire-and-forget face calls
Face calls should never block the main pipeline. Use a background task or thread so that a slow or unresponsive face service does not delay audio delivery.

---

## Verifying the face service

```bash
# Check the face service is running
systemctl --user status baymax-face-api baymax-face-ui

# Test health endpoint
curl http://localhost:7000/health
# → {"status": "ok", "face_emotion": 0, "expression": "idle"}

# Manually set an emotion
curl -X POST http://localhost:7000/face_emotion \
     -H "Content-Type: application/json" \
     -d '{"emotion": 2}'
# → {"status": "ok", "emotion": 2, "expression": "happy"}

# View live logs
journalctl --user -u baymax-face-api -f
```

---

## Quick reference

| Moment | Emotion |
|--------|---------|
| Person detected / greeting about to fire | happy (2) |
| STT ready, POSTing to server | thinking (4) |
| Audio playback starts | talking (3) |
| Audio playback ends (chat / info / farewell) | idle (0) |
| Audio playback ends (navigate intent) | scanning (1) |
| Robot arrives at destination | idle (0) |
