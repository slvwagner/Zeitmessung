# MicroPython v1.27.0 on 2025-12-09;
# RP2350 / Raspberry Pico 2 W  Development board
# DMX512 Controller — PIO + DMA
#
# Based on DMX_PIO.py.  The only architectural change is the FIFO-loading
# path: instead of a Python loop calling sm_data.put() for every word, a
# single DMA channel streams the packed frame buffer directly into the
# active data SM TX FIFO, paced by DREQ so it matches exactly the rate at which
# the data state machine consumes words.
#
# Public API is identical to DMXControllerPIO so the two files are
# interchangeable.

import gc
import rp2
from machine import Pin, Timer, mem32, RTC
import time

try:
    import micropython
except Exception:
    micropython = None

try:
    import network
except Exception:
    network = None

try:
    import ntptime
except Exception:
    ntptime = None

try:
    import urequests as requests
except Exception:
    requests = None

try:
    import ujson as json
except Exception:
    try:
        import json
    except Exception:
        json = None

try:
    from credentials import SSID, PASSWORD, TIMEZONE_OFFSET
except Exception:
    SSID = None
    PASSWORD = None
    TIMEZONE_OFFSET = ""

# ---------------------------------------------------------------------------
# DMX Configuration
# DMX512-A allows one START Code slot plus up to 512 data slots.
# This implementation uses byte-sized DMA transfers into the PIO TX FIFO so a
# full 513-slot packet can be transmitted without padding a partial final word.
# ---------------------------------------------------------------------------

DMX_CHANNELS        = 512       # Full DMX universe: 511 data channels + slot 0 start code
DMX_REFRESH_RATE    = 40        # 40 Hz is the safe full-universe rate with a 1 ms accuracy periodic timer
DMX_TX_PIN          = 2
DMX_TRIGGER_PIN     = 3
PIN_TRIGGER         = DMX_TRIGGER_PIN
start_code          = 0xFF
DMX_BREAK_US        = 92
DMX_MAB_US          = 12
DMX_SLOT_US         = 44

DEBUG               = False
PRINT_UPDATES       = False
AUTO_STATUS_LOG     = False
STATUS_LOG_PERIOD_MS = 120_000
AUTO_NTP_SYNC       = True
USE_FRAME_DONE_SCHEDULER = False
NTP_SYNC_TIMEOUT_MS = 12_000
NTP_HOSTS           = ("pool.ntp.org", "time.google.com", "129.6.15.28")
HTTP_TIME_URLS      = (
    "http://worldtimeapi.org/api/timezone/Etc/UTC",
    "http://worldtimeapi.org/api/ip",
)

# ---------------------------------------------------------------------------
# State Machine layout
# Keep control/data SMs in the same PIO block so IRQ4 / IRQ5 handshake stays
# local to that block. Default is PIO2 (SM8/SM9).
# ---------------------------------------------------------------------------
PIO_BASES            = (0x50200000, 0x50300000, 0x50400000)
PIO_BLOCK_SM_IDS     = (
    (0, 1),
    (4, 5),
    (8, 9),
)
# Preferred fallback order for auto-allocation retries.
# Keep (4,5) last because SM5 is used by beam logic in gate apps.
PIO_FALLBACK_SM_PAIRS = (
    (8, 9),
    (0, 1),
    (4, 5),
)
PIO_TX_DREQS         = (
    (0, 1, 2, 3),
    (8, 9, 10, 11),
    (16, 17, 18, 19),
)
SM_CTRL_CLOCK_HZ     = 6_000_000
SM1_DATA_CLOCK_HZ    = 3_000_000
_IRQ_FRAME_DONE      = 2
DEFAULT_CTRL_SM_ID   = 8
DEFAULT_DATA_SM_ID   = 9


def _resolve_sm_pair_config(sm_ctrl_id, sm_data_id):
    pio_block = None
    for idx, pair in enumerate(PIO_BLOCK_SM_IDS):
        if pair == (sm_ctrl_id, sm_data_id):
            pio_block = idx
            break
    if pio_block is None:
        raise ValueError("sm_ctrl_id/sm_data_id must be one of (0,1), (4,5), or (8,9)")
    local_sm_data = sm_data_id & 0x03
    pio_base = PIO_BASES[pio_block]
    pio_txf_data = pio_base + 0x10 + (local_sm_data * 0x04)
    dma_dreq = PIO_TX_DREQS[pio_block][local_sm_data]
    return {
        "pio_block": pio_block,
        "sm_ctrl_id": sm_ctrl_id,
        "sm_data_id": sm_data_id,
        "pio_base": pio_base,
        "pio_txf_data": pio_txf_data,
        "dma_dreq": dma_dreq,
    }


