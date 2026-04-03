#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PICO_PORT:-auto}"
SYNC_MODE="all"
DO_FLASH=true

# --------------------------------------------------------------------------
# find_mpremote — locate mpremote binary
# --------------------------------------------------------------------------
find_mpremote() {
    if command -v mpremote >/dev/null 2>&1; then
        command -v mpremote
    elif [[ -x "$HOME/.local/bin/mpremote" ]]; then
        echo "$HOME/.local/bin/mpremote"
    else
        echo "Error: mpremote not found." >&2
        echo "Install with: /usr/bin/python3 -m pip install --user --break-system-packages mpremote" >&2
        exit 1
    fi
}

# --------------------------------------------------------------------------
# resolve_port — auto-detect or validate a serial port
# --------------------------------------------------------------------------
resolve_port() {
    local requested="$1"
    local found=()
    while IFS= read -r dev; do
        [[ -n "$dev" ]] && found+=("$dev")
    done < <(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true)

    if [[ "$requested" == "auto" ]]; then
        if [[ ${#found[@]} -eq 0 ]]; then
            echo "Error: no serial ports found (/dev/ttyACM* or /dev/ttyUSB*)." >&2
            return 1
        fi
        if [[ ${#found[@]} -gt 1 ]]; then
            echo "Error: multiple serial ports found, specify one with --port=..." >&2
            printf '  %s\n' "${found[@]}" >&2
            return 1
        fi
        echo "${found[0]}"
        return
    fi

    if [[ -e "$requested" ]]; then
        echo "$requested"
        return
    fi

    echo "Error: port '$requested' not found." >&2
    if [[ ${#found[@]} -gt 0 ]]; then
        echo "Available ports:" >&2
        printf '  %s\n' "${found[@]}" >&2
        [[ ${#found[@]} -eq 1 ]] && echo "Tip: use --port=${found[0]} or omit for auto." >&2
    else
        echo "No /dev/ttyACM* or /dev/ttyUSB* devices detected." >&2
    fi
    return 1
}

# --------------------------------------------------------------------------
# find_bootloader_drive — wait for Pico USB mass storage to mount
# --------------------------------------------------------------------------
find_bootloader_drive() {
    local drive=""
    local waited=0
    local timeout=15

    echo "Waiting for USB mass storage..." >&2
    while [[ -z "$drive" && $waited -lt $timeout ]]; do
        printf '\r  BOOTSEL mount: %2ds / %2ds' "$waited" "$timeout" >&2
        sleep 1
        waited=$((waited + 1))
        for name in "RP2350" "RPI-RP2" "RPI-RP2350"; do
            drive="$(find /media /run/media 2>/dev/null -maxdepth 3 -name "$name" -type d 2>/dev/null | head -1 || true)"
            [[ -n "$drive" ]] && break
        done
    done
    printf '\r  BOOTSEL mount: %2ds / %2ds\n' "$waited" "$timeout" >&2
    echo "$drive"
}

# --------------------------------------------------------------------------
# flash_uf2 — reboot Pico into BOOTSEL, copy UF2, wait for reboot
# --------------------------------------------------------------------------
flash_uf2() {
    local uf2="$1"
    local port="$2"
    local mpremote="$3"

    # Refuse to proceed if port is locked by another process
    local lock_info
    lock_info="$(lsof "$port" 2>/dev/null || true)"
    if [[ -n "$lock_info" ]]; then
        echo "Error: port '$port' is busy. Disconnect Pico extension/REPL first." >&2
        echo "$lock_info" >&2
        exit 1
    fi

    echo "Rebooting Pico into bootloader mode..."
    "$mpremote" connect "$port" exec "import machine; machine.bootloader()" 2>/dev/null || true
    sleep 1

    local drive
    drive="$(find_bootloader_drive)"
    if [[ -z "$drive" ]]; then
        echo "Error: Pico USB mass storage not found. Is the Pico connected via USB?" >&2
        exit 1
    fi

    echo "Copying $(basename "$uf2") to $drive ..."
    cp "$uf2" "$drive/"
    sync

    local timeout=20
    local wait2=0

    echo "Waiting for Pico to come back on $port ..." >&2
    while [[ ! -e "$port" && $wait2 -lt $timeout ]]; do
        printf '\r  Serial reconnect: %2ds / %2ds' "$wait2" "$timeout" >&2
        sleep 1
        wait2=$((wait2 + 1))
    done
    printf '\r  Serial reconnect: %2ds / %2ds\n' "$wait2" "$timeout" >&2
    if [[ ! -e "$port" ]]; then
        echo "Warning: port '$port' not back after ${wait2}s; Pico may still be booting." >&2
    else
        sleep 2  # extra settle time after USB re-enumeration
        echo "Pico is back on $port."
    fi
}

# --------------------------------------------------------------------------
# flash_uf2_direct — flash UF2 when board is already in BOOTSEL mode
# --------------------------------------------------------------------------
flash_uf2_direct() {
    local uf2="$1"
    local drive

    drive="$(find_bootloader_drive)"
    if [[ -z "$drive" ]]; then
        echo "Error: Pico USB mass storage not found." >&2
        echo "Put Pico into BOOTSEL mode (hold BOOTSEL while plugging USB), then retry." >&2
        return 1
    fi

    echo "Copying $(basename "$uf2") to $drive ..."
    cp "$uf2" "$drive/"
    sync
}

# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
for arg in "$@"; do
    case "$arg" in
        --all-py)
            SYNC_MODE="all"
            ;;
        --core)
            SYNC_MODE="core"
            ;;
        --port=*)
            PORT="${arg#*=}"
            ;;
        --no-flash)
            DO_FLASH=false
            ;;
        -h|--help)
            cat <<'EOF'
Usage:
  ./full_update.sh [OPTIONS]

What it does (default):
  1) Builds firmware (build_firmware.sh)
  2) Flashes UF2 to Pico (machine.bootloader() + USB mass storage copy)
  3) Uploads Python files to board filesystem (sync_pico.sh)

Options:
  --no-flash       Skip firmware flash step (only build + sync Python files).
  --all-py         Upload all top-level *.py files after flash (default).
  --core           Upload only DMX_controller.py and DMX_native_wrapper.py.
  --port=...       Serial device or auto (default: auto or $PICO_PORT).
  -h, --help       Show this help.

Note:
  Disconnect MicroPico vREPL/extension before running — the port must be free.
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Run './full_update.sh --help'" >&2
            exit 1
            ;;
    esac
done

# --------------------------------------------------------------------------
# Step 1: Build firmware
# --------------------------------------------------------------------------
echo "==> Building firmware"
bash "$SCRIPT_DIR/build_firmware.sh"

UF2="$SCRIPT_DIR/firmware/firmware-RPI_PICO2_W.uf2"
if [[ ! -f "$UF2" ]]; then
    echo "Error: UF2 not found at $UF2" >&2
    exit 1
fi

# --------------------------------------------------------------------------
# Step 2: Flash UF2 to Pico
# --------------------------------------------------------------------------
if [[ "$DO_FLASH" == "true" ]]; then
    echo "==> Flashing firmware to Pico"
    MPREMOTE_BIN="$(find_mpremote)"

    if RESOLVED_PORT="$(resolve_port "$PORT")"; then
        flash_uf2 "$UF2" "$RESOLVED_PORT" "$MPREMOTE_BIN"
    else
        echo "No serial port available; trying direct BOOTSEL mass-storage flash..." >&2
        flash_uf2_direct "$UF2"
    fi
fi

# --------------------------------------------------------------------------
# Step 3: Sync Python files
# --------------------------------------------------------------------------
echo "==> Syncing Python files to Pico"
if [[ "$SYNC_MODE" == "all" ]]; then
    "$SCRIPT_DIR/sync_pico.sh" --all-py --port="$PORT"
else
    "$SCRIPT_DIR/sync_pico.sh" --core --port="$PORT"
fi

echo "==> Full update finished"
