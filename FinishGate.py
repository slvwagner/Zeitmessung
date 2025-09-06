# === Finish Gate — uses your credentials paths + OLEDScroller + SAFE SHUTDOWN ===
# Beam (active-low) on GP2, CANCEL on GP3, LED on GP15, OLED via OLED.py on I2C0 (GP4/GP5).
# RC522 optional; if driver ctor mismatches, we skip RFID gracefully.

import network, ntptime, time, urequests, json, sys
from machine import Pin, SPI
import credentials
import OLED

import ubinascii, machine
try:
    import uhashlib  # MicroPython SHA256
except Exception:
    uhashlib = None

DEVICE_ID = ""
DEVICE_NAME = getattr(credentials, "DEVICE_NAME", "FinishGate")

# --- Minimal debug logger ---
DEBUG = True
_LOG = []  # ring buffer of (t_ms, message)

def dbg(msg):
    try:
        s = str(msg)
    except Exception:
        s = repr(msg)
    print(s)
    _LOG.append((time.ticks_ms(), s))
    if len(_LOG) > 60:
        _LOG.pop(0)

def show_error(where, exc):
    try:
        if hasattr(sys, "print_exception"):
            sys.print_exception(exc)
    except Exception:
        pass
    short = ("%s: %s" % (where, exc))[:21]
    dbg("ERR @%s: %s" % (where, exc))
    try:
        OLED.oled_text(["ERR @"+where, short])
    except Exception:
        pass

def show_recent_log(n=7):
    lines = ["Recent log:"]
    for _, m in _LOG[-n:]:
        lines.append(m[:21])
    OLED.oled_text(lines[:8])

# ---------- Optional RC522 ----------
HAVE_RC522 = False
MFRC522 = None
try:
    from mfrc522 import MFRC522 as _MFRC
    MFRC522 = _MFRC
    HAVE_RC522 = True
except Exception:
    HAVE_RC522 = False

# ---------- Pins ----------
PIN_BEAM      = 2
PIN_CANCEL    = 3
PIN_LED_SYNC  = 15

# RC522 on SPI1
SPI_ID        = 1
RC522_SCK     = 10
RC522_MOSI    = 11
RC522_MISO    = 12
RC522_CS      = 13
RC522_RST     = 22

# ---------- Config / Endpoints (from credentials.py) ----------
SERVER_BASE    = str(credentials.SERVER_HOST).rstrip('/')            # e.g. "http://192.168.0.13"
INSERT_EP      = getattr(credentials, "INSERT", "/insert_race.php")  # path or full URL
READ_EP        = getattr(credentials, "READ_URL", "/read.php")       # path or full URL
OPEN_RUNS_EP   = getattr(credentials, "OPEN_RUNS", "/open_runs.php") # path or full URL
RFID_LOOKUP_EP = getattr(credentials, "RFID_LOOKUP", "/rfid_lookup.php")
API_KEY        = getattr(credentials, "API_KEY", "")
TZ_OFFSET_H    = int(getattr(credentials, "TIMEZONE_OFFSET", 0))
FINISH_STATUS  = getattr(credentials, "FINISH_STATUS", "finished")   # or "finish time"

# ---------- URL builder ----------
def build_url(path_or_url: str) -> str:
    s = str(path_or_url)
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if not s.startswith("/"):
        s = "/" + s
    return SERVER_BASE + s

# ---------- State ----------
wlan = None
beam_pin = None
cancel_pin = None
led = None

_epoch_anchor_s  = None
_ticks_anchor_ms = None

_pending_finish_ts_ms = None

_open_runs = []                 # [{'Startnummer':int,'run':int,'started_at':str}]
_last_open_runs_fetch = 0
OPEN_RUNS_REFRESH_MS = 3000

_scroller = None
_last_ui = 0
UI_REFRESH_MS = 2000
_last_post_result = ""

# For fallback aggregation
START_SET = {"started", "race_started"}
FIN_SET   = {"finished", "finish time", "time confirmed"}

