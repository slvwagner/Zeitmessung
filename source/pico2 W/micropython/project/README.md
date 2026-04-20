# Zeitmessung MicroPython Project

Custom MicroPython build for Pico 2 W with timing measurement hardware control.

## Current Setup

- The project uses the `micropython/` submodule from the `slvwagner/micropython` fork as its base.
- The fork is currently based on the official MicroPython `v1.28.0` release, plus project-specific compatibility fixes.
- The parent project branch only records which MicroPython submodule commit to use.

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

---

## Firmware Versioning

Firmware version is set automatically by the root-level `update_version.R` script. Run this script before building to update all version tags in the project.

## SDK Version

This project is built and tested with **Pico SDK version 2.2.0** (see `micropython/lib/pico-sdk/pico_sdk_version.cmake`).

If you use a different SDK version, results may vary.

## Credentials

This project expects local network credentials for features that use Wi-Fi or network services.

Before building or deploying to a board that needs network access:

1. Copy `../credentials_template.py` to `credentials.py`
2. Fill in your local Wi-Fi / network settings in `credentials.py`

`credentials.py` is intended to stay local and should not be committed.

## OLED Display

The current display target for this branch is a `128x64` I2C OLED module with an `SSD1309` controller
(for example the 4-pin `2.42"` module using `GND`, `VCC`, `SCL`, `SDA`).

Software notes:

- The MicroPython display driver lives in `OLED.py`
- `OLED_CONTROLLER` is set to `SSD1309` on this branch
- The project still uses I2C bus `0` on `GP4` (`SDA`) and `GP5` (`SCL`)
- The display address is expected to be `0x3C` unless your module is strapped differently

If you swap back to an older SSD1306-based module later, change `OLED_CONTROLLER` in `OLED.py` back to `SSD1306`.

## Windows Build And Flash

Windows is a supported workflow for this project.

### Requirements

- Raspberry Pi Pico VS Code extension toolchain under `%USERPROFILE%\.pico-sdk`
- PowerShell
- `mpremote`
- Visual Studio MSBuild for building host `mpy-cross.exe`

Install `mpremote` into the same Python you use for Pico tooling, for example:

```powershell
$env:USERPROFILE\.pico-sdk\python\3.13.7\python.exe -m pip install mpremote
```

### Main Scripts

- `build_firmware.ps1` builds the custom MicroPython firmware for `RPI_PICO2_W`
- `sync_pico.ps1` uploads project `.py` files to the board filesystem
- `full_update.ps1` does build, flash, reconnect, sync, and verification
- `full_upgrade.ps1` is a compatibility wrapper for `full_update.ps1`

### Build Only

```powershell
.\build_firmware.ps1
```

Output files:

- `firmware/firmware-RPI_PICO2_W.uf2`
- `firmware/firmware-RPI_PICO2_W.bin`
- `firmware/firmware-RPI_PICO2_W.hex`

## Daily Update Workflow (Recommended)

Use the PowerShell wrapper to build firmware, flash it, and sync the project files:

```powershell
.\full_update.ps1
```

Useful options:

```powershell
# Upload only DMX core files
.\full_update.ps1 -Core

# Use another serial port
.\full_update.ps1 -Port COM3

# Skip UF2 flashing and only sync Python files
.\full_update.ps1 -NoFlash
```

What this does:
1. Runs `build_firmware.ps1`
2. Auto-detects a connected Pico USB serial port on Windows
3. Reboots the Pico into bootloader mode with `machine.bootloader()`
4. Flashes the UF2 via the BOOTSEL mass-storage drive
5. Waits for the Pico to reconnect
6. Runs `sync_pico.ps1` to upload Python files
7. Soft-resets the Pico and verifies DMX native API (`start_code`)

### Scripts

- `build_firmware.ps1` — builds firmware and writes files into `firmware/`
- `sync_pico.ps1` — uploads Python files to Pico with `mpremote`
- `full_update.ps1` — build + flash + sync in one command
- `full_upgrade.ps1` — compatibility wrapper for `full_update.ps1`

## Linux / Bash Scripts

Linux and Bash-based workflows are also supported:

- `build_firmware.sh`
- `sync_pico.sh`
- `full_update.sh`

These scripts provide the same project workflow for Linux, WSL, or Git Bash environments.

## Important: VS Code Pico Extension Lock

