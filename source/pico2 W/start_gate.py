# start_gate.py — Start Gate with dual-beam speed measurement (PIO + hard IRQ),
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
PIN_START_NUM   = 2    # Beam 1: idle HIGH, FALLING when broken
PIN_START_NUM_2 = 3    # Beam 2: idle HIGH, FALLING when broken
PIN_STOP_NUM    = 22   # cancel/STOP button (hold to show log / shutdown)
LED_PIN         = Pin("LED", Pin.OUT, value=1)  # Pico2 W onboard LED (GPIO15)

START_PIN  = Pin(PIN_START_NUM,   Pin.IN, Pin.PULL_UP)
START_PIN2 = Pin(PIN_START_NUM_2, Pin.IN, Pin.PULL_UP)
STOP_PIN   = Pin(PIN_STOP_NUM,    Pin.IN, Pin.PULL_UP)

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

# Speed measurement
BEAM_DISTANCE_MM      = 1000     # distance between beam 1 and beam 2 (default 1.0 m)
BEAM_PAIR_TIMEOUT_MS  = 5000     # if 2nd beam doesn't arrive within this, cancel pairing
STRICT_ORDER          = False    # if True, require 1 then 2 (ignore 2->1)

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

# Pairing for speed
_first_beam_src = None    # 1 or 2
_first_beam_us  = None    # ticks_us timestamp
_first_beam_set_ms_deadline = 0

_SETTINGS_REFRESH_MS = 120000
_last_settings_fetch = 0

# Simple lock state
_locked_snr = None
def current_snr(): return _locked_snr
def lock_snr(snr):
    global _locked_snr
    _locked_snr = int(snr)
def unlock_snr(reason=""):
    global _locked_snr, _first_beam_src, _first_beam_us
    _locked_snr = None
    _first_beam_src = None
    _first_beam_us  = None
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
def _root(): return C.build_root(credentials.SERVER_HOST)
def _full(path): return _root() + (path if path.startswith("/") else ("/"+path))

def post_race(payload):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    res = C.http_post_json(_full(INSERT_PATH), payload, headers=headers)
    return bool(res and res.get("status") == "success")

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
    if speed_mps is not None:       payload["speed_mps"] = float(speed_mps)
    if speed_kmh is not None:       payload["speed_kmh"] = float(speed_kmh)
    if beam_distance_mm is not None: payload["beam_distance_mm"] = int(beam_distance_mm)
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
    global _last_settings_fetch, RELOCK_COOLDOWN_MS, MIN_START_INTERVAL_MS, _UID_COOLDOWN_MS, TRACK_HEADWAY_MS, BEAM_DISTANCE_MM, BEAM_PAIR_TIMEOUT_MS
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

        # beam spacing + timeout (optional)
        bd_mm = _to_int(s.get("beam_distance_mm") or s.get("BEAM_DISTANCE_MM"))
        if isinstance(bd_mm,int) and bd_mm>0:
            BEAM_DISTANCE_MM = bd_mm
            C.dbg("Setting BEAM_DISTANCE_MM =", BEAM_DISTANCE_MM)
        bto  = _to_int(s.get("beam_pair_timeout_ms") or s.get("BEAM_PAIR_TIMEOUT_MS"))
        if isinstance(bto,int) and bto>0:
            BEAM_PAIR_TIMEOUT_MS = bto
            C.dbg("Setting BEAM_PAIR_TIMEOUT_MS =", BEAM_PAIR_TIMEOUT_MS)

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

# --- Hard-IRQ ring buffers (separate per beam) ---
micropython.alloc_emergency_exception_buf(256)
_Q_SIZE = 16

_ev1_buf  = [0] * _Q_SIZE
_ev1_head = 0
_ev1_tail = 0
_ev2_buf  = [0] * _Q_SIZE
_ev2_head = 0
_ev2_tail = 0
dropped1 = 0
dropped2 = 0

def _sm1_irq_handler(sm):
    # HARD IRQ: avoid allocations / prints
    global _ev1_head, dropped1
    tsus = time.ticks_us()
    nxt = (_ev1_head + 1) & (_Q_SIZE - 1)
    if nxt == _ev1_tail:
        dropped1 += 1
        return
    _ev1_buf[_ev1_head] = tsus
    _ev1_head = nxt

