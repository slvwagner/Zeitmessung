# start_gate_safe.py — Start Gate with crash protection and stable Core1
import time, sys, micropython, gc
from machine import Pin
from rp2 import StateMachine, asm_pio


import credentials
import common as C
import OLED

# --- RFID driver selection ---
# Set to True to force native driver, False to force Python driver, or None for auto-detect
USE_NATIVE_RC522 = False  # True | False | None

from rc522_lowlevel import RC522LL, uid4_display_hex
try:
    import rc522_native as _rc522_native
except Exception:
    _rc522_native = None

try:
    from DMX_native_wrapper import DMXControllerPIO_DMA
except ImportError as exc:
    print("DMX native wrapper import failed:", exc)
    DMXControllerPIO_DMA = None

DEVICE_NAME = "StartGate"
DEVICE_ID = C.build_device_id()

# FIXED: Better int conversion for TIMEZONE_OFFSET
TZ_H_val = getattr(credentials, "TIMEZONE_OFFSET")
try:
    TZ_H = int(TZ_H_val)
except (ValueError, TypeError):
    TZ_H = 0
    print(f"Warning: TIMEZONE_OFFSET '{TZ_H_val}' is not a valid integer, defaulting to 0")
    
API_KEY = getattr(credentials, "API_KEY", "")

# --- GPIOs ---
PIN_START_NUM   = 2
PIN_START_NUM_2 = 3
PIN_STOP_NUM    = 14
BEAM1_SM_ID     = 5  # PIO1 SM5 — separate from DMX (forced PIO2) and WiFi (PIO0)
LED_PIN         = Pin("LED", Pin.OUT, value=1)

# --- RC522 wiring (single source of truth for native + Python fallback) ---
RC522_SPI_ID    = 1
RC522_PIN_SCK   = 10
RC522_PIN_MOSI  = 11
RC522_PIN_MISO  = 12
RC522_PIN_CS    = 13
RC522_PIN_RST   = 22
RC522_BAUD      = 50_000

START_PIN  = Pin(PIN_START_NUM,   Pin.IN, Pin.PULL_DOWN)
START_PIN2 = Pin(PIN_START_NUM_2, Pin.IN, Pin.PULL_DOWN)
STOP_PIN   = Pin(PIN_STOP_NUM,    Pin.IN, Pin.PULL_UP)

# --- Endpoints ---
LOOKUP_PATH    = "/participant_lookup_by_RFID.php"
INSERT_PATH    = "/insert_race.php"
READ_URL       = "/read.php"
SETTINGS_PATH  = "/device_params.php"
STATUS_PATH    = "/status.php"

# --- tunables ---
MIN_START_INTERVAL_MS = 800
_UID_COOLDOWN_MS      = 1200
RELOCK_COOLDOWN_MS    = 60000
TRACK_HEADWAY_MS      = 60000
CONNECTION_ERROR_COOLDOWN_MS = 5000
BEAM_DISTANCE_MM      = 43.18
BEAM_PAIR_TIMEOUT_MS  = 500
# Keep strict order by default (LS1 -> LS2). Can be overridden via settings key strict_order.
STRICT_ORDER          = True
DIAG_PAIR_COOLDOWN_MS = 1200
BEAM_BREAK_LEVEL      = 1  # 1: beam break is HIGH pulse, 0: beam break is LOW pulse
PIO_DUAL_FREQ_HZ     = 2_000_000
PIO_DUAL_DEBOUNCE_CYCLES = 8
PIO_DUAL_CYCLES_PER_COUNT = 2
PIO_BOTH_HIGH_FAULT_MS = 250
DEBUG_BEAMS = True
DEBUG_RFID = True

# --- DMX event signalling ---
DMX_TX_PIN            = 0
DMX_TRIGGER_PIN       = 1
DMX_START_CODE        = 0xFF
DMX_CTRL_SM_ID        = 8
DMX_DATA_SM_ID        = 9
DMX_EVENT_PULSE_MS    = 500
DMX_IDLE_PATTERN      = ((1, 1), (2, 3), (7, 50), (8, 50))
DMX_START_PATTERN     = ((1, 0), (2, 0), (7, 25), (8, 25))

# --- Thread-safe state ---
import _thread
_lock_state = _thread.allocate_lock()
_lock_pending = _thread.allocate_lock()
_lock_settings = _thread.allocate_lock()
_core1_thread_lock = _thread.allocate_lock()  # NEW: Protect Core1 thread management

# State with thread protection
_locked_snr = None
_pending_uid_queue = []
_PENDING_UID_QUEUE_MAX = 6
_PENDING_UID_MAX_AGE_MS = 12000

_last_sn_start   = {}
_last_uid_full   = {}
_deny_until      = {}
_sn_relock_until = {}
_global_headway_until = 0
_snr_next_run = {}

_diag_last_ms = {}

# Settings
_SETTINGS_REFRESH_MS = 5000
_last_settings_fetch = 0

# Crash recovery
_crash_count = 0
_max_crashes = 10
_last_crash_time = 0

# Race status - FIXED: Initialize variable
race_status_running = None

# Core1 thread control - FIXED: Track if thread is running
_core1_thread_running = False
_core1_thread_id = None

_dmx_controller = None
_dmx_event_until = 0


class _RC522NativeAdapter:
    """Adapter matching the RC522LL interface used by the Core1 worker."""

    def __init__(self):
        if _rc522_native is None:
            raise RuntimeError("rc522_native module unavailable")

        # Native bring-up can be timing-sensitive on some boards; try a short retry/baud ladder first.
        bauds = (RC522_BAUD, 40_000, 20_000, 10_000)
        last_exc = None

        for baud in bauds:
            for _ in range(3):
                try:
                    ok = _rc522_native.init(
                        spi_id=RC522_SPI_ID,
                        sck=RC522_PIN_SCK,
                        mosi=RC522_PIN_MOSI,
                        miso=RC522_PIN_MISO,
                        cs=RC522_PIN_CS,
                        rst=RC522_PIN_RST,
                        baud=baud,
                    )
                    if ok is False:
                        raise RuntimeError("rc522_native init returned False")
                    print(f"Core1: native rc522 init OK @ {baud} baud")
                    return
                except Exception as exc:
                    last_exc = exc
                    try:
                        _rc522_native.deinit()
                    except Exception:
                        pass
                    time.sleep_ms(30)

        # Print native status if available to aid diagnosis before falling back.
        try:
            print("Core1: native rc522 status after failed init:", _rc522_native.status())
        except Exception:
            pass
        raise RuntimeError(f"rc522_native init failed after retries: {last_exc}")

    def get_uid(self):
        return _rc522_native.get_uid4()

    def deinit(self):
        try:
            _rc522_native.deinit()
        except Exception:
            pass


