# finish_gate.py — Finish Gate with dual-beam speed measurement (PIO + GPIO IRQ),
# similar structure to start_gate.py but without RFID

import time, sys, micropython
from machine import Pin
from rp2 import StateMachine, asm_pio

import credentials
import common as C
import OLED

try:
    from DMX_PIO_DMA import DMXControllerPIO_DMA
except Exception:
    DMXControllerPIO_DMA = None

DEVICE_NAME = "FinishGate"
DEVICE_ID = C.build_device_id()  # Initial device ID

# FIXED: Better int conversion for TIMEZONE_OFFSET
TZ_H_val = getattr(credentials, "TIMEZONE_OFFSET", 0)
try:
    TZ_H = int(TZ_H_val)
except (ValueError, TypeError):
    TZ_H = 0
    print(f"Warning: TIMEZONE_OFFSET '{TZ_H_val}' is not a valid integer, defaulting to 0")

API_KEY = getattr(credentials, "API_KEY", "")

# --- GPIOs ---
PIN_FINISH_NUM   = 2    # Beam 1 input (PIO IRQ)
PIN_FINISH_NUM_2 = 3    # Beam 2 input (GPIO IRQ)
PIN_STOP_NUM     = 14   # cancel/STOP button (hold to show log / shutdown)
BEAM1_SM_ID      = 6    # Avoid DMX fallback SM4/SM5 collisions while staying off PIO0.
LED_PIN          = Pin("LED", Pin.OUT, value=1)  # Pico2 W onboard LED (GPIO15)

FINISH_PIN  = Pin(PIN_FINISH_NUM,   Pin.IN, Pin.PULL_DOWN)
FINISH_PIN2 = Pin(PIN_FINISH_NUM_2, Pin.IN, Pin.PULL_DOWN)
STOP_PIN    = Pin(PIN_STOP_NUM,     Pin.IN, Pin.PULL_UP)

# --- Endpoints ---
OPEN_RUNS_PATH  = "/open_runs.php"   # get currently active runs
INSERT_PATH     = "/insert_race.php"
READ_PATH       = "/read.php"
SETTINGS_PATH   = "/device_params.php"
STATUS_PATH     = "/status.php"
EDIT_RUN        = "/edit_run.php"

# --- tunables (overridden via /device_params.php when available) ---
MIN_FINISH_INTERVAL_MS = 800        # duplicate-beam protection per SNr
RELOCK_COOLDOWN_MS     = 60000      # after FINISH, same SNr cannot be finished again
TRACK_HEADWAY_MS       = 60000      # after ANY FINISH, next racer may only finish after this

# Speed measurement
BEAM_DISTANCE_MM      = 43.18       # distance between beam 1 and beam 2
BEAM_PAIR_TIMEOUT_MS  = 500         # if 2nd beam doesn't arrive within this, cancel pairing
STRICT_ORDER          = True        # if True, require 1 then 2 (ignore 2->1)
DEBUG_BEAMS = True                  # set to False to silence Beam1/Beam2 timestamp prints

# --- DMX event signalling ---
DMX_TX_PIN            = 0
DMX_TRIGGER_PIN       = 1
DMX_PIO_BLOCK         = None  # Auto-select; DMX module prefers PIO2 then falls back if claimed
DMX_EVENT_PULSE_MS    = 500
DMX_IDLE_PATTERN      = ((1, 0), (2, 0), (3, 0))
DMX_FINISH_PATTERN    = ((1, 0), (2, 255), (3, 40))

# PIO beam conditioning
REFRACTORY_US         = 80_000
MIN_LOW_US_DEFAULT    = 20

# --- state ---
_last_sn_finish   = {}     # Startnummer -> last finish ticks_ms
_sn_relock_until  = {}     # Startnummer -> ticks_ms (relock block)
_global_headway_until = 0  # ticks_ms when next finish allowed (global finish spacing)
_open_runs = []            # List of open runs (started but not finished)
_open_runs_version = 0     # Version counter for open runs changes
_last_open_fetch = 0       # Last time we fetched open runs
_OPEN_RUNS_REFRESH_MS = 5000  # Refresh open runs every 5 seconds