If serial/REPL is connected (for example MicroPico vREPL), upload can fail because the port is busy.

Before running `sync_pico.ps1` or `full_update.ps1`:
1. Disconnect Pico extension REPL/serial monitor
2. Retry the script once the COM port is no longer busy

If the Pico port is busy, `full_update.ps1` now stops with a clear message instead of incorrectly falling back to BOOTSEL detection.

## Python-Only Sync (Without Rebuild)

If firmware is already flashed and you only changed `.py` files:

```powershell
.\sync_pico.ps1
```

Optional:

```powershell
# Upload only DMX core files
.\sync_pico.ps1 -Core

# Force a specific serial port
.\sync_pico.ps1 -Port COM3
```

## Flashing to Pico 2 W (Manual)

`full_update.ps1` flashes automatically. To flash manually:

1. **Hold BOOTSEL** button on Pico 2 W
2. **Plug in via USB** (while holding BOOTSEL)
3. A USB drive appears (RP2350)
4. **Copy** the `.uf2` file to the drive
5. Device reboots with new firmware

## REPL Welcome Message

The custom firmware displays:
```
MicroPython v1.28.0-... on YYYY-MM-DD; Firmware for Zeitmessung 0.1.2 on Raspberry Pi Pico 2 W (built YYYY-MM-DD HH:MM:SS)
```

Notes:

- The `MicroPython v...` part comes from the `micropython/` submodule commit.
- Parent-project commits do not change that version string.
- If the submodule has local commits on top of `v1.28.0`, the REPL will show a suffix such as `-1-g<hash>`.
- The `Firmware for Zeitmessung ...` part comes from the project banner set in CMake.

## IRQ & Timing Architecture

Both `start_gate.py` and `finish_gate.py` use the same PIO-based dual-beam timing design. The DMX native C module adds a second independent PIO+DMA interrupt subsystem.


### PIO instance allocation (RP2350 / Pico 2 W)

The RP2350 (Pico 2 W) has three PIO blocks (PIO0–PIO2), each with 4 state machines. MicroPython SM IDs map as: 0–3 → PIO0, 4–7 → PIO1, 8–11 → PIO2.

| PIO    | Local SM | Global SM ID | Owner                | Program              | Notes |
|--------|----------|--------------|----------------------|----------------------|-------|
| **PIO0** | SM0–SM3 | 0–3          | WiFi / cyw43 driver  | CYW43 SPI/SDIO       | Reserved by MicroPython W firmware; **do not use** |
| **PIO1** | SM1      | **5**        | Beam timing          | `dual_beam_measure_irq` | `BEAM1_SM_ID = 5`; runs at 2 MHz |
| **PIO2** | SM0      | **8**        | DMX (ctrl)           | `sm_dmx_control`     | **Only valid pair for DMX on Pico 2 W** |
| **PIO2** | SM1      | **9**        | DMX (data)           | `sm_dmx_data`        | Paired with SM8; DMA DREQ linked to this SM's TX FIFO |

**For this RP2350 Pico 2 W project, DMX uses only PIO2 SM0+SM1 (global SM IDs 8 and 9).**
Other pairs are not supported or valid. This avoids conflicts with WiFi (PIO0) and beam timing (PIO1 SM5).

The PIO programs for DMX are located in `native_modules/dmx_native/dmx_native.pio` and `native_modules/dmx_native/dmx_native_sdk.pio`.

### Complete interrupt resource map

| Subsystem | Mechanism | Hardware | IRQ / Signal | CPU interrupt? |
|---|---|---|---|---|
| Beam timing | PIO SM (MicroPython) | PIO1 SM5 | `irq(0)` → `_pio_dual_irq_handler` | ✅ Yes — MicroPython SM IRQ callback |
| DMX frame start | PIO force (C) | DMX PIO | `IRQ_FRAME_START` (0) | ❌ No — intra-PIO signal only |
| DMX slot sync | PIO inter-SM (C) | DMX PIO | `IRQ 4` / `IRQ 5` | ❌ No — intra-PIO signal only |
| DMX frame done | PIO → C poll (C) | DMX PIO | `IRQ_FRAME_DONE` (2) | ❌ No — polled by C code |
| DMX data transfer | DMA + DREQ (C) | DMA ch + PIO FIFO | DREQ pacing | ❌ No — hardware pacing only |
| DMX frame update | Global irq disable (C) | All | `save_and_disable_interrupts()` | ❌ No — atomic guard only |
| Stop button | Polling (Python) | GP14 | none | ❌ No — main loop poll |
| RFID scan | SPI poll, Core 1 (C+Py) | SPI1 + RC522 COMIRQ reg | none | ❌ No — device register poll |

