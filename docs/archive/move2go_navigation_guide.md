# ired_move2go & ired_navigation — Implementation Guide

> Based on the design specification in `docs/design.md`
> Robot: KhanomTan | Platform: Raspberry Pi 5 + ROS2 Jazzy

---

## 1. System Architecture

```
GPU Server (10.100.16.22:8000)
        │
        │  HTTP POST /nav_state   { state, destination? }
        │  HTTP POST /face_emotion { emotion }
        ▼
PI 5 — pi5_service  (FastAPI, port 8767)           ← ired_move2go
        │
        │  /navigation_command  (std_msgs/String)
        │  /face_command        (std_msgs/String)
        ▼
navigation_manager node                            ← ired_move2go
        │s
        │  BasicNavigator API (action client)
        ▼
Nav2 stack (ired_navigation)
        │
        │  /cmd_vel  (TwistStamped)
        ▼
Robot hardware (ired_bringup → motors)
```

There are three layers:

| Layer | Package | Role |
|---|---|---|
| HTTP bridge | `ired_move2go` (`pi5_service.py`) | Accepts HTTP from GPU server, publishes ROS2 commands |
| Nav controller | `ired_move2go` (`navigation_manager.py`) | Receives ROS2 commands, drives Nav2 |
| Nav2 stack | `ired_navigation` | AMCL localization, path planning, local control |

---

## 2. Navigation States (from design.md §2.1)

| State value | Label | What the robot does |
|---|---|---|
| `0` | Stop | Cancel current goal, halt all movement |
| `1` | Roam | Cycle through patrol waypoints continuously |
| `2` | Navigate | Cancel roaming, go to the destination room |

### State transitions

```
                 state=1 (roam)
   IDLE ────────────────────────────► ROAMING
     ▲                                    │
     │  state=0 (stop)                    │ state=0 (stop)
     │◄───────────────────────────────────┤
     │                                    │ state=2 (navigate)
     │◄──────── NAVIGATING:<room> ◄───────┘
     │               │
     │  SUCCEEDED     │  FAILED
     └───────────────┘
```

---

## 3. HTTP Endpoints (pi5_service.py)

### POST /nav_state

Sent by the GPU server to change the robot's movement state.

```json
// Go to room A directly
{ "state": 2, "dest": "A" }

// Start roaming
{ "state": 1 }

// Stop
{ "state": 0 }
```

Response (always immediate — does not wait for nav to complete):
```json
{ "status": "ok", "state": 2 }
```

Errors:
- `400` if state not in {0, 1, 2}
- `400` if state=2 and `dest` is missing or unknown

### POST /face_emotion

```json
{ "emotion": 3 }
```

| Code | Label | When |
|---|---|---|
| 0 | Normal | Idle / after TTS |
| 1 | Searching | Roaming, looking for person |
| 2 | Happy | Greeting / farewell |
| 3 | Talking | TTS playing |
| 4 | Thinking | LLM processing |

### GET /health

Returns current state for debugging:
```json
{ "status": "ok", "nav_state": 1, "face_emotion": 1 }
```

---

## 4. Room Codename Mapping

The GPU server sends short codenames. `pi5_service.py` maps them to ROS2 goal names.
Edit the `ROOM_MAP` dict in `pi5_service.py` and add matching poses in `goals.yaml`.

| Server codename | ROS2 goal name | Location |
|---|---|---|
| `e12-1` | `A` | *(fill in)* |
| `e12-2` | `B` | *(fill in)* |
| `e12-3` | `C` | *(fill in)* |

### Adding a new room

1. Add to `ROOM_MAP` in `pi5_service.py`:
   ```python
   ROOM_MAP = {
       ...
       'e12-4': 'D',
   }
   ```

2. Add pose to `param/goals.yaml`:
   ```yaml
   goal_names: ["home", "A", "B", "C", "D"]
   D_pose: [12.0, 5.0, 0.0]   # measure from RViz /amcl_pose
   ```

3. Tell the GPU server team the new codename string.

4. Rebuild:
   ```bash
   cd ~/robot_cap
   colcon build --packages-select ired_move2go
   ```

---

## 5. Patrol (Roam) Mode

When state=1, `navigation_manager` cycles through `patrol_waypoints` in order.
Configure in `param/goals.yaml`:

