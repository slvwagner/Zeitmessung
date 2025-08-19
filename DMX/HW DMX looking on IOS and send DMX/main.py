# MicroPython v1.26.0 — Raspberry Pi Pico W (RP2040)
# DMX512 transmitter (~44 Hz) using dual cores:
#   - Core 0: DMX generation (UART0, 250k 8N2) with BREAK+MAB via baud-switch trick
#   - Core 1: Timing monitor (frame period, jitter, payload time), prints stats every ~1s
#
# Channels:
#   CH1: GPIO3 (digital, pull-down) -> 0 or 255
#   CH2: GPIO4 (digital, pull-down) -> 0 or 255
#   CH3..CH5: ADC0..ADC2 (GPIO26..GPIO28) -> 0..255
#
# Optional pins:
#   TX_EN_PIN: drive RS-485 DE/!RE (active-high) or set to None to ignore
#   PROBE_FRAME_PIN: high during whole DMX frame (break..data)
#   PROBE_BREAK_PIN: high during BREAK only
#   TRIG_PIN: short pulse just BEFORE each frame (oscilloscope EXT trigger)

from machine import Pin, UART, ADC
import time, _thread

# ---------------- Configuration ----------------
UART_ID         = 0             # UART0
UART_TX_PIN     = 0             # GP0 = UART0 TX
TX_EN_PIN       = None          # e.g., 15 (or None if not used)

DMX_CHANNELS    = 5             # We'll send 5 slots (SC + 5 channels)
TARGET_HZ       = 44.0
FRAME_US        = int(1_000_000 / TARGET_HZ)  # ≈ 22_727 us

# Inputs
DIN_PINS        = (2, 3)        # CH1..CH2
ADC_PINS        = (26, 27, 28)  # CH3..CH5

# Optional scope probes (set to None to disable)
PROBE_FRAME_PIN = 14            # High during full frame; set None to disable
PROBE_BREAK_PIN = 13            # High only during BREAK; set None to disable

# External trigger (pulse before each frame)
TRIG_PIN        = 4             # GPIO2 to your scope's EXT TRIG
TRIG_PULSE_US   = 10            # pulse width (microseconds)

# Monitor output interval
REPORT_MS       = 1000

# ---------------- Hardware setup ----------------
tx_en = None
if TX_EN_PIN is not None:
    tx_en = Pin(TX_EN_PIN, Pin.OUT)
    tx_en.value(1)  # enable driver

uart = UART(UART_ID, baudrate=250_000, bits=8, parity=None, stop=2, tx=Pin(UART_TX_PIN))

din  = [Pin(p, Pin.IN, Pin.PULL_DOWN) for p in DIN_PINS]
adcs = [ADC(Pin(p)) for p in ADC_PINS]

dmx = bytearray(1 + DMX_CHANNELS)
dmx[0] = 0x00  # Start Code

# Probes
probe_frame = Pin(PROBE_FRAME_PIN, Pin.OUT) if PROBE_FRAME_PIN is not None else None
probe_break = Pin(PROBE_BREAK_PIN, Pin.OUT) if PROBE_BREAK_PIN is not None else None
if probe_frame: probe_frame.value(0)
if probe_break: probe_break.value(0)

# External trigger
trigger = Pin(TRIG_PIN, Pin.OUT)
trigger.value(0)

# ---------------- Shared telemetry (core0 -> core1) ----------------
lock = _thread.allocate_lock()
telemetry = {
    "last_frame_start_us": 0,
    "last_frame_end_us": 0,
    "last_payload_us": 0,
    "frame_count": 0,
    # store the last sent DMX frame as bytes (start code + channels)
    "channel_values": b""
}

# ---------------- Helpers ----------------
def adc8(a: ADC) -> int:
    """Map 16-bit ADC (0..65535) to 0..255 with rounding."""
    return (a.read_u16() + 128) >> 8

def break_and_mab():
    """
    Emit BREAK (~90us low) + MAB (~20us high) using baudrate trick at 100000 8N2.
    One byte 0x00 gives: start(1)+data(8)=9 low bits = ~90us, then 2 stop bits high = ~20us.
    """
    if probe_break: probe_break.value(1)
    uart.init(baudrate=100_000, bits=8, parity=None, stop=2)
    uart.write(b"\x00")
    time.sleep_us(120)  # ensure full frame sent at 100k
    uart.init(baudrate=250_000, bits=8, parity=None, stop=2)
    if probe_break: probe_break.value(0)