# ---------- device_id ----------
def build_device_id():
    """Derive a stable 16-hex device_id from hardware (MAC preferred)."""
    raw = None
    try:
        sta = network.WLAN(network.STA_IF)
        mac = sta.config('mac')  # bytes(6)
        if mac:
            raw = mac
    except Exception:
        pass
    if raw is None:
        try:
            raw = machine.unique_id()
        except Exception:
            raw = None
    if raw is None:
        try:
            raw = (str(wlan.ifconfig()[0]) + "-" + str(time.ticks_cpu())).encode()
        except Exception:
            raw = b'fallback-id'
    if uhashlib:
        digest = uhashlib.sha256(raw).digest()
        return ubinascii.hexlify(digest[-8:]).decode()
    else:
        return ubinascii.hexlify(raw)[-16:].decode()

# ---------- WiFi / Time ----------
def connect_wifi():
    global wlan
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(credentials.SSID, credentials.PASSWORD)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise RuntimeError("WiFi connect timeout")
            time.sleep(0.2)
    dbg("WiFi: %s" % (wlan.ifconfig(),))
    return wlan

def sync_time():
    global _epoch_anchor_s, _ticks_anchor_ms
    for _ in range(3):
        try:
            ntptime.settime()
            break
        except Exception as e:
            dbg("NTP retry: %s" % e)
            time.sleep(1)
    _epoch_anchor_s  = time.time()
    _ticks_anchor_ms = time.ticks_ms()
    dbg("Time synced, anchor=%s" % _epoch_anchor_s)

def now_ms_utc():
    delta_ms = time.ticks_diff(time.ticks_ms(), _ticks_anchor_ms)
    return int(_epoch_anchor_s * 1000 + delta_ms)

def format_dt_millis_local(ms_utc):
    ms_local = ms_utc + TZ_OFFSET_H * 3600 * 1000
    sec = ms_local // 1000
    mmm = ms_local % 1000
    tm = time.gmtime(sec)
    return "%04d-%02d-%02d %02d:%02d:%02d.%03d" % (tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], mmm)

# ---------- HTTP ----------
def http_get_json(path_or_url, params=None):
    url = build_url(path_or_url)
    if params:
        qs = []
        for k, v in params.items():
            qs.append("%s=%s" % (k, str(v)))
        url += ("?" + "&".join(qs))
    r = None
    try:
        headers = {'X-API-Key': API_KEY} if API_KEY else {}
        r = urequests.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
        dbg("GET %s -> http %s" % (url, r.status_code))
        return None
    except Exception as e:
        dbg("GET %s -> EXC %s" % (url, e))
        return None
    finally:
        try:
            if r: r.close()
        except: pass

def http_post_json(path_or_url, payload):
    url = build_url(path_or_url)
    r = None
    try:
        headers = {'Content-Type': 'application/json'}
        if API_KEY:
            headers['X-API-Key'] = API_KEY
        r = urequests.post(url, headers=headers, data=json.dumps(payload))
        if r.status_code == 200:
            return r.json()
        dbg("POST %s -> http %s" % (url, r.status_code))
        return {"status":"error","data":{"http":r.status_code}}
    except Exception as e:
        dbg("POST %s -> EXC %s" % (url, e))
        return {"status":"error","data":{"exception":str(e)}}
    finally:
        try:
            if r: r.close()
        except: pass

# ---------- OLED helpers ----------
def oled_lines(lines):
    OLED.oled_text(lines)

def banner(lines):
    OLED.oled_text(lines)

def make_open_runs_text():
    if not _open_runs:
        return ["No open runs", "Waiting...", "", "Beam: idle"]
    cur = _open_runs[0]
    head = [
        "Expected:",
        " SN #%s  Run %s" % (str(cur.get("Startnummer","-")), str(cur.get("run","-"))),
        "On track: %d" % len(_open_runs),
        ""
    ]
    queue = []
    for idx, r in enumerate(_open_runs[:24]):
        prefix = ">" if idx == 0 else " "
        sn = str(r.get("Startnummer","-"))
        rn = str(r.get("run","-"))
        queue.append("%s#%s r%s" % (prefix, sn, rn))
    return head + queue

