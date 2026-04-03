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
1. Runs `build_firmware.sh`
2. Runs `sync_pico.sh` to upload Python files to board filesystem
3. Soft-resets the Pico
4. Verifies DMX native API (`start_code`)

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

## Flashing to Pico 2 W

1. **Hold BOOTSEL** button on Pico 2 W
2. **Plug in via USB** (while holding BOOTSEL)
3. A USB drive appears (RPI-RP2)
4. **Copy** the `.uf2` file to the drive
5. Device reboots with new firmware

## REPL Welcome Message

The custom firmware displays:
```
MicroPython ... ; Firmware for ZeitmessungRaspberry Pi Pico 2 W (built YYYY-MM-DD HH:MM:SS)
```

This confirms you have the project-specific firmware.

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
