# MicroPython v1.27.0 on 2025-12-09;
# RP2350 / Raspberry Pico 2 W  Development board
# DMX512 Controller — PIO + DMA
#
# Based on DMX_PIO.py.  The only architectural change is the FIFO-loading
# path: instead of a Python loop calling sm_data.put() for every word, a
# single DMA channel streams the packed frame buffer directly into the
# PIO0 SM1 TX FIFO, paced by DREQ so it matches exactly the rate at which
# the data state machine consumes words.
#
# Public API is identical to DMXControllerPIO so the two files are
# interchangeable.

import rp2
from machine import Pin, Timer, mem32
import time

# ---------------------------------------------------------------------------
# DMX Configuration
# DMX512-A allows one START Code slot plus up to 512 data slots.
# This implementation uses byte-sized DMA transfers into the PIO TX FIFO so a
# full 513-slot packet can be transmitted without padding a partial final word.
# ---------------------------------------------------------------------------

DMX_CHANNELS        = 512       # Full DMX universe: 511 data channels + slot 0 start code
DMX_REFRESH_RATE    = 44        # 44 Hz is the safe full-universe rate with a 1 ms accuracy periodic timer
DMX_TX_PIN          = 0
PIN_TRIGGER         = 1
start_code          = 0x00
DMX_BREAK_US        = 92
DMX_MAB_US          = 12
DMX_SLOT_US         = 44

DEBUG               = False
PRINT_UPDATES       = False

# ---------------------------------------------------------------------------
# State Machine IDs — both in PIO0 so IRQ4 / IRQ5 handshake works
# ---------------------------------------------------------------------------
PIO_BLOCK           = 0
SM_CTRL             = 0
SM_CTRL_CLOCK_HZ    = 6_000_000
SM_DATA             = 1
SM1_DATA_CLOCK_HZ   = 3_000_000

# ---------------------------------------------------------------------------
# DMA constants derived from SM_DATA
#
# PIO0 TX FIFO addresses:  PIO0_BASE + 0x010 + SM * 0x004
#   SM0 → 0x50200010,  SM1 → 0x50200014,  …
#
# DREQ for PIO0 TX channels = SM index (0–3).
# The DMA uses byte transfers so each FIFO entry carries exactly one DMX slot.
# ---------------------------------------------------------------------------
_PIO0_BASE      = 0x50200000
_PIO0_TXF1      = _PIO0_BASE + 0x10 + SM_DATA * 0x04   # 0x50200014
_DREQ_PIO0_TX1  = SM_DATA                               # 1


# ============================================================================
# PIO Program 1: Control SM  (19 instructions)
# ============================================================================
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, sideset_init=rp2.PIO.OUT_HIGH)
def sm_DMX_control():
    """SM0: Wait for CPU IRQ0, generate Break/MAB, handshake one slot at a time."""
    BREAK = 21
    MAB   = 21

    wait(1, irq, 0)                     # 1  wait for CPU IRQ0 (first call only)
    pull()                              # 2  pull slot-count minus one from TX FIFO
    mov(y, osr)                         # 3  save slot-count in y

    wrap_target()
    wait(1, irq, 0)         .side(0)    # 4  wait for CPU IRQ0 each frame

    set(x, BREAK)                       # 5  Break loop counter
    set(pins, 0)            [5]         # 6  line low — Break starts
    label("Break")
    nop()                   [7]         # 7
    nop()                   [7]         # 8
    nop()                   [7]         # 9
    jmp(x_dec, "Break")                 # 10 loop ~92 µs @ 6 MHz

    set(pins, 0)                        # 11 transition point
    set(x, MAB)             [1]         # 12 MAB loop counter
    label("MAB")
    set(pins, 1)            [1]         # 13 line high — MAB
    jmp(x_dec, "MAB")                   # 14 loop ~12 µs @ 6 MHz

    mov(x, y)               .side(1)    # 15 copy slot-count to x; trigger pin high
    label("slot_loop")
    irq(4)                              # 16 tell SM1 to send next slot
    wait(1, irq, 5)                     # 17 wait for SM1 done
    jmp(x_dec, "slot_loop")             # 18 repeat for all slots
    set(pins, 1)            .side(0)    # 19 idle high; trigger pin low
    wrap()


