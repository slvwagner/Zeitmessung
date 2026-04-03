#!/bin/bash
# Build MicroPython firmware for Pico 2 W with Zeitmessung customizations
# Automatically copies firmware to project/firmware directory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RP2_DIR="${SCRIPT_DIR}/micropython/ports/rp2"
FIRMWARE_DIR="${SCRIPT_DIR}/firmware"
BOARD="RPI_PICO2_W"
BUILD_DIR="${RP2_DIR}/build-${BOARD}"

echo "Building MicroPython firmware for ${BOARD}..."

# Clean build directory
rm -rf "${BUILD_DIR}"

# Change to RP2 directory and use relative path for USER_C_MODULES
# This way the relative path with no spaces is passed directly to make/cmake
cd "${RP2_DIR}"

export CFLAGS="-DMICROPY_BANNER_MACHINE='\"Raspberry Pi Pico 2 W [Zeitmessung FW] with RP2350\"'"

# Use absolute path through PWD substitution to avoid space issues
# Convert to relative from current RP2 directory
make -j$(nproc) BOARD=${BOARD} USER_C_MODULES=../../../native_modules/micropython.cmake

# Copy firmware to project directory
mkdir -p "${FIRMWARE_DIR}"
echo "Copying firmware to ${FIRMWARE_DIR}..."
cp "${BUILD_DIR}/firmware.uf2" "${FIRMWARE_DIR}/firmware-${BOARD}.uf2"
cp "${BUILD_DIR}/firmware.bin" "${FIRMWARE_DIR}/firmware-${BOARD}.bin"
cp "${BUILD_DIR}/firmware.hex" "${FIRMWARE_DIR}/firmware-${BOARD}.hex" 2>/dev/null || true

echo "✓ Firmware build complete!"
echo "  UF2: ${FIRMWARE_DIR}/firmware-${BOARD}.uf2"
echo "  BIN: ${FIRMWARE_DIR}/firmware-${BOARD}.bin"
echo ""
echo "To flash: Hold BOOTSEL and plug in Pico 2 W, then copy the UF2 file to the USB drive."
echo ""
echo "REPL banner will show: Raspberry Pi Pico 2 W [Zeitmessung FW] with RP2350"