---

### PIO State Machine (primary beam timing)

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



### DMX Native C Module (`dmx_native/moddmx_native.c`)

The DMX output is the most interrupt-intensive part of the system. It uses **two PIO state machines** (PIO2 SM0 and SM1, global SM IDs 8 and 9), **one DMA channel**, and **four PIO IRQ signals** all coordinated together.

**PIO resources:**

| Parameter      | Value |
|---------------|-------|
| PIO instance  | PIO2 (fixed on Pico 2 W to avoid conflicts) |
| Control SM    | SM8 (global SM ID 8, PIO2 SM0) |
| Data SM       | SM9 (global SM ID 9, PIO2 SM1) |
| DMA channel   | 1 per instance, dynamically claimed (`dma_claim_unused_channel`) |
| DMA data flow | RAM frame buffer → PIO TX FIFO, rate-gated by PIO DREQ signal |

**PIO IRQ signals (`dmx_native.pio`):**

| Signal | Direction | Who sets it | Who waits | Purpose |
|---|---|---|---|---|
| `IRQ 0` (`IRQ_FRAME_START`) | C → Control SM | C code via `pio->irq_force` | Control SM `wait 1 irq 0` | Trigger each new DMX frame |
| `IRQ 2` (`IRQ_FRAME_DONE`) | Control SM → C | Control SM `irq 2` | C code polls `pio_interrupt_get()` | Frame complete — C updates frame version |
| `IRQ 4` | Control SM → Data SM | Control SM `irq 4` | Data SM `wait 1 irq 4` | Start sending next DMX slot |
| `IRQ 5` | Data SM → Control SM | Data SM `irq 5` | Control SM `wait 1 irq 5` | Slot transmission complete |

**Coordination flow:**
```
C code
  │  pio->irq_force = 1<<IRQ_FRAME_START    ← force IRQ 0 each frame
  ▼
Control SM
  wait IRQ 0 → load slot count → loop:
    irq 4 ──────────────────────────────►  Data SM
                                            wait IRQ 4 → shift 8 bits out pins
    wait IRQ 5  ◄───────────────────────   irq 5
    irq 2  ────────────────────────────►  C polls pio_interrupt_get(IRQ_FRAME_DONE)
```

**DMA configuration:**
- Source: `dmx_state.tx_frame` (RAM, 513 bytes: start code + 512 channels)
- Destination: `pio->txf[data_sm]` (PIO TX FIFO register address)
- DREQ: tied to PIO data SM TX FIFO empty — DMA pauses until SM consumes each byte
- Restart: called per-frame via `dma_channel_configure(..., true)` in update loop

**Global interrupt disable (4 call sites):**
Frame data updates (`clear`, `set_channel`, `set_channels`, `set_invert_data_bits`) use `save_and_disable_interrupts()` / `restore_interrupts()` to make the RAM buffer write atomic — preventing a mid-frame DMA transfer from reading a partially updated frame.

**RC522 Native C Module (`rc522_native/modrc522_native.c`):**
- Uses the RC522 chip's `COMIRQ` register (`0x04`) to detect transceive completion — but this is a **device register read via SPI polling**, not a CPU interrupt.
- No DMA, no CPU IRQ handlers, no PIO — SPI only.

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

---

For backend, frontend, and overall system integration, see the [main project README](../../../README.md).

## Submodule Updates

The `micropython/` directory is a Git submodule. This project currently tracks the `slvwagner/micropython` fork, which is based on the official `v1.28.0` release plus a small project-specific patch set.

If you need to make MicroPython changes:

```powershell
cd micropython
git remote -v
git switch <your-fork-branch>
```

Commit inside the submodule first, then record the new pointer in the parent repo:

```powershell
cd micropython
git add <files>
git commit -m "Describe MicroPython change"
cd ..
git add micropython
git commit -m "Update MicroPython submodule pointer"
```

## Development

- Edit project files in the root directory and subdirectories as usual.
- Keep MicroPython-specific patches small and commit them inside the submodule branch.
- The build scripts automatically include the `native_modules/` customizations.