# ============================================================================
# PIO Program 2: Data SM  (13 instructions) 
# ============================================================================
@rp2.asm_pio(
    set_init=rp2.PIO.OUT_HIGH,
    out_init=rp2.PIO.OUT_HIGH,
    sideset_init=rp2.PIO.OUT_HIGH,
    out_shiftdir=rp2.PIO.SHIFT_RIGHT,
    autopull=True,
    pull_thresh=8,
    fifo_join=rp2.PIO.JOIN_TX,
)
def sm_DMX_data():
    """SM1: Wait for IRQ4 from SM0, serialise one slot, signal back via IRQ5."""

    wrap_target()

    wait(1, irq, 4)                     # 1  wait for SM0 IRQ4
    set(y, 7)               .side(0)[5] # 2  start bit low; 8-bit loop counter
    nop()                           [5] # 3  start-bit hold
    label("bit_loop")
    out(pins, 1)                    [4] # 4  shift out 1 bit 4us @ 3 MHz
    nop()                           [5] # 5
    jmp(y_dec, "bit_loop")              # 6  8 data bits
    set(pins, 1)                    [4] # 7  stop bit high 4us @ 3 MHz
    nop()                           [5] # 8
    nop()                           [5] # 9  stop-bit hold 4us @ 3 MHz
    nop()                           [5] # 10
    irq(5)                  .side(1)    # 11 signal SM0 slot done

    wrap()