# Pairing for speed
_first_beam_src = None    # 1 or 2
_first_beam_us  = None    # ticks_us timestamp
_first_beam_set_ms_deadline = 0

# load setting form Database
_SETTINGS_REFRESH_MS = 120000
_last_settings_fetch = 0

# Current expected runner
_expected_snr = None
_expected_run = None

_dmx_controller = None
_dmx_event_until = 0

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
        print("DMX disabled: DMX_PIO_DMA module unavailable")
        return False
    try:
        _dmx_controller = DMXControllerPIO_DMA(
            tx_pin=DMX_TX_PIN,
            trigger_pin=DMX_TRIGGER_PIN,
            channels=512,
            refresh_rate=43,
            pio_block=DMX_PIO_BLOCK,
        )
        _dmx_controller.auto_ntp_sync = False
        _dmx_controller.auto_status_log = False
        _dmx_controller.start()
        _dmx_apply_pattern(DMX_IDLE_PATTERN)
        print(f"DMX ready on TX GPIO{DMX_TX_PIN}, TRIG GPIO{DMX_TRIGGER_PIN} (FinishGate)")
        return True
    except Exception as e:
        _dmx_controller = None
        print(f"DMX init failed (TX GPIO{DMX_TX_PIN}, TRIG GPIO{DMX_TRIGGER_PIN}): {e}")
        return False

def _dmx_trigger_finish_event():
    global _dmx_event_until
    if _dmx_controller is None:
        return
    _dmx_apply_pattern(DMX_FINISH_PATTERN)
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

def current_expected():
    """Return (snr, run) of expected runner, or (None, None) if no open runs."""
    if _open_runs:
        return _open_runs[0]["Startnummer"], _open_runs[0]["run"]
    return None, None

def advance_to_next_runner():
    """Move to next runner in queue."""
    global _open_runs, _open_runs_version
    if _open_runs:
        _open_runs.pop(0)
        _open_runs_version += 1
        update_expected_runner()

def update_expected_runner():
    """Update global expected runner variables."""
    global _expected_snr, _expected_run
    if _open_runs:
        _expected_snr = _open_runs[0]["Startnummer"]
        _expected_run = _open_runs[0]["run"]
    else:
        _expected_snr = None
        _expected_run = None

# --- OLED helpers ---
def draw_waiting():
    try:
        C.OLED.oled.fill(0)
        C.OLED.oled_text([
            "Warte auf Fahrer", "im Rennen", "zur", "Zeitmessung", "",
            "Zeit:", C.format_local(C.epoch_ms(), TZ_H)[11:23]
        ])
    except Exception:
        pass

def draw_expected(sn, run, speed_kmh=None):
    subtitle = f"Run {run}  {C.format_local(C.epoch_ms(), TZ_H)[11:19]}"
    try:
        if speed_kmh is None:
            C.render_locked_startnummer(sn, subtitle=subtitle)
        else:
            C.render_locked_startnummer(sn, subtitle=subtitle + f"  {speed_kmh:.1f} km/h")
    except Exception:
        pass

def draw_queue():
    """Show queue of expected runners."""
    try:
        lines = ["Erwartete Fahrer:", ""]
        count = 0
        for run in _open_runs[:6]:  # Show up to 6 runners
            lines.append(f"SN {run['Startnummer']} Run {run['run']}")
            count += 1
            if count >= 6:
                lines.append("...")
                break
        if count == 0:
            lines.append("Keine Fahrer")
            lines.append("im Rennen")
        
        C.OLED.oled.fill(0)
        for i, line in enumerate(lines):
            C.OLED.oled.text(line, 0, i * 10)
        C.OLED.oled.show()
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
        log_msg = f"Insert_race: SNr={payload.get('Startnummer')}, run={payload.get('run')}, ts={payload.get('timestamp_ms')}, status={payload.get('race_status')}, dev_id={payload.get('device_id')}"
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