def refresh_scroller():
    global _scroller
    text = make_open_runs_text()
    if _scroller is None:
        _scroller = OLED.OLEDScroller(
            OLED.oled, OLED.oled_lock,
            max_cols=21, max_lines=8, line_height=8,
            interval_ms=1200, loop=True, max_loops=None,
            break_long_words=True, hyphenate=False, collapse_spaces=True
        )
    _scroller.set_text(text, y0=0)

# ---------- RC522 (optional) ----------
_rc522 = None
def rc522_init():
    global _rc522
    if not HAVE_RC522:
        return
    try:
        _rc522 = MFRC522(RC522_SCK, RC522_MOSI, RC522_MISO, RC522_CS, RC522_RST)
        dbg("RC522 ready (pins ctor)")
        return
    except Exception as e:
        dbg("RC522 pins-ctor failed: %s" % e)
    try:
        spi = SPI(SPI_ID, baudrate=2500000, polarity=0, phase=0,
                  sck=Pin(RC522_SCK), mosi=Pin(RC522_MOSI), miso=Pin(RC522_MISO))
        _rc522 = MFRC522(spi, Pin(RC522_RST), Pin(RC522_CS))
        dbg("RC522 ready (positional SPI ctor)")
        return
    except Exception as e:
        dbg("RC522 positional SPI-ctor failed: %s" % e)
    dbg("RC522 not initialized; continuing without RFID.")
    _rc522 = None

def poll_rfid_le():
    if _rc522 is None:
        return None
    try:
        (stat, _) = _rc522.request(_rc522.REQIDL)
        if stat != _rc522.OK:
            return None
        (stat, raw_uid) = _rc522.anticoll()
        if stat != _rc522.OK or not raw_uid:
            return None
        le = list(reversed(raw_uid[:4]))
        return ("%02X:%02X:%02X:%02X" % (le[0], le[1], le[2], le[3]))
    except Exception:
        return None

def lookup_rfid(rfid_le):
    if not rfid_le:
        return None
    res = http_get_json(RFID_LOOKUP_EP, {"rfid": rfid_le})
    try:
        if res and res.get("status") in ("ok","success"):
            return (res.get("data", {}) or {}).get("participant") or res.get("participant")
    except Exception:
        pass
    return None

# ---------- Open runs (prefers open_runs.php) ----------
def fetch_open_runs(force=False):
    global _open_runs, _last_open_runs_fetch
    if (not force) and time.ticks_diff(time.ticks_ms(), _last_open_runs_fetch) < OPEN_RUNS_REFRESH_MS:
        return

    # 1) Try open_runs.php
    res = http_get_json(OPEN_RUNS_EP, {})
    if res and res.get("status") == "success":
        rows = res.get("data", [])
        if isinstance(rows, list):
            cleaned = []
            for r in rows:
                try:
                    cleaned.append({
                        "Startnummer": int(r["Startnummer"]),
                        "run": int(r["run"]),
                        "started_at": r.get("started_at","")
                    })
                except Exception:
                    pass
            _open_runs = cleaned
            _last_open_runs_fetch = time.ticks_ms()
            dbg("Open runs (open_runs.php): %d" % len(_open_runs))
            refresh_scroller()
            return

    # 2) Fallback: compute from read.php (ascending id)
    res2 = http_get_json(READ_EP, {"limit": 400, "order": "asc"})
    cleaned = []
    if res2 and res2.get("status") == "success":
        evs = res2.get("data", [])
        last_status = {}
        first_started_at = {}
        for e in evs:
            try:
                sn  = int(e.get("Startnummer"))
                rn  = int(e.get("run", 1))
                st  = (e.get("race_status") or "").strip().lower()
                ts  = e.get("timestamp_ms", "")
                key = (sn, rn)
                if st in START_SET:
                    if key not in first_started_at:
                        first_started_at[key] = ts
                last_status[key] = st
            except Exception:
                pass
        tmp = []
        for (sn, rn), st in last_status.items():
            if st in START_SET:
                tmp.append({"Startnummer": sn, "run": rn, "started_at": first_started_at.get((sn, rn), "")})
        tmp.sort(key=lambda r: r.get("started_at",""))
        cleaned = tmp

    _open_runs = cleaned
    _last_open_runs_fetch = time.ticks_ms()
    dbg("Open runs (fallback): %d" % len(_open_runs))
    refresh_scroller()