# ============================================================================
# DMX Controller Class
# ============================================================================
class DMXControllerPIO_DMA:
    """
    DMX512 transmitter using two PIO state machines and one DMA channel.

    The control SM (SM0) generates Break/MAB and orchestrates slot timing.
    The data SM  (SM1) serialises bytes at 250 kbps.
    A DMA channel streams one byte per DMX slot directly into the SM1 TX FIFO,
    paced automatically by DREQ_PIO0_TX1.

    Public API is identical to DMXControllerPIO in DMX_PIO.py.
    """

    def __init__(self, tx_pin=0, channels=512, refresh_rate=43):
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = min(max(1, refresh_rate), 48)
        self.tx_pin = tx_pin

        # TX pin
        self.tx = Pin(tx_pin, Pin.OUT)
        self.tx.value(1)
        self.tx.value(0)

        # Channel shadow + CPU-side frame buffer (start-code byte at index 0)
        self.dmx_data = bytearray(self.channels)
        self.frame    = bytearray([start_code]) + bytearray(self.channels)
        self.tx_frame = bytearray(self.frame)

        # State machines
        self.sm_ctrl = rp2.StateMachine(
            SM_CTRL, sm_DMX_control,
            freq=SM_CTRL_CLOCK_HZ,
            set_base=Pin(DMX_TX_PIN),
            sideset_base=Pin(PIN_TRIGGER),
        )
        self.sm_data = rp2.StateMachine(
            SM_DATA, sm_DMX_data,
            freq=SM1_DATA_CLOCK_HZ,
            set_base=Pin(DMX_TX_PIN),
            out_base=Pin(DMX_TX_PIN),
            sideset_base=Pin(DMX_TX_PIN),
        )

        # DMA channel —————————————————————————————————————————————————————
        self.dma = rp2.DMA()
        # Build control word once; reused every frame.
        # size=0   : one byte per transfer (one FIFO entry per DMX slot)
        # inc_read : advance through the source buffer
        # inc_write: False — always write to the same FIFO address
        # treq_sel : DREQ_PIO0_TX1 = 1 (paced by SM1 TX FIFO vacancy)
        # irq_quiet: True — frame completion is tracked by deterministic PIO time
        self._dma_ctrl = self.dma.pack_ctrl(
            size=0,
            inc_read=True,
            inc_write=False,
            treq_sel=_DREQ_PIO0_TX1,
            irq_quiet=True,
            enable=True,
        )
        self.dma.ctrl = self._dma_ctrl
        # ——————————————————————————————————————————————————————————————————

        # Runtime state
        self.transmitting       = False
        self.timer              = Timer()
        self._frame_in_progress = False
        self._frame_deadline_us = 0
        self._version_in_flight = 0
        self.print_updates      = PRINT_UPDATES
        self.active_refresh_rate = self.refresh_rate
        self.timer_period_ms    = 0
        self.frame_time_us      = DMX_BREAK_US + DMX_MAB_US + (len(self.frame) * DMX_SLOT_US)
        self.data_version       = 0
        self.last_sent_version  = 0
        self.frame_count        = 0
        self.skipped_callbacks  = 0
        self.max_update_us      = 0
        self.sum_update_us      = 0
        self.n_slots            = 0

        if DEBUG:
            print(f"DMX Controller (DMA) initialized: {self.channels} channels, {refresh_rate} Hz")
            print(f"DMA channel: {self.dma.channel}  DREQ: {_DREQ_PIO0_TX1}  "
                  f"FIFO addr: 0x{_PIO0_TXF1:08X}")

    # -----------------------------------------------------------------------
    # Frame progress helpers
    # -----------------------------------------------------------------------
    def _poll_frame_complete(self):
        """Release the in-flight guard once the deterministic PIO transmit time elapsed."""
        if not self._frame_in_progress:
            return False
        if time.ticks_diff(time.ticks_us(), self._frame_deadline_us) < 0:
            return False
        self._frame_in_progress = False
        self.last_sent_version = self._version_in_flight
        return True

    # -----------------------------------------------------------------------
    # Hardware helpers
    # -----------------------------------------------------------------------
    def force_pio_irq0(self):
        """Write to PIO0 FORCEIRQ register to assert IRQ0 in the PIO block."""
        mem32[_PIO0_BASE + 0x34] = 1 << 0
        time.sleep_us(1)

    # -----------------------------------------------------------------------
    # Public control
    # -----------------------------------------------------------------------
    def start(self):
        """Start continuous DMX transmission."""
        if self.transmitting:
            print("DMX transmission already running")
            return

        # Clamp refresh rate to what this frame size can safely sustain with a
        # 1 ms periodic timer.
        frame_bytes     = len(self.frame)       
        self.frame_time_protocol = (DMX_BREAK_US + DMX_MAB_US + ((frame_bytes) * DMX_SLOT_US))/1000
        min_period_ms   = self.frame_time_protocol  # add 1 ms timer overhead
        print(f"Transmitter protocol minimum frame time: {self.frame_time_protocol:.3f} ms ")
        safe_max_hz     = max(1, 1000 // int(min_period_ms))
        self.active_refresh_rate = self.refresh_rate
        if self.active_refresh_rate > safe_max_hz:
            print(f"Refresh {self.active_refresh_rate} Hz too high for {frame_bytes} bytes.")
            print(f"Clamping to safe maximum: {safe_max_hz} Hz.")
            self.active_refresh_rate = safe_max_hz
        self.timer_period_ms = max(min_period_ms, (1000 + self.active_refresh_rate - 1) // self.active_refresh_rate)

        # Activate state machines
        self.sm_data.active(1)
        self.sm_ctrl.active(1)
        time.sleep_ms(100)

        self.transmitting       = True
        self.frame_count        = 0
        self.skipped_callbacks  = 0
        self.max_update_us      = 0
        self.sum_update_us      = 0

        self._frame_in_progress = False
        self._frame_deadline_us = 0
        self._version_in_flight = self.data_version

        # The control SM decrements after each transmitted slot, so preload the
        # total slot count minus one.
        self.n_slots = len(self.frame) - 1
        print(f"Starting DMX transmission: {self.channels} channels, "
              f"{self.n_slots + 1} slots/frame (DMA)")

        # Load slot-count into control SM FIFO, then kick SM past the
        # one-shot initialisation sequence (instructions 1-3).
        try:
            self.sm_ctrl.put(self.n_slots)
        except Exception as e:
            print(f"Error loading control SM FIFO: {e}")
            self.transmitting = False
            return

        self.force_pio_irq0()   # SM0 reads FIFO → moves to wrap_target

        # Start periodic timer; each callback arms one DMA transfer + IRQ0
        if DEBUG:
            print(f"Timer period: {self.timer_period_ms} ms ({self.active_refresh_rate} Hz requested)")
        self.timer.init(period=self.timer_period_ms, mode=Timer.PERIODIC,
                        callback=self.update_frame)
        print("DMX transmission initialised")

    def update_frame(self, timer):
        """Timer callback: snapshot frame, arm DMA, trigger control SM.

        The DMA streams tx_frame → PIO0 SM1 TX FIFO, paced by DREQ so it
        automatically refills the FIFO as the data SM consumes slots.
        The in-flight guard is cleared using the deterministic PIO transmit time,
        not when DMA finishes feeding the FIFO.
        """
        if not self.transmitting:
            return
        self._poll_frame_complete()
        if self._frame_in_progress:
            self.skipped_callbacks += 1
            return

        start_time = time.ticks_us()
        self._frame_in_progress = True
        self._frame_deadline_us = time.ticks_add(start_time, self.frame_time_us)

        try:
            self.tx_frame[:] = self.frame
            self._version_in_flight = self.data_version

            # Arm DMA ———————————————————————————————————————————————————
            # Snapshot the current frame, then stream one byte per DMX slot.
            self.dma.read  = self.tx_frame      # source: exact DMX slot stream
            self.dma.write = _PIO0_TXF1         # destination: PIO0 SM1 TX FIFO
            self.dma.count = len(self.tx_frame) # number of byte transfers
            self.dma.active(1)                  # start — DREQ paces the flow
            # ————————————————————————————————————————————————————————————

            # Trigger control SM to begin Break → MAB → data sequence
            self.force_pio_irq0()

            total_time = time.ticks_diff(time.ticks_us(), start_time)
            self.frame_count  += 1
            self.sum_update_us += total_time
            if total_time > self.max_update_us:
                self.max_update_us = total_time

            if DEBUG:
                print(f"[DMA] Frame {self.frame_count} armed in {total_time} µs")

        except Exception as e:
            print(f"[ERROR] update_frame: {e}")
            self._frame_in_progress = False

    def stop(self):
        """Stop DMX transmission."""
        if self.transmitting:
            self.timer.deinit()
            self.dma.active(0)          # abort any in-progress DMA transfer
            time.sleep_ms(10)
            self.sm_ctrl.active(0)
            self.sm_data.active(0)
            self._frame_in_progress = False
            self._frame_deadline_us = 0
            self.transmitting = False
            print("DMX transmission stopped")

        self.tx = Pin(self.tx_pin, Pin.OUT)
        self.tx.value(1)

    # -----------------------------------------------------------------------
    # Channel setters — identical to DMX_PIO.py
    # -----------------------------------------------------------------------
    def set_channel(self, channel, value):
        """Set a single DMX channel (1-indexed, 1-512)."""
        if 1 <= channel <= self.channels:
            value = max(0, min(255, value))
            self.dmx_data[channel - 1]  = value
            self.frame[channel]          = value   # +1 offset for start code
            self.data_version += 1
            if self.print_updates:
                print(f"Channel {channel} = {value}")
        else:
            print(f"Error: Channel {channel} out of range (1-{self.channels})")

    def set_all(self, value):
        """Set all channels to the same value."""
        value = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i]    = value
            self.frame[i + 1]   = value
        self.data_version += 1
        if self.print_updates:
            print(f"All channels set to {value}")

    def set_channels_bulk(self, values):
        """Set many channels at once from bytes / bytearray / list / tuple."""
        n = min(len(values), self.channels)
        if n <= 0:
            return
        if isinstance(values, (bytes, bytearray)):
            self.dmx_data[:n]       = values[:n]
            self.frame[1:n + 1]     = values[:n]
        else:
            for i in range(n):
                v = max(0, min(255, values[i]))
                self.dmx_data[i]    = v
                self.frame[i + 1]   = v
        self.data_version += 1
        if self.print_updates:
            print(f"Bulk update applied to {n} channels")

    def clear_all(self):
        self.set_all(0)

    def set_lsb_test_pattern(self):
        if self.channels < 3:
            print("Need at least 3 channels for LSB test pattern")
            return
        self.set_channel(1, 0x01)
        self.set_channel(2, 0x80)
        self.set_channel(3, 0x55)
        print("LSB test pattern loaded: CH1=0x01 CH2=0x80 CH3=0x55")
        print("Expected LSB-first bits: 0x01→10000000  0x80→00000001")

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------
    def benchmark_updates(self):
        """Measure setter-path cost (no transmission side effects)."""
        timer_was_running  = self.transmitting
        restore_refresh    = self.active_refresh_rate
        if timer_was_running:
            self.timer.deinit()
            self.transmitting = False

        old_verbose        = self.print_updates
        self.print_updates = False

        t0 = time.ticks_us()
        self.set_all(255)
        t_all = time.ticks_diff(time.ticks_us(), t0)

        t1 = time.ticks_us()
        for ch in range(1, self.channels + 1):
            self.set_channel(ch, 0)
        t_single = time.ticks_diff(time.ticks_us(), t1)

        t2 = time.ticks_us()
        bulk = bytearray(self.channels)
        for i in range(self.channels):
            bulk[i] = 128
        self.set_channels_bulk(bulk)
        t_bulk = time.ticks_diff(time.ticks_us(), t2)

        self.print_updates = old_verbose

        if timer_was_running:
            self.timer.init(period=self.timer_period_ms, mode=Timer.PERIODIC,
                            callback=self.update_frame)
            self.transmitting = True

        print("Benchmark (setter path only):")
        print(f"  set_all():           {t_all   / 1000:.3f} ms")
        print(f"  512x set_channel():  {t_single / 1000:.3f} ms")
        print(f"  set_channels_bulk(): {t_bulk   / 1000:.3f} ms")

    def benchmark_live_latency(self, value=255, timeout_ms=2000):
        """Measure command-to-next-sent-frame latency while transmitting."""
        if not self.transmitting:
            print("Start transmission first")
            return
        self.set_all(value)
        target  = self.data_version
        t0      = time.ticks_us()
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while self.last_sent_version < target:
            self._poll_frame_complete()
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                print("Live latency timeout")
                return
            time.sleep_ms(1)
        dt_us = time.ticks_diff(time.ticks_us(), t0)
        print(f"Live command→sent latency: {dt_us / 1000:.3f} ms")

    def status(self):
        print("\n" + "=" * 40)
        print("DMX Controller Status  (DMA mode)")
        print("=" * 40)
        print(f"Channels:                {self.channels}")
        print(f"Transmitting:            {self.transmitting}")
        print(f"Refresh (req / active):  {self.refresh_rate} / {self.active_refresh_rate} Hz")
        print(f"Timer period:            {self.timer_period_ms} ms")
        print(f"Frame time:              {self.frame_time_us / 1000:.3f} ms")
        print(f"Frame count:             {self.frame_count}")
        print(f"Skipped callbacks:       {self.skipped_callbacks}")
        if self.frame_count > 0:
            avg_us = self.sum_update_us / self.frame_count
            print(f"Callback time avg/max:   {avg_us/1000:.3f} / {self.max_update_us/1000:.3f} ms")
            print(f"  (time to snapshot + arm DMA; actual TX is in PIO)")
        print(f"\nDMA channel:   {self.dma.channel}")
        print(f"DMA DREQ:      {_DREQ_PIO0_TX1}")
        print(f"DMA FIFO addr: 0x{_PIO0_TXF1:08X}")
        print("\nFirst 8 channels:")
        for i in range(min(8, self.channels)):
            print(f"  Channel {i+1}: {self.dmx_data[i]}")
        if self.transmitting:
            try:
                print(f"\nFIFO status:")
                print(f"  Data SM TX FIFO:    {self.sm_data.tx_fifo()} / 8 entries")
                print(f"  Control SM TX FIFO: {self.sm_ctrl.tx_fifo()} / 8 entries")
            except Exception:
                pass
        print("=" * 40)

    def help(self):
        print("\nAvailable commands:")
        print("  start           - Start DMX transmission")
        print("  stop            - Stop DMX transmission")
        print("  status          - Show current status")
        print("  clear           - Clear all channels to 0")
        print("  c <ch> <val>    - Set channel <ch> to value <val> (1-indexed)")
        print("  all <val>       - Set all channels to value <val>")
        print("  bench           - Benchmark setter path")
        print("  benchlive       - Benchmark live command-to-sent latency")
        print("  lsbtest         - Load LSB test pattern into first channels")
        print("  verbose on/off  - Toggle update prints")
        print("  help            - Show this help")
        print("  exit            - Exit\n")


# ============================================================================
# Interactive Test Interface
# ============================================================================
def main():
    print("=" * 50)
    print("DMX512 Controller — RP2350 with PIO + DMA")
    print("=" * 50)

    dmx = DMXControllerPIO_DMA(
        tx_pin=DMX_TX_PIN,
        channels=DMX_CHANNELS,
        refresh_rate=DMX_REFRESH_RATE,
    )
    dmx.start()
    dmx.help()

    while True:
        try:
            cmd = input("DMX> ").strip().lower()

            if cmd == "exit":
                dmx.stop()
                print("Exiting...")
                break

            elif cmd == "start":
                dmx.start()

            elif cmd == "stop":
                dmx.stop()

            elif cmd == "status":
                dmx.status()

            elif cmd == "clear":
                dmx.clear_all()

            elif cmd == "bench":
                dmx.benchmark_updates()

            elif cmd == "benchlive":
                dmx.benchmark_live_latency()

            elif cmd == "lsbtest":
                dmx.set_lsb_test_pattern()

            elif cmd in ("verbose on", "verbose off"):
                dmx.print_updates = (cmd == "verbose on")
                print(f"Update prints: {'ON' if dmx.print_updates else 'OFF'}")

            elif cmd.startswith("c "):
                parts = cmd.split()
                if len(parts) == 3:
                    try:
                        dmx.set_channel(int(parts[1]), int(parts[2]))
                    except ValueError:
                        print("Error: channel and value must be integers")
                else:
                    print("Usage: c <channel> <value>")

            elif cmd.startswith("all "):
                parts = cmd.split()
                if len(parts) == 2:
                    try:
                        dmx.set_all(int(parts[1]))
                    except ValueError:
                        print("Error: value must be an integer")
                else:
                    print("Usage: all <value>")

            elif cmd == "help":
                dmx.help()

            else:
                print(f"Unknown command: '{cmd}'")

        except KeyboardInterrupt:
            dmx.stop()
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()