# MicroPython v1.26.0 — Raspberry Pi Pico W (RP2040)
# DMX512 transmitter (full 512 slots) ~44 Hz using dual cores
#
# - Core 0: Generates DMX on UART0 (250k 8N2) with BREAK+MAB
# - Core 1: Monitors timing and prints stats + DMX CH1..CH5 values
#
# CH1: GPIO2 (DIN, pull-down) -> 0/255
# CH2: GPIO3 (DIN, pull-down) -> 0/255
# CH3..CH5: ADC0..ADC2 (GPIO26..28) -> 0–255
# CH6..CH512: fixed 0
#
# Optional:
#   PROBE_FRAME_PIN = 14  (high during full frame)
#   PROBE_BREAK_PIN = 13  (high during BREAK)
#   TRIG_PIN = 4          (short pulse before each frame, scope EXT trigger)

from machine import Pin, UART, ADC
import time, _thread

# ---------------- Configuration ----------------
UART_ID         = 0
UART_TX_PIN     = 0             # GP0 = UART0 TX
TX_EN_PIN       = None          # RS485 DE/!RE pin or None

DMX_CHANNELS    = 512          # full DMX universe
TARGET_HZ       = 100
FRAME_US        = int(1_000_000 / TARGET_HZ)

DIN_PINS        = (2, 3)        # CH1..CH2
ADC_PINS        = (26, 27, 28)  # CH3..CH5

PROBE_FRAME_PIN = None
PROBE_BREAK_PIN = None

TRIG_PIN        = 4
TRIG_PULSE_US   = 10

REPORT_MS       = 2000

# ---------------- Hardware setup ----------------
if TX_EN_PIN is not None:
    tx_en = Pin(TX_EN_PIN, Pin.OUT)
    tx_en.value(1)

uart = UART(UART_ID, baudrate=250_000, bits=8, parity=None, stop=2, tx=Pin(UART_TX_PIN))

din  = [Pin(p, Pin.IN, Pin.PULL_UP) for p in DIN_PINS] # PUll_UP (GPIO PIN needs to be pulled down to get true)
adcs = [ADC(Pin(p)) for p in ADC_PINS]

# Build DMX frame: start code + 512 slots
dmx = bytearray(1 + DMX_CHANNELS)
dmx[0] = 0x00
dmx[1:] = b"\x00" * DMX_CHANNELS   # initialize all slots to 0

probe_frame = Pin(PROBE_FRAME_PIN, Pin.OUT) if PROBE_FRAME_PIN is not None else None
probe_break = Pin(PROBE_BREAK_PIN, Pin.OUT) if PROBE_BREAK_PIN is not None else None
if probe_frame: probe_frame.value(0)
if probe_break: probe_break.value(0)

trigger = Pin(TRIG_PIN, Pin.OUT)
trigger.value(0)

# ---------------- Shared telemetry ----------------
lock = _thread.allocate_lock()
telemetry = {
    "frame_count": 0,
    "last_period_us": 0,
    "channel_values": b""
}

# ---------------- Helpers ----------------
def adc8(a: ADC) -> int:
    return (a.read_u16() + 128) >> 8

def break_and_mab():
    if probe_break: probe_break.value(1)
    uart.init(baudrate=100_000, bits=8, parity=None, stop=2)
    uart.write(b"\x00")
    time.sleep_us(120)  # covers ~90us BREAK + ~20us MAB
    uart.init(baudrate=250_000, bits=8, parity=None, stop=2)
    if probe_break: probe_break.value(0)

# ---------------- Core 0: DMX loop ----------------
def dmx_loop():
    prev_end = time.ticks_us()
    while True:
        frame_start = time.ticks_us()
        if probe_frame: probe_frame.value(1)

        # CH1..2 from digital inputs
        for i, pin in enumerate(din, start=1):
            dmx[i] = 255 if pin.value() else 0
        # CH3..CH5 from ADCs
        for j, a in enumerate(adcs, start=3):
            if j > DMX_CHANNELS:
                break
            dmx[j] = adc8(a)

        # External trigger pulse
        trigger.value(1)
        time.sleep_us(TRIG_PULSE_US)
        trigger.value(0)

        # BREAK + MAB
        break_and_mab()

        # Send DMX frame
        uart.write(dmx)

        if probe_frame: probe_frame.value(0)

        # Update telemetry
        now = time.ticks_us()
        period = time.ticks_diff(now, prev_end)
        prev_end = now
        with lock:
            telemetry["frame_count"] += 1
            telemetry["last_period_us"] = period
            telemetry["channel_values"] = bytes(dmx)

        # Pace to ~44 Hz (subtract BREAK+payload time)
        # One slot = 11 bits @ 250k = 44us
        payload_us = (len(dmx) * 11 * 1_000_000) // 250_000
        frame_time = 110 + payload_us
        elapsed = time.ticks_diff(time.ticks_us(), frame_start)
        remaining = FRAME_US - elapsed
        if remaining > 0:
            time.sleep_us(remaining)

# ---------------- Core 1: Monitor loop ----------------
def monitor_loop():
    last_report = time.ticks_ms()
    count_prev = 0
    while True:
        time.sleep_ms(10)
        with lock:
            count = telemetry["frame_count"]
            period = telemetry["last_period_us"]
            values = telemetry["channel_values"]
        if count != count_prev and values:
            if time.ticks_diff(time.ticks_ms(), last_report) >= REPORT_MS:
                hz = 1_000_000.0 / period if period else 0
                print("[DMX] frames:%d  rate: %.2f Hz  period us:%d" %
                      (count, hz, period))
                # Print CH1..CH5 values
                print("  CH1 (DIN) = %3d" % values[1])
                print("  CH2 (DIN) = %3d" % values[2])
                print("  CH3 (ADC) = %3d" % values[3])
                print("  CH4 (ADC) = %3d" % values[4])
                print("  CH5 (ADC) = %3d" % values[5])
                last_report = time.ticks_ms()
        count_prev = count

# ---------------- Boot ----------------
def main():
    _thread.start_new_thread(monitor_loop, ())
    try:
        dmx_loop()
    except KeyboardInterrupt:
        if probe_frame: probe_frame.value(0)
        if probe_break: probe_break.value(0)
        trigger.value(0)
        print("Shutdown / Stopped.")

if __name__ == "__main__":
    main()

