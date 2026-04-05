# [Zeitmessung] Project Version: 0.1.0
# Zeitmessung

Zeitmessung is a modular lap and race time measurement system built around Raspberry Pi Pico 2 W microcontrollers. The system is designed for sports events and features:

- **StartGates** and **FinishGates** with dual-beam laser/light barriers for precise timing
- **RFID** for racer identification at the start
- **OLED displays** for real-time status and feedback
- **WiFi** connectivity for time sync and backend communication
- **Backend** (PHP/MySQL) for data collection, race management, and results
- **Frontend** (Shiny/Web) for registration, live dashboards, and result display

The system is highly configurable and supports robust, low-latency timing using custom MicroPython firmware and native C modules for hardware control.

---

## System Overview

The Zeitmessung system consists of:

- **StartGate**: Detects beam break, reads RFID, logs start time, enforces headway
- **FinishGate**: Detects finish beam break, logs finish time
- **RFID (RC522)**: Identifies racers at the start
- **OLED Display**: Shows status, racer info, or finish time
- **Backend**: Collects and manages all race data
- **Frontend**: Registration, management, and results

For a detailed technical description of the firmware, hardware architecture, and timing logic, **see the [MicroPython Project README](source/pico2%20W/micropython/project/README.md)**.

---

## Hardware (Pico 2 W)

| Function | Interface | Pin |
|---|---|---|
| Beam 1 (timing) | GPIO input, pull-down | **GP2** |
| Beam 2 (debounce) | GPIO input, pull-down | **GP3** |
| Cancel/Stop button | GPIO input, pull-up | **GP14** |
| On-board LED | GPIO output | `LED` |
| External LED | GPIO output | **GP15** |
| OLED Display | I²C (0x3C) | **GP4** / **GP5** |
| RFID Reader | SPI | **GP10/11/12/13/22** |

---

## Key Features

- Dual-beam timing with PIO and DMA for microsecond accuracy
- Native C modules for DMX and RFID (RC522) support
- Automatic WiFi/NTP time sync
- Centralized configuration via backend
- One-step firmware build, flash, and Python file sync

---

## Quick Start

All firmware and deployment scripts are in `source/pico2 W/micropython/project/`. See the [detailed project README](source/pico2%20W/micropython/project/README.md) for build, update, and architecture details.

---

## Software Structure

...existing code...
- Time sync via WiFi/NTP; millisecond resolution.

---

## Software Structure

```
source/
├── pico2 W/
│   ├── create credentials.R
│   ├── credentials_template.py
│   └── micropython/
│       └── project/
│           ├── build_firmware.sh           # Build custom MicroPython UF2
│           ├── common.py                   # Shared helpers
│           ├── DMX_controller.py           # DMX output support
│           ├── DMX_native_wrapper.py       # Native DMX wrapper
│           ├── DMX_PIO_DMA.py              # PIO/DMA timing
│           ├── finish_gate.py              # FinishGate logic
│           ├── full_update.sh              # Build + flash + sync in one step
│           ├── OLED.py                     # Display driver
│           ├── pico_sdk_import.cmake       # Pico SDK import
│           ├── rc522_lowlevel.py           # RFID driver
│           ├── README.md                   # MicroPython project docs
│           ├── squarewave generator.py     # Squarewave generator
│           ├── start_gate.py               # StartGate logic
│           ├── sync_pico.sh                # Upload Python files via mpremote
│           └── native_modules/             # Native C modules
│               ├── dmx_native/
│               ├── dualbeam_native/
│               ├── rc522_native/
│               └── zeitmessung.cmake
├── OS_support/                  # R helper scripts and templates
├── Server_admin/
│   ├── xampp/                   # PHP API endpoints
│   ├── www_register/            # Participant registration web app
│   └── www_check_registrations/ # Race dashboard
└── SQL/                         # Database scripts
```

---

## Firmware Build & Deployment

All scripts live in `source/pico2 W/micropython/project/`. Disconnect any serial monitor / VS Code Pico extension before running.

### One-shot full update (recommended)

```bash
./full_update.sh
```

This builds the firmware, flashes it via USB mass storage, and syncs all Python files — automatically detecting the serial port.

**Options:**

| Flag | Effect |
|---|---|
| `--no-flash` | Skip firmware flash; only build and sync Python files |
| `--core` | Sync only `DMX_controller.py` and `DMX_native_wrapper.py` |
| `--port=/dev/ttyACM0` | Use a specific serial port instead of auto-detect |

### Build firmware only

```bash
./build_firmware.sh
```

Builds a custom MicroPython UF2 for `RPI_PICO2_W` with native C modules (`dmx_native`, `rc522_native`). Output is placed in `project/firmware/`.

### Sync Python files only

```bash
./sync_pico.sh [--all-py|--core] [--port=auto|/dev/ttyACM0]
```

Uploads `.py` files to the board filesystem using `mpremote`.

**Install mpremote if missing:**

```bash
/usr/bin/python3 -m pip install --user --break-system-packages mpremote
```

---

## Configuration

- **WiFi credentials:** copy `source/pico2 W/credentials_template.py` → `source/pico2 W/credentials.py` and fill in SSID / password.
- **Server endpoints & headway:** managed centrally via `Server_admin/xampp/device_params.php` on the backend.
- **I²C address / bus:** adjust in `source/pico2 W/micropython/project/OLED.py` if using a different display model.

