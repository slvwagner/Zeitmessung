# Zeitmessung MicroPython Project

Custom MicroPython build for Pico 2 W with timing measurement hardware control.

## Quick Start

### Clone the Main Repository

The micropython submodule is managed at the **Zeitmessung root level** (parent directory). Always clone from there:

```bash
cd ~/SW-Entwicklung
git clone --recursive <zeitmessung-repo-url>
```

This automatically downloads the micropython submodule for all projects including this one.

### Or If Already Cloned

If you cloned Zeitmessung without `--recursive`, initialize submodules:

```bash
cd ~/SW-Entwicklung/Zeitmessung
git submodule update --init --recursive
```

This loads micropython for all projects.

### Navigate to Project

```bash
cd source/"pico2 W"/micropython/project
```

## Building Firmware

Build the customized MicroPython firmware with Zeitmessung banner:

```bash
./build_firmware.sh
```

**Output files:**
- `firmware/firmware-RPI_PICO2_W.uf2` — UF2 format (flashable via USB)
- `firmware/firmware-RPI_PICO2_W.bin` — Binary format
- `firmware/firmware-RPI_PICO2_W.hex` — Hex format

## Daily Update Workflow (Recommended)

Use the wrapper script to run your existing firmware build and then sync Python files to the Pico in one step:

Prerequisite (one-time):

```bash
/usr/bin/python3 -m pip install --user --break-system-packages mpremote
```

```bash
./full_update.sh
```

Useful options:

```bash
# Upload only DMX core files
./full_update.sh --core

# Use another serial port
./full_update.sh --port=/dev/ttyACM0
```

What this does:
1. Runs `build_firmware.sh` to compile firmware
2. Reboots Pico into bootloader mode (`machine.bootloader()`) and flashes UF2 via USB mass storage
3. Waits for Pico to come back, then runs `sync_pico.sh` to upload Python files
4. Soft-resets the Pico and verifies DMX native API (`start_code`)

To skip flashing (Python files only, firmware already flashed):

```bash
./full_update.sh --no-flash
```

### Scripts

- `build_firmware.sh` — builds firmware and writes files into `firmware/`
- `sync_pico.sh` — uploads Python files to Pico with `mpremote`
- `full_update.sh` — build + sync in one command

## Important: VS Code Pico Extension Lock

If serial/REPL is connected (for example MicroPico vREPL), upload can fail because the port is busy.

Before running `sync_pico.sh` or `full_update.sh`:
1. Disconnect Pico extension REPL/serial monitor
2. Confirm port is free:

```bash
lsof /dev/ttyACM0
```

If command prints no output, the port is free.

## Python-Only Sync (Without Rebuild)

If firmware is already flashed and you only changed `.py` files:

```bash
./sync_pico.sh
```

Optional:

```bash
# Upload only DMX core files
./sync_pico.sh --core

# Force a specific serial port
./sync_pico.sh --port=/dev/ttyACM0
```

## Flashing to Pico 2 W (Manual)

`full_update.sh` flashes automatically. To flash manually:

1. **Hold BOOTSEL** button on Pico 2 W
2. **Plug in via USB** (while holding BOOTSEL)
3. A USB drive appears (RP2350)
4. **Copy** the `.uf2` file to the drive
5. Device reboots with new firmware

## REPL Welcome Message

The custom firmware displays:
```
MicroPython ... ; Firmware for ZeitmessungRaspberry Pi Pico 2 W (built YYYY-MM-DD HH:MM:SS)
```

This confirms you have the project-specific firmware.

## IRQ & Timing Architecture

Both `start_gate.py` and `finish_gate.py` use the same PIO-based dual-beam timing design.

### PIO State Machine (primary timing)

| Parameter | Value |
|---|---|
| State machine | PIO1 SM5 (`BEAM1_SM_ID = 5`) |
| Clock frequency | 2 MHz (`PIO_DUAL_FREQ_HZ`) — 0.5 µs per cycle |
| Beam 1 input | GP2, `PULL_DOWN`, idle LOW, break = HIGH |
| Beam 2 input | GP3, `PULL_DOWN`, used as `jmp_pin` |
| Debounce cycles | 8 (`PIO_DUAL_DEBOUNCE_CYCLES`) |
| Max measurable interval | ~35 min (32-bit counter at 2 MHz) |

**PIO program flow (`dual_beam_measure_irq`):**
1. Receives debounce count from `sm.put()` into OSR.
2. Waits for GP2 LOW (idle) → GP2 HIGH (beam 1 broken) — starts counting down from `0xFFFFFFFF`.
3. On each cycle: `jmp(pin)` checks GP3 (beam 2) via `jmp_pin`. If GP3 goes HIGH → enter debounce loop.
4. After debounce passes, pushes elapsed count to RX FIFO and fires `irq(0)`.
5. If counter hits zero before GP3 (timeout/overflow), pushes count and fires `irq(0)` with overflow flag.
6. Resets: waits for both beams LOW before arming next measurement (finish gate only — prevents double-trigger).

**IRQ handler (`_pio_dual_irq_handler`):**
- Runs in interrupt context (ISR-safe, no allocation).
- Records `time.ticks_us()` into a lock-free ring buffer (8 slots).
- If buffer full, increments `_pio_done_dropped` and discards.

**Main loop:**
- Drains ring buffer, reads elapsed count from RX FIFO, converts to µs: `elapsed_us = (count × cycles_per_count × 1_000_000) / freq`.
- Fallback: `_maybe_enqueue_pio_from_rx()` polls RX FIFO directly if an IRQ callback was missed.

### Stop / Cancel Button (GP14)

- `PULL_UP`, active LOW — **polled** in the main loop, no IRQ.
- Short press (< 1 s): cancel/unlock current state.
- Long press (≥ 1 s): safe shutdown or display log.

### RFID RC522 — start_gate only (Core 1)

- Runs entirely on **Core 1** via `_thread`; no IRQ involved.
- Prefers native C module (`rc522_native`), falls back to pure-Python `RC522LL` driver.
- Communicates results to Core 0 via `_lock_state`-protected shared variables.
- SPI bus: ID 1, GP10/11/12/13/22, 50 kBaud default (retries at lower rates on init failure).

### DMX Output (optional)

- Managed by `DMX_controller.py` / `DMX_native_wrapper.py`.
- Uses a separate PIO instance (forced to PIO2) — does not share state machines with beam timing.
- Triggered by `_dmx_trigger_start_event()` / `_dmx_trigger_finish_event()` from the main loop.

---

## Project Structure

```
.
├── build_firmware.sh          # Build script (run this)
├── firmware/                  # Built firmware binaries (version-controlled)
├── native_modules/            # Custom C modules & project config
│   ├── dmx_native/
│   ├── micropython.cmake
│   └── zeitmessung.cmake      # Custom banner settings
├── micropython/               # MicroPython (git submodule)
├── *.py                       # Project Python files (main, helpers, etc)
└── credentials.py             # WiFi/network credentials (not committed)
```

## Submodule Updates

To update the micropython submodule to the latest:

```bash
cd micropython
git pull origin master
cd ..
git add micropython
git commit -m "Update micropython submodule to latest"
```

## Development

- **Edit project files** in the root directory and subdirectories (tracked in git)
- **Edit MicroPython** only if extending the system (changes in micropython/ need to stay maintainable)
- **Build script automatically includes** your `native_modules/` customizations