def _create_rfid_driver():
    """Select RC522 backend based on USE_NATIVE_RC522 constant."""
    if USE_NATIVE_RC522 is True:
        if _rc522_native is None:
            raise RuntimeError("rc522_native module unavailable but USE_NATIVE_RC522=True")
        try:
            drv = _RC522NativeAdapter()
            print("Core1: Using native rc522 backend (forced)")
            return drv
        except Exception as e:
            print("Core1: native rc522 init failed (forced):", e)
            raise
    elif USE_NATIVE_RC522 is False:
        print("Core1: Forcing Python RC522LL backend")
        return RC522LL(
            spi_id=RC522_SPI_ID,
            sck=RC522_PIN_SCK,
            mosi=RC522_PIN_MOSI,
            miso=RC522_PIN_MISO,
            cs=RC522_PIN_CS,
            rst=RC522_PIN_RST,
            baud=RC522_BAUD,
        )
    else:
        # Auto-detect: prefer native, fallback to Python
        if _rc522_native is not None:
            try:
                drv = _RC522NativeAdapter()
                print("Core1: Using native rc522 backend (auto)")
                return drv
            except Exception as e:
                print("Core1: native rc522 init failed, fallback to RC522LL:", e)
        print("Core1: Using Python RC522LL backend (auto)")
        return RC522LL(
            spi_id=RC522_SPI_ID,
            sck=RC522_PIN_SCK,
            mosi=RC522_PIN_MOSI,
            miso=RC522_PIN_MISO,
            cs=RC522_PIN_CS,
            rst=RC522_PIN_RST,
            baud=RC522_BAUD,
        )


def _safe_send_piclog(log_text, min_free=12000):
    """Best-effort log sender that avoids crashing on low heap."""
    try:
        gc.collect()
        free = gc.mem_free()
        if free < min_free:
            C.dbg("Log skipped (low mem):", free)
            return False
        return send_Piclog(log_text)
    except Exception as e:
        gc.collect()
        C.dbg("Log skipped (exception):", e)
        return False

def _dmx_apply_pattern(pattern):
    if _dmx_controller is None:
        return
    for channel, value in pattern:
        _dmx_controller.set_channel(channel, value)

def _dmx_init():
    global _dmx_controller
    if _dmx_controller is not None:
        return True
    if DMXControllerPIO_DMA is None:
        print("DMX disabled: native dmx firmware/module unavailable")
        return False
    try:
        _dmx_controller = DMXControllerPIO_DMA(
            tx_pin=DMX_TX_PIN,
            trigger_pin=DMX_TRIGGER_PIN,
            channels=20,
            refresh_rate=200,
            start_code=DMX_START_CODE,
            sm_ctrl_id=DMX_CTRL_SM_ID,
            sm_data_id=DMX_DATA_SM_ID,
        )
        try:
            # Native backend now handles byte inversion in C for lowest overhead.
            _dmx_controller.set_invert_data_bits(True)
        except AttributeError:
            print("DMX native inversion API unavailable; using firmware default")
        except Exception as exc:
            print("DMX inversion setup failed:", exc)
        _dmx_controller.auto_ntp_sync = False
        _dmx_controller.auto_status_log = False
        _dmx_controller.start()
        _dmx_apply_pattern(DMX_IDLE_PATTERN)
        try:
            status = _dmx_controller._native.status()
            backend = status.get("backend", "unknown")
            invert = status.get("invert_data_bits", "?")
            start_code = int(status.get("start_code", DMX_START_CODE)) & 0xFF
            print("DMX backend:", backend, "invert_data_bits:", invert, "start_code:", "0x{:02X}".format(start_code))
        except Exception:
            pass
        print(f"DMX ready on TX GPIO{DMX_TX_PIN}, TRIG GPIO{DMX_TRIGGER_PIN} (StartGate)")
        return True
    except Exception as e:
        _dmx_controller = None
        print(f"DMX init failed (TX GPIO{DMX_TX_PIN}, TRIG GPIO{DMX_TRIGGER_PIN}): {e}")
        return False

def _dmx_trigger_start_event():
    global _dmx_event_until
    if _dmx_controller is None:
        return
    _dmx_apply_pattern(DMX_START_PATTERN)
    _dmx_event_until = time.ticks_add(time.ticks_ms(), DMX_EVENT_PULSE_MS)

def _dmx_tick():
    global _dmx_event_until
    if _dmx_controller is None:
        return
    try:
        _dmx_controller.service()
    except Exception:
        pass
    if _dmx_event_until == 0:
        return
    if time.ticks_diff(time.ticks_ms(), _dmx_event_until) >= 0:
        _dmx_apply_pattern(DMX_IDLE_PATTERN)
        _dmx_event_until = 0

def _dmx_stop():
    global _dmx_controller, _dmx_event_until
    if _dmx_controller is None:
        return
    try:
        _dmx_controller.stop()
    except Exception:
        pass
    _dmx_controller = None
    _dmx_event_until = 0

# --- Thread-safe accessors ---
def get_locked_snr():
    with _lock_state:
        return _locked_snr

def set_locked_snr(snr):
    with _lock_state:
        global _locked_snr
        _locked_snr = int(snr) if snr else None

def unlock_snr_safe(reason=""):
    with _lock_state:
        global _locked_snr
        _locked_snr = None
        if reason: 
            C.dbg("Unlocked:", reason)

def get_pending_uid():
    with _lock_pending:
        now = time.ticks_ms()
        # Drop stale entries so short network stalls do not permanently block queue progress.
        while _pending_uid_queue and time.ticks_diff(now, _pending_uid_queue[0][1]) >= _PENDING_UID_MAX_AGE_MS:
            _pending_uid_queue.pop(0)
        if _pending_uid_queue:
            uid_bytes, uid_time = _pending_uid_queue[0]
            return bytes(uid_bytes), uid_time
        return None, 0

def clear_pending_uid():
    with _lock_pending:
        if _pending_uid_queue:
            _pending_uid_queue.pop(0)