```yaml
patrol_waypoints: ["A", "B", "C"]   # visits A → B → C → A → B → ...
```

The patrol continues until state=0 (stop) is received.

---

## 6. ROS2 Topics

| Topic | Type | Direction | Purpose |
|---|---|---|---|
| `/navigation_command` | `std_msgs/String` | → navigation_manager | Commands: `stop`, `roam`, `A`, `B`, … |
| `/navigation_status` | `std_msgs/String` | navigation_manager → | Status feedback |
| `/face_command` | `std_msgs/String` | → face display node | Emotion code as string |

**Status values published to `/navigation_status`:**

| Value | Meaning |
|---|---|
| `IDLE` | Ready, no active task |
| `STOPPED` | Explicitly stopped |
| `ROAMING` | Patrol mode active |
| `ROAMING:<goal>` | Currently heading to `<goal>` within patrol |
| `NAVIGATING:<goal>` | Heading to specific destination `<goal>` |
| `SUCCEEDED:<goal>` | Reached `<goal>` (briefly, then → IDLE) |
| `FAILED:<goal>` | Nav2 could not reach `<goal>` (briefly, then → IDLE) |

---

## 7. How to Run

### Prerequisites

```bash
# Install FastAPI and uvicorn if not present
pip install fastapi uvicorn
```

### Terminal 1 — Robot hardware

```bash
ros2 launch ired_bringup bringup.launch.py
```

### Terminal 2 — Navigation stack

```bash
ros2 launch ired_navigation navigation.launch.xml
```

### Terminal 3 — Navigation manager + PI5 HTTP service

```bash
ros2 launch ired_move2go pi5_service.launch.py
```

This starts both nodes:
- `navigation_manager` — waits for Nav2 to be active, then listens for commands
- `pi5_service` — starts FastAPI on `0.0.0.0:8767`

### Test from command line (no GPU server needed)

```bash
# Stop
curl -X POST http://localhost:8767/nav_state -H "Content-Type: application/json" -d '{"state": 0}'

# Start roaming
curl -X POST http://localhost:8767/nav_state -H "Content-Type: application/json" -d '{"state": 1}'

# Navigate to room A
curl -X POST http://localhost:8767/nav_state -H "Content-Type: application/json" -d '{"state": 2, "dest": "A"}'

# Navigate to room B
curl -X POST http://localhost:8767/nav_state -H "Content-Type: application/json" -d '{"state": 2, "dest": "B"}'

# Navigate to room C
curl -X POST http://localhost:8767/nav_state -H "Content-Type: application/json" -d '{"state": 2, "dest": "C"}'

# Go home
curl -X POST http://localhost:8767/nav_state -H "Content-Type: application/json" -d '{"state": 2, "dest": "home"}'

# Set face to Thinking
curl -X POST http://localhost:8767/face_emotion -H "Content-Type: application/json" -d '{"emotion": 4}'

# Health check
curl http://localhost:8767/health
```

---

## 8. Measuring Goal Poses

To get the correct x, y, yaw for each room:

1. Launch bringup + navigation.
2. Open RViz: `ros2 launch ired_navigation navigation.launch.xml rviz:=true`
3. Drive the robot to the target room with teleop.
4. Read the current pose:
   ```bash
   ros2 topic echo /amcl_pose --once
   ```
5. Note `position.x`, `position.y`, and convert quaternion to yaw:
   ```python
   import math
   # yaw = atan2(2*(w*z), 1 - 2*z*z)  where w,z are from orientation
   ```
6. Enter the values into `param/goals.yaml`.

---

## 9. Known Issues to Resolve

See `docs/progress.md` for full task list.

| Issue | Impact | Location |
|---|---|---|
| `base_footprint` vs `base_link` mixed in navigation.yaml | TF errors | `ired_navigation/param/navigation.yaml` |
| `imu_link` missing from URDF | TF warning | `ired_bringup/urdf/ired.urdf.xacro` |
| Dual `/odom` publishers (odom.cpp + laser_scan_matcher) | Conflicting odometry | `ired_bringup` |
| Room poses A/B/C not set yet | Robot won't navigate correctly | `ired_move2go/param/goals.yaml` |
| face_command subscriber not implemented yet | Face display won't update | face display package (TBD) |
