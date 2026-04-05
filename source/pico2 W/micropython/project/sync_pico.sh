#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PICO_PORT:-auto}"
MODE="all"
CLEAN=0

resolve_port() {
    local requested="$1"
    local found=()


    while IFS= read -r dev; do
        [[ -n "$dev" ]] && found+=("$dev")
    done < <(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true)

    if [[ "$requested" == "auto" ]]; then
        if [[ ${#found[@]} -eq 0 ]]; then
            echo "Error: no serial ports found (/dev/ttyACM* or /dev/ttyUSB*)." >&2
            exit 1
        fi
        if [[ ${#found[@]} -gt 1 ]]; then
            echo "Error: multiple serial ports found, specify one with --port=..." >&2
            printf '  %s\n' "${found[@]}" >&2
            exit 1
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
        if [[ ${#found[@]} -eq 1 ]]; then
            echo "Tip: use --port=${found[0]} or omit --port for auto detection." >&2
        fi
    else
        echo "No /dev/ttyACM* or /dev/ttyUSB* devices detected." >&2
    fi
    exit 1
}

for arg in "$@"; do
    case "$arg" in
        --all-py)
            MODE="all"
            ;;
        --core)
            MODE="core"
            ;;
        --clean)
            CLEAN=1
            ;;
        --port=*)
            PORT="${arg#*=}"
            ;;
        -h|--help)
            cat <<'EOF'
Usage:
        ./sync_pico.sh [--all-py|--core] [--clean] [--port=auto|/dev/ttyACM0]

Default behavior:
    Upload all top-level *.py files from project root.

Options:
    --all-py         Upload all top-level *.py files from project root.
    --core           Upload only DMX_controller.py and DMX_native_wrapper.py.
    --clean          Delete all files on Pico before upload.
    --port=...       Serial device or auto (default: auto or $PICO_PORT).
  -h, --help       Show this help.

Notes:
  - Make sure VS Code Pico extension / serial monitor is disconnected.
  - This script does not flash UF2; it syncs Python files to board FS.
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Run './sync_pico.sh --help'" >&2
            exit 1
            ;;
    esac
done

if command -v mpremote >/dev/null 2>&1; then
    MPREMOTE_BIN="$(command -v mpremote)"
elif [[ -x "$HOME/.local/bin/mpremote" ]]; then
    MPREMOTE_BIN="$HOME/.local/bin/mpremote"
else
    echo "Error: mpremote not found." >&2
    echo "Install with: /usr/bin/python3 -m pip install --user --break-system-packages mpremote" >&2
    exit 1
fi

PORT="$(resolve_port "$PORT")"

LOCK_INFO="$(lsof "$PORT" 2>/dev/null || true)"
if [[ -n "$LOCK_INFO" ]]; then
    echo "Error: port '$PORT' is busy. Disconnect Pico extension/REPL first." >&2
    echo "$LOCK_INFO" >&2
    exit 1
fi


echo "Connecting to Pico on $PORT ..."
"$MPREMOTE_BIN" connect "$PORT" fs ls >/dev/null

# If --clean is set, delete all files on Pico
if [[ "$CLEAN" == "1" ]]; then
    echo "Deleting all files on Pico..."
    # List all files and delete them
    FILES_ON_PICO=$("$MPREMOTE_BIN" connect "$PORT" fs ls | awk '{print $NF}')
    for f in $FILES_ON_PICO; do
        echo "  Removing: $f"
        "$MPREMOTE_BIN" connect "$PORT" fs rm ":$f" || true
    done
fi

FILES=()
if [[ "$MODE" == "core" ]]; then
    FILES+=(
        "$SCRIPT_DIR/DMX_controller.py"
        "$SCRIPT_DIR/DMX_native_wrapper.py"
    )
else
    while IFS= read -r -d '' file; do
        FILES+=("$file")
    done < <(find "$SCRIPT_DIR" -maxdepth 1 -type f -name "*.py" -print0 | sort -z)
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "No files selected for upload." >&2
    exit 1
fi

echo "Uploading ${#FILES[@]} file(s)..."
for file in "${FILES[@]}"; do
    if [[ ! -f "$file" ]]; then
        echo "Warning: missing file, skipping: $file" >&2
        continue
    fi
    base="$(basename "$file")"
    echo "  -> $base"
    "$MPREMOTE_BIN" connect "$PORT" fs cp "$file" ":$base"
done

echo "Soft reset..."
"$MPREMOTE_BIN" connect "$PORT" soft-reset

echo "Verifying DMX native API..."
"$MPREMOTE_BIN" connect "$PORT" exec "import dmx_native; s=dmx_native.status(); print('status_has_start_code=', 'start_code' in s, 'start_code=', s.get('start_code'))"

echo "Done."