# ---------- Beam IRQ ----------
def _beam_irq(pin):
    global _pending_finish_ts_ms
    if pin.value() == 0 and _pending_finish_ts_ms is None:
        _pending_finish_ts_ms = now_ms_utc()

# ---------- Insert finish ----------
def post_finish_for_current(ts_ms):
    global _open_runs, _last_post_result
    if not _open_runs:
        _last_post_result = "No open run!"
        dbg("Finish ignored: queue empty")
        return False
    current = _open_runs[0]
    sn  = current["Startnummer"]
    run = current["run"]

    ts_str = format_dt_millis_local(ts_ms)
    payload = {
        "Startnummer": sn,
        "run": run,
        "timestamp_ms": ts_str,
        "device_id": DEVICE_ID,
        "device_name": DEVICE_NAME,
        "race_status": FINISH_STATUS,   # from credentials (default 'finished')
        "timezone_offset": TZ_OFFSET_H
    }
    dbg("POST finish sn=%s run=%s ts=%s" % (sn, run, ts_str))
    res = http_post_json(INSERT_EP, payload)
    ok = bool(res and res.get("status") == "success")
    if not ok:
        dbg("POST failed: %s" % (res,))
    _last_post_result = ("OK id=%s" % (res.get("data",{}).get("id"))) if ok else ("ERR %s" % (res,))
    if ok:
        _open_runs = _open_runs[1:]
        refresh_scroller()
    return ok

# ---------- SAFE SHUTDOWN ----------
def _persist_log_to_file():
    try:
        with open("last_log.txt", "w") as f:
            f.write("device_id=%s name=%s\n" % (DEVICE_ID, DEVICE_NAME))
            f.write("tz=%s queue=%d\n" % (TZ_OFFSET_H, len(_open_runs)))
            for _, m in _LOG[-30:]:
                f.write(m + "\n")
        dbg("Log saved to last_log.txt")
    except Exception as e:
        dbg("Save log failed: %s" % e)

def safe_shutdown():
    """Gracefully stop logging, save a small log file, and park CPU."""
    dbg("SAFE SHUTDOWN start")
    try:
        # Stop IRQ so accidental beam breaks don't re-arm
        try:
            beam_pin.irq(handler=None)
        except Exception:
            pass

        # Save logs
        _persist_log_to_file()

        # Show message
        try:
            OLED.oled_text(["Shutting down...",
                            "ID "+DEVICE_ID,
                            "Safe to power off"])
        except Exception:
            pass
        time.sleep(0.6)

        # Try to sleep the OLED panel
        try:
            if hasattr(OLED.oled, "_cmd"):
                OLED.oled._cmd(0xAE)  # display OFF
        except Exception:
            pass

        # Turn off Wi-Fi
        try:
            wlan.active(False)
        except Exception:
            pass

        # LED off
        try:
            led.value(0)
        except Exception:
            pass

        # Park CPU "forever"
        try:
            # On rp2, deepsleep may not exist; lightsleep parks until reset
            if hasattr(machine, "lightsleep"):
                while True:
                    machine.lightsleep(2147483647)
            else:
                while True:
                    time.sleep(3600)
        except KeyboardInterrupt:
            # If connected over REPL and user interrupts, just exit main
            pass
    except Exception as e:
        show_error("shutdown", e)
    finally:
        # Last resort: stop script
        raise SystemExit

