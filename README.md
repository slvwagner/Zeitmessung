# Zeitmessung

`Zeitmessung` is a lap / race time measurement system using Pico 2 W microcontrollers. It consists of **StartGates** and **FinishGates** with laser/light barriers, RFID for racer identification, OLED status displays, and a PHP/MySQL backend with a Shiny/Web frontend.

---

## Architecture

| Component | Purpose |
|---|---|
| **StartGate** | Detects beam break, reads RFID tag, logs start time via WiFi. Enforces configurable headway between starts. |
| **FinishGate** | Detects beam break at finish line, sends finish time to backend. |
| **OLED Display** | Shows status, locked startnummer, or finish time. |
| **RFID (RC522)** | Identifies each racer at the start via their RFID tag. |
| **Backend (PHP + MySQL)** | Collects start/finish times, stores participants and race parameters. |
| **Frontend (Shiny / Web)** | Participant registration, race management, disqualifications, results. |

---

## GPIO / Wiring (Pico 2 W)

| Function | Interface | Pin |
|---|---|---|
| Beam 1 (primary timing beam) | GPIO input, pull-down, rising-edge = break | **GP2** |
| Beam 2 (second timing beam, debounce reference) | GPIO input, pull-down, rising-edge = break | **GP3** |
| Cancel / Stop button | GPIO input, pull-up, active LOW | **GP14** |
| On-board Status LED | GPIO output | `"LED"` |
| External LED (optional) | GPIO output | **GP15** |
| OLED Display (SSD1306) | I²C — SDA / SCL | **GP4** / **GP5** (addr `0x3C`) |
| RFID Reader (RC522) | SPI — SCK / MOSI / MISO / CS / RST | **GP10** / **GP11** / **GP12** / **GP13** / **GP22** |

---

## Behaviour

- Beam pins (GP2, GP3) use **PULL_DOWN**; idle = LOW. A beam break drives the pin **HIGH** (`BEAM_BREAK_LEVEL = 1`).
- The PIO program waits for GP2 LOW → HIGH (break start), counts clock cycles until GP3 goes HIGH, then fires.
- Button (GP14) uses **PULL_UP**, active LOW. Short press: cancel/unlock. Long press: shutdown or show log.
- **Headway** (minimum gap between consecutive starts) is configurable via the backend (`device_params.php`).
- Device parameters are fetched centrally from the backend — no per-device config files.
- Time sync via WiFi/NTP; millisecond resolution.

---

## Software Structure

```
source/
├── pico2 W/micropython/project/   # MicroPython source + build/deploy scripts
│   ├── start_gate.py              # StartGate logic
│   ├── finish_gate.py             # FinishGate logic
│   ├── common.py                  # Shared helpers
│   ├── rc522_lowlevel.py          # RFID driver
│   ├── OLED.py                    # Display driver
│   ├── DMX_controller.py          # DMX output support
│   ├── build_firmware.sh          # Build custom MicroPython UF2
│   ├── full_update.sh             # Build + flash + sync in one step
│   └── sync_pico.sh               # Upload Python files via mpremote
├── Server_admin/xampp/            # PHP API endpoints
├── Server_admin/www_register/     # Participant registration web app
└── Server_admin/www_check_registrations/  # Race dashboard
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

- **WiFi credentials:** copy `credentials_template.py` → `credentials.py` and fill in SSID / password.
- **Server endpoints & headway:** managed centrally via `device_params.php` on the backend.
- **I²C address / bus:** adjust in `OLED.py` if using a different display model.

