#!/bin/bash
# Build MicroPython firmware for Pico 2 W with Zeitmessung customizations
# Includes project user C modules from native_modules/micropython.cmake
# (currently dmx_native and rc522_native)
# Automatically copies firmware to project/firmware directory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RP2_DIR="${SCRIPT_DIR}/micropython/ports/rp2"
FIRMWARE_DIR="${SCRIPT_DIR}/firmware"
BOARD="RPI_PICO2_W"
BUILD_DIR="${RP2_DIR}/build-${BOARD}"
MP_GIT_DIR="${SCRIPT_DIR}/micropython"
BUILD_JOBS="${BUILD_JOBS:-$(nproc)}"

MP_DESCRIBE_RAW="$(git -C "${MP_GIT_DIR}" describe --tags --always 2>/dev/null || echo unknown)"
MP_COMMIT_RAW="$(git -C "${MP_GIT_DIR}" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
MP_STABLE_BASE_RAW="$(git -C "${MP_GIT_DIR}" tag --merged HEAD 2>/dev/null | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n1)"
if [[ -z "${MP_STABLE_BASE_RAW}" ]]; then
	MP_STABLE_BASE_RAW="unknown"
fi

echo "Building MicroPython firmware for ${BOARD}..."
echo "MicroPython describe: ${MP_DESCRIBE_RAW}"
echo "MicroPython commit: ${MP_COMMIT_RAW}"
echo "MicroPython stable base: ${MP_STABLE_BASE_RAW}"
echo "Build jobs: ${BUILD_JOBS}"

# Clean build directory
rm -rf "${BUILD_DIR}"

# Change to RP2 directory and use relative path for USER_C_MODULES
# This way the relative path with no spaces is passed directly to make/cmake
cd "${RP2_DIR}"

# Use absolute path through PWD substitution to avoid space issues
# Convert to relative from current RP2 directory
if ! make -j"${BUILD_JOBS}" BOARD=${BOARD} USER_C_MODULES=../../../native_modules/micropython.cmake; then
	echo ""
	echo "Parallel build failed. Retrying with a single job (-j1)..."
	echo "This often avoids host compiler ICE/OOM issues while building picotool."
	rm -rf "${BUILD_DIR}"
	make -j1 BOARD=${BOARD} USER_C_MODULES=../../../native_modules/micropython.cmake
fi

echo "Built with USER_C_MODULES=../../../native_modules/micropython.cmake"

# Copy firmware to project directory
mkdir -p "${FIRMWARE_DIR}"
echo "Copying firmware to ${FIRMWARE_DIR}..."
cp "${BUILD_DIR}/firmware.uf2" "${FIRMWARE_DIR}/firmware-${BOARD}.uf2"
cp "${BUILD_DIR}/firmware.bin" "${FIRMWARE_DIR}/firmware-${BOARD}.bin"
cp "${BUILD_DIR}/firmware.hex" "${FIRMWARE_DIR}/firmware-${BOARD}.hex" 2>/dev/null || true

echo "✓ Firmware build complete!"
echo "  MicroPython describe: ${MP_DESCRIBE_RAW}"
echo "  MicroPython commit: ${MP_COMMIT_RAW}"
echo "  MicroPython stable base: ${MP_STABLE_BASE_RAW}"
echo "  UF2: ${FIRMWARE_DIR}/firmware-${BOARD}.uf2"
echo "  BIN: ${FIRMWARE_DIR}/firmware-${BOARD}.bin"
echo ""
echo "To flash: Hold BOOTSEL and plug in Pico 2 W, then copy the UF2 file to the USB drive."
echo ""
echo "REPL banner will show device name plus a build timestamp from CMake"
