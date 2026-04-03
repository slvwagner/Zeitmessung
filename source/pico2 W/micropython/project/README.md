# Zeitmessung MicroPython Project

Custom MicroPython build for Pico 2 W with timing measurement hardware control.

## Quick Start

### Clone with Submodules

Before cloning, use the `--recursive` flag to automatically load the micropython submodule:

```bash
git clone --recursive <repo-url>
cd "pico2 W/micropython/project"
```

### Or If Already Cloned

If you've already cloned without submodules, initialize them:

```bash
git submodule update --init --recursive
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

## Flashing to Pico 2 W

1. **Hold BOOTSEL** button on Pico 2 W
2. **Plug in via USB** (while holding BOOTSEL)
3. A USB drive appears (RPI-RP2)
4. **Copy** the `.uf2` file to the drive
5. Device reboots with new firmware

## REPL Welcome Message

The custom firmware displays:
```
Raspberry Pi Pico 2 W [Zeitmessung FW] with RP2350
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
