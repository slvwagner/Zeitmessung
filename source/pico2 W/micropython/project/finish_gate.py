# finish_gate.py — Finish Gate with dual-beam speed measurement (PIO-only),
# similar structure to start_gate.py but without RFID

import time, sys, micropython, gc
from machine import Pin
from rp2 import StateMachine, asm_pio

import credentials
import common as C
import OLED

try:
    from DMX_native_wrapper import DMXControllerPIO_DMA
except ImportError as exc:
    print("DMX native wrapper import failed:", exc)
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
PIN_FINISH_NUM   = 2    # Beam 1 input
PIN_FINISH_NUM_2 = 3    # Beam 2 input
PIN_STOP_NUM     = 14   # cancel/STOP button (hold to show log / shutdown)
BEAM1_SM_ID      = 5   # PIO1 SM5 — separate from DMX (forced PIO2) and WiFi (PIO0)
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
DEBUG_BEAMS = True                  # set to False to silence Beam1/Beam2 timestamp prints
PIO_DUAL_FREQ_HZ      = 2_000_000
PIO_DUAL_DEBOUNCE_CYCLES = 8
PIO_DUAL_CYCLES_PER_COUNT = 2

# --- DMX event signalling ---
DMX_TX_PIN            = 0
DMX_TRIGGER_PIN       = 1
DMX_START_CODE        = 0xFF
DMX_CTRL_SM_ID        = 8
DMX_DATA_SM_ID        = 9
DMX_EVENT_PULSE_MS    = 500
DMX_IDLE_PATTERN      = ((1, 1), (2, 3), (7, 50), (8, 50))
DMX_FINISH_PATTERN    = ((1, 0), (2, 0), (7, 25), (8, 25))

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

# load setting form Database
_SETTINGS_REFRESH_MS = 120000
_last_settings_fetch = 0

# Current expected runner
_expected_snr = None
_expected_run = None

_dmx_controller = None
_dmx_event_until = 0

_dual_beam_sm = None
_pio_debug_last_ms = 0

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
            channels=512,
            refresh_rate=43,
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
    try:
        res = C.http_post_json(_full("/log.php"), payload, headers=headers)
        C.dbg("log.php response:", res)
    except Exception as e:
        C.dbg("log.php post failed:", e)
        return False

    if res and res.get("status") == "success":
        return True
    else:
        # Tag this as log data for proper retry
        payload['_type'] = 'log'
        C.outbox_queue(payload)
        return False

def send_Piclog(log, Device_ID = DEVICE_ID, Device_Name = DEVICE_NAME):
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


def _safe_send_piclog(log_text, min_free=12000):
    """Best-effort log sender that skips silently on low heap."""
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
    global _last_settings_fetch, RELOCK_COOLDOWN_MS, MIN_START_INTERVAL_MS, _UID_COOLDOWN_MS, TRACK_HEADWAY_MS, BEAM_DISTANCE_MM
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

        # From PHP: "local_time_offset" -> "local_time_offset_h"
        tz_offset = _to_int(s.get("local_time_offset_h"))
        if tz_offset is not None:
            global TZ_H
            TZ_H = tz_offset

    except Exception as e:
        C.dbg("Settings fetch failed:", msg := f"Settings fetch failed: {e}")
        send_Piclog(msg)

@asm_pio()
def dual_beam_measure_irq():
    pull(block)
    mov(isr, osr)

    wrap_target()
    # Require both beams low before arming a new measurement.
    wait(0, gpio, 2)
    wait(0, gpio, 3)
    wait(1, gpio, 2)
    mov(x, invert(null))

    label("count_loop")
    jmp(pin, "beam2_candidate")
    jmp(x_dec, "count_loop")
    jmp("overflow")

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

micropython.alloc_emergency_exception_buf(256)
_PIO_DONE_Q_SIZE = 8
_pio_done_buf = [0] * _PIO_DONE_Q_SIZE
_pio_done_head = 0
_pio_done_tail = 0
_pio_done_dropped = 0
_pio_poll_enqueued = 0

def _pio_dual_irq_handler(sm):
    global _pio_done_head, _pio_done_dropped
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
    # Fallback: if IRQ callback is missed but RX has data, synthesize a completion event.
    if _pio_done_head != _pio_done_tail:
        return
    try:
        if sm.rx_fifo() > 0 and _enqueue_pio_done_now():
            _pio_poll_enqueued += 1
    except Exception:
        pass

def _flush_pio_done_events():
    global _pio_done_tail
    _pio_done_tail = _pio_done_head

def _consume_pio_dual_result(sm, completion_ts_us):
    try:
        x_val = sm.get()
    except Exception as e:
        C.dbg("PIO read failed:", e)
        return None

    elapsed_counts = (~x_val) & 0xFFFFFFFF
    if elapsed_counts <= 0:
        return None

    dt_us = (elapsed_counts * PIO_DUAL_CYCLES_PER_COUNT * 1000000) // PIO_DUAL_FREQ_HZ
    if dt_us <= 0:
        return None
    start_ts_us = time.ticks_diff(completion_ts_us, dt_us)
    return start_ts_us, dt_us

def _rearm_pio_dual_sm(reason=""):
    sm = _dual_beam_sm
    if sm is None:
        return
    try:
        sm.active(0)
        while sm.rx_fifo():
            sm.get()
        sm.restart()
        sm.put(PIO_DUAL_DEBOUNCE_CYCLES)
        sm.active(1)
        if reason:
            C.dbg("PIO rearm:", reason)
    except Exception as e:
        C.dbg("PIO rearm failed:", e)

