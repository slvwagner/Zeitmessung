# start_gate.py — Start Gate with dual-beam speed measurement (PIO + GPIO IRQ),
# Core1 RFID worker, re-lock cooldown + global headway, OLED big digits

import time, sys, micropython
from machine import Pin
from rp2 import StateMachine, asm_pio

import credentials
import common as C
from rc522_lowlevel import RC522LL, uid4_display_hex

DEVICE_NAME = "StartGate"
DEVICE_ID = C.build_device_id()  # Initial device ID
TZ_H  = int(getattr(credentials, "TIMEZONE_OFFSET", 0))
API_KEY = getattr(credentials, "API_KEY", "")


# --- GPIOs ---
PIN_START_NUM   = 2    # Beam 1: idle LOW, RISING when broken (PIO)
PIN_START_NUM_2 = 3    # Beam 2: idle LOW, RISING when broken (GPIO IRQ)
PIN_STOP_NUM    = 14   # cancel/STOP button (hold to show log / shutdown)
LED_PIN         = Pin("LED", Pin.OUT, value=1)  # Pico2 W onboard LED (GPIO15)

START_PIN  = Pin(PIN_START_NUM,   Pin.IN, Pin.PULL_DOWN)
START_PIN2 = Pin(PIN_START_NUM_2, Pin.IN, Pin.PULL_DOWN)
STOP_PIN   = Pin(PIN_STOP_NUM,    Pin.IN, Pin.PULL_UP) # The pin can be pulled down to GND this is interpreted as signal

# --- Endpoints ---
LOOKUP_PATH    = "/participant_lookup_by_RFID.php"   # expects ?rfid=AA:BB:CC:DD (LE low 4 bytes)
INSERT_PATH    = "/insert_race.php"
READ_URL       = "/read.php"
SETTINGS_PATH  = "/device_params.php"
STATUS_PATH    = "/status.php"

# --- tunables (overridden via /device_params.php when available) ---
MIN_START_INTERVAL_MS = 800             # duplicate-beam protection per SNr
_UID_COOLDOWN_MS      = 1200            # RFID anti-spam (same tag)
RELOCK_COOLDOWN_MS    = 60000           # after START, same SNr cannot be locked again
TRACK_HEADWAY_MS      = 60000           # after ANY START, next racer may only lock after this
CONNECTION_ERROR_COOLDOWN_MS = 5000     # if a Startnummber look up fails on the sconde core 
                                        # the user will be infomed every CONNECTION_ERROR_COOLDOWN_MS ms
RACE_STATUS =  False                     # If the race status is true the race is running else stopped

# Speed measurement
BEAM_DISTANCE_MM      = 43.18    # distance between beam 1 and beam 2
BEAM_PAIR_TIMEOUT_MS  = 500      # if 2nd beam doesn't arrive within this, cancel pairing
STRICT_ORDER          = True     # if True, require 1 then 2 (ignore 2->1)
DEBUG_BEAMS = True  # set to False to silence Beam1/Beam2 timestamp prints


# PIO beam conditioning (kept for reference; hybrid path doesn't use OSR threshold)
REFRACTORY_US         = 80_000
MIN_LOW_US_DEFAULT    = 20

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

# load setting form Database
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
    C.dbg("Sending payload to insert_race.php:", payload)
    
    # Log the payload details
    try:
        log_msg = f"Insert_race: SNr={payload.get('Startnummer')}, run={payload.get('run')}, ts={payload.get('timestamp_ms')}, dev_id={payload.get('device_id')}"
        send_Piclog(log_msg)
    except Exception as e:
        C.dbg("Failed to create log message:", e)
    
    res = C.http_post_json(_full(INSERT_PATH), payload, headers=headers)
    C.dbg("Server response:", res)
    
    if res and res.get("status") == "success":
        return True
    else:
        # Tag this as race data for proper retry
        payload['_type'] = 'race'
        C.outbox_queue(payload)
        return False

def post_log(payload):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    C.dbg("Sending payload to log.php:", payload)
    res = C.http_post_json(_full("/log.php"), payload, headers=headers)
    C.dbg("log.php response:", res)
    
    if res and res.get("status") == "success":
        return True
    else:
        # Tag this as log data for proper retry
        payload['_type'] = 'log'
        C.outbox_queue(payload)
        return False

