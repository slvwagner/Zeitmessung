#!/bin/bash
# Build MicroPython firmware for Pico 2 W with Zeitmessung customizations
# Automatically copies firmware to project/firmware directory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RP2_DIR="${SCRIPT_DIR}/micropython/ports/rp2"
FIRMWARE_DIR="${SCRIPT_DIR}/firmware"
BOARD="RPI_PICO2_W"

echo "Building MicroPython firmware for ${BOARD}..."
cd "${RP2_DIR}"
make -j$(nproc) BOARD=${BOARD} USER_C_MODULES=../../../native_modules/micropython.cmake

# Copy firmware to project directory
mkdir -p "${FIRMWARE_DIR}"
echo "Copying firmware to ${FIRMWARE_DIR}..."
cp "${RP2_DIR}/build-${BOARD}/firmware.uf2" "${FIRMWARE_DIR}/firmware-${BOARD}.uf2"
cp "${RP2_DIR}/build-${BOARD}/firmware.bin" "${FIRMWARE_DIR}/firmware-${BOARD}.bin"
cp "${RP2_DIR}/build-${BOARD}/firmware.hex" "${FIRMWARE_DIR}/firmware-${BOARD}.hex" 2>/dev/null || true

echo "✓ Firmware build complete!"
echo "  UF2: ${FIRMWARE_DIR}/firmware-${BOARD}.uf2"
echo "  BIN: ${FIRMWARE_DIR}/firmware-${BOARD}.bin"
echo ""
echo "To flash: Hold BOOTSEL and plug in Pico 2 W, then copy the UF2 file to the USB drive."