def _sm2_irq_handler(sm):
    global _ev2_head, dropped2
    tsus = time.ticks_us()
    nxt = (_ev2_head + 1) & (_Q_SIZE - 1)
    if nxt == _ev2_tail:
        dropped2 += 1
        return
    _ev2_buf[_ev2_head] = tsus
    _ev2_head = nxt

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

def _reset_pairing():
    global _first_beam_src, _first_beam_us, _first_beam_set_ms_deadline
    _first_beam_src = None
    _first_beam_us  = None
    _first_beam_set_ms_deadline = 0

def _drain_next_event():
    """Merge-reads the next earliest event (src, ts_us) across both beams, or (None, None)."""
    global _ev1_tail, _ev2_tail
    if _ev1_tail == _ev1_head and _ev2_tail == _ev2_head:
        return (None, None)
    if _ev1_tail == _ev1_head:
        ts = _ev2_buf[_ev2_tail]; _ev2_tail = (_ev2_tail + 1) & (_Q_SIZE - 1)
        return (2, ts)
    if _ev2_tail == _ev2_head:
        ts = _ev1_buf[_ev1_tail]; _ev1_tail = (_ev1_tail + 1) & (_Q_SIZE - 1)
        return (1, ts)
    # both non-empty: pick earlier using ticks_diff (wrap safe)
    ts1 = _ev1_buf[_ev1_tail]; ts2 = _ev2_buf[_ev2_tail]
    d12 = time.ticks_diff(ts1, ts2)
    if d12 <= 0:
        _ev1_tail = (_ev1_tail + 1) & (_Q_SIZE - 1)
        return (1, ts1)
    else:
        _ev2_tail = (_ev2_tail + 1) & (_Q_SIZE - 1)
        return (2, ts2)

