#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PICO_PORT:-auto}"
SYNC_MODE="all"

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
        -h|--help)
            cat <<'EOF'
Usage:
  ./full_update.sh [--all-py|--core] [--port=auto|/dev/ttyACM0]

What it does:
  1) Runs existing firmware build script (build_firmware.sh)
  2) Runs sync_pico.sh to upload Python files and verify API

Options:
  --all-py         Upload all top-level *.py files after build (default).
  --core           Upload only DMX_controller.py and DMX_native_wrapper.py.
  --port=...       Serial device or auto for sync step (default: auto or $PICO_PORT).
  -h, --help       Show this help.

Note:
  If Pico extension/REPL is connected, sync step will fail until port is free.
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

echo "==> Building firmware"
bash "$SCRIPT_DIR/build_firmware.sh"

echo "==> Syncing Python files to Pico"
if [[ "$SYNC_MODE" == "all" ]]; then
    "$SCRIPT_DIR/sync_pico.sh" --all-py --port="$PORT"
else
    "$SCRIPT_DIR/sync_pico.sh" --port="$PORT"
fi

echo "==> Full update finished"