def set_pending_uid(uid_bytes):
    with _lock_pending:
        now = time.ticks_ms()
        uid_b = bytes(uid_bytes)

        while _pending_uid_queue and time.ticks_diff(now, _pending_uid_queue[0][1]) >= _PENDING_UID_MAX_AGE_MS:
            _pending_uid_queue.pop(0)

        # Avoid redundant duplicates if the same card is repeatedly seen in a short burst.
        if _pending_uid_queue:
            last_uid, last_time = _pending_uid_queue[-1]
            if last_uid == uid_b and time.ticks_diff(now, last_time) < 500:
                return

        if len(_pending_uid_queue) >= _PENDING_UID_QUEUE_MAX:
            _pending_uid_queue.pop(0)

        _pending_uid_queue.append((uid_b, now))
        if DEBUG_RFID:
            print("RFID queued:", uid4_display_hex(uid_b) or ("UID_LEN=" + str(len(uid_b))))

# --- OLED helpers ---
def draw_unlocked():
    try:
        C.OLED.oled.fill(0)
        C.OLED.oled_text([
            "RFID scan zum", "Rennstart", "",
            "Zeit:", C.format_local(C.epoch_ms(), TZ_H)[11:23]
        ])
    except Exception:
        pass

def draw_locked(sn, run_no, speed_kmh=None):
    subtitle = "Run %s  %s" % (run_no, C.format_local(C.epoch_ms(), TZ_H)[11:19])
    try:
        if speed_kmh is None:
            C.render_locked_startnummer(sn, subtitle=subtitle)
        else:
            C.render_locked_startnummer(sn, subtitle=subtitle + f"  {speed_kmh:.1f} km/h")
    except Exception:
        pass

# --- HTTP helpers ---
def _root(): 
    return C.build_root(credentials.SERVER_HOST)

def _full(path): 
    return _root() + (path if path.startswith("/") else ("/"+path))

def post_race(payload):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    C.dbg("Sending payload to insert_race.php:", payload)
    
    try:
        log_msg = f"Insert_race: SNr={payload.get('Startnummer')}, run={payload.get('run')}, ts={payload.get('timestamp_ms')}"
        send_Piclog(log_msg)
    except Exception as e:
        C.dbg("Failed to create log message:", e)
    
    res = C.http_post_json(_full(INSERT_PATH), payload, headers=headers)
    C.dbg("Server response:", res)
    
    if res and res.get("status") == "success":
        return True
    else:
        payload['_type'] = 'race'
        C.outbox_queue(payload)
        return False

def post_log(payload):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    C.dbg("Sending payload to log.php:", payload)
    try:
        res = C.http_post_json(_full("/log.php"), payload, headers=headers)
        C.dbg("log.php response:", res)
    except Exception as e:
        C.dbg("log.php post failed:", e)
        return False
    
    if res and res.get("status") == "success":
        return True
    else:
        payload['_type'] = 'log'
        C.outbox_queue(payload)
        return False

def send_started(snr, run_no, ts_str, speed_mps=None, speed_kmh=None, beam_distance_mm=None):
    payload = {
        "Startnummer": int(snr),
        "run": int(run_no),
        "timestamp_ms": ts_str,
        "timezone_offset": TZ_H,
        "device_id": DEVICE_ID,
        "device_name": DEVICE_NAME,
        "race_status": "started",
    }
    if speed_mps is not None:        payload["speed_mps"] = float(speed_mps)
    if speed_kmh is not None:        payload["speed_kmh"] = float(speed_kmh)
    if beam_distance_mm is not None: payload["beam_distance_mm"] = float(beam_distance_mm)
    
    ok = post_race(payload)
    if not ok:
        C.outbox_queue(payload)
    return ok
  
def send_Piclog(log, Device_ID=DEVICE_ID, Device_Name=DEVICE_NAME):
    try:
        if isinstance(log, (list, tuple)):
            log = " ".join(str(item) for item in log)
        elif not isinstance(log, str):
            log = str(log)

        # Keep payload bounded to avoid large transient allocations on low heap.
        if len(log) > 180:
            log = log[:180]

        payload = {
            "Device_ID": Device_ID,
            "Device_Name": Device_Name,
            "log": log
        }
        ok = post_log(payload)
        if not ok:
            try:
                C.outbox_queue(payload)
            except Exception as e:
                C.dbg("OUTBOX queue skipped:", e)
        return ok
    except Exception as e:
        C.dbg("send_Piclog failed:", e)
        return False

def lookup_snr_by_rfid(uid_hex_le4):
    with _lock_state:
        if time.ticks_diff(_deny_until.get(uid_hex_le4, 0), time.ticks_ms()) > 0:
            return None
    
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    url = _full(LOOKUP_PATH) + "?rfid=" + uid_hex_le4.replace(":", "%3A")
    
    data = C.http_get_json(url, headers=headers, timeout=3)
    
    if data is None:
        return "CONNECTION_FAILED"
    
    if not (isinstance(data, dict) and data.get("status") in ("ok", "success")):
        return None
    
    if data is None:
        return "CONNECTION_FAILED"
    
    if not (isinstance(data, dict) and data.get("status") in ("ok", "success")):
        return None
    
    payload = data.get("data") or {}
    p = payload.get("participant")
    allowed = bool(payload.get("allowed_to_lock", False))
    ontrk = bool(payload.get("on_track", False))
    run_cur = payload.get("current_run")
    
    if not p:
        post = ["RFID unbekannt", uid_hex_le4, "Bitte bei der", "Rennleitung", "melden"]
        C.ui_post(post, 5000)
        C.dbg(" ".join(post))
        send_Piclog(" ".join(post))
        C.dbg(payload)
        return None
    
    if not allowed:
        sn = (p or {}).get("Startnummer")
        sn_txt = f"Startnummer {sn}" if sn is not None else "Startnummer ?"
        if ontrk:
            C.ui_post(["RFID Scann:", sn_txt, "Starnummer", "ist bereits", "im Rennen:", "", f"Run {run_cur or '-'}"], 5000)
        else:
            C.ui_post(["RFID Scann:", sn_txt, "Startnummer", "nicht erlaubt", "", f"Run {run_cur or '-'}"], 5000)
        
        C.dbg(" ".join([sn_txt, ("ist bereits im Rennen:" if ontrk else "nicht erlaubt"), f"Run {run_cur or '-'}"]))
        send_Piclog(" ".join([sn_txt, ("ist bereits im Rennen:" if ontrk else "nicht erlaubt"), f"Run {run_cur or '-'}"]))
        
        with _lock_state:
            _deny_until[uid_hex_le4] = time.ticks_add(time.ticks_ms(), 3000)
        return None
    
    try:
        return int(p.get("Startnummer"))
    except Exception:
        return None