# ---------- Setup / Main ----------
def setup():
    global beam_pin, cancel_pin, led, DEVICE_ID
    connect_wifi()
    sync_time()

    DEVICE_ID = build_device_id()

    OLED.oled_init()
    banner([
        "Finish Gate",
        "ID " + DEVICE_ID,
        "WiFi " + (wlan.ifconfig()[0] if wlan else "?"),
        "Syncing runs..."
    ])

    beam_pin   = Pin(PIN_BEAM, Pin.IN, Pin.PULL_UP)
    cancel_pin = Pin(PIN_CANCEL, Pin.IN, Pin.PULL_UP)
    led        = Pin(PIN_LED_SYNC, Pin.OUT)
    led.value(0)

    try:
        beam_pin.irq(trigger=Pin.IRQ_FALLING, handler=_beam_irq)
    except Exception:
        beam_pin.irq(handler=_beam_irq, trigger=Pin.IRQ_FALLING)

    if HAVE_RC522:
        rc522_init()

    fetch_open_runs(force=True)
    refresh_scroller()

def main():
    setup()
    last_blink = 0
    blink = 0
    global _pending_finish_ts_ms

    # Hold thresholds (ms)
    LOG_HOLD_MS  = 1200
    SHUT_HOLD_MS = 4000

    while True:
        # Blink LED
        if time.ticks_diff(time.ticks_ms(), last_blink) > 500:
            last_blink = time.ticks_ms()
            blink ^= 1
            led.value(blink)

        # Refresh queue
        fetch_open_runs(force=False)

        # Drive scroller
        if _scroller is not None:
            _scroller.tick()

        # Optional RFID confirmation
        rfid = poll_rfid_le()
        if rfid:
            p = lookup_rfid(rfid)
            if p:
                OLED.oled_text([
                    "RFID %s" % rfid,
                    "SN #%s  %s %s" % (p.get("Startnummer","?"), p.get("Vorname",""), p.get("Name","")),
                    "Nick: %s" % (p.get("Nickname","")),
                    "OK at finish"
                ])
                time.sleep(0.9)
                refresh_scroller()

        # CANCEL: short = clear; 1.2s = show logs; 4s = SAFE SHUTDOWN
        if cancel_pin.value() == 0:
            t0 = time.ticks_ms()
            logs_shown = False
            while cancel_pin.value() == 0:
                dt = time.ticks_diff(time.ticks_ms(), t0)
                if (not logs_shown) and dt >= LOG_HOLD_MS and dt < SHUT_HOLD_MS:
                    show_recent_log(7)
                    logs_shown = True
                if dt >= SHUT_HOLD_MS:
                    safe_shutdown()  # does not return
                time.sleep(0.02)
            # Released
            if logs_shown:
                time.sleep(0.8)
                refresh_scroller()
            else:
                _pending_finish_ts_ms = None
                OLED.oled_text(["Cancelled", "Pending cleared"])
                time.sleep(0.6)
                refresh_scroller()

        # Beam captured → insert finish for expected
        if _pending_finish_ts_ms is not None:
            ts_ms = _pending_finish_ts_ms
            _pending_finish_ts_ms = None
            try:
                cur = _open_runs[0] if _open_runs else {"Startnummer":"-", "run":"-"}
                OLED.oled_text([
                    "FINISH captured!",
                    "SN #%s  Run %s" % (str(cur.get("Startnummer","-")), str(cur.get("run","-"))),
                    format_dt_millis_local(ts_ms),
                    "Uploading..."
                ])
                ok = post_finish_for_current(ts_ms)
                OLED.oled_text([
                    "FINISH %s" % ("OK" if ok else "FAIL"),
                    _last_post_result[:21]
                ])
                time.sleep(0.9)
                fetch_open_runs(force=True)
                refresh_scroller()
            except Exception as e:
                show_error("finish", e)

        # Periodic UI keep-alive
        global _last_ui
        if time.ticks_diff(time.ticks_ms(), _last_ui) > UI_REFRESH_MS:
            _last_ui = time.ticks_ms()
            refresh_scroller()

        time.sleep(0.02)

# ---------- Entry ----------
try:
    main()
except KeyboardInterrupt:
    # If user hits Ctrl-C from REPL/IDE, shut down safely
    safe_shutdown()
except Exception as e:
    try:
        show_error("main", e)
    except:
        pass
    raise