def send_finished(snr, run_no, ts_str, speed_mps=None, speed_kmh=None, beam_distance_mm=None):
    # FIXED: Use safe_int to avoid conversion errors
    payload = {
        "Startnummer": snr,
        "run": run_no,
        "timestamp_ms": ts_str,
        "timezone_offset": TZ_H,
        "device_id": DEVICE_ID,
        "device_name": DEVICE_NAME,
        "race_status": "finished",  # Always finished for finish gate
    }
    if speed_mps is not None:        
        try:
            payload["speed_mps"] = float(speed_mps)
        except (ValueError, TypeError):
            pass
    if speed_kmh is not None:        
        try:
            payload["speed_kmh"] = float(speed_kmh)
        except (ValueError, TypeError):
            pass
    if beam_distance_mm is not None: 
        try:
            payload["beam_distance_mm"] = float(beam_distance_mm)
        except (ValueError, TypeError):
            pass
    
    ok = post_race(payload)
    
    # Also update the participant's last_run if the race was successfully recorded
    if ok and snr is not None:
        # Update last_run for this participant
        update_participant_run(snr, action='set_last', run_no=run_no)
    elif not ok:
        # If race recording failed, queue both
        payload['_type'] = 'race'
        C.outbox_queue(payload)
    
    return ok

def update_participant_run(snr, action='increment_next', run_no=None):
    """Update participant's next_run or last_run in the database."""
    if snr is None:
        C.dbg("Cannot update participant: snr is None")
        return False
        
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    
    # FIXED: Use 
    payload = {
        "Startnummer": (snr),
        "action": action
    }
    
    if action == 'set_last' and run_no is not None:
        payload["run"] = run_no
    
    C.dbg(f"Updating participant run: SNr {snr}, action {action}")
    
    try:
        res = C.http_post_json(_full(EDIT_RUN), payload, headers=headers)
        
        if res and res.get("status") == "success":
            data = res.get("data", {})
            C.dbg(f"Participant updated successfully: next_run={data.get('next_run')}, last_run={data.get('last_run')}")
            return True
        else:
            C.dbg(f"Failed to update participant: {res}")
            # Queue for retry
            payload['_type'] = 'participant_update'
            C.outbox_queue(payload)
            return False
    except Exception as e:
        C.dbg(f"Error updating participant: {e}")
        # Queue for retry
        payload['_type'] = 'participant_update'
        C.outbox_queue(payload)
        return False


# handle outbox flushing for participant updates
def post_participant_update(payload):
    """Special handler for participant update payloads."""
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    
    # Remove the _type field before sending
    if '_type' in payload:
        del payload['_type']
    
    res = C.http_post_json(_full(EDIT_RUN), payload, headers=headers)
    return bool(res and res.get("status") == "success")

def fetch_open_runs(force=False):
    """Fetch list of runners who have started but not finished."""
    global _open_runs, _open_runs_version, _last_open_fetch
    
    now = time.ticks_ms()
    if not force and time.ticks_diff(now, _last_open_fetch) < _OPEN_RUNS_REFRESH_MS:
        return
    
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    url = _full(OPEN_RUNS_PATH) + f"?device_name={DEVICE_NAME}&device_id={DEVICE_ID}"
    
    try:
        data = C.http_get_json(url, headers=headers, timeout=2)
        if data is None:
            # Connection failed
            C.dbg("Failed to fetch open runs: connection error")
            return
        
        if isinstance(data, dict) and data.get("status") in ("ok", "success"):
            runs = data.get("data", [])
            if isinstance(runs, list):
                # Process runs
                processed_runs = []
                for run in runs:
                    try:
                        processed_runs.append({
                            "Startnummer": run.get("Startnummer"),
                            "run": run.get("run", 1),
                            "started_at": run.get("started_at", "")
                        })
                    except Exception:
                        continue
                
                # Check if list changed
                changed = (len(processed_runs) != len(_open_runs))
                if not changed and processed_runs:
                    # Compare first and last elements
                    if _open_runs:
                        changed = (_open_runs[0]["Startnummer"] != processed_runs[0]["Startnummer"] or
                                 _open_runs[0]["run"] != processed_runs[0]["run"])
                
                if changed:
                    _open_runs = processed_runs
                    _open_runs_version += 1
                    update_expected_runner()
                    C.dbg(f"Open runs updated: {len(_open_runs)} runners")
        
        _last_open_fetch = now
        
    except Exception as e:
        C.dbg(f"Error fetching open runs: {e}")


