# start_gate.py — Start Gate with Core1 RFID worker
import time, sys
from machine import Pin
import credentials
import common as C
from rc522_lowlevel import RC522LL, uid4_display_hex

DEVICE_NAME = "StartGate"
TZ_H = int(getattr(credentials, "TIMEZONE_OFFSET", 0))
API_KEY = getattr(credentials, "API_KEY", "")

PIN_START  = Pin(2, Pin.IN, Pin.PULL_UP)   # beam (active-low)
PIN_STOP   = Pin(3, Pin.IN, Pin.PULL_UP)   # stop/cancel
PIN_LED    = Pin(15, Pin.OUT)

LOOKUP_PATH = "/participant_lookup_by_RFID.php"     # expects ?rfid=AA:BB:CC:DD
INSERT_PATH = getattr(credentials, "INSERT", "/insert_race.php")
READ_URL    = getattr(credentials, "READ_URL", "/read.php")  # for seeding next_run

# --- duplicate protections ---
MIN_START_INTERVAL_MS = 800
_last_sn_start = {}
_UID_COOLDOWN_MS = 1200
_last_uid_full = {}
_deny_until = {}       # uid_hex_le4 -> ticks_ms until retry allowed

def _recent_uid(uid_full):
    now = time.ticks_ms()
    last = _last_uid_full.get(uid_full, 0)
    if time.ticks_diff(now, last) < _UID_COOLDOWN_MS:
        return True
    _last_uid_full[uid_full] = now
    return False

# --- simple state ---
_locked_snr = None
_snr_next_run = {}  # Startnummer -> next run (cached)

def current_snr(): return _locked_snr
def lock_snr(snr):  # Core 0 only
    global _locked_snr
    _locked_snr = int(snr)
def unlock_snr(reason=""):
    global _locked_snr
    _locked_snr = None
    if reason: C.dbg("Unlocked:", reason)

# --- OLED helpers ---
def draw_unlocked():
    C.ui_post(["Startnummer: --", C.format_local(C.epoch_ms(), TZ_H)[11:23], "Tap RFID to lock"], 900)

def draw_locked(sn, run_no):
    C.ui_post([f"LOCKED SNr {sn}", f"Run {run_no}", C.format_local(C.epoch_ms(), TZ_H)[11:23]], 900)

# --- HTTP / endpoints ---
def _root(): return C.build_root(credentials.SERVER_HOST)

def _full(path):
    p = path if path.startswith("/") else ("/" + path)
    return _root() + p

def post_race(payload):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    res = C.http_post_json(_full(INSERT_PATH), payload, headers=headers)
    return bool(res and res.get("status") == "success")

def send_started(snr, run_no, ts_str):
    payload = {
        "Startnummer": int(snr),
        "run": int(run_no),
        "timestamp_ms": ts_str,
        "timezone_offset": TZ_H,
        "device_id": DEVICE_ID,
        "device_name": DEVICE_NAME,
        "race_status": "started",
    }
    ok = post_race(payload)
    if not ok:
        C.outbox_queue(payload)
    return ok

def lookup_snr_by_rfid(uid_hex_le4):
    # deny cooldown
    if time.ticks_diff(_deny_until.get(uid_hex_le4, 0), time.ticks_ms()) > 0:
        return None
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    url = _full(LOOKUP_PATH) + "?rfid=" + uid_hex_le4.replace(":", "%3A")
    data = C.http_get_json(url, headers=headers, timeout=4)
    if not (isinstance(data, dict) and data.get("status") in ("ok","success")):
        return None
    payload = data.get("data") or {}
    p = payload.get("participant")
    allowed = bool(payload.get("allowed_to_lock", False))
    ontrk   = bool(payload.get("on_track", False))
    run_cur = payload.get("current_run")
    if not p:
        C.ui_post(["RFID unknown", uid_hex_le4], 1200)
        return None
    if not allowed:
        C.ui_post(["LOCK REFUSED", ("on track" if ontrk else "not allowed"), f"Run {run_cur or '-'}"], 1200)
        _deny_until[uid_hex_le4] = time.ticks_add(time.ticks_ms(), 1500)
        return None
    try:
        return int(p.get("Startnummer"))
    except Exception:
        return None