# ============================================================================
# PIO Program 1: Control SM  (20 instructions)
# ============================================================================
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, sideset_init=rp2.PIO.OUT_HIGH)
def sm_DMX_control():
    """SM0: Wait for CPU IRQ0, generate Break/MAB, handshake one slot at a time."""
    BREAK = 22
    MAB   = 21

    wait(1, irq, 0)                     # 1  wait for CPU IRQ0 (first call only)
    pull()                              # 2  pull slot-count minus one from TX FIFO
    mov(y, osr)                         # 3  save slot-count in y

    wrap_target()
    wait(1, irq, 0)         .side(1)    # 4  wait for CPU IRQ0 to start frame transmission

    set(x, BREAK)                       # 5  Break loop counter
    set(pins, 1)            [5]         # 6  line low — Break starts
    label("Break")
    nop()                   [7]         # 7
    nop()                   [7]         # 8
    nop()                   [7]         # 9
    jmp(x_dec, "Break")                 # 10 loop ~92 µs @ 6 MHz

    set(pins, 0)                        # 11 transition point
    set(x, MAB)             [1]         # 12 MAB loop counter
    label("MAB")
    set(pins, 0)            [1]         # 13 line high — MAB
    jmp(x_dec, "MAB")                   # 14 loop ~12 µs @ 6 MHz

    mov(x, y)               .side(0)    # 15 copy slot-count to x; trigger pin high
    label("slot_loop")
    irq(4)                              # 16 tell SM1 to send next slot
    wait(1, irq, 5)                     # 17 wait for SM1 done
    jmp(x_dec, "slot_loop")             # 18 repeat for all slots
    set(pins, 0)            .side(1)    # 19 idle high; trigger pin low
    irq(2)                              # 20 frame complete marker for CPU gating (processor-visible)
    wrap()


# ============================================================================
# PIO Program 2: Data SM  (11 instructions)
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
    set(y, 7)               .side(1)[5] # 2  start bit low; 8-bit loop counter
    nop()                           [5] # 3  start-bit hold
    label("bit_loop")
    out(pins, 1)                    [4] # 4  shift out 1 bit 4us @ 3 MHz
    nop()                           [5] # 5
    jmp(y_dec, "bit_loop")              # 6  8 data bits
    set(pins, 0)                    [4] # 7  stop bit high 4us @ 3 MHz
    nop()                           [5] # 8
    nop()                           [5] # 9  stop-bit hold 4us @ 3 MHz
    nop()                           [5] # 10
    irq(5)                  .side(0)    # 11 signal SM0 slot done

    wrap()