# --- Settings fetch/refresh ---
def _maybe_refresh_settings():
    global _last_settings_fetch, RELOCK_COOLDOWN_MS, MIN_START_INTERVAL_MS, _UID_COOLDOWN_MS, TRACK_HEADWAY_MS, BEAM_DISTANCE_MM, BEAM_PAIR_TIMEOUT_MS
    now = time.ticks_ms()
    if time.ticks_diff(now, _last_settings_fetch) < _SETTINGS_REFRESH_MS:
        return
    _last_settings_fetch = now
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        # FIXED: Removed query parameters since PHP endpoint returns all settings
        url = _full(SETTINGS_PATH)
        resp = C.http_get_json(url, headers=headers, timeout=4)
        if not (isinstance(resp, dict) and resp.get("status") in ("ok","success")):
            return
        s = resp.get("data") or {}

        def _to_int(v):
            try: return int(v)
            except: return None

        # From PHP: "relock cooldown time" -> "relock_cooldown_s"
        # But your Pico code uses RELOCK_COOLDOWN_MS, so convert seconds to ms
        relock_s = _to_int(s.get("relock_cooldown_s"))
        if relock_s is not None and relock_s >= 0:
            RELOCK_COOLDOWN_MS = relock_s * 1000

        # From PHP: "track_headway time" -> "track_headway_s"
        track_headway_s = _to_int(s.get("track_headway_s"))
        if track_headway_s is not None and track_headway_s >= 0:
            TRACK_HEADWAY_MS = track_headway_s * 1000

        # From PHP: "beam distance" -> "beam_distance_mm" (but as float)
        beam_dist = s.get("beam_distance_mm")
        if beam_dist is not None:
            try:
                BEAM_DISTANCE_MM = float(beam_dist)
            except ValueError:
                pass

        # From PHP: "beam pair timeout" -> handles both ms and s
        # Check for beam_pair_timeout_ms first, then beam_pair_timeout_s
        bto_ms = _to_int(s.get("beam_pair_timeout_ms"))
        if bto_ms is not None and bto_ms > 0:
            BEAM_PAIR_TIMEOUT_MS = bto_ms
        else:
            # Check for seconds version
            bto_s = _to_int(s.get("beam_pair_timeout_s"))
            if bto_s is not None and bto_s > 0:
                BEAM_PAIR_TIMEOUT_MS = bto_s * 1000

        # From PHP: "local_time_offset" -> "local_time_offset_h"
        tz_offset = _to_int(s.get("local_time_offset_h"))
        if tz_offset is not None:
            global TZ_H
            TZ_H = tz_offset

    except Exception as e:
        C.dbg("Settings fetch failed:", msg := f"Settings fetch failed: {e}")
        send_Piclog(msg)

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
      

def custom_outbox_flush(payload):
    """Handle different types of outbox payloads."""
    if '_type' not in payload:
        # Default to race posting
        return post_race(payload)
    
    if payload['_type'] == 'race':
        # Remove the type before sending
        del payload['_type']
        return post_race(payload)
    elif payload['_type'] == 'log':
        del payload['_type']
        return post_log(payload)
    elif payload['_type'] == 'participant_update':
        return post_participant_update(payload)
    else:
        # Unknown type, try as race
        del payload['_type']
        return post_race(payload)