def seed_next_run_from_read(snr, limit=80):
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        url = _full(READ_URL) + f"?Startnummer={int(snr)}&limit={int(limit)}&order=desc"
        data = C.http_get_json(url, headers=headers, timeout=4)
        rows = (data or {}).get("data", [])
        maxr = 0
        for r in rows:
            try:
                if int(r.get("Startnummer")) == int(snr):
                    rr = int(r.get("run", 1))
                    if rr > maxr: maxr = rr
            except:
                pass
        return (maxr + 1) if maxr > 0 else 1
    except Exception:
        return 1

# --- Rennstatus ---
def race_status():
    global race_status_running  # FIXED: Declare global
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        url = _full(STATUS_PATH) + f"?device_name={DEVICE_NAME}&device_id={DEVICE_ID}"
        resp = C.http_get_json(url, headers=headers, timeout=4)

        if not (isinstance(resp, dict) and resp.get("status") in ("ok","success")):
            return None
        
        s = resp.get("data") or {}
        try:
            race_status_running = bool(s.get("Rennstatus"))
        except:
            race_status_running = None
        
        return race_status_running

    except Exception as e:
        C.dbg("Race status fetch failed:", e)
        send_Piclog(f"race status fetch failed: {e}")
        return None

# --- Settings fetch/refresh ---
def _maybe_refresh_settings():
    global _last_settings_fetch, RELOCK_COOLDOWN_MS, MIN_START_INTERVAL_MS, TRACK_HEADWAY_MS, BEAM_DISTANCE_MM, BEAM_PAIR_TIMEOUT_MS, TZ_H, STRICT_ORDER
    
    with _lock_settings:
        now = time.ticks_ms()
        if time.ticks_diff(now, _last_settings_fetch) < _SETTINGS_REFRESH_MS:
            return
        _last_settings_fetch = now
    
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        url = _full(SETTINGS_PATH)
        resp = C.http_get_json(url, headers=headers, timeout=4)

        
        if not (isinstance(resp, dict) and resp.get("status") in ("ok","success")):
            print("DEBUG: Settings fetch failed - bad response")
            return
        
        s = resp.get("data") or {}
        # print(s)

        def _to_int(v):
            try: 
                return int(v)
            except: 
                return None

        def _to_bool(v):
            if isinstance(v, bool):
                return v
            if isinstance(v, int):
                return v != 0
            if isinstance(v, str):
                t = v.strip().lower()
                if t in ("1", "true", "yes", "on"):
                    return True
                if t in ("0", "false", "no", "off"):
                    return False
            return None

        with _lock_settings:
            # Update settings with thread protection
            relock_s = _to_int(s.get("relock_cooldown_s"))
            if relock_s is not None and relock_s >= 0:
                old = RELOCK_COOLDOWN_MS
                RELOCK_COOLDOWN_MS = relock_s * 1000
                if old != RELOCK_COOLDOWN_MS:
                    print(f"DEBUG: Updated RELOCK_COOLDOWN_MS: {old} -> {RELOCK_COOLDOWN_MS}")

            track_headway_s = _to_int(s.get("track_headway_s"))
            if track_headway_s is not None and track_headway_s >= 0:
                old = TRACK_HEADWAY_MS
                TRACK_HEADWAY_MS = track_headway_s * 1000
                if old != TRACK_HEADWAY_MS:
                    print(f"DEBUG: Updated TRACK_HEADWAY_MS: {old} -> {TRACK_HEADWAY_MS}")

            beam_dist = s.get("beam_distance_mm")
            if beam_dist is not None:
                try:
                    old = BEAM_DISTANCE_MM
                    BEAM_DISTANCE_MM = float(beam_dist)
                    if old != BEAM_DISTANCE_MM:
                        print(f"DEBUG: Updated BEAM_DISTANCE_MM: {old} -> {BEAM_DISTANCE_MM}")
                except ValueError:
                    pass

            bto_ms = _to_int(s.get("beam_pair_timeout_ms"))
            if bto_ms is not None and bto_ms > 0:
                old = BEAM_PAIR_TIMEOUT_MS
                BEAM_PAIR_TIMEOUT_MS = bto_ms
                if old != BEAM_PAIR_TIMEOUT_MS:
                    print(f"DEBUG: Updated BEAM_PAIR_TIMEOUT_MS: {old} -> {BEAM_PAIR_TIMEOUT_MS}")

            strict_order = _to_bool(s.get("strict_order"))
            if strict_order is not None:
                old = STRICT_ORDER
                STRICT_ORDER = strict_order
                if old != STRICT_ORDER:
                    print(f"DEBUG: Updated STRICT_ORDER: {old} -> {STRICT_ORDER}")
            
            tz_offset = _to_int(s.get("local_time_offset_h"))
            if tz_offset is not None:
                old = TZ_H
                TZ_H = tz_offset
                if old != TZ_H:
                    print(f"DEBUG: Updated TZ_H: {old} -> {TZ_H}")

    except Exception as e:
        C.dbg("Settings fetch failed:", msg := f"Settings fetch failed: {e}")
        send_Piclog(msg)

def _recent_uid(uid_full):
    with _lock_state:
        now = time.ticks_ms()
        last = _last_uid_full.get(uid_full, 0)
        if time.ticks_diff(now, last) < _UID_COOLDOWN_MS:
            return True
        _last_uid_full[uid_full] = now
        return False

@asm_pio()
def dual_beam_measure_irq():
    pull(block)
    mov(isr, osr)

    wrap_target()
    # wait(gpio, N) uses absolute GPIO numbering per rp2 docs.
    # The second beam is checked with jmp(pin, ...) via jmp_pin=GPIO3 when the SM is created.
    wait(0, gpio, 2)                    # First beam (GPIO2) must be LOW to start counting  
    wait(1, gpio, 2)                    # First beam (GPIO2) goes HIGH: start counting
    mov(x, invert(null))                # x = 0xFFFFFFFF, (4'294'967'296) cycles a 0.5us at 2MHz equals ~35min max count time

    label("count_loop")
    jmp(pin, "beam2_candidate")         # as soon as the First beam goes high  we jump to to debounc loop
    jmp(x_dec, "count_loop")            # decrement x every cycle, means time passes by and therefore gets measured until the first beam goes low again (end of first beam break) 

    label("overflow")
    mov(isr, x)
    push()
    irq(0)
    wait(0, gpio, 3)
    wait(0, gpio, 2)
    wrap()

    label("beam2_candidate")
    mov(y, isr)

    label("debounce_loop")
    jmp(pin, "debounce_continue")
    jmp("count_loop")

    label("debounce_continue")
    jmp(y_dec, "debounce_loop")
    mov(isr, x)
    push()
    irq(0)
    wait(0, gpio, 3)
    wait(0, gpio, 2)
    wrap()

