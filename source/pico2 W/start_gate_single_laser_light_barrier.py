# start_gate.py — Start Gate with PIO-timestamped beam (hard IRQ),
# Core1 RFID worker, re-lock cooldown + global headway, OLED big digits

import time, sys, micropython
from machine import Pin
import rp2
from rp2 import PIO, StateMachine, asm_pio

import credentials
import common as C
from rc522_lowlevel import RC522LL, uid4_display_hex

DEVICE_NAME = "StartGate"
TZ_H  = int(getattr(credentials, "TIMEZONE_OFFSET", 0))
API_KEY = getattr(credentials, "API_KEY", "")

# --- GPIOs ---
PIN_START_NUM = 2     # laser/beam sensor: idle HIGH, FALLING when beam is broken
PIN_STOP_NUM  = 3     # cancel/STOP button (hold to show log / shutdown)
LED_PIN       = Pin("LED", Pin.OUT, value=1)  # Pico2 W onboard LED (GPIO15)

START_PIN = Pin(PIN_START_NUM, Pin.IN, Pin.PULL_UP)
STOP_PIN  = Pin(PIN_STOP_NUM,  Pin.IN, Pin.PULL_UP)

# --- Endpoints ---
LOOKUP_PATH    = "/participant_lookup_by_RFID.php"   # expects ?rfid=AA:BB:CC:DD (LE low 4 bytes)
INSERT_PATH    = "/insert_race.php"
READ_URL       = "/read.php"
SETTINGS_PATH  = "/device_params.php"

# --- tunables (overridden via /device_params.php when available) ---
MIN_START_INTERVAL_MS = 800      # duplicate-beam protection per SNr
_UID_COOLDOWN_MS      = 1200     # RFID anti-spam (same tag)
RELOCK_COOLDOWN_MS    = 60000    # after START, same SNr cannot be locked again
TRACK_HEADWAY_MS      = 60000    # after ANY START, next racer may only lock after this

# PIO beam conditioning
REFRACTORY_US         = 80_000   # ignore second edges within 80 ms (contact bounce)
MIN_LOW_US_DEFAULT    = 20       # require beam low ≥ this (µs) at 2 MHz (≈ 1 iter = 0.5 µs)

# --- state ---
_last_sn_start   = {}     # Startnummer -> last start ticks_ms
_last_uid_full   = {}     # full UID anti-spam
_deny_until      = {}     # uid_le4 -> ticks_ms throttle
_sn_relock_until = {}     # Startnummer -> ticks_ms (relock block)
_global_headway_until = 0 # ticks_ms when next lock allowed (global start spacing)
_snr_next_run = {}        # SNr -> next run cache

_SETTINGS_REFRESH_MS = 120000
_last_settings_fetch = 0

# Simple lock state
_locked_snr = None
def current_snr(): return _locked_snr
def lock_snr(snr):
    global _locked_snr
    _locked_snr = int(snr)
def unlock_snr(reason=""):
    global _locked_snr
    _locked_snr = None
    if reason: C.dbg("Unlocked:", reason)

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

def draw_locked(sn, run_no):
    subtitle = "Run %s  %s" % (run_no, C.format_local(C.epoch_ms(), TZ_H)[11:19])
    try:
        C.render_locked_startnummer(sn, subtitle=subtitle)
    except Exception:
        pass

# --- HTTP helpers ---
def _root(): return C.build_root(credentials.SERVER_HOST)
def _full(path): return _root() + (path if path.startswith("/") else ("/"+path))

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
    # throttle repeated attempts on this tag
    if time.ticks_diff(_deny_until.get(uid_hex_le4, 0), time.ticks_ms()) > 0:
        return None
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    url = _full(LOOKUP_PATH) + "?rfid=" + uid_hex_le4.replace(":", "%3A")
    data = C.http_get_json(url, headers=headers, timeout=4)
    if not (isinstance(data, dict) and data.get("status") in ("ok", "success")):
        return None

    payload = data.get("data") or {}
    p       = payload.get("participant")
    allowed = bool(payload.get("allowed_to_lock", False))
    ontrk   = bool(payload.get("on_track", False))
    run_cur = payload.get("current_run")

    if not p:
        C.ui_post(["RFID unbekannt", uid_hex_le4, "Bitte bei der", "Rennleitung", "melden"], 3000)
        return None

    if not allowed:
        sn = (p or {}).get("Startnummer")
        sn_txt = f"Startnummer {sn}" if sn is not None else "Startnummer ?"
        C.ui_post([sn_txt, ("ist im Rennen:" if ontrk else "nicht erlaubt"), f"Run {run_cur or '-'}"], 1500)
        _deny_until[uid_hex_le4] = time.ticks_add(time.ticks_ms(), 3000)
        return None

    try:
        return int(p.get("Startnummer"))
    except Exception:
        return None