# ticks_us → epoch_ms with wrap-safe base captured after NTP sync
_BASE_TICKS_US = 0
_BASE_EPOCH_MS = 0
def epoch_ms_from_ticks_us(ts_us):
    du = time.ticks_diff(ts_us, _BASE_TICKS_US)
    return _BASE_EPOCH_MS + (du + 500) // 1000

# --- Entry / Main ---
      

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
    global _dual_beam_sm, _pio_debug_last_ms
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
    try:
        response = C.http_get_json(test_url, timeout=5)
        if isinstance(response, dict):
            rows = response.get("data") or []
            C.dbg("Server test:", response.get("status"), "rows=", len(rows))
        else:
            C.dbg("Server test response is None/invalid")
        if response is None:
            C.ui_post(["Server nicht", "erreichbar!", "check it..."], 5000)
    except Exception as e:
        C.dbg(f"Server test failed: {e}")
        C.ui_post(["Server-Fehler:", str(e)], 5000)
    
    
    # epoch base for fast ts conversion
    _BASE_EPOCH_MS = C.epoch_ms()
    _BASE_TICKS_US = time.ticks_us()

    # Check beam status (harmonized with start_gate.py)
    if (FINISH_PIN.value() + FINISH_PIN2.value()) == 0:
        msg = [
            DEVICE_NAME,
            str(sta.ifconfig()[0]),
            "is ready",
            f"Beam1={'Laser can be seen by sensor' if FINISH_PIN.value() == 0 else 'Laser not seen'}",
            f"Beam2={'Laser can be seen by sensor' if FINISH_PIN2.value() == 0 else 'Laser not seen'}"
        ]
        C.ui_post(msg, 3000)
        _safe_send_piclog(" ".join(msg))
    else:
        msg = [
            DEVICE_NAME,
            "WiFi " + str(sta.ifconfig()[0]),
            "is not ready",
            "The beams state",
            "is not correct.",
            f"Beam1={'Laser can be seen by sensor' if FINISH_PIN.value() == 0 else 'Laser not seen'}",
            f"Beam2={'Laser can be seen by sensor' if FINISH_PIN2.value() == 0 else 'Laser not seen'}"
        ]
        C.ui_post(msg, 10000)
        _safe_send_piclog(" ".join(msg))
        C.safe_shutdown(["Beam error"], sta=sta, led_pin=LED_PIN)

    # --- Arm dual-beam PIO measurement SM ---
    gc.collect()
    _dual_beam_sm = StateMachine(
        BEAM1_SM_ID,
        dual_beam_measure_irq,
        freq=PIO_DUAL_FREQ_HZ,
        jmp_pin=Pin(PIN_FINISH_NUM_2),
    )
    _dual_beam_sm.irq(handler=_pio_dual_irq_handler)
    _dual_beam_sm.put(PIO_DUAL_DEBOUNCE_CYCLES)
    _dual_beam_sm.active(1)

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
    C.dbg("FinishGate main loop (dual-beam PIO armed)")

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

            # Poll RX FIFO as fallback in case an IRQ callback was missed.
            _maybe_enqueue_pio_from_rx(_dual_beam_sm)
            
            # Drain PIO completion events
            while True:
                completion_ts_us = _drain_next_pio_done()
                if completion_ts_us is None:
                    break
                
                # Check if we have expected runner
                if not _expected_snr:
                    msg = ["FINISH ignoriert", "Kein Fahrer", "erwartet"]
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
                last_ms = _last_sn_finish.get(_expected_snr, 0)
                if time.ticks_diff(now_ms, last_ms) < MIN_FINISH_INTERVAL_MS:
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
                t_s = dt_us / 1_000_000.0
                speed_mps = dist_m / t_s
                speed_kmh = speed_mps * 3.6

                ts_ms = epoch_ms_from_ticks_us(start_ts_us)
                ts_str = C.format_local(ts_ms, TZ_H)

                _sn_relock_until[_expected_snr] = time.ticks_add(time.ticks_ms(), RELOCK_COOLDOWN_MS)
                _global_headway_until = time.ticks_add(time.ticks_ms(), TRACK_HEADWAY_MS)
                _last_sn_finish[_expected_snr] = now_ms

                C.dbg("FINISH+PIO: SNr %s  Run %s  @ %s  dt_us=%s  v=%.3f m/s (%.2f km/h)" %
                      (_expected_snr, _expected_run, ts_str, dt_us, speed_mps, speed_kmh))
                C.ui_post([f"SNr {_expected_snr}  Run {_expected_run}", f"{speed_kmh:.1f} km/h", "PIO sende..."], 900)
                draw_expected(_expected_snr, _expected_run, speed_kmh=speed_kmh)
                _dmx_trigger_finish_event()

                ok = send_finished(_expected_snr, _expected_run, ts_str,
                                 speed_mps=speed_mps,
                                 speed_kmh=speed_kmh,
                                 beam_distance_mm=BEAM_DISTANCE_MM)
                if ok:
                    msg = ["FINISH gespeichert", f"{speed_kmh:.1f} km/h", "PIO Ready"]
                    C.ui_post(msg, 1100)
                    send_Piclog(" ".join(msg))
                    advance_to_next_runner()
                    if _expected_snr:
                        draw_expected(_expected_snr, _expected_run)
                    else:
                        draw_waiting()
                    _flush_pio_done_events()
                else:
                    msg = ["FINISH in Warteschlange", f"{speed_kmh:.1f} km/h"]
                    C.ui_post(msg, 1100)
                    send_Piclog(" ".join(msg))

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