def seed_next_run_from_read(snr, limit=400):
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        data = C.http_get_json(_full(READ_URL) + f"?limit={int(limit)}&order=desc", headers=headers)
        rows = (data or {}).get("data", [])
        maxr = 0
        for r in rows:
            try:
                if int(r.get("Startnummer")) == int(snr):
                    rr = int(r.get("run", 1))
                    if rr > maxr: maxr = rr
            except: pass
        return (maxr + 1) if maxr > 0 else 1
    except Exception:
        return 1

# --- Beam IRQ ---
start_ts_str = None
beam_fired = False

def _beam_isr(pin):
    global start_ts_str, beam_fired
    if pin.value() == 0:
        ts_ms = C.epoch_ms()
        start_ts_str = C.format_local(ts_ms, TZ_H)
        beam_fired = True
        pin.irq(handler=None)  # debounce; re-arm later

# --- Core1 RFID worker (owns RC522 + background chores) ---
try:
    import _thread
    _pending_lock_snr = None
    _pending_lock_lock = _thread.allocate_lock()
except Exception:
    _thread = None
    class _DummyLock:
        def __enter__(self): pass
        def __exit__(self, *a): pass
    _pending_lock_snr = None
    _pending_lock_lock = _DummyLock()

def _set_pending_lock(snr):
    global _pending_lock_snr
    with _pending_lock_lock:
        _pending_lock_snr = int(snr)

def _take_pending_lock():
    global _pending_lock_snr
    with _pending_lock_lock:
        sn = _pending_lock_snr
        _pending_lock_snr = None
    return sn

def core1_worker():
    rfid = RC522LL()
    C.dbg("RC522 VersionReg =", hex(rfid._rd(0x37)))
    last_flush = time.ticks_ms()
    while True:
        try:
            uid = rfid.get_uid()
            if uid and current_snr() is None:
                uid_full = ":".join("{:02X}".format(b) for b in uid)
                if not _recent_uid(uid_full):
                    le4 = uid4_display_hex(uid)
                    # skip if recently denied
                    if time.ticks_diff(_deny_until.get(le4 or "", 0), time.ticks_ms()) <= 0:
                        snr = lookup_snr_by_rfid(le4)
                        if snr is None:
                            _deny_until[le4] = time.ticks_add(time.ticks_ms(), 1500)
                        else:
                            # Pre-seed next_run (optional; quick HTTP)
                            if snr not in _snr_next_run:
                                _snr_next_run[snr] = seed_next_run_from_read(snr)
                            _set_pending_lock(snr)
        except Exception:
            pass

        # periodic outbox flush (background)
        if time.ticks_diff(time.ticks_ms(), last_flush) > 2500:
            try:
                C.outbox_flush(lambda p: post_race(p))
            except Exception:
                pass
            last_flush = time.ticks_ms()

        time.sleep_ms(15)

# --- Entry / Main ---
DEVICE_ID = ""

