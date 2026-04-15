# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Academic robotics project: automated package classification using **LEGO MINDSTORMS NXT** and **QR code** vision. Full requirements in `Untitled_Document.md`.

Each package has a QR sticker (`QR1`, `QR2`, etc.). The camera reads it, and the NXT robot drives slowly forward until it passes over the matching floor QR at the destination, then drops the package and reverses home.

## System Architecture

```
Capa 1 (Capture)   → Camera reads QR on the package → determines destination
Capa 2 (Logic)     → router.py validates hole number against config/destinations.yaml
Capa 3 (Execution) → NXT advances until floor QR disappears under robot → drops → returns
Capa 4 (Coord)     → Orchestrator (MQTT) authorizes cycles, logs events, serves dashboard
```

## Robot Hardware (NXT via USB)

The NXT is controlled **from the PC** using `nxt-python`. Nothing is installed on the brick.

| Motor | Port | Function |
|-------|------|----------|
| Motor A | OUT A | Tilting platform — drops the package |
| Motor B | OUT B | Left wheel |
| Motor C | OUT C | Right wheel |

**Delivery logic** (`nxt/nxt_controller.py`):
1. Reset tacho counter → start motors at `advance_power`
2. Phase 1: scan floor with camera until **target QR is seen**
3. Phase 2: keep moving until target QR **disappears** (robot is on top) for `frames_on_top` consecutive frames
4. Brake → tilt Motor C → restore Motor C
5. Reverse motors by exact tacho distance → home

## QR Codes

Two sets of QR codes, **same content** per destination:
- **Package QR** — sticker on top of package, read while robot is at home
- **Floor QR** — placed on the ground at each hole, used as the stop signal

Content format: `QR1`, `QR2`, `QR3` (or plain `1`, `2`, `3` — both work).

## Technology Stack

| Layer | Technology |
|-------|-----------|
| NXT control | nxt-python ≥ 3.3 (USB, runs on PC) |
| QR reading | opencv-python + pyzbar |
| Communication | paho-mqtt + Mosquitto broker |
| Orchestrator | paho-mqtt, FastAPI, uvicorn |
| Dashboard + calibration UI | FastAPI WebSocket + HTML/JS at `http://localhost:8000` |
| Config | PyYAML |

## Key Files

| File | Purpose |
|------|---------|
| `nxt/nxt_controller.py` | Motor control + QR-guided delivery loop |
| `station/main.py` | State machine: IDLE→DETECTING→CLASSIFYING→WAITING_AUTH→EXECUTING→RETURNING |
| `station/vision/classifier.py` | QR reader (pyzbar preferred, OpenCV fallback) |
| `station/decision/router.py` | Validates hole number from QR against `valid_holes` in destinations.yaml |
| `station/comms/mqtt_client.py` | Publishes status/events, requests auth from orchestrator |
| `orchestrator/server.py` | Authorizes cycles, logs to `orchestrator/events.csv` |
| `orchestrator/dashboard/app.py` | Dashboard UI + `/calibration` GET/POST endpoints |
| `config/calibration.yaml` | Runtime-editable motor parameters (written by dashboard) |
| `config/destinations.yaml` | `valid_holes` list — holes that exist physically on the robot |

## Commands

```bash
# Install dependencies (libzbar must be installed first at OS level)
#   macOS:  brew install zbar
#   Linux:  sudo apt install libzbar0
pip install -r requirements.txt

# Verify NXT is detected
python -c "import nxt.locator; b = nxt.locator.find(); print(b.get_device_info())"

# Start MQTT broker
mosquitto -v

# Start orchestrator
python orchestrator/server.py

# Start dashboard (http://localhost:8000 — includes calibration panel)
python orchestrator/dashboard/app.py

# Start station (NXT connected via USB)
python station/main.py --station-id 1

# Start station in simulation mode (no NXT or camera needed)
python station/main.py --simulate
```

## Calibration

Motor parameters are in `config/calibration.yaml` and editable live from the dashboard UI without restarting. The NXT controller reads the file at the start of every delivery cycle.

| Parameter | Effect |
|-----------|--------|
| `advance_power` | Forward speed — lower = more precise stop |
| `return_power` | Return-to-home speed |
| `tilt_power` | Motor C force |
| `tilt_degrees` | How far Motor C tilts to drop the package |
| `frames_on_top` | Consecutive no-QR frames before stopping — raise if robot stops early |

## Operational Cycle (per station)

1. Package placed at home → camera reads package QR
2. Router validates destination hole exists in `valid_holes`
3. Orchestrator authorizes (checks station is in `waiting_auth` state)
4. NXT advances slowly, camera reads floor QRs
5. Target floor QR seen → phase 2: keep moving until QR disappears → stop
6. Motor C drops package → restores → NXT reverses home (tacho-based)
7. Event logged to `orchestrator/events.csv` with cycle time
8. Station → IDLE

## Key Constraints

- Station blocks new cycles while robot is in motion (RF-07)
- Orchestrator only authorizes when station status is `waiting_auth`
- `valid_holes` in `destinations.yaml` defines physically existing holes — QR numbers outside this list are rejected
- Calibration changes apply on the next cycle with no restart needed