# --- PIO event ring buffer ---
micropython.alloc_emergency_exception_buf(256)
_PIO_DONE_Q_SIZE = 8
_pio_done_buf = [0] * _PIO_DONE_Q_SIZE
_pio_done_head = 0
_pio_done_tail = 0
_pio_done_dropped = 0
_pio_irq_count = 0
_pio_poll_enqueued = 0
_pio_debug_last_ms = 0
_pio_lock_ms = 0
_pio_both_high_since_ms = 0
_dual_beam_sm = None

def _pio_dual_irq_handler(sm):
    global _pio_done_head, _pio_done_dropped, _pio_irq_count
    _pio_irq_count += 1
    tsus = time.ticks_us()
    nxt = (_pio_done_head + 1) & (_PIO_DONE_Q_SIZE - 1)
    if nxt == _pio_done_tail:
        _pio_done_dropped += 1
        return
    _pio_done_buf[_pio_done_head] = tsus
    _pio_done_head = nxt

def _drain_next_pio_done():
    global _pio_done_tail
    if _pio_done_tail == _pio_done_head:
        return None
    ts = _pio_done_buf[_pio_done_tail]
    _pio_done_tail = (_pio_done_tail + 1) & (_PIO_DONE_Q_SIZE - 1)
    return ts

def _enqueue_pio_done_now():
    global _pio_done_head, _pio_done_dropped
    tsus = time.ticks_us()
    nxt = (_pio_done_head + 1) & (_PIO_DONE_Q_SIZE - 1)
    if nxt == _pio_done_tail:
        _pio_done_dropped += 1
        return False
    _pio_done_buf[_pio_done_head] = tsus
    _pio_done_head = nxt
    return True

def _maybe_enqueue_pio_from_rx(sm):
    global _pio_poll_enqueued
    if sm is None:
        return
    # Fallback: if IRQ notification is missed but RX has data, synthesize a done event.
    if _pio_done_head != _pio_done_tail:
        return
    try:
        if sm.rx_fifo() > 0 and _enqueue_pio_done_now():
            _pio_poll_enqueued += 1
    except Exception:
        pass

def _rearm_pio_dual_sm(reason=""):
    global _dual_beam_sm
    sm = _dual_beam_sm
    if sm is None:
        return False
    try:
        sm.active(0)
        sm.restart()
        try:
            while sm.rx_fifo() > 0:
                sm.get()
        except Exception:
            pass
        sm.put(PIO_DUAL_DEBOUNCE_CYCLES)
        sm.active(1)
        _flush_pio_done_events()
        if DEBUG_BEAMS:
            C.dbg("PIO rearmed", reason)
        return True
    except Exception as e:
        _diag_pair_once("pio_rearm_fail", ["PIO Rearm Fehler", str(e)[:20]], f"PIO rearm failed: {e}")
        return False

# --- Core1 RFID worker (SAFE VERSION - NO NETWORK/DISPLAY) ---
def core1_worker_safe():
    """Core1: Only RFID scanning, no network/display operations"""
    import gc
    rfid = None
    last_scan = 0
    scan_count = 0
    error_count = 0
    
    print("Core1: RFID worker started")
    
    while True:
        try:
            # Rate limiting
            now = time.ticks_ms()
            if time.ticks_diff(now, last_scan) < 30:  # ~33 scans/sec max
                time.sleep_ms(5)
                continue
            
            # Initialize/Reinitialize RFID
            if rfid is None:
                try:
                    rfid = _create_rfid_driver()
                    print(f"Core1: RFID initialized, mem: {gc.mem_free()}")
                except Exception as e:
                    print(f"Core1: RFID init failed: {e}")
                    time.sleep(1)
                    continue
            

            # Scan for card
            uid = rfid.get_uid()
            last_scan = now
            scan_count += 1

            # Periodic debug
            if scan_count % 500 == 0:
                print(f"Core1: Scans: {scan_count}, Errors: {error_count}, Mem: {gc.mem_free()}")
                gc.collect()

            # Process UID if found
            if uid:
                # DEBUG: Print raw UID bytes from native driver for analysis
                print("Core1: RAW UID bytes:", list(uid), "HEX:", ":".join("{:02X}".format(b) for b in uid))

                # Check if main core is busy
                if get_locked_snr() is not None:
                    # Main core processing, skip
                    time.sleep_ms(50)
                    continue

                # Anti-spam
                uid_full = ":".join("{:02X}".format(b) for b in uid)
                if _recent_uid(uid_full):
                    continue

                # Pass to main core
                set_pending_uid(uid)

            time.sleep_ms(20)
            
        except Exception as e:
            error_count += 1
            print(f"Core1 ERROR #{error_count}: {e}")
            
            # Clean up
            if rfid:
                try:
                    rfid.deinit()
                except:
                    pass
                rfid = None
            
            gc.collect()
            
            if error_count > 10:
                print("Core1: Too many errors, waiting...")
                time.sleep(5)
                error_count = 0

# --- Core1 thread management ---
def start_core1_worker():
    """Safely start Core1 worker thread"""
    global _core1_thread_running, _core1_thread_id
    
    with _core1_thread_lock:
        if _core1_thread_running:
            print("Core1: Thread already running")
            return False
        
        try:
            _thread.start_new_thread(core1_worker_safe, ())
            _core1_thread_running = True
            print("Core1: Thread started successfully")
            return True
        except Exception as e:
            print(f"Core1: Failed to start thread: {e}")
            return False

def stop_core1_worker():
    """Clean up Core1 resources (we can't actually stop thread, but we can mark it as stopped)"""
    global _core1_thread_running
    with _core1_thread_lock:
        _core1_thread_running = False
        print("Core1: Thread marked as stopped")

# --- Measurement system ---
_BASE_TICKS_US = 0
_BASE_EPOCH_MS = 0

def epoch_ms_from_ticks_us(ts_us):
    du = time.ticks_diff(ts_us, _BASE_TICKS_US)
    return _BASE_EPOCH_MS + (du + 500) // 1000

def _diag_pair_once(key, lines, log_text=None, hold_ms=900):
    now = time.ticks_ms()
    last = _diag_last_ms.get(key, 0)
    if time.ticks_diff(now, last) < DIAG_PAIR_COOLDOWN_MS:
        return
    _diag_last_ms[key] = now
    C.ui_post(lines, hold_ms)
    C.dbg("PAIR DIAG:", " | ".join(lines))
    try:
        send_Piclog(log_text or " | ".join(lines))
    except Exception:
        pass

def _flush_pio_done_events():
    global _pio_done_tail
    _pio_done_tail = _pio_done_head