def main():
    global DEVICE_ID, _BASE_TICKS_US, _BASE_EPOCH_MS, _global_headway_until

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

    C.dbg("Beam1 idle =", START_PIN.value(), "  Beam2 idle =", START_PIN2.value())

    # --- Arm two PIO SMs (one per beam) ---
    # PIO clock 2 MHz -> ~0.5 us per iteration for MIN_LOW_US_DEFAULT
    sm1 = StateMachine(0, beam_fall_irq, freq=2_000_000,
                       jmp_pin=PIN_START_NUM, in_base=PIN_START_NUM, set_base=PIN_START_NUM)
    sm2 = StateMachine(1, beam_fall_irq, freq=2_000_000,
                       jmp_pin=PIN_START_NUM_2, in_base=PIN_START_NUM_2, set_base=PIN_START_NUM_2)

    # prime thresholds
    sm1.put(MIN_LOW_US_DEFAULT)
    sm2.put(MIN_LOW_US_DEFAULT)

    sm1.irq(handler=_sm1_irq_handler, hard=True)
    sm2.irq(handler=_sm2_irq_handler, hard=True)
    sm1.active(1)
    sm2.active(1)

    # Core1 worker
    if _thread: _thread.start_new_thread(core1_worker, ())

    draw_unlocked()
    LOG_HOLD_MS=1200; SHUT_HOLD_MS=4000
    last_idle = time.ticks_ms()
    last_blink = time.ticks_ms()
    C.dbg("StartGate main loop (dual PIO armed)")

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
                rem_headway = time.ticks_diff(_global_headway_until, time.ticks_ms())
                if rem_headway > 0:
                    C.ui_post(["Startabstand aktiv", f"warte {max(1, rem_headway//1000)}s"], 900)
                else:
                    until = _sn_relock_until.get(int(snr_to_lock), 0)
                    if time.ticks_diff(until, time.ticks_ms()) > 0:
                        rem = time.ticks_diff(until, time.ticks_ms())
                        C.ui_post([f"SNr {snr_to_lock} gesperrt", f"warte {max(1, rem//1000)}s"], 900)
                    else:
                        lock_snr(snr_to_lock)
                        draw_locked(snr_to_lock, _snr_next_run.get(snr_to_lock, 1))
                        _reset_pairing()
                        C.dbg("RFID LOCKED →", snr_to_lock)

            # Idle repaint
            if time.ticks_diff(time.ticks_ms(), last_idle) > 600 and not C.notice_active():
                if current_snr() is None:
                    draw_unlocked()
                else:
                    sn=current_snr(); run_no=int(_snr_next_run.get(sn,1))
                    draw_locked(sn, run_no)
                last_idle = time.ticks_ms()

            # --- Dual-beam event handling with pairing ---
            # Timeout pending pair
            if _first_beam_us is not None:
                if time.ticks_diff(time.ticks_ms(), _first_beam_set_ms_deadline) >= 0:
                    C.ui_post(["2. Lichtschranke fehlt", "Messung verworfen"], 900)
                    _reset_pairing()

            # Drain in time order
            while True:
                src, ts_us = _drain_next_event()
                if src is None:
                    break

                sn = current_snr()
                if sn is None:
                    C.ui_post(["START ignoriert", "Keine SNr gelockt"], 700)
                    continue

                now_ms = time.ticks_ms()
                last_ms = _last_sn_start.get(sn, 0)
                if time.ticks_diff(now_ms, last_ms) < MIN_START_INTERVAL_MS:
                    # Per-SNr duplicate protection
                    continue

                # Pairing logic
                if _first_beam_us is None:
                    # First beam latched
                    _first_beam_src = src
                    _first_beam_us  = ts_us
                    _first_beam_set_ms_deadline = time.ticks_add(time.ticks_ms(), BEAM_PAIR_TIMEOUT_MS)
                    # Optional: show hint which beam first
                    C.ui_post([f"LS{src} erkannt", "warte LS"+("2" if src==1 else "1")], 400)
                else:
                    # Second beam: if strict order is on, require 1->2
                    if STRICT_ORDER and not (_first_beam_src==1 and src==2):
                        # restart pairing with this edge as first
                        _first_beam_src = src
                        _first_beam_us  = ts_us
                        _first_beam_set_ms_deadline = time.ticks_add(time.ticks_ms(), BEAM_PAIR_TIMEOUT_MS)
                        continue

                    # Accept second beam only if it's the *other* sensor
                    if src == _first_beam_src:
                        # same sensor again → treat as re-arm noise; restart pairing
                        _first_beam_src = src
                        _first_beam_us  = ts_us
                        _first_beam_set_ms_deadline = time.ticks_add(time.ticks_ms(), BEAM_PAIR_TIMEOUT_MS)
                        continue

                    # We have a complete pair
                    dt_us = time.ticks_diff(ts_us, _first_beam_us)
                    if dt_us <= 0:
                        # If clocks wrap ordering is odd, skip
                        C.ui_post(["Zeitmessfehler", "Pair verworfen"], 800)
                        _reset_pairing()
                        continue

                    # Compute speed
                    dist_m = BEAM_DISTANCE_MM / 1000.0
                    t_s    = dt_us / 1_000_000.0
                    speed_mps = dist_m / t_s
                    speed_kmh = speed_mps * 3.6

                    # Prepare official "start" timestamp = first beam epoch ms
                    ts_ms  = epoch_ms_from_ticks_us(_first_beam_us)
                    ts_str = C.format_local(ts_ms, TZ_H)
                    run_no = int(_snr_next_run.get(sn, 1))

                    # enforce spacing going forward
                    _sn_relock_until[int(sn)] = time.ticks_add(time.ticks_ms(), RELOCK_COOLDOWN_MS)
                    _global_headway_until = time.ticks_add(time.ticks_ms(), TRACK_HEADWAY_MS)
                    _last_sn_start[sn] = now_ms

                    C.dbg("START+SPEED: SNr %s  Run %s  @ %s  v=%.3f m/s (%.2f km/h)" %
                          (sn, run_no, ts_str, speed_mps, speed_kmh))
                    C.ui_post([f"SNr {sn}  Run {run_no}", f"{speed_kmh:.1f} km/h", "Sende..."], 900)
                    draw_locked(sn, run_no, speed_kmh=speed_kmh)

                    ok = send_started(sn, run_no, ts_str,
                                      speed_mps=speed_mps,
                                      speed_kmh=speed_kmh,
                                      beam_distance_mm=BEAM_DISTANCE_MM)
                    if ok:
                        _snr_next_run[sn] = run_no + 1
                        C.ui_post(["START gespeichert", f"{speed_kmh:.1f} km/h", "Ready"], 1100)
                        unlock_snr("start logged")
                    else:
                        C.ui_post(["START in Warteschlange", f"{speed_kmh:.1f} km/h"], 1100)

                    _reset_pairing()

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