def seed_next_run_from_read(snr, limit=80):
    """Ask read.php filtered by Startnummer (faster) and compute next run."""
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

# --- Settings fetch/refresh (override local tunables) ---
def _maybe_refresh_settings():
    global _last_settings_fetch, RELOCK_COOLDOWN_MS, MIN_START_INTERVAL_MS, _UID_COOLDOWN_MS, TRACK_HEADWAY_MS
    now = time.ticks_ms()
    if time.ticks_diff(now, _last_settings_fetch) < _SETTINGS_REFRESH_MS:
        return
    _last_settings_fetch = now
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        url = _full(SETTINGS_PATH) + f"?device_name={DEVICE_NAME}&device_id={DEVICE_ID}"
        resp = C.http_get_json(url, headers=headers, timeout=4)
        if not (isinstance(resp, dict) and resp.get("status") in ("ok","success")):
            return
        s = resp.get("data") or {}

        def _to_int(v):
            try: return int(v)
            except: return None

        # same-SNr re-lock cooldown
        rlc_s  = _to_int(s.get("relock_cooldown_s")  or s.get("RELOCK_COOLDOWN_S"))
        rlc_ms = _to_int(s.get("relock_cooldown_ms") or s.get("RELOCK_COOLDOWN_MS"))
        if rlc_ms is None and rlc_s is not None: rlc_ms = rlc_s * 1000
        if isinstance(rlc_ms,int) and rlc_ms>=0:
            RELOCK_COOLDOWN_MS = rlc_ms
            C.dbg("Setting RELOCK_COOLDOWN_MS =", RELOCK_COOLDOWN_MS)

        # global headway
        th_s  = _to_int(s.get("track_headway_s")  or s.get("TRACK_HEADWAY_S"))
        th_ms = _to_int(s.get("track_headway_ms") or s.get("TRACK_HEADWAY_MS"))
        if th_ms is None and th_s is not None: th_ms = th_s * 1000
        if isinstance(th_ms,int) and th_ms>=0:
            TRACK_HEADWAY_MS = th_ms
            C.dbg("Setting TRACK_HEADWAY_MS =", TRACK_HEADWAY_MS)

        # local beam dup + RFID anti-spam
        msi = _to_int(s.get("min_start_interval_ms") or s.get("MIN_START_INTERVAL_MS"))
        if isinstance(msi,int) and msi>=0:
            MIN_START_INTERVAL_MS = msi
            C.dbg("Setting MIN_START_INTERVAL_MS =", MIN_START_INTERVAL_MS)

        uid_cd = _to_int(s.get("uid_cooldown_ms") or s.get("UID_COOLDOWN_MS"))
        if isinstance(uid_cd,int) and uid_cd>=0:
            _UID_COOLDOWN_MS = uid_cd
            C.dbg("Setting _UID_COOLDOWN_MS =", _UID_COOLDOWN_MS)
    except Exception as e:
        C.dbg("Settings fetch failed:", e)

def _recent_uid(uid_full):
    now = time.ticks_ms()
    last = _last_uid_full.get(uid_full, 0)
    if time.ticks_diff(now, last) < _UID_COOLDOWN_MS:
        return True
    _last_uid_full[uid_full] = now
    return False

# --- PIO program (falling edge with min-LOW filter → hard IRQ; re-arm after release) ---
@asm_pio()
def beam_fall_irq():
    wait(1, pin, 0)        # require idle HIGH before arming

    label("arm")
    pull(noblock)          # optional new threshold from Python
    mov(x, osr)
    jmp(not_x, "y_default")
    mov(y, x)              # Y = threshold iterations (~µs*2 at 2 MHz)
    jmp("armed_y")
    label("y_default")
    set(y, 1)              # safe fallback if Python didn't prime OSR
    label("armed_y")

    wait(0, pin, 0)        # falling edge (beam broken)

    # min-LOW-width loop: if HIGH returns early, abort & re-arm
    label("glitch_chk")
    jmp(pin, "arm")
    jmp(y_dec, "glitch_chk")

    irq(0)                 # LOW held long enough → raise IRQ
    wait(1, pin, 0)        # wait for release (HIGH) before re-arming
    jmp("arm")

# --- Hard-IRQ ring buffer for µs timestamps (no allocations in IRQ) ---
micropython.alloc_emergency_exception_buf(128)
_Q_SIZE = 16
_ev_buf  = [0] * _Q_SIZE
_ev_head = 0
_ev_tail = 0
dropped_events = 0

def _sm_irq_handler(sm):
    # HARD IRQ: avoid allocations / prints
    global _ev_head, dropped_events
    tsus = time.ticks_us()
    nxt = (_ev_head + 1) & (_Q_SIZE - 1)
    if nxt == _ev_tail:
        dropped_events += 1   # overwrite protection: drop newest
        return
    _ev_buf[_ev_head] = tsus
    _ev_head = nxt