def _consume_pio_dual_result(sm, completion_ts_us):
    try:
        x_remaining = sm.get()
    except Exception as e:
        _diag_pair_once("pio_get_fail", ["PIO RX Fehler", str(e)[:20]], f"PIO RX get failed: {e}")
        return None

    loop_count = (~x_remaining) & 0xffffffff
    if loop_count == 0:
        _diag_pair_once("pio_overflow", ["PIO Overflow", "Messung verworfen"], "PIO dual-beam overflow")
        return None

    dt_us = (loop_count * PIO_DUAL_CYCLES_PER_COUNT * 1000000 + (PIO_DUAL_FREQ_HZ // 2)) // PIO_DUAL_FREQ_HZ
    if dt_us <= 0:
        _diag_pair_once("pio_dt_invalid", ["PIO Zeitfehler", f"dt_us={dt_us}"], f"PIO dual-beam invalid dt_us={dt_us}")
        return None

    start_ts_us = time.ticks_add(completion_ts_us, -int(dt_us))
    return start_ts_us, dt_us

# --- Main implementation ---
def _actual_main():
    global DEVICE_ID, _BASE_TICKS_US, _BASE_EPOCH_MS, _global_headway_until
    global race_status_running, stop  # FIXED: Declare global
    global _dual_beam_sm, _pio_debug_last_ms, _pio_lock_ms, _pio_both_high_since_ms
    
    # Initialize OLED
    OLED.oled_init()
    _dmx_init()
    
    # WiFi connection
    max_retries = 3
    sta = None
    for attempt in range(max_retries):
        try:
            sta = C.wifi_connect(credentials.SSID, credentials.PASSWORD)
            if sta:
                msg = ["WiFi connected on", f"attempt {attempt+1}"]
                C.dbg(" ".join(msg))
                time.sleep(2)
                break
        except Exception as e:
            msg = ["WiFi connection ", f"attempt {attempt+1}", f"failed: {e}"]
            C.dbg(" ".join(msg))
            if attempt < max_retries - 1:
                time.sleep(2)
    
    if not sta:
        send_Piclog("WiFi connection failed after all retries")
        C.safe_shutdown(["Wifi failed!",""], sta=sta, led_pin=LED_PIN)
    
    # NTP sync
    try:
        C.time_sync_ntp()
    except Exception as e:
        C.dbg(f"NTP sync failed: {e}")
        C.ui_post(["Zeitsync fehlgeschlagen", "lokale Zeit wird", "verwendet"], 3000)
    
    C.dbg(f"DEVICE_ID: {DEVICE_ID}")
    
    # Test server connection
    test_url = _full(READ_URL) + "?limit=1"
    try:
        response = C.http_get_json(test_url, timeout=5)
        if isinstance(response, dict):
            rows = response.get("data") or []
            C.dbg("Server test:", response.get("status"), "rows=", len(rows))
        else:
            C.dbg("Server test response is None/invalid")
        if response is None:
            C.ui_post(["Server nicht", "erreichbar!", "Bitte prüfen..."], 5000)
    except Exception as e:
        C.dbg(f"Server test failed: {e}")
        C.ui_post(["Server-Fehler:", str(e)], 5000)
    
    # Initial settings
    gc.collect()
    _maybe_refresh_settings()
    
    # Epoch base for timestamp conversion
    _BASE_EPOCH_MS = C.epoch_ms()
    _BASE_TICKS_US = time.ticks_us()
    
    # Check beam status
    if (START_PIN.value() + START_PIN2.value()) == 0:
        msg = [DEVICE_NAME, str(sta.ifconfig()[0]), "is ready", 
               f"Beam1={START_PIN.value()}", f"Beam2={START_PIN2.value()}"]
        C.ui_post(msg, 3000)
        _safe_send_piclog(" ".join(msg))
        stop = False
    else:
        msg = [DEVICE_NAME, "WiFi "+ str(sta.ifconfig()[0]), "is not ready",
               "The beams state", "is not correct.", 
               f"Beam1={START_PIN.value()}", f"Beam2={START_PIN2.value()}"]
        C.ui_post(msg, 10000)
        _safe_send_piclog(" ".join(msg))
        stop = True
    
    # --- Arm dual-beam PIO measurement SM ---
    gc.collect()
    _dual_beam_sm = StateMachine(
        BEAM1_SM_ID,
        dual_beam_measure_irq,
        freq=PIO_DUAL_FREQ_HZ,
        jmp_pin=Pin(PIN_START_NUM_2),
    )
    _dual_beam_sm.irq(handler=_pio_dual_irq_handler)
    _dual_beam_sm.put(PIO_DUAL_DEBOUNCE_CYCLES)
    _dual_beam_sm.active(1)
    print(f"Dual-beam PIO mode armed on GPIO{PIN_START_NUM}->GPIO{PIN_START_NUM_2} @ {PIO_DUAL_FREQ_HZ}Hz")
    
    # Start Core1 worker - FIXED: Use safe start function
    if _thread:
        if not start_core1_worker():
            print("WARNING: Could not start Core1 worker")
    
    draw_unlocked()
    LOG_HOLD_MS = 1200
    SHUT_HOLD_MS = 5000
    last_idle = time.ticks_ms()
    last_blink = time.ticks_ms()
    last_race_status_check = time.ticks_ms()
    last_connection_error_msg = 0
    last_settings_check = time.ticks_ms()
    
    # Initial race status check
    race_status_running = race_status()
    if race_status_running is None:
        # If we can't get status, assume race is running (fail-safe)
        race_status_running = True
        C.ui_post(["Rennstatus unbekannt", "Starte trotzdem..."], 2000)
    elif not race_status_running:
        C.ui_post(["RENN GESTOPPT", "Bitte warten!"], 3000)
    else:
        C.ui_post(["Rennstatus: AKTIV", "Bereit zum Start"], 2000)

    
    C.dbg("StartGate SAFE main loop started")
    
    # Main loop
    while True:
        _dmx_tick()
        # Alive blink
        if time.ticks_diff(time.ticks_ms(), last_blink) > 100:
            last_blink = time.ticks_ms()
            LED_PIN.value(1 - LED_PIN.value())
        
        # STOP button
        if STOP_PIN.value() == 0:
            t0 = time.ticks_ms()
            shown = False
            while STOP_PIN.value() == 0:
                dt = time.ticks_diff(time.ticks_ms(), t0)
                if (not shown) and dt >= LOG_HOLD_MS and dt < SHUT_HOLD_MS:
                    C.ui_post(["Last log:"] + C.recent_log(7), 1400)
                    shown = True
                if dt >= SHUT_HOLD_MS:
                    C.log_to_file(head_lines=[DEVICE_NAME, "ID "+DEVICE_ID, "tz="+str(TZ_H)])
                    C.safe_shutdown(["Power off"], sta=sta, led_pin=LED_PIN)
                time.sleep_ms(18)
            if shown: 
                time.sleep_ms(700)
            else:
                unlock_snr_safe("STOP short-press")
                msg = ["Start wurde", "abgebrochen"]
                C.ui_post(msg, 1200)
                send_Piclog(" ".join(msg))
            draw_unlocked()
            
        # settings update
        _maybe_refresh_settings()
        
        # Race status check - FIXED: Initialize race_status_running if None
        if time.ticks_diff(time.ticks_ms(), last_race_status_check) > 3000:
            last_race_status_check = time.ticks_ms()
            current_status = race_status()
            if current_status is not None:
                race_status_running = current_status
            elif race_status_running is None:
                # Initialize to True if we can't get status
                race_status_running = True
        
        # Only process if race is running
        if race_status_running:
            # Idle repaint
            if time.ticks_diff(time.ticks_ms(), last_idle) > 600 and not C.notice_active():
                if get_locked_snr() is None:
                    draw_unlocked()
                else:
                    sn = get_locked_snr()
                    run_no = int(_snr_next_run.get(sn, 1))
                    draw_locked(sn, run_no)
                last_idle = time.ticks_ms()
            
            # Drain UI notices
            if C.ui_drain_once():
                last_idle = time.ticks_ms()
            
            # --- Process pending RFID scan from Core1 ---
            uid_bytes, scan_time = get_pending_uid()
            if uid_bytes and get_locked_snr() is None:
                clear_pending_uid()
                
                # Convert to hex
                le4 = uid4_display_hex(uid_bytes)
                if not le4:
                    if DEBUG_RFID:
                        print("RFID drop: UID shorter than 4 bytes")
                    continue

                if DEBUG_RFID:
                    try:
                        # Always show little-endian integer, matching rc522_lowlevel.py
                        int_le = int.from_bytes(uid_bytes, "little")
                        print(f"RFID processing: {le4} (int_le: {int_le})")
                    except Exception as exc:
                        print(f"RFID processing: {le4} (int_le conversion failed: {exc})")
                
                # Check deny list FIRST (before wasting time on network)
                with _lock_state:
                    if time.ticks_diff(_deny_until.get(le4, 0), time.ticks_ms()) > 0:
                        continue
                    
                    # HEADWAY CHECK (with user feedback)
                    rem_headway = time.ticks_diff(_global_headway_until, time.ticks_ms())
                    if rem_headway > 0:
                        secs = max(1, rem_headway // 1000)
                        C.ui_post(["Startabstand aktiv", f"warte {secs}s"], 900)
                        send_Piclog(f"Startabstand aktiv - warte {secs}s")
                        _deny_until[le4] = time.ticks_add(time.ticks_ms(), min(1200, rem_headway))
                        continue
                
                # Lookup Startnummer (ON MAIN CORE - SAFE)
                snr = lookup_snr_by_rfid(le4)
                
                if snr == "CONNECTION_FAILED":
                    # Database connection failed
                    now = time.ticks_ms()
                    if time.ticks_diff(now, last_connection_error_msg) > CONNECTION_ERROR_COOLDOWN_MS:
                        C.ui_post(["Server nicht", "erreichbar!", "check it..."], 2000)
                        last_connection_error_msg = now
                    with _lock_state:
                        _deny_until[le4] = time.ticks_add(now, 1500)
                    
                elif snr is not None:
                    # Valid SNr found - check RELOCK
                    with _lock_state:
                        until = _sn_relock_until.get(int(snr), 0)
                        rem_ms = time.ticks_diff(until, time.ticks_ms())
                        if rem_ms > 0:
                            secs = max(1, rem_ms // 1000)
                            C.ui_post([f"SNr {snr} gesperrt", f"warte {secs}s"], 900)
                            send_Piclog(f"SNr {snr} gesperrt - warte {secs}s")
                            _deny_until[le4] = time.ticks_add(time.ticks_ms(), min(1200, rem_ms))
                            continue
                    
                    # All checks passed - LOCK IT
                    set_locked_snr(snr)
                    if snr not in _snr_next_run:
                        _snr_next_run[snr] = seed_next_run_from_read(snr)
                    
                    draw_locked(snr, _snr_next_run.get(snr, 1))
                    _pio_lock_ms = time.ticks_ms()
                    _pio_both_high_since_ms = 0
                    _rearm_pio_dual_sm("lock")
                    
                    msg = f"RFID LOCKED: {snr}"
                    C.dbg(msg)
                    send_Piclog(msg)
        
            
            # --- Dual-beam PIO event handling ---
            _maybe_enqueue_pio_from_rx(_dual_beam_sm)

            # False-state guard: both barriers high for too long indicates a stuck sensor/state.
            b1 = START_PIN.value()
            b2 = START_PIN2.value()
            if b1 and b2:
                if _pio_both_high_since_ms == 0:
                    _pio_both_high_since_ms = time.ticks_ms()
                elif time.ticks_diff(time.ticks_ms(), _pio_both_high_since_ms) >= PIO_BOTH_HIGH_FAULT_MS:
                    _diag_pair_once(
                        "pio_both_high",
                        ["LS1+LS2 HIGH", "PIO wird neu armed"],
                        "PIO false state: both barriers high"
                    )
                    _rearm_pio_dual_sm("both high fault")
                    _pio_both_high_since_ms = 0
            else:
                _pio_both_high_since_ms = 0

            if DEBUG_BEAMS and get_locked_snr() is not None:
                now_dbg = time.ticks_ms()
                if time.ticks_diff(now_dbg, _pio_debug_last_ms) >= 1000:
                    _pio_debug_last_ms = now_dbg
                    pending = (_pio_done_head - _pio_done_tail) & (_PIO_DONE_Q_SIZE - 1)
                    rx_level = -1
                    sm_active = -1
                    try:
                        rx_level = _dual_beam_sm.rx_fifo() if _dual_beam_sm is not None else -1
                        sm_active = _dual_beam_sm.active() if _dual_beam_sm is not None else -1
                    except Exception:
                        pass
                    C.dbg(
                        "PIO DBG:",
                        f"active={sm_active}",
                        f"rx={rx_level}",
                        f"done_q={pending}",
                        f"irq={_pio_irq_count}",
                        f"poll={_pio_poll_enqueued}",
                        f"drop={_pio_done_dropped}",
                        f"B1={START_PIN.value()}",
                        f"B2={START_PIN2.value()}",
                        f"lock_ms={time.ticks_diff(now_dbg, _pio_lock_ms)}",
                    )

            while True:
                completion_ts_us = _drain_next_pio_done()
                if completion_ts_us is None:
                    break

                sn = get_locked_snr()
                if sn is None:
                    msg = ["START ignoriert", "Keine SNr gelockt"]
                    C.dbg(msg)
                    C.ui_post(msg, 700)
                    send_Piclog(" ".join(msg))
                    if _dual_beam_sm is not None:
                        try:
                            _dual_beam_sm.get()
                        except Exception:
                            pass
                    continue

                now_ms = time.ticks_ms()
                with _lock_state:
                    last_ms = _last_sn_start.get(sn, 0)
                    if time.ticks_diff(now_ms, last_ms) < MIN_START_INTERVAL_MS:
                        if _dual_beam_sm is not None:
                            try:
                                _dual_beam_sm.get()
                            except Exception:
                                pass
                        continue

                result = _consume_pio_dual_result(_dual_beam_sm, completion_ts_us)
                if result is None:
                    continue

                start_ts_us, dt_us = result
                dist_m = BEAM_DISTANCE_MM / 1000.0
                t_s = dt_us / 1000000.0
                speed_mps = dist_m / t_s
                speed_kmh = speed_mps * 3.6

                ts_ms = epoch_ms_from_ticks_us(start_ts_us)
                ts_str = C.format_local(ts_ms, TZ_H)
                run_no = int(_snr_next_run.get(sn, 1))

                with _lock_state:
                    _sn_relock_until[int(sn)] = time.ticks_add(time.ticks_ms(), RELOCK_COOLDOWN_MS)
                    _global_headway_until = time.ticks_add(time.ticks_ms(), TRACK_HEADWAY_MS)
                    _last_sn_start[sn] = now_ms

                C.dbg(f"START+PIO: SNr {sn} Run {run_no} @ {ts_str} dt_us={dt_us} v={speed_mps:.3f} m/s ({speed_kmh:.2f} km/h)")
                C.ui_post([f"SNr {sn}  Run {run_no}", f"{speed_kmh:.1f} km/h", "PIO sende..."], 900)
                draw_locked(sn, run_no, speed_kmh=speed_kmh)
                _dmx_trigger_start_event()
                ok = send_started(
                    sn,
                    run_no,
                    ts_str,
                    speed_mps=speed_mps,
                    speed_kmh=speed_kmh,
                    beam_distance_mm=BEAM_DISTANCE_MM,
                )
                if ok:
                    _snr_next_run[sn] = run_no + 1
                    msg = ["START gespeichert", f"{speed_kmh:.1f} km/h", "PIO Ready"]
                    C.ui_post(msg, 1100)
                    send_Piclog(" ".join(msg))
                    unlock_snr_safe("pio start logged")
                    _flush_pio_done_events()
                else:
                    msg = ["START in Warteschlange", f"{speed_kmh:.1f} km/h"]
                    C.ui_post(msg, 1100)
                    send_Piclog(" ".join(msg))
            
            # Beam error check
            if stop:
                msg = ["System can not measure", "because the beams are not in", "correct state"]
                C.ui_post(msg, 5000)
                send_Piclog(" ".join(msg))
                time.sleep(5)
                C.safe_shutdown(["Beam error"], sta=sta, led_pin=LED_PIN)
            
            time.sleep_ms(10)
        
        else:  # Race not running
            msg = ["Rennunterbruch", "Bitte warten!", "", "Es kann nicht", "gestarted werden."]
            C.dbg(" ".join(msg))
            OLED.oled_text(msg)
            time.sleep(3)

# --- Crash recovery wrapper ---
def safe_main():
    """Main function with crash recovery"""
    global _crash_count, _max_crashes, _last_crash_time
    
    print(f"\n{'='*60}")
    print(f"StartGate SAFE v1.1 - Crash recovery enabled")
    print(f"Max crashes: {_max_crashes}")
    print(f"{'='*60}\n")
    
    while _crash_count < _max_crashes:
        try:
            _actual_main()
            
        except KeyboardInterrupt:
            print("\nKeyboard interrupt - shutting down...")
            _dmx_stop()
            break
            
        except Exception as e:
            _crash_count += 1
            now = time.ticks_ms()
            crash_interval = time.ticks_diff(now, _last_crash_time) if _last_crash_time > 0 else 0
            _last_crash_time = now
            
            print(f"\n{'!'*60}")
            print(f"CRASH #{_crash_count} after {crash_interval}ms")
            print(f"Error: {e}")
            print(f"{'!'*60}")
            
            import sys
            sys.print_exception(e)
            
            # Log crash details
            try:
                with open("startgate_crashes.log", "a") as f:
                    f.write(f"\n--- Crash #{_crash_count} at {now} ---\n")
                    f.write(f"Uptime: {crash_interval}ms\n")
                    f.write(f"Error: {e}\n")
                    sys.print_exception(e, f)
                    f.write(f"State: locked_snr={get_locked_snr()}\n")
                    f.write(f"Memory: {gc.mem_free()} free\n")
                    f.write("-"*40 + "\n")
            except:
                pass
            
            # Clean up - FIXED: Mark Core1 thread as stopped
            try:
                LED_PIN.value(0)
                unlock_snr_safe("crash recovery")
                clear_pending_uid()
                _dmx_stop()
                stop_core1_worker()  # Mark thread as stopped
            except:
                pass
            
            if _crash_count >= _max_crashes:
                print(f"\nFATAL: Too many crashes ({_crash_count})")
                C.safe_shutdown(["Too many crashes"], sta=None, led_pin=LED_PIN)
                break
            
            print(f"\nRestarting in 5 seconds... (crash {_crash_count}/{_max_crashes})")
            
            # Show crash message on display if possible
            try:
                import OLED
                OLED.oled_init()
                OLED.oled_text([f"Crash #{_crash_count}", "Restarting...", f"in 5s", f"Mem: {gc.mem_free()}"])
            except:
                pass
            
            # IMPORTANT: Wait longer to let Core1 thread potentially die
            time.sleep(10)  # Increased from 5 to 10 seconds
            
            # Force garbage collection
            gc.collect()
            print(f"Memory after cleanup: {gc.mem_free()}")
            print(f"\n{'='*60}")
            print(f"RESTARTING - Attempt {_crash_count + 1}")
            print(f"{'='*60}\n")

# --- Entry point ---
if __name__ == "__main__":
    safe_main()