def main():
    global DEVICE_ID, beam_fired, start_ts_str

    # Boot: WiFi, time, OLED
    sta = C.wifi_connect(credentials.SSID, credentials.PASSWORD)
    C.time_sync_ntp()
    DEVICE_ID = C.build_device_id()

    import OLED
    OLED.oled_init()
    C.ui_post([DEVICE_NAME, "WiFi "+sta.ifconfig()[0], "Ready"], 1200)

    # Hardware
    PIN_LED.value(1)
    try:
        PIN_START.irq(trigger=Pin.IRQ_FALLING, handler=_beam_isr)
    except Exception:
        PIN_START.irq(handler=_beam_isr, trigger=Pin.IRQ_FALLING)

    # Start Core1 worker (RFID + network helpers)
    if _thread:
        _thread.start_new_thread(core1_worker, ())

    draw_unlocked()

    LOG_HOLD_MS  = 1200
    SHUT_HOLD_MS = 4000
    last_idle = time.ticks_ms()

    try:
        while True:
            # STOP: short=unlock / 1.2s=show log / 4s=shutdown
            if PIN_STOP.value() == 0:
                t0 = time.ticks_ms(); shown = False
                while PIN_STOP.value() == 0:
                    dt = time.ticks_diff(time.ticks_ms(), t0)
                    if (not shown) and dt >= LOG_HOLD_MS and dt < SHUT_HOLD_MS:
                        C.ui_post(["Last log:"] + C.recent_log(7), 1400)
                        shown = True
                    if dt >= SHUT_HOLD_MS:
                        C.log_to_file(head_lines=[DEVICE_NAME, "ID "+DEVICE_ID])
                        C.safe_shutdown(["Safe to power off"], sta=sta, led_pin=PIN_LED)
                    time.sleep_ms(18)
                if shown:
                    time.sleep_ms(700)
                else:
                    unlock_snr("STOP short-press")
                draw_unlocked()

            # Drain one UI message if any
            if C.ui_drain_once():
                last_idle = time.ticks_ms()

            # Consume pending lock from Core1
            snr_to_lock = _take_pending_lock()
            if snr_to_lock is not None and current_snr() is None:
                lock_snr(snr_to_lock)
                draw_locked(snr_to_lock, _snr_next_run.get(snr_to_lock, 1))
                C.dbg("RFID LOCKED →", snr_to_lock)

            # Idle repaint (keep time fresh)
            if time.ticks_diff(time.ticks_ms(), last_idle) > 600 and not C.notice_active():
                if current_snr() is None:
                    draw_unlocked()
                else:
                    sn = current_snr()
                    run_no = int(_snr_next_run.get(sn, 1))
                    draw_locked(sn, run_no)
                last_idle = time.ticks_ms()

            # Beam captured → send "started"
            if beam_fired:
                beam_fired = False
                sn = current_snr()
                if sn is None:
                    C.ui_post(["START ignored", "No SNr locked"], 1000)
                    PIN_START.irq(trigger=Pin.IRQ_FALLING, handler=_beam_isr)
                    continue

                now = time.ticks_ms()
                last = _last_sn_start.get(sn, 0)
                if time.ticks_diff(now, last) < MIN_START_INTERVAL_MS:
                    C.ui_post(["Ignored duplicate", f"SNr {sn} too soon"], 1000)
                    PIN_START.irq(trigger=Pin.IRQ_FALLING, handler=_beam_isr)
                    continue
                _last_sn_start[sn] = now

                run_no = int(_snr_next_run.get(sn, 1))
                C.ui_post([f"START SNr {sn}", f"Run {run_no}", "Uploading..."], 900)
                ok = send_started(sn, run_no, start_ts_str)
                if ok:
                    _snr_next_run[sn] = run_no + 1
                    C.ui_post(["START logged", f"SNr {sn}  Run {run_no}", "Ready"], 1100)
                    unlock_snr("start logged")
                else:
                    C.ui_post(["START queued (offline)", f"SNr {sn} Run {run_no}"], 1100)

                time.sleep_ms(250)
                PIN_START.irq(trigger=Pin.IRQ_FALLING, handler=_beam_isr)

            time.sleep_ms(15)

    except KeyboardInterrupt:
        C.safe_shutdown(["KeyboardInterrupt"], sta=sta, led_pin=PIN_LED)
    except Exception as e:
        C.show_error("main", e)
        C.log_to_file(head_lines=[DEVICE_NAME, "ID "+DEVICE_ID])
        C.safe_shutdown(["Error exit"], sta=sta, led_pin=PIN_LED)

if __name__ == "__main__":
    main()