# ticks_us → epoch_ms with wrap-safe base captured after NTP sync
_BASE_TICKS_US = 0
_BASE_EPOCH_MS = 0
def epoch_ms_from_ticks_us(ts_us):
    du = time.ticks_diff(ts_us, _BASE_TICKS_US)
    return _BASE_EPOCH_MS + (du + 500) // 1000

# --- Core1 RFID worker (owns RC522 and background chores) ---
try:
    import _thread
    _pending_lock_snr = None
    _pending_lock_lock = _thread.allocate_lock()
except Exception:
    _thread = None
    class _DummyLock:
        def __enter__(self): pass
        def __exit__(self, *a): pass
    _pending_lock_snr=None; _pending_lock_lock=_DummyLock()

def _set_pending_lock(snr):
    global _pending_lock_snr
    with _pending_lock_lock:
        _pending_lock_snr=int(snr)

def _take_pending_lock():
    global _pending_lock_snr
    with _pending_lock_lock:
        sn=_pending_lock_snr; _pending_lock_snr=None
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

                    # temporary deny on this RFID?
                    if time.ticks_diff(_deny_until.get(le4 or "", 0), time.ticks_ms()) > 0:
                        pass
                    else:
                        # global headway gate
                        rem_headway = time.ticks_diff(_global_headway_until, time.ticks_ms())
                        if rem_headway > 0:
                            secs = max(1, rem_headway // 1000)
                            C.ui_post(["Startabstand aktiv", f"warte {secs}s"], 900)
                            _deny_until[le4] = time.ticks_add(time.ticks_ms(), min(1200, rem_headway))
                        else:
                            snr = lookup_snr_by_rfid(le4)
                            if snr is None:
                                _deny_until[le4] = time.ticks_add(time.ticks_ms(), 1500)
                            else:
                                # per-SNr re-lock gate
                                until = _sn_relock_until.get(int(snr), 0)
                                rem_ms = time.ticks_diff(until, time.ticks_ms())
                                if rem_ms > 0:
                                    C.ui_post([f"SNr {snr} gesperrt", f"warte {max(1, rem_ms//1000)}s"], 900)
                                    _deny_until[le4] = time.ticks_add(time.ticks_ms(), min(1200, rem_ms))
                                else:
                                    if snr not in _snr_next_run:
                                        _snr_next_run[snr] = seed_next_run_from_read(snr)
                                    _set_pending_lock(snr)
        except Exception:
            pass

        # background: outbox + settings refresh
        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, last_flush) > 2500:
            try:
                C.outbox_flush(lambda p: post_race(p))
            except Exception:
                pass
            last_flush = now_ms

        _maybe_refresh_settings()
        time.sleep_ms(15)

# --- Entry / Main ---
DEVICE_ID = ""

def main():
    global DEVICE_ID, _ev_tail, _BASE_TICKS_US, _BASE_EPOCH_MS, _global_headway_until

    # WiFi + time sync + device id
    sta = C.wifi_connect(credentials.SSID, credentials.PASSWORD)
    C.time_sync_ntp()
    DEVICE_ID = C.build_device_id()

    # epoch base for fast ts conversion
    _BASE_EPOCH_MS = C.epoch_ms()
    _BASE_TICKS_US = time.ticks_us()

    # OLED hello
    import OLED
    OLED.oled_init()
    C.ui_post([DEVICE_NAME, "WiFi "+sta.ifconfig()[0], "Ready"], 1200)

    C.dbg("START pin idle level =", START_PIN.value())

    # PIO: hard-IRQ falling-edge capture with min-LOW filter
    sm = StateMachine(
        0, beam_fall_irq, freq=2_000_000,
        in_base=Pin(PIN_START_NUM, Pin.IN, Pin.PULL_UP)
    )
    # prime threshold (iterations ~= microseconds at 2 MHz)
    sm.put(MIN_LOW_US_DEFAULT)
    sm.irq(handler=_sm_irq_handler, hard=True)
    sm.active(1)

    # Core1 worker
    if _thread: _thread.start_new_thread(core1_worker, ())

    draw_unlocked()
    LOG_HOLD_MS=1200; SHUT_HOLD_MS=4000
    last_idle = time.ticks_ms()
    last_blink = time.ticks_ms()
    C.dbg("StartGate main loop (PIO armed)")

    try:
        while True:
            # Alive blink
            if time.ticks_diff(time.ticks_ms(), last_blink) > 500:
                last_blink = time.ticks_ms()
                LED_PIN.value(1 - LED_PIN.value())

            # STOP behavior
            if STOP_PIN.value()==0:
                t0=time.ticks_ms(); shown=False
                while STOP_PIN.value()==0:
                    dt=time.ticks_diff(time.ticks_ms(), t0)
                    if (not shown) and dt>=LOG_HOLD_MS and dt<SHUT_HOLD_MS:
                        C.ui_post(["Last log:"] + C.recent_log(7), 1400); shown=True
                    if dt>=SHUT_HOLD_MS:
                        C.log_to_file(head_lines=[DEVICE_NAME,"ID "+DEVICE_ID, "tz="+str(TZ_H)])
                        C.safe_shutdown(["Safe to power off"], sta=sta, led_pin=LED_PIN)
                    time.sleep_ms(18)
                if shown: time.sleep_ms(700)
                else:
                    unlock_snr("STOP short-press")
                    C.ui_post(["Start wurde", "abgebrochen"], 1200)
                draw_unlocked()

            # Drain one UI notice if any
            if C.ui_drain_once():
                last_idle = time.ticks_ms()

            # Consume pending lock from Core1
            snr_to_lock = _take_pending_lock()
            if snr_to_lock is not None and current_snr() is None:
                # double-check global headway right before taking the lock
                rem_headway = time.ticks_diff(_global_headway_until, time.ticks_ms())
                if rem_headway > 0:
                    C.ui_post(["Startabstand aktiv", f"warte {max(1, rem_headway//1000)}s"], 900)
                else:
                    # optional per-SNr re-lock recheck
                    until = _sn_relock_until.get(int(snr_to_lock), 0)
                    if time.ticks_diff(until, time.ticks_ms()) > 0:
                        rem = time.ticks_diff(until, time.ticks_ms())
                        C.ui_post([f"SNr {snr_to_lock} gesperrt", f"warte {max(1, rem//1000)}s"], 900)
                    else:
                        lock_snr(snr_to_lock)
                        draw_locked(snr_to_lock, _snr_next_run.get(snr_to_lock, 1))
                        C.dbg("RFID LOCKED →", snr_to_lock)

            # Idle repaint (keeps clock fresh)
            if time.ticks_diff(time.ticks_ms(), last_idle) > 600 and not C.notice_active():
                if current_snr() is None:
                    draw_unlocked()
                else:
                    sn=current_snr(); run_no=int(_snr_next_run.get(sn,1))
                    draw_locked(sn, run_no)
                last_idle = time.ticks_ms()

            # --- Drain PIO beam events ---
            while _ev_tail != _ev_head:
                ts_us = _ev_buf[_ev_tail]
                _ev_tail = (_ev_tail + 1) & (_Q_SIZE - 1)

                # refractory (ignore additional edges too close)
                # Note: we compare converted ms via last start per SNr below; this guards at IRQ level too.
                # (We don't need a separate _last_evt_us for basic operation.)

                sn = current_snr()
                if sn is None:
                    C.ui_post(["START ignoriert", "Keine SNr gelockt"], 900)
                    continue

                now_ms = time.ticks_ms()
                last_ms = _last_sn_start.get(sn, 0)
                if time.ticks_diff(now_ms, last_ms) < MIN_START_INTERVAL_MS:
                    C.ui_post(["Ignoriert (zu früh)", f"SNr {sn}"], 900)
                    continue
                _last_sn_start[sn] = now_ms

                ts_ms  = epoch_ms_from_ticks_us(ts_us)
                ts_str = C.format_local(ts_ms, TZ_H)
                run_no = int(_snr_next_run.get(sn, 1))

                # enforce spacing going forward
                _sn_relock_until[int(sn)] = time.ticks_add(time.ticks_ms(), RELOCK_COOLDOWN_MS)
                _global_headway_until = time.ticks_add(time.ticks_ms(), TRACK_HEADWAY_MS)

                C.dbg("START captured: SNr %s  Run %s  @ %s" % (sn, run_no, ts_str))
                C.ui_post([f"START SNr {sn}", f"Run {run_no}", "Uploading..."], 900)

                ok = send_started(sn, run_no, ts_str)
                if ok:
                    _snr_next_run[sn] = run_no + 1
                    C.ui_post(["START logged", f"SNr {sn}  Run {run_no}", "Ready"], 1100)
                    unlock_snr("start logged")
                else:
                    C.ui_post(["START queued (offline)", f"SNr {sn} Run {run_no}"], 1100)

            # small idle
            time.sleep_ms(10)

    except KeyboardInterrupt:
        C.safe_shutdown(["KeyboardInterrupt"], sta=sta, led_pin=LED_PIN)
    except Exception as e:
        C.show_error("main", e)
        C.log_to_file(head_lines=[DEVICE_NAME, "ID "+DEVICE_ID])
        C.safe_shutdown(["Error exit"], sta=sta, led_pin=LED_PIN)

if __name__ == "__main__":
    DEVICE_ID = ""
    main()