def main():
    global DEVICE_ID, _BASE_TICKS_US, _BASE_EPOCH_MS, _global_headway_until
    global _first_beam_us, _first_beam_src, _first_beam_set_ms_deadline
    global _expected_snr, _expected_run
    
    # OLED initialization
    OLED.oled_init()
    _dmx_init()

    # WiFi connection with retry
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
                msg = [f"WiFi retry {attempt+1}/{max_retries}", "..."]
                time.sleep(2)
    
    if not sta:
        send_Piclog("WiFi connection failed after all retries")
        C.safe_shutdown(["Wifi failed!",""], sta=sta, led_pin=LED_PIN)
    
    # Only try NTP if WiFi is connected
    try:
        C.time_sync_ntp()
    except Exception as e:
        C.dbg(f"NTP sync failed: {e}")
        C.ui_post(["Zeitsync fehlgeschlagen", "lokale Zeit wird", "verwendet"], 3000)
    
    C.dbg(f"DEVICE_ID set to: {DEVICE_ID}")

    # TEST CONNECTION TO SERVER
    test_url = _full(READ_PATH) + "?limit=1"
    C.dbg(f"Testing connection to server: {test_url}")

    try:
        response = C.http_get_json(test_url, timeout=5)
        C.dbg(f"Server connection test result: {response}")
        if response is None:
            C.ui_post(["Server nicht", "erreichbar!", "check it..."], 5000)
    except Exception as e:
        C.dbg(f"Server test failed: {e}")
        C.ui_post(["Server-Fehler:", str(e)], 5000)
    
    
    # epoch base for fast ts conversion
    _BASE_EPOCH_MS = C.epoch_ms()
    _BASE_TICKS_US = time.ticks_us()

    # Check beam status
    if (FINISH_PIN.value() + FINISH_PIN2.value()) == 0:
        msg = [DEVICE_NAME,
               str(sta.ifconfig()[0]), 
               "is ready", 
               "Beam1 idle =" + str(FINISH_PIN.value()), 
               "Beam2 idle =" + str(FINISH_PIN2.value())]
        C.ui_post(msg, 3000)
        send_Piclog(" ".join(msg))
    else:
        msg = [DEVICE_NAME, "WiFi "+ str(sta.ifconfig()[0]), "is not ready","The beams state", "is not correct.", 
               "Beam1 idle =" + str(FINISH_PIN.value()), "Beam2 idle =" + str(FINISH_PIN2.value())]
        C.ui_post(msg, 10000)
        send_Piclog(" ".join(msg))
        C.safe_shutdown(["Beam error"], sta=sta, led_pin=LED_PIN)

    # --- Arm Beam 1 via dedicated SM on PIO1 (precise timing) ---
    sm1 = StateMachine(BEAM1_SM_ID, beam_rise_irq, freq=2_000_000,
                       in_base=Pin(PIN_FINISH_NUM), jmp_pin=PIN_FINISH_NUM)
    sm1.irq(handler=_sm1_irq_handler)   # hard=False (safe in MicroPython)
    sm1.active(1)

    # --- Arm Beam 2 via GPIO interrupt (independent, no PIO cross-talk) ---
    FINISH_PIN2.irq(handler=_sm2_irq_handler, trigger=Pin.IRQ_RISING)

    # Initial fetch of open runs
    fetch_open_runs(force=True)
    update_expected_runner()

    if _expected_snr:
        draw_expected(_expected_snr, _expected_run)
    else:
        draw_waiting()
    
    LOG_HOLD_MS = 1200
    SHUT_HOLD_MS = 5000
    last_idle = time.ticks_ms()
    last_blink = time.ticks_ms()
    last_race_status_check = time.ticks_ms()
    last_open_fetch_display = time.ticks_ms()
    C.dbg("FinishGate main loop (PIO+GPIO armed)")

    try:
        while True:
            _dmx_tick()
            # Alive blink
            if time.ticks_diff(time.ticks_ms(), last_blink) > 100:
                last_blink = time.ticks_ms()
                LED_PIN.value(1 - LED_PIN.value())
            
            # STOP behavior (Stop button)
            if STOP_PIN.value() == 0:
                t0 = time.ticks_ms()
                shown = False
                while STOP_PIN.value() == 0:
                    dt = time.ticks_diff(time.ticks_ms(), t0)
                    if (not shown) and dt >= LOG_HOLD_MS and dt < SHUT_HOLD_MS:
                        C.ui_post(["Last log:"] + C.recent_log(7), 1400)
                        shown = True
                    if dt >= SHUT_HOLD_MS:
                        C.log_to_file(head_lines=[DEVICE_NAME, "ID " + DEVICE_ID, "tz=" + str(TZ_H)])
                        C.safe_shutdown(["Power off"], sta=sta, led_pin=LED_PIN)
                    time.sleep_ms(18)
                if shown:
                    time.sleep_ms(1500)
                else:
                    # Short press - show current queue
                    draw_queue()
                    time.sleep(3)
                    if _expected_snr:
                        draw_expected(_expected_snr, _expected_run)
                    else:
                        draw_waiting()

            # Fetch open runs periodically
            fetch_open_runs()
            
            # Update display with open runs info periodically
            if time.ticks_diff(time.ticks_ms(), last_open_fetch_display) > 10000:
                last_open_fetch_display = time.ticks_ms()
                if len(_open_runs) == 0:
                    draw_waiting()
                elif len(_open_runs) == 1:
                    draw_expected(_expected_snr, _expected_run)
                else:
                    draw_queue()
                    time.sleep(3)
                    if _expected_snr:
                        draw_expected(_expected_snr, _expected_run)
            
            # Idle repaint
            if time.ticks_diff(time.ticks_ms(), last_idle) > 600 and not C.notice_active():
                if not _expected_snr:
                    draw_waiting()
                else:
                    draw_expected(_expected_snr, _expected_run)
                last_idle = time.ticks_ms()
            
            # Drain one UI notice if any
            if C.ui_drain_once():
                last_idle = time.ticks_ms()
            
            # Settings refresh
            _maybe_refresh_settings()
            
            # Drain in time order
            while True:
                src, ts_us = _drain_next_event()
                if src is None:
                    break
                
                # Check if we have expected runner
                if not _expected_snr:
                    msg = ["FINISH ignoriert", "Kein Fahrer", "erwartet"]
                    C.dbg(msg)
                    C.ui_post(msg, 700)
                    send_Piclog(" ".join(msg))
                    continue
                
                now_ms = time.ticks_ms()
                last_ms = _last_sn_finish.get(_expected_snr, 0)
                if time.ticks_diff(now_ms, last_ms) < MIN_FINISH_INTERVAL_MS:
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
                    
                    _sn_relock_until[_expected_snr] = time.ticks_add(time.ticks_ms(), RELOCK_COOLDOWN_MS)
                    _global_headway_until = time.ticks_add(time.ticks_ms(), TRACK_HEADWAY_MS)
                    _last_sn_finish[_expected_snr] = now_ms
                    
                    C.dbg("FINISH+SPEED: SNr %s  Run %s  @ %s  v=%.3f m/s (%.2f km/h)" %
                          (_expected_snr, _expected_run, ts_str, speed_mps, speed_kmh))
                    C.ui_post([f"SNr {_expected_snr}  Run {_expected_run}", f"{speed_kmh:.1f} km/h", "Sende..."], 900)
                    draw_expected(_expected_snr, _expected_run, speed_kmh=speed_kmh)
                    _dmx_trigger_finish_event()
                    
                    ok = send_finished(_expected_snr, _expected_run, ts_str,
                                     speed_mps=speed_mps,
                                     speed_kmh=speed_kmh,
                                     beam_distance_mm=BEAM_DISTANCE_MM)
                    if ok:
                        msg = ["FINISH gespeichert", f"{speed_kmh:.1f} km/h", "Ready"]
                        C.ui_post(msg, 1100)
                        send_Piclog(" ".join(msg))
                        # Move to next runner
                        advance_to_next_runner()
                        if _expected_snr:
                            draw_expected(_expected_snr, _expected_run)
                        else:
                            draw_waiting()
                    else:
                        msg = ["FINISH in Warteschlange", f"{speed_kmh:.1f} km/h"]
                        C.ui_post(msg, 1100)
                        send_Piclog(" ".join(msg))
                    _reset_pairing()

            time.sleep_ms(10)

    except KeyboardInterrupt:
        msg = ["Keyboard", "interrupt"]
        C.ui_post(msg, 900)
        _dmx_stop()
        C.safe_shutdown(["Keyboard exit"], sta=sta, led_pin=LED_PIN)
    except Exception as e:
        C.show_error("main", e)
        C.log_to_file(head_lines=[DEVICE_NAME, "ID " + DEVICE_ID])
        _dmx_stop()
        C.safe_shutdown(["Error exit"], sta=sta, led_pin=LED_PIN)

if __name__ == "__main__":
    main()