# ============================================================================
# DMX Controller Class
# ============================================================================
class DMXControllerPIO_DMA:
    """
    DMX512 transmitter using two PIO state machines and one DMA channel.

    The control SM generates Break/MAB and orchestrates slot timing.
    The data SM serialises bytes at 250 kbps.
    A DMA channel streams one byte per DMX slot directly into the active
    data SM TX FIFO, paced by that block's TX DREQ.

    Public API is identical to DMXControllerPIO in DMX_PIO.py.
    """

    def __init__(self, tx_pin=DMX_TX_PIN, trigger_pin=DMX_TRIGGER_PIN,
                 channels=512, refresh_rate=43,
                 sm_ctrl_id=DEFAULT_CTRL_SM_ID, sm_data_id=DEFAULT_DATA_SM_ID):
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = min(max(1, refresh_rate), 43)
        self.tx_pin = tx_pin
        self.trigger_pin = trigger_pin
        self.requested_sm_ctrl_id = sm_ctrl_id
        self.requested_sm_data_id = sm_data_id
        self.pio_block = None
        self.sm_ctrl_id = None
        self.sm_data_id = None
        self._pio_base = None
        self._pio_txf_data = None
        self._dma_dreq = None
        self.sm_ctrl = None
        self.sm_data = None
        # Invert transmitted channel bytes in Python before DMA/PIO sends them.
        # Start code (slot 0) is intentionally not inverted.
        self.invert_data_bits = True

        # TX pin
        self.tx = Pin(tx_pin, Pin.OUT)
        self.tx.value(1)
        self.tx.value(0)

        # Channel shadow + edit buffer (start-code byte at index 0).
        # DMA always reads tx_frame; edit writes go to frame and are copied as
        # dirty-byte deltas right before arming each frame.
        self.dmx_data = bytearray(self.channels)
        self.frame    = bytearray([start_code]) + bytearray(self.channels)
        self.tx_frame = bytearray(self.frame)
        self._dirty_mask = bytearray(len(self.frame))
        self._dirty_first = len(self.frame)
        self._dirty_last = -1

        # Scheduler/control callback state must exist before SM allocation,
        # because first-init config may attach optional IRQ handlers.
        self._ctrl_irq_supported = False
        self._frame_done_scheduler = USE_FRAME_DONE_SCHEDULER
        self._frame_period_us = 0
        self._next_frame_due_us = 0

        # State machines
        self._allocate_state_machines()

        # DMA channel —————————————————————————————————————————————————————
        self.dma = rp2.DMA()
        # Build control word once; reused every frame.
        # size=0   : one byte per transfer (one FIFO entry per DMX slot)
        # inc_read : advance through the source buffer
        # inc_write: False — always write to the same FIFO address
        # treq_sel : DREQ for the selected data SM TX FIFO vacancy
        # irq_quiet: True — frame completion is tracked by deterministic PIO time
        self._dma_ctrl = self.dma.pack_ctrl(
            size=0,
            inc_read=True,
            inc_write=False,
            treq_sel=self._dma_dreq,
            irq_quiet=True,
            enable=True,
        )
        self.dma.ctrl = self._dma_ctrl
        # ——————————————————————————————————————————————————————————————————

        # Runtime state
        self.transmitting       = False
        self.timer              = None
        self._manual_scheduler  = False
        self._next_frame_ms     = 0
        self._timer_tick_pending = False
        self._timer_schedule_pending = False
        self.timer_schedule_overruns = 0
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
        self.prime_timeouts     = 0
        self.auto_resyncs       = 0
        self._consecutive_prime_timeouts = 0
        self.frame_timeouts     = 0
        self.auto_status_log    = AUTO_STATUS_LOG
        self.status_log_period_ms = STATUS_LOG_PERIOD_MS
        self._next_status_log_ms = 0
        self.auto_ntp_sync      = AUTO_NTP_SYNC
        self.time_synced        = False
        self.last_ntp_sync_s    = None
        try:
            self.tz_offset_hours = float(TIMEZONE_OFFSET) if TIMEZONE_OFFSET not in (None, "") else 0.0
        except Exception:
            self.tz_offset_hours = 0.0

        if DEBUG:
            print(f"DMX Controller (DMA) initialized: {self.channels} channels, {refresh_rate} Hz")
            print(f"PIO block: {self.pio_block}  CTRL SM: {self.sm_ctrl_id}  DATA SM: {self.sm_data_id}")
            print(f"DMA channel: {self.dma.channel}  DREQ: {self._dma_dreq}  "
                f"FIFO addr: 0x{self._pio_txf_data:08X}")

    def _tx_encode_value(self, value):
        """Encode one channel byte for transmission (optional bit inversion)."""
        value = int(value) & 0xFF
        if self.invert_data_bits:
            return value ^ 0xFF
        return value

    # -----------------------------------------------------------------------
    # Frame progress helpers
    # -----------------------------------------------------------------------
    def _poll_frame_complete(self):
        """Release in-flight guard when control SM reports frame-done IRQ."""
        if not self._frame_in_progress:
            return False
        irq_flags = mem32[self._pio_base + 0x30]
        if irq_flags & (1 << _IRQ_FRAME_DONE):
            mem32[self._pio_base + 0x30] = 1 << _IRQ_FRAME_DONE
            self._frame_in_progress = False
            self.last_sent_version = self._version_in_flight
            return True
        if time.ticks_diff(time.ticks_us(), self._frame_deadline_us) >= 0:
            self.frame_timeouts += 1
            self._frame_in_progress = False
            self._resync_after_fault("frame completion timeout")
            return True
        return False

    # -----------------------------------------------------------------------
    # Hardware helpers
    # -----------------------------------------------------------------------
    def _clear_pio_irqs(self):
        """Clear all latched IRQ flags in the active PIO block (RW1C)."""
        mem32[self._pio_base + 0x30] = 0xFF

    def _configure_sm_pair(self, sm_ctrl_id, sm_data_id):
        cfg = _resolve_sm_pair_config(sm_ctrl_id, sm_data_id)
        self.pio_block = cfg["pio_block"]
        self.sm_ctrl_id = cfg["sm_ctrl_id"]
        self.sm_data_id = cfg["sm_data_id"]
        self._pio_base = cfg["pio_base"]
        self._pio_txf_data = cfg["pio_txf_data"]
        self._dma_dreq = cfg["dma_dreq"]

    def _deactivate_state_machines(self):
        for sm in (self.sm_ctrl, self.sm_data):
            if sm is None:
                continue
            try:
                sm.active(0)
            except Exception:
                pass

    def _is_retryable_alloc_error(self, exc):
        msg = str(exc)
        if isinstance(exc, OSError):
            return True
        if "claimed by external resource" in msg:
            return True
        if "ENOMEM" in msg:
            return True
        return False

    def _candidate_sm_pairs(self):
        requested = (self.requested_sm_ctrl_id, self.requested_sm_data_id)
        ordered = [requested]
        for pair in PIO_FALLBACK_SM_PAIRS:
            if pair not in ordered:
                ordered.append(pair)
        return ordered

    def _allocate_state_machines(self):
        gc.collect()
        last_exc = None
        for idx, pair in enumerate(self._candidate_sm_pairs()):
            sm_ctrl_id, sm_data_id = pair
            self._configure_sm_pair(sm_ctrl_id, sm_data_id)
            try:
                self._init_state_machines(first_init=True)
                if idx > 0:
                    print(
                        "[WARN] DMX SM fallback: requested ({},{}) unavailable, using ({},{})".format(
                            self.requested_sm_ctrl_id,
                            self.requested_sm_data_id,
                            sm_ctrl_id,
                            sm_data_id,
                        )
                    )
                else:
                    print(
                        "[INFO] DMX SM allocation: using requested pair ({},{})".format(
                            sm_ctrl_id,
                            sm_data_id,
                        )
                    )
                return
            except Exception as exc:
                last_exc = exc
                print(
                    "[WARN] DMX SM alloc failed for pair ({},{}): {}".format(
                        sm_ctrl_id,
                        sm_data_id,
                        exc,
                    )
                )
                self._deactivate_state_machines()
                self.sm_ctrl = None
                self.sm_data = None
                if not self._is_retryable_alloc_error(exc):
                    raise
        raise last_exc

    def _init_state_machines(self, first_init=False):
        """(Re)initialise SM configuration so TX pin mux/fifos are clean."""
        tx_pin = Pin(self.tx_pin)
        trig_pin = Pin(self.trigger_pin)

        if first_init:
            sm_ctrl = None
            sm_data = None
            try:
                sm_ctrl = rp2.StateMachine(
                    self.sm_ctrl_id, sm_DMX_control,
                    freq=SM_CTRL_CLOCK_HZ,
                    set_base=tx_pin,
                    sideset_base=trig_pin,
                )
            except Exception as exc:
                raise RuntimeError(
                    "SM{} control allocation failed: {}".format(self.sm_ctrl_id, exc)
                )

            try:
                sm_data = rp2.StateMachine(
                    self.sm_data_id, sm_DMX_data,
                    freq=SM1_DATA_CLOCK_HZ,
                    set_base=tx_pin,
                    out_base=tx_pin,
                    sideset_base=tx_pin,
                )
            except Exception as exc:
                try:
                    sm_ctrl.active(0)
                except Exception:
                    pass
                raise RuntimeError(
                    "SM{} data allocation failed: {}".format(self.sm_data_id, exc)
                )

            self.sm_ctrl = sm_ctrl
            self.sm_data = sm_data
            self._configure_ctrl_irq_callback()
            return

        self.sm_ctrl.init(
            sm_DMX_control,
            freq=SM_CTRL_CLOCK_HZ,
            set_base=tx_pin,
            sideset_base=trig_pin,
        )
        self.sm_data.init(
            sm_DMX_data,
            freq=SM1_DATA_CLOCK_HZ,
            set_base=tx_pin,
            out_base=tx_pin,
            sideset_base=tx_pin,
        )
        # Reset program counters + shift state to a known start point.
        self.sm_ctrl.restart()
        self.sm_data.restart()
        self._configure_ctrl_irq_callback()

    def _configure_ctrl_irq_callback(self):
        self._ctrl_irq_supported = False
        # Only required for frame-done scheduler mode.
        if not self._frame_done_scheduler:
            return
        try:
            self.sm_ctrl.irq(handler=self._sm_ctrl_irq_handler)
            self._ctrl_irq_supported = True
        except Exception:
            self._ctrl_irq_supported = False

    def _sm_ctrl_irq_handler(self, _sm):
        """Frame-done callback from control SM: request non-IRQ service pass."""
        if not self.transmitting:
            return
        if micropython is None:
            return
        if self._timer_schedule_pending:
            return
        self._timer_schedule_pending = True
        try:
            micropython.schedule(self._scheduled_service, 0)
        except Exception:
            self._timer_schedule_pending = False
            self.timer_schedule_overruns += 1

    def force_pio_irq0(self):
        """Write to PIO FORCEIRQ register to assert IRQ0 in the active PIO block."""
        mem32[self._pio_base + 0x34] = 1 << 0
        time.sleep_us(1)

    def _wait_dma_fifo_prime(self, timeout_us=500):
        """Wait briefly until DMA has pushed at least one byte into SM1 TX FIFO."""
        t0 = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), t0) < timeout_us:
            try:
                if self.sm_data.tx_fifo() > 0:
                    return True
            except Exception:
                return False
            time.sleep_us(2)
        return False

    def _resync_after_fault(self, reason):
        """Recover without user stop/start when SM/DMA handshake gets stuck."""
        print(f"Recover SM/DMA handshake: {self._consecutive_prime_timeouts}")
        try:
            self.dma.active(0)
            self.sm_ctrl.active(0)
            self.sm_data.active(0)
            self._clear_pio_irqs()
            self._init_state_machines(first_init=False)
            self.sm_data.active(1)
            self.sm_ctrl.active(1)
            time.sleep_us(200)
            self.sm_ctrl.put(self.n_slots)
            self.force_pio_irq0()
            self._frame_in_progress = False
            self._frame_deadline_us = 0
            self._consecutive_prime_timeouts = 0
            self.auto_resyncs += 1
            print(f"[WARN] DMX auto-resync: {reason}")
            return True
        except Exception as e:
            print(f"[ERROR] auto-resync failed: {e}")
            return False

    def _start_periodic_timer(self):
        """Start periodic DMX timer with a lightweight hard-IRQ callback."""
        if self.timer is not None:
            try:
                self.timer.deinit()
            except Exception:
                pass
        gc.collect()
        self._timer_tick_pending = False
        self._timer_schedule_pending = False
        self.timer = Timer()
        self.timer.init(period=self.timer_period_ms, mode=Timer.PERIODIC,
                        callback=self._timer_callback)

    def _timer_callback(self, timer):
        """Hard-IRQ context: never arm DMA here, only request service."""
        if not self.transmitting:
            return
        self._timer_tick_pending = True
        if micropython is None:
            return
        if self._timer_schedule_pending:
            return
        self._timer_schedule_pending = True
        try:
            micropython.schedule(self._scheduled_service, 0)
        except Exception:
            self._timer_schedule_pending = False
            self.timer_schedule_overruns += 1

    def _scheduled_service(self, _arg):
        self._timer_schedule_pending = False
        self.service()

    def _schedule_next_frame(self):
        self._next_frame_ms = time.ticks_add(time.ticks_ms(), int(self.timer_period_ms))

    def service(self):
        """Service DMX frame scheduling and completion handling."""
        if not self.transmitting:
            return
        self._poll_frame_complete()

        if self._frame_done_scheduler and self._ctrl_irq_supported:
            if self._frame_in_progress:
                return
            if self._next_frame_due_us:
                now_us = time.ticks_us()
                rem_us = time.ticks_diff(self._next_frame_due_us, now_us)
                if rem_us > 0:
                    # Keep wait short and deterministic; avoids ms-timer jitter.
                    time.sleep_us(rem_us)
            self.update_frame(None)
            return

        if self._frame_in_progress:
            return
        if self._manual_scheduler:
            if time.ticks_diff(time.ticks_ms(), self._next_frame_ms) < 0:
                return
            self.update_frame(None)
            self._schedule_next_frame()
            return
        if not self._timer_tick_pending:
            return
        self._timer_tick_pending = False
        self.update_frame(None)

    def _mark_dirty_index(self, idx):
        self._dirty_mask[idx] = 1
        if idx < self._dirty_first:
            self._dirty_first = idx
        if idx > self._dirty_last:
            self._dirty_last = idx

    def _apply_dirty_to_tx_frame(self):
        if self._dirty_last < self._dirty_first:
            return
        first = self._dirty_first
        last = self._dirty_last
        for idx in range(first, last + 1):
            if self._dirty_mask[idx]:
                self.tx_frame[idx] = self.frame[idx]
                self._dirty_mask[idx] = 0
        self._dirty_first = len(self.frame)
        self._dirty_last = -1

    def _format_timestamp(self):
        """Return local timestamp string with sync marker."""
        try:
            now_s = int(time.time() + (self.tz_offset_hours * 3600))
            tm = time.gmtime(now_s)
            stamp = "%04d-%02d-%02d %02d:%02d:%02d" % (tm[0], tm[1], tm[2], tm[3], tm[4], tm[5])
        except Exception:
            stamp = "0000-00-00 00:00:00"
        if self.time_synced:
            return stamp
        return stamp + " (unsynced)"

    def _set_rtc_from_epoch(self, epoch_s):
        """Set RP2 RTC from Unix epoch seconds."""
        tm = time.gmtime(int(epoch_s))
        # RTC tuple: (year, month, day, weekday, hour, minute, second, subseconds)
        RTC().datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))
        self.time_synced = True
        self.last_ntp_sync_s = int(time.time())

    def _sync_time_http(self):
        """Fallback time sync using HTTP API when ntptime is unavailable."""
        if requests is None or json is None:
            print("[TIME] HTTP fallback unavailable (urequests/ujson missing)")
            return False

        for url in HTTP_TIME_URLS:
            r = None
            try:
                r = requests.get(url, timeout=5)
                if r.status_code != 200:
                    print("[TIME] HTTP time host failed:", url, "status", r.status_code)
                    continue

                try:
                    data = r.json()
                except Exception:
                    data = json.loads(r.text)

                epoch = data.get("unixtime") if isinstance(data, dict) else None
                if epoch is None:
                    print("[TIME] HTTP time host invalid payload:", url)
                    continue

                self._set_rtc_from_epoch(int(epoch))
                print("[TIME] HTTP time synced via", url)
                return True
            except Exception as e:
                print("[TIME] HTTP time host failed:", url, e)
            finally:
                try:
                    if r is not None:
                        r.close()
                except Exception:
                    pass

        print("[TIME] all HTTP time hosts failed")
        return False

    def sync_time_ntp(self, timeout_ms=NTP_SYNC_TIMEOUT_MS):
        """Sync RTC from NTP using Pico W Wi-Fi. Returns True on success."""
        if network is None:
            print("[TIME] network module unavailable (non-Wi-Fi build)")
            return False
        if not SSID or not PASSWORD:
            print("[TIME] missing Wi-Fi credentials for NTP sync")
            return False

        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        try:
            if not sta.isconnected():
                print("[TIME] connecting Wi-Fi for NTP sync...")
                sta.connect(SSID, PASSWORD)
                t0 = time.ticks_ms()
                while not sta.isconnected():
                    if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                        print("[TIME] Wi-Fi timeout; NTP sync skipped")
                        return False
                    time.sleep_ms(200)

            try:
                print("[TIME] Wi-Fi ready:", sta.ifconfig()[0])
            except Exception:
                print("[TIME] Wi-Fi connected")

            if ntptime is None:
                print("[TIME] ntptime module unavailable; trying HTTP time fallback")
                return self._sync_time_http()

            for host in NTP_HOSTS:
                try:
                    ntptime.host = host
                    ntptime.settime()
                    self.time_synced = True
                    self.last_ntp_sync_s = time.time()
                    print("[TIME] NTP synced via", host)
                    return True
                except Exception as e:
                    print("[TIME] NTP host failed:", host, e)

            print("[TIME] all NTP hosts failed; trying HTTP time fallback")
            return self._sync_time_http()
        except Exception as e:
            print("[TIME] NTP sync error:", e)
            return False

    def _maybe_auto_status_log(self):
        """Auto status logging is intentionally disabled in realtime TX path."""
        return

    # -----------------------------------------------------------------------
    # Public control
    # -----------------------------------------------------------------------
    def start(self):
        """Start continuous DMX transmission."""
        if self.transmitting:
            print("DMX transmission already running")
            return

        # Re-prime restart-sensitive hardware state.
        self.dma.active(0)
        self.dma.ctrl = self._dma_ctrl
        self._clear_pio_irqs()
        self._init_state_machines(first_init=False)

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
        self.prime_timeouts     = 0
        self.auto_resyncs       = 0
        self._consecutive_prime_timeouts = 0
        self.frame_timeouts     = 0
        self._next_status_log_ms = time.ticks_add(time.ticks_ms(), self.status_log_period_ms)

        self._frame_in_progress = False
        self._frame_deadline_us = 0
        self._version_in_flight = self.data_version
        self._manual_scheduler = False
        self._next_frame_ms = 0
        self._timer_tick_pending = False
        self._timer_schedule_pending = False
        self.timer_schedule_overruns = 0
        self._frame_period_us = max(1, int(1_000_000 / self.active_refresh_rate))
        self._next_frame_due_us = 0
        if self._frame_done_scheduler and not self._ctrl_irq_supported:
            print("[WARN] Frame-done scheduler unsupported here; falling back to timer scheduler")
            self._frame_done_scheduler = False

        if self.auto_ntp_sync and not self.time_synced:
            self.sync_time_ntp()

        # The control SM decrements after each transmitted slot, so preload the
        # total slot count minus one.
        self.n_slots = len(self.frame) - 1
        print(f"Using PIO{self.pio_block}: CTRL SM{self.sm_ctrl_id}, DATA SM{self.sm_data_id}")
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

        # Scheduler selection:
        # - frame-done scheduler: next frame is armed from SM frame-done callback.
        # - timer scheduler: legacy periodic callback path.
        if self._frame_done_scheduler and self._ctrl_irq_supported:
            self.update_frame(None)
        else:
            if DEBUG:
                print(f"Timer period: {self.timer_period_ms} ms ({self.active_refresh_rate} Hz requested)")
            self._start_periodic_timer()
        print("DMX transmission initialised")

    def update_frame(self, timer):
        """Timer callback: snapshot frame, arm DMA, trigger control SM.

        The DMA streams tx_frame → the active data SM TX FIFO, paced by DREQ so it
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
        self._frame_deadline_us = time.ticks_add(start_time, self.frame_time_us + 3000)

        try:
            self._apply_dirty_to_tx_frame()
            self._version_in_flight = self.data_version

            # Arm DMA ———————————————————————————————————————————————————
            # Snapshot the current frame, then stream one byte per DMX slot.
            self.dma.read  = self.tx_frame      # source: exact DMX slot stream
            self.dma.write = self._pio_txf_data # destination: active data SM TX FIFO
            self.dma.count = len(self.tx_frame) # number of byte transfers
            self.dma.active(1)                  # start — DREQ paces the flow
            # ————————————————————————————————————————————————————————————

            # Ensure first slot data is present before SM0 begins slot loop.
            if not self._wait_dma_fifo_prime(timeout_us=500):
                self.prime_timeouts += 1
                self._consecutive_prime_timeouts += 1
                self._frame_in_progress = False
                self.dma.active(0)
                if self._consecutive_prime_timeouts >= 2:
                    self._resync_after_fault("DMA prime timeout")
                return
            self._consecutive_prime_timeouts = 0

            # Ensure frame-done IRQ is fresh for this frame.
            mem32[self._pio_base + 0x30] = 1 << _IRQ_FRAME_DONE

            # Trigger control SM to begin Break → MAB → data sequence
            self.force_pio_irq0()
            self._next_frame_due_us = time.ticks_add(start_time, self._frame_period_us)

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
            self._resync_after_fault("update_frame exception")

    def stop(self):
        """Stop DMX transmission."""
        if self.transmitting:
            if self.timer is not None:
                self.timer.deinit()
            self.timer = None
            self.dma.active(0)          # abort any in-progress DMA transfer
            time.sleep_ms(10)
            self.sm_ctrl.active(0)
            self.sm_data.active(0)
            self._clear_pio_irqs()
            self._manual_scheduler = False
            self._next_frame_ms = 0
            self._frame_in_progress = False
            self._frame_deadline_us = 0
            self._timer_tick_pending = False
            self._timer_schedule_pending = False
            self._next_frame_due_us = 0
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
            idx = channel - 1
            if self.dmx_data[idx] != value:
                self.dmx_data[idx] = value
                self.frame[channel] = self._tx_encode_value(value)   # +1 offset for start code
                self._mark_dirty_index(channel)
                self.data_version += 1
            if self.print_updates:
                print(f"Channel {channel} = {value}")
        else:
            print(f"Error: Channel {channel} out of range (1-{self.channels})")

    def set_all(self, value):
        """Set all channels to the same value."""
        value = max(0, min(255, value))
        tx_value = self._tx_encode_value(value)
        changed = False
        for i in range(self.channels):
            if self.dmx_data[i] != value:
                self.dmx_data[i] = value
                self.frame[i + 1] = tx_value
                self._mark_dirty_index(i + 1)
                changed = True
        if changed:
            self.data_version += 1
        if self.print_updates:
            print(f"All channels set to {value}")

    def set_channels_bulk(self, values):
        """Set many channels at once from bytes / bytearray / list / tuple."""
        n = min(len(values), self.channels)
        if n <= 0:
            return
        changed = False
        if isinstance(values, (bytes, bytearray)):
            for i in range(n):
                v = values[i]
                if self.dmx_data[i] != v:
                    self.dmx_data[i] = v
                    self.frame[i + 1] = self._tx_encode_value(v)
                    self._mark_dirty_index(i + 1)
                    changed = True
        else:
            for i in range(n):
                v = max(0, min(255, values[i]))
                if self.dmx_data[i] != v:
                    self.dmx_data[i] = v
                    self.frame[i + 1] = self._tx_encode_value(v)
                    self._mark_dirty_index(i + 1)
                    changed = True
        if changed:
            self.data_version += 1
        if self.print_updates:
            print(f"Bulk update applied to {n} channels")

    def clear_all(self):
        self.set_all(0)

    def set_invert_data_bits(self, enabled):
        """Enable/disable transmit-side inversion for DMX channel bytes."""
        enabled = bool(enabled)
        if self.invert_data_bits == enabled:
            return
        self.invert_data_bits = enabled
        # Re-encode current logical values into the outgoing frame slots.
        changed = False
        for i in range(self.channels):
            encoded = self._tx_encode_value(self.dmx_data[i])
            slot_idx = i + 1
            if self.frame[slot_idx] != encoded:
                self.frame[slot_idx] = encoded
                self._mark_dirty_index(slot_idx)
                changed = True
        if changed:
            self.data_version += 1

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
            if self.timer is not None:
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
            if self._manual_scheduler:
                self._schedule_next_frame()
            else:
                self._start_periodic_timer()
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
            self.service()
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                print("Live latency timeout")
                return
            time.sleep_ms(1)
        dt_us = time.ticks_diff(time.ticks_us(), t0)
        print(f"Live command→sent latency: {dt_us / 1000:.3f} ms")

    def status(self):
        print("\n" + "=" * 40)
        print(f"DMX Controller Status  (DMA mode)  @ {self._format_timestamp()}")
        print("=" * 40)
        print(f"Channels:                {self.channels}")
        print(f"Transmitting:            {self.transmitting}")
        print(f"Scheduler mode:          {'manual' if self._manual_scheduler else 'timer'}")
        print(f"Refresh (req / active):  {self.refresh_rate} / {self.active_refresh_rate} Hz")
        print(f"Timer period:            {self.timer_period_ms} ms")
        print(f"Frame time:              {self.frame_time_us / 1000:.3f} ms")
        print(f"Frame count:             {self.frame_count}")
        print(f"Invert data bits:        {self.invert_data_bits}")
        print(f"Skipped callbacks:       {self.skipped_callbacks}")
        print(f"Timer schedule overruns: {self.timer_schedule_overruns}")
        print(f"DMA prime timeouts:      {self.prime_timeouts}")
        print(f"Frame timeouts:          {self.frame_timeouts}")
        print(f"Auto-resync count:       {self.auto_resyncs}")
        if self.frame_count > 0:
            avg_us = self.sum_update_us / self.frame_count
            print(f"Callback time avg/max:   {avg_us/1000:.3f} / {self.max_update_us/1000:.3f} ms")
            print(f"  (time to snapshot + arm DMA; actual TX is in PIO)")
        print(f"\nPIO block:     {self.pio_block}")
        print(f"SM ids:        ctrl={self.sm_ctrl_id} data={self.sm_data_id}")
        print(f"DMA channel:   {self.dma.channel}")
        print(f"DMA DREQ:      {self._dma_dreq}")
        print(f"DMA FIFO addr: 0x{self._pio_txf_data:08X}")
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
        print("  timesync        - Sync RTC from NTP (requires Wi-Fi)")
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
        trigger_pin=DMX_TRIGGER_PIN,
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

            elif cmd == "timesync":
                dmx.sync_time_ntp()

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