def send_started(snr, run_no, ts_str, speed_mps=None, speed_kmh=None, beam_distance_mm=None):
    payload = {
        "Startnummer": int(snr),
        "run": int(run_no),
        "timestamp_ms": ts_str,
        "timezone_offset": TZ_H,
        "device_id": DEVICE_ID,  # Use the global DEVICE_ID
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
  
def send_Piclog(log, Device_ID = DEVICE_ID, Device_Name = DEVICE_NAME):
    # Ensure log is a string
    if isinstance(log, (list, tuple)):
        log = " ".join(str(item) for item in log)
    elif not isinstance(log, str):
        log = str(log)
    
    payload = {
        "Device_ID": Device_ID,
        "Device_Name": Device_Name,
        "log": log
    }
    ok = post_log(payload)
    if not ok:
        C.outbox_queue(payload)
    return ok

def lookup_snr_by_rfid(uid_hex_le4):
    if time.ticks_diff(_deny_until.get(uid_hex_le4, 0), time.ticks_ms()) > 0:
        return None
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    url = _full(LOOKUP_PATH) + "?rfid=" + uid_hex_le4.replace(":", "%3A")
    data = C.http_get_json(url, headers=headers, timeout=2)
    
    # Check if it's a connection failure (None) or invalid response
    if data is None:
        # Connection failed - return special value
        return "CONNECTION_FAILED"
    
    # Server responded but with error (RFID unknown, not allowed, etc.)
    if not (isinstance(data, dict) and data.get("status") in ("ok", "success")):   
        return None
    
    payload = data.get("data") or {}
    p       = payload.get("participant")
    allowed = bool(payload.get("allowed_to_lock", False))
    ontrk   = bool(payload.get("on_track", False))
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
            C.ui_post([sn_txt, "ist bereits", "im Rennen:" , f"Run {run_cur or '-'}"], 5000)
        else:
            C.ui_post([sn_txt, "nicht erlaubt", f"Run {run_cur or '-'}"], 5000)

        C.dbg(" ".join([sn_txt, ("ist bereits im Rennen:" if ontrk else "nicht erlaubt"), f"Run {run_cur or '-'}"]))
        send_Piclog(" ".join([sn_txt, ("ist bereits im Rennen:" if ontrk else "nicht erlaubt"), f"Run {run_cur or '-'}"]))
        C.dbg(payload)
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
    global RACE_STATUS 
    
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        url = _full(STATUS_PATH) + f"?device_name={DEVICE_NAME}&device_id={DEVICE_ID}"
        resp = C.http_get_json(url, headers=headers, timeout=4)
        C.dbg(resp)

        if not (isinstance(resp, dict) and resp.get("status") in ("ok","success")):
            return
        s = resp.get("data") or {}
        
        def _to_bool(v):
            try: return bool(v)
            except: return None

        _race_status  = _to_bool(s.get("Rennstatus"))
        if isinstance(_race_status,bool):
            RACE_STATUS = _race_status
            C.dbg("Setting RACE_STATUS =", RACE_STATUS)

    except Exception as e:
        C.dbg("Settings fetch failed:", msg := f"race status fetch failed: {e}")
        send_Piclog(msg)


# --- Settings fetch/refresh ---
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

        rlc_s  = _to_int(s.get("relock cooldown time")  or s.get("RELOCK_COOLDOWN_S"))
        rlc_ms = _to_int(s.get("relock_cooldown_ms") or s.get("RELOCK_COOLDOWN_MS"))
        if rlc_ms is None and rlc_s is not None: rlc_ms = rlc_s * 1000
        if isinstance(rlc_ms,int) and rlc_ms>=0:
            RELOCK_COOLDOWN_MS = rlc_ms
            C.dbg("Setting RELOCK_COOLDOWN_MS =", RELOCK_COOLDOWN_MS)

        th_s  = _to_int(s.get("track headway")  or s.get("TRACK_HEADWAY_S"))
        th_ms = _to_int(s.get("track_headway_ms") or s.get("TRACK_HEADWAY_MS"))
        if th_ms is None and th_s is not None: th_ms = th_s * 1000
        if isinstance(th_ms,int) and th_ms>=0:
            TRACK_HEADWAY_MS = th_ms
            C.dbg("Setting TRACK_HEADWAY_MS =", TRACK_HEADWAY_MS)

        msi = _to_int(s.get("min_start_interval_ms") or s.get("MIN_START_INTERVAL_MS"))
        if isinstance(msi,int) and msi>=0:
            MIN_START_INTERVAL_MS = msi
            C.dbg("Setting MIN_START_INTERVAL_MS =", MIN_START_INTERVAL_MS)

        uid_cd = _to_int(s.get("uid_cooldown_ms") or s.get("UID_COOLDOWN_MS"))
        if isinstance(uid_cd,int) and uid_cd>=0:
            _UID_COOLDOWN_MS = uid_cd
            C.dbg("Setting _UID_COOLDOWN_MS =", _UID_COOLDOWN_MS)

        bd_mm = _to_int(s.get("beam distance") or s.get("BEAM_DISTANCE_MM"))
        if isinstance(bd_mm,int) and bd_mm>0:
            BEAM_DISTANCE_MM = bd_mm
            C.dbg("Setting BEAM_DISTANCE_MM =", BEAM_DISTANCE_MM)
        bto  = _to_int(s.get("beam pair timeout") or s.get("BEAM_PAIR_TIMEOUT_MS"))
        if isinstance(bto,int) and bto>0:
            BEAM_PAIR_TIMEOUT_MS = bto
            C.dbg("Setting BEAM_PAIR_TIMEOUT_MS =", BEAM_PAIR_TIMEOUT_MS)

    except Exception as e:
        C.dbg("Settings fetch failed:", msg := f"Settings fetch failed: {e}")
        send_Piclog(msg)

def _recent_uid(uid_full):
    now = time.ticks_ms()
    last = _last_uid_full.get(uid_full, 0)
    if time.ticks_diff(now, last) < _UID_COOLDOWN_MS:
        return True
    _last_uid_full[uid_full] = now
    return False

# --- PIO program for Beam 1 (rising-edge → IRQ) ---
@asm_pio()
def beam_rise_irq():
    label("start")
    wait(0, pin, 0)      # require idle LOW
    wait(1, pin, 0)      # rising edge (beam broken)
    irq(0)               # raise IRQ
    wait(0, pin, 0)      # wait for release (LOW)
    jmp("start")

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
    # PIO Beam1 IRQ → record ticks_us
    global _ev1_head, dropped1
    tsus = time.ticks_us()
    if DEBUG_BEAMS:
        print("Beam1 IRQ @", tsus)
    nxt = (_ev1_head + 1) & (_Q_SIZE - 1)
    if nxt == _ev1_tail:
        dropped1 += 1
        return
    _ev1_buf[_ev1_head] = tsus
    _ev1_head = nxt

def _sm2_irq_handler(pin=None):
    # GPIO Beam2 IRQ → record ticks_us
    global _ev2_head, dropped2
    tsus = time.ticks_us()
    if DEBUG_BEAMS:
        print("Beam2 IRQ @", tsus)
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
    last_connection_error_msg = 0  # last database connection error happend [ms]
    while True:
        try:
            # RFID sann
            uid = rfid.get_uid()
            if uid and current_snr() is None:
                uid_full = ":".join("{:02X}".format(b) for b in uid)
                if not _recent_uid(uid_full):
                    le4 = uid4_display_hex(uid)

                    # Deny next sann 
                    if time.ticks_diff(_deny_until.get(le4 or "", 0), time.ticks_ms()) > 0:
                        pass

                    else: # Headway to start next racer
                        rem_headway = time.ticks_diff(_global_headway_until, time.ticks_ms())
                        if rem_headway > 0:
                            secs = max(1, rem_headway // 1000)
                            C.ui_post(["Startabstand aktiv", f"warte {secs}s"], 900)
                            _deny_until[le4] = time.ticks_add(time.ticks_ms(), min(1200, rem_headway))

                        else: # Lookup Startnummer to start the race
                            snr = lookup_snr_by_rfid(le4)
                            if snr == "CONNECTION_FAILED":
                                # Database connection failed - inform user occasionally
                                now = time.ticks_ms()
                                if time.ticks_diff(now, last_connection_error_msg) > CONNECTION_ERROR_COOLDOWN_MS:
                                    C.ui_post(["Server nicht", "erreichbar!", "Bitte prüfen..."], 2000)
                                    last_connection_error_msg = now
                                _deny_until[le4] = time.ticks_add(time.ticks_ms(), 1500)
                                
                            elif snr is None:
                                # RFID unknown or not allowed (already handled in lookup function)
                                _deny_until[le4] = time.ticks_add(time.ticks_ms(), 1500)
                                
                            else:
                                # Valid SNr found
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
                # FIXED LINE: Send to correct endpoint based on payload type
                C.outbox_flush(lambda p: post_race(p) if 'Startnummer' in p else post_log(p))
            except Exception:
                pass
            last_flush = now_ms

        _maybe_refresh_settings()
        time.sleep_ms(15)

# --- Entry / Main ---
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
    global _first_beam_us, _first_beam_src, _first_beam_set_ms_deadline
    # check beam status 
    global stop 

    # OLED hello
    import OLED
    OLED.oled_init()

    # WiFi connection with retry
    max_retries = 3
    sta = None
    for attempt in range(max_retries):
        try:
            sta = C.wifi_connect(credentials.SSID, credentials.PASSWORD)
            if sta:
                C.dbg(f"WiFi connected on attempt {attempt+1}")
                break
        except Exception as e:
            C.dbg(f"WiFi connection attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                C.ui_post([f"WiFi retry {attempt+1}/{max_retries}", "..."], 1000)
                time.sleep(2)
    
    if not sta:
        C.ui_post(["WiFi failed!", "Check credentials", "and network"], 5000)
        send_Piclog("WiFi connection failed after all retries")
        time.sleep(5)
        # Either reboot or continue in limited mode
        C.safe_shutdown(["KeyboardInterrupt"], sta=sta, led_pin=LED_PIN)
    
    # Only try NTP if WiFi is connected
    try:
        C.time_sync_ntp()
    except Exception as e:
        C.dbg(f"NTP sync failed: {e}")
        C.ui_post(["Zeitsync fehlgeschlagen", "lokale Zeit wird", "verwendet"], 3000)
    
    C.dbg(f"DEVICE_ID set to: {DEVICE_ID}")

    # TEST CONNECTION TO SERVER
    test_url = _full(READ_URL) + "?limit=1"
    C.dbg(f"Testing connection to server: {test_url}")

    try:
        response = C.http_get_json(test_url, timeout=5)
        C.dbg(f"Server connection test result: {response}")
        if response is None:
            C.ui_post(["Server nicht", "erreichbar!", "Bitte prüfen..."], 5000)
        
    except Exception as e:
        C.dbg(f"Server test failed: {e}")
        C.ui_post(["Server-Fehler:", str(e)], 5000)

    # check race status
    race_status()

    # Mesurment system    
    # epoch base for fast ts conversion
    _BASE_EPOCH_MS = C.epoch_ms()
    _BASE_TICKS_US = time.ticks_us()

    if (START_PIN.value() + START_PIN2.value()) == 0:
        msg = [DEVICE_NAME,
         str(sta.ifconfig()[0]), 
         "is ready", 
        "Beam1 idle =" + str(START_PIN.value()), 
        "Beam2 idle =" + str(START_PIN2.value())]
        C.ui_post(msg, 3000)
        send_Piclog(" ".join(msg))
        stop = False
    else:
        msg = [DEVICE_NAME, "WiFi "+ str(sta.ifconfig()[0]), "is not ready","The beams state", "is not correct.", 
        "Beam1 idle =" + str(START_PIN.value()), "Beam2 idle =" + str(START_PIN2.value())]
        C.ui_post(msg, 10000)
        send_Piclog(" ".join(msg))
        stop = True

    # --- Arm Beam 1 via PIO (precise timing) ---
    sm1 = StateMachine(0, beam_rise_irq, freq=2_000_000,
                       in_base=Pin(PIN_START_NUM), jmp_pin=PIN_START_NUM)
    sm1.irq(handler=_sm1_irq_handler)   # hard=False (safe in MicroPython)
    sm1.active(1)

    # --- Arm Beam 2 via GPIO interrupt (independent, no PIO cross-talk) ---
    START_PIN2.irq(handler=_sm2_irq_handler, trigger=Pin.IRQ_RISING)

    # Core1 worker
    if _thread:
        _thread.start_new_thread(core1_worker, ())

    draw_unlocked()
    LOG_HOLD_MS=1200; SHUT_HOLD_MS=5000
    last_idle = time.ticks_ms()
    last_blink = time.ticks_ms()
    C.dbg("StartGate main loop (PIO+GPIO armed)")

    try:
        while True:
             # Alive blink
            if time.ticks_diff(time.ticks_ms(), last_blink) > 100:
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
                    msg = ["Start wurde", "abgebrochen"]
                    C.ui_post(msg, 1200)
                    send_Piclog(" ".join(msg))
                draw_unlocked()
            
            # Drain one UI notice if any
            if C.ui_drain_once():
                last_idle = time.ticks_ms()

            # Consume pending lock from Core1
            # Starnummer login
            snr_to_lock = _take_pending_lock()
            if snr_to_lock is not None and current_snr() is None:
                # Vorlauf letzter Fahrer. Wie viel Zeitabstand muss der nächste Fahrer haben
                rem_headway = time.ticks_diff(_global_headway_until, time.ticks_ms())
                if rem_headway > 0:
                    msg = ["Startabstand aktiv", f"warte {max(1, rem_headway//1000)}s"]
                    C.ui_post(msg, 900)
                    send_Piclog(" ".join(msg))
                else:
                    until = _sn_relock_until.get(int(snr_to_lock), 0)
                    if time.ticks_diff(until, time.ticks_ms()) > 0:
                        rem = time.ticks_diff(until, time.ticks_ms())
                        msg  = [f"SNr {snr_to_lock} gesperrt", f"warte {max(1, rem//1000)}s"]
                        C.ui_post(msg, 900)
                        send_Piclog(" ".join(msg))
                    else:
                        # Race start only possible if race is unloked
                        race_status()
                        if RACE_STATUS: 
                            # Einloggen zum Rennstart
                            lock_snr(snr_to_lock)
                            draw_locked(snr_to_lock, _snr_next_run.get(snr_to_lock, 1))
                            _reset_pairing()
                            msg = f"RFID LOCKED: {snr_to_lock}"
                            C.dbg(msg)
                            send_Piclog(msg)

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
                    msg = ["2. Lichtschranke fehlt", "Messung verworfen"]
                    C.ui_post(msg, 900)
                    C.dbg(msg)
                    # FIX: Use send_Piclog with a string
                    send_Piclog("2. Lichtschranke fehlt - Messung verworfen")
                    _reset_pairing()

            # Drain in time order
            while True:
                src, ts_us = _drain_next_event()
                if src is None:
                    break

                sn = current_snr()
                if sn is None:
                    msg = ["START ignoriert", "Keine SNr gelockt"]
                    C.dbg(msg)
                    C.ui_post(msg, 700)
                    send_Piclog(" ".join(msg))
                    continue

                now_ms = time.ticks_ms()
                last_ms = _last_sn_start.get(sn, 0)
                if time.ticks_diff(now_ms, last_ms) < MIN_START_INTERVAL_MS:
                    continue

                # Pairing logic
                if _first_beam_us is None:
                    _first_beam_src = src
                    _first_beam_us  = ts_us
                    _first_beam_set_ms_deadline = time.ticks_add(time.ticks_ms(), BEAM_PAIR_TIMEOUT_MS)
                    C.ui_post([f"LS{src} erkannt", "warte LS"+("2" if src==1 else "1")], 400)
                else:
                    if STRICT_ORDER and not (_first_beam_src==1 and src==2):
                        _first_beam_src = src
                        _first_beam_us  = ts_us
                        _first_beam_set_ms_deadline = time.ticks_add(time.ticks_ms(), BEAM_PAIR_TIMEOUT_MS)
                        continue

                    if src == _first_beam_src:
                        _first_beam_src = src
                        _first_beam_us  = ts_us
                        _first_beam_set_ms_deadline = time.ticks_add(time.ticks_ms(), BEAM_PAIR_TIMEOUT_MS)
                        continue

                    # We have a complete pair
                    dt_us = time.ticks_diff(ts_us, _first_beam_us)
                    if dt_us <= 0:
                        msg = ["Zeitmessfehler", "Pair verworfen"]
                        C.ui_post(["Zeitmessfehler", "Pair verworfen"], 800)
                        send_Piclog(" ".join(msg))
                        _reset_pairing()
                        continue

                    dist_m = BEAM_DISTANCE_MM / 1000.0
                    t_s    = dt_us / 1_000_000.0
                    speed_mps = dist_m / t_s
                    speed_kmh = speed_mps * 3.6

                    ts_ms  = epoch_ms_from_ticks_us(_first_beam_us)
                    ts_str = C.format_local(ts_ms, TZ_H)
                    run_no = int(_snr_next_run.get(sn, 1))

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
                        msg = ["START gespeichert", f"{speed_kmh:.1f} km/h", "Ready"]
                        C.ui_post(msg, 1100)
                        send_Piclog(" ".join(msg)) 
                        unlock_snr("start logged")
                    else:
                        msg = ["START in Warteschlange", f"{speed_kmh:.1f} km/h"]
                        C.ui_post(msg, 1100)
                        send_Piclog(" ".join(msg))
                    _reset_pairing()
        
        

            # if beam status is not correct exit the loop
            if stop:
                msg = ["System can not measure", "because the beams are not in", "correct state"]
                C.ui_post(msg, 5000)
                send_Piclog(" ".join(msg))
                time.sleep(5)
                C.safe_shutdown(["Beamstatus not correct to start measuring"], sta=sta, led_pin=LED_PIN)
            time.sleep_ms(10)
    

    
    except KeyboardInterrupt:
        C.safe_shutdown(["KeyboardInterrupt"], sta=sta, led_pin=LED_PIN)
    except Exception as e:
        C.show_error("main", e)
        C.log_to_file(head_lines=[DEVICE_NAME, "ID "+DEVICE_ID])
        C.safe_shutdown(["Error exit"], sta=sta, led_pin=LED_PIN)

if __name__ == "__main__":
    main()