# ---------------- Core 0: DMX loop ----------------
def dmx_loop():
    while True:
        frame_start = time.ticks_us()
        if probe_frame: probe_frame.value(1)

        # Build channel data
        # CH1..2 from digital inputs
        for i, pin in enumerate(din, start= 1):
            dmx[i] = 255 if pin.value() else 0
        # CH3.. from ADCs (guard against DMX_CHANNELS smaller than available ADCs)
        for j, a in enumerate(adcs, start=3):
            if j > DMX_CHANNELS:
                break
            dmx[j] = adc8(a)

        # ---- External trigger: pulse BEFORE any UART action of this frame ----
        trigger.value(1)
        time.sleep_us(TRIG_PULSE_US)
        trigger.value(0)

        # BREAK + MAB
        break_and_mab()

        # Send SC + slots at 250k 8N2
        tx0 = time.ticks_us()
        uart.write(dmx)
        tx1 = time.ticks_us()
        payload_us = time.ticks_diff(tx1, tx0)

        frame_end = tx1
        if probe_frame: probe_frame.value(0)

        # Update telemetry (locked) — store a bytes copy of the frame
        with lock:
            telemetry["last_frame_start_us"] = frame_start
            telemetry["last_frame_end_us"]   = frame_end
            telemetry["last_payload_us"]     = payload_us
            telemetry["frame_count"]        += 1
            telemetry["channel_values"]      = bytes(dmx)  # <-- FIX: store as bytes

        # Pace to ~44 Hz
        elapsed = time.ticks_diff(time.ticks_us(), frame_start)
        remaining = FRAME_US - elapsed
        if remaining > 0:
            if remaining > 1000:
                time.sleep_ms(remaining // 1000)
                time.sleep_us(remaining % 1000)
            else:
                time.sleep_us(remaining)

# ---------------- Core 1: Monitor loop ----------------
def monitor_loop():
    # Keep rolling stats over the last second
    last_report = time.ticks_ms()
    samples = 0
    period_sum = 0
    period_min = 10**9
    period_max = 0
    payload_sum = 0
    payload_min = 10**9
    payload_max = 0
    prev_end = None
    prev_seen_count = 0

    while True:
        time.sleep_ms(1)  # small yield

        with lock:
            end_us    = telemetry["last_frame_end_us"]
            start_us  = telemetry["last_frame_start_us"]
            payload_us = telemetry["last_payload_us"]
            count     = telemetry["frame_count"]
            values    = telemetry["channel_values"]  # bytes: [SC, CH1..]

        # Only process a new sample when frame_count advances
        if count != prev_seen_count and start_us and end_us:
            if prev_end is not None:
                period = time.ticks_diff(end_us, prev_end)
                period_sum += period
                period_min = period if period < period_min else period_min
                period_max = period if period > period_max else period_max
                samples += 1
            prev_end = end_us
            prev_seen_count = count

            payload_sum += payload_us
            payload_min = payload_us if payload_us < payload_min else payload_min
            payload_max = payload_us if payload_us > payload_max else payload_max

        now = time.ticks_ms()
        if time.ticks_diff(now, last_report) >= REPORT_MS and samples > 0:
            avg_period = period_sum / samples
            avg_hz = 1_000_000.0 / avg_period
            avg_payload = payload_sum / samples
            print(
                "[DMX] frames:%d  rate: %.2f Hz  period us: avg=%.0f min=%d max=%d  "
                "payload us: avg=%.0f min=%d max=%d" %
                (count, avg_hz, avg_period, period_min, period_max,
                 avg_payload, payload_min, payload_max)
            )

            # Print channel values (skip start code at index 0)
            if values:
                for ch in range(1, min(DMX_CHANNELS, len(values)-1) + 1):
                    src = "DIN" if ch <= len(DIN_PINS) else "ADC"
                    print("  CH%-2d (%s) = %3d" % (ch, src, values[ch]))

            # Reset rolling stats
            last_report = now
            samples = 0
            period_sum = 0
            period_min = 10**9
            period_max = 0
            payload_sum = 0
            payload_min = 10**9
            payload_max = 0

# ---------------- Boot ----------------
def main():
    # Start monitor on the second core
    _thread.start_new_thread(monitor_loop, ())
    # Run DMX on core 0
    try:
        dmx_loop()
    except KeyboardInterrupt:
        if probe_frame: probe_frame.value(0)
        if probe_break: probe_break.value(0)
        trigger.value(0)
        print("save shut down")

if __name__ == "__main__":
    main()

