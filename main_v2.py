# === Race logger + SAFE SSD1306 OLED (no external ssd1306.py needed) ===
# Pico / Pico W / Pico2 W, MicroPython
# I2C OLED on GP4 (SDA) and GP5 (SCL), 128x64, address 0x3C (auto-detect)
# Uses a robust SSD1306 driver with small I2C chunks and 50 kHz clock.

import network, ntptime, time, urequests, json, _thread, machine, ubinascii, sys
from machine import Pin, Timer, I2C
import credentials  # must define: SSID, PASSWORD, SERVER_HOST, API_KEY, INSERT, TIMEZONE_OFFSET
import OLED

import usocket as socket
import ujson, framebuf, os

# For non-blocking USB keyboard input
try:
    import uselect
except ImportError:
    uselect = None

try:
    from machine import USB_VCP
    _usb = USB_VCP()
except Exception:
    _usb = None

# ----------------------------------------------------------------------
# Hardware definitions
# ----------------------------------------------------------------------
DEVICE_ID = ubinascii.hexlify(machine.unique_id()).decode()
DEVICE_NAME = "Start"

INPUT_PIN_start_race = Pin(2, Pin.IN, Pin.PULL_UP)
INPUT_PIN_stop_race  = Pin(3, Pin.IN, Pin.PULL_UP)
OUTPUT_PIN_time_synced = Pin(12, Pin.OUT)


timer = Timer()  # main ms-ticker

# ---------- Utilities: persist counter safely ----------
STATE_FILE = "race_state.json"

def load_state(default_cnt=1):
    try:
        with open(STATE_FILE, "r") as f:
            d = ujson.loads(f.read())
        return int(d.get("cnt", default_cnt))
    except:
        return default_cnt

def save_state(cnt):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(ujson.dumps({"cnt": cnt}))
        if hasattr(os, "sync"):
            os.sync()
    except Exception as e:
        print("WARN: could not save state:", e)

# ----------------------------------------------------------------------
# Wi-Fi 
# ----------------------------------------------------------------------
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(credentials.SSID, credentials.PASSWORD)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise RuntimeError("WiFi connect timeout")
            time.sleep(0.2)
    print("Connected to WiFi:", wlan.ifconfig())
    return wlan

# ----------------------------------------------------------------------
# --- High-resolution wall-clock ms with optional NTP sync ---
# ----------------------------------------------------------------------
# Works on MicroPython (Pico/Pico W/Pico2 W) and CPython.

# Imports compatible across MP/CP
try:
    import utime as time
except ImportError:
    import time

# machine/Timer may not exist on CPython
try:
    from machine import Timer, Pin
    _HAVE_MACHINE = True
except Exception:
    Timer = None
    Pin = None
    _HAVE_MACHINE = False

# ntptime may not exist (or network not up yet)
try:
    import ntptime
except Exception:
    ntptime = None

# ----------------------------------------------------------------------
# Monotonic millisecond clock + diff (MP uses ticks_*, CP uses monotonic_ns)
# ----------------------------------------------------------------------
try:
    ticks_ms = time.ticks_ms           # MicroPython
    ticks_diff = time.ticks_diff
except AttributeError:
    # CPython fallback (no wrap-around)
    def ticks_ms():
        try:
            return int(time.monotonic_ns() // 1_000_000)
        except AttributeError:
            return int(time.monotonic() * 1000)
    def ticks_diff(a, b):
        return a - b

# ----------------------------------------------------------------------
# Wall-clock epoch ms (ms since 1970-01-01) using RTC + monotonic anchor
# ----------------------------------------------------------------------
_BASE_EPOCH_MS = None
_BASE_TICKS_MS = None

def _init_epoch_ms():
    """Anchor epoch (seconds) to monotonic ms for sub-second precision."""
    global _BASE_EPOCH_MS, _BASE_TICKS_MS
    # Best-effort: if ntptime exists, try once to set RTC.
    if ntptime is not None:
        try:
            ntptime.settime()
        except Exception:
            pass
    _BASE_EPOCH_MS = int(time.time()) * 1000  # seconds -> ms
    _BASE_TICKS_MS = ticks_ms()

def epoch_ms():
    """
    Return wall-clock milliseconds (int) with sub-second precision,
    immune to RTC jumps after initialization.
    """
    global _BASE_EPOCH_MS, _BASE_TICKS_MS
    if _BASE_EPOCH_MS is None:
        _init_epoch_ms()
    return _BASE_EPOCH_MS + int(ticks_diff(ticks_ms(), _BASE_TICKS_MS))

def get_timestamp():
    """
    Human-readable timestamp "YYYY-MM-DD HH:MM:SS.mmm" in local time.
    """
    ms = epoch_ms()
    sec = ms // 1000
    mmm = ms % 1000
    tm = time.localtime(sec)  # (Y, M, D, h, m, s, wday, yday[, isdst])
    return "%04d-%02d-%02d %02d:%02d:%02d.%03d" % (
        tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], mmm
    )

# ----------------------------------------------------------------------
# Optional NTP sync helper (with graceful no-ops for your GPIO/OLED hooks)
# ----------------------------------------------------------------------
def _maybe(fn_name, *args, **kwargs):
    """
    Call a global hook only if it exists. Lets you keep:
      - OUTPUT_PIN_time_synced.on()
      - OLED.oled_text([...], 0)
    without breaking CPython tests or minimal builds.
    """
    g = globals().get(fn_name)
    if callable(g):
        try:
            return g(*args, **kwargs)
        except Exception:
            pass
    # Allow Pin-like objects too (OUTPUT_PIN_time_synced.on)
    obj = globals().get(fn_name)
    if obj is not None:
        # try .on() if present
        try:
            return getattr(obj, "on")()
        except Exception:
            pass
    return None

def sync_time(ntp_servers=None):
    """
    Try multiple NTP servers, set RTC if possible, and (optionally) show status.
    Returns True on first success, False otherwise.

    NOTE: You no longer need a 1 ms Timer; high-res comes from epoch_ms().
    """
    if ntptime is None:
        _maybe("OLED.oled_text", ["NTP unsupported", "ntptime not found"], 0)
        return False

    if ntp_servers is None:
        ntp_servers = ["pool.ntp.org", "time.google.com", "129.6.15.28"]

    for server in ntp_servers:
        try:
            ntptime.host = server
            ntptime.settime()
            # Re-anchor to updated RTC so epoch_ms stays correct.
            _init_epoch_ms()

            _maybe("OUTPUT_PIN_time_synced")  # calls .on() if present
            print("Time synced:", server)
            _maybe("OLED.oled_text", ["NTP synced", server, get_timestamp().split()[1]], 0)
            try:
                time.sleep(1.5)
            except Exception:
                pass
            return True
        except Exception as e:
            print("NTP fail:", server, e)
            _maybe("OLED.oled_text", ["NTP fail", server, str(e)[:21]], 0)
            try:
                time.sleep(1.0)
            except Exception:
                pass

    _maybe("OLED.oled_text", ["NTP FAILED", "Check WiFi/DNS"], 0)
    return False


# --- Remove these if you only used them for ms in the timestamp ---
# ms_counter = 0
# def update_ms(_):
#     global ms_counter
#     ms_counter = (ms_counter + 1) % 1000

# Keep your epoch_ms() exactly as you wrote it.
# It anchors ms to the RTC once and stays monotonic even if RTC jumps later.

def get_timestamp_ms_utc():
    """
    Milliseconds since Unix epoch (UTC), anchored to NTP if sync_time() succeeded.
    """
    return epoch_ms()

def get_timestamp_ms_local(tz_hours=0):
    """
    Milliseconds since Unix epoch in *local* civil time (epoch shifted by tz).
    Use credentials.TIMEZONE_OFFSET (hours) if you keep it.
    """
    return epoch_ms() + int(tz_hours * 3600 * 1000)

def get_timestamp_local_string(tz_hours=0):
    """
    Human-readable local timestamp 'YYYY-MM-DD HH:MM:SS.mmm'.
    Uses epoch_ms() for true, NTP-synced milliseconds.
    """
    ms = get_timestamp_ms_local(tz_hours)
    sec = ms // 1000
    mmm = ms % 1000
    # We already shifted by tz_hours, so format with gmtime()
    tm = time.gmtime(sec)
    return "%04d-%02d-%02d %02d:%02d:%02d.%03d" % (
        tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], mmm
    )

# If you want a name compatible with your previous code:
def get_timestamp():
    return get_timestamp_local_string(getattr(credentials, "TIMEZONE_OFFSET", 0))



# ----------------------------------------------------------------------
# Non-blocking single-character input from USB serial
# ----------------------------------------------------------------------

if uselect:
    _poll = uselect.poll()
    _poll.register(sys.stdin, uselect.POLLIN)
else:
    _poll = None

def read_char_nonblocking():
    # Prefer USB_VCP if available (fast, reliable)
    if _usb:
        if _usb.any():
            b = _usb.read(1)
            return b.decode() if b else None
        return None
    # Fallback to polling sys.stdin (works in Thonny)
    if _poll and _poll.poll(0):
        ch = sys.stdin.read(1)
        return ch
    return None

# ----------------------------------------------------------------------
# HTTP helpers (short, stoppable operations)
# ----------------------------------------------------------------------

def _full_url(path):
    base = credentials.SERVER_HOST.rstrip('/')
    
    # Ensure base has a protocol
    if not base.startswith(('http://', 'https://')):
        base = 'http://' + base  # Default to http if no protocol specified
    
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    
    result = base + p
    print(f"URL built: {result}")  # Debug line
    return result


def send_db_entry(startnummer, run, race_status):
    url = _full_url(credentials.INSERT)
    payload = {
        "Startnummer": int(startnummer),
        "run": int(run),
        "timestamp_ms": get_timestamp_ms_utc(),
        "device_id": DEVICE_ID[:32],
        "device_name": DEVICE_NAME[:50],
        "race_status": str(race_status)[:50],
    }
    r = None
    try:
        headers = {"X-API-Key": getattr(credentials, "API_KEY", "")} if getattr(credentials, "API_KEY", "") else {}
        r = urequests.post(url, json=payload, headers=headers, timeout=5)
        print("HTTP", r.status_code, r.text)
        if r.status_code != 200:
            print(f"Server returned error: {r.status_code}")
            return False
        data = r.json()
        return isinstance(data, dict) and data.get("status") == "success"
    except Exception as e:
        print("POST error details:", e)
        import sys
        sys.print_exception(e)  # This will give more detailed error info
        return False
    finally:
        if r:
            try: 
                r.close()
            except: 
                pass


def build_url(base):
    s = str(base).strip()
    if not s: 
        raise ValueError("credentials.READ_URL empty")
    
    # Ensure URL has a protocol
    if not s.startswith(('http://', 'https://')):
        s = 'http://' + s
    
    if s.rstrip("/").endswith("/read.php"): 
        return s
    return s.rstrip("/") + "/read.php"

def _parse_host_port(base):
    s = str(base).strip(); scheme = "http"
    if s.startswith("http://"): s = s[7:]
    elif s.startswith("https://"): s = s[8:]; scheme = "https"
    hostpart = s.split("/", 1)[0]
    if ":" in hostpart:
        host, p = hostpart.split(":", 1)
        port = int(p) if p.isdigit() else (443 if scheme=="https" else 80)
    else: host, port = hostpart, (443 if scheme=="https" else 80)
    return scheme, host, port

def http_get(host, path, port=80, timeout_s=1.0):
    # Small, timed HTTP GET. Returns (status_code, body_bytes) or raises OSError.
    ai = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    s.settimeout(timeout_s)
    try:
        s.connect(ai)
        req = "GET {} HTTP/1.0\r\nHost: {}\r\nConnection: close\r\n\r\n".format(path, host)
        s.send(req.encode())
        buf = b""
        while True:
            try:
                chunk = s.recv(1024)
                if not chunk: break
                buf += chunk
            except OSError:
                break
    finally:
        try: s.close()
        except: pass
    sep = buf.find(b"\r\n\r\n")
    head = buf[:sep] if sep >= 0 else b""
    body = buf[sep+4:] if sep >= 0 else buf
    # parse status
    status = 0
    try:
        line = head.split(b"\r\n", 1)[0]
        status = int(line.split()[1])
    except:
        status = 0
    return status, body

# Convenience JSON fetchers
def fetch_json(url, timeout=2):
    r = None
    try:
        r = urequests.get(url, timeout=timeout)
        if r.status_code != 200:
            raise OSError("HTTP %s from %s"%(r.status_code,url))
        return r.json()
    finally:
        if r:
            try: r.close()
            except: pass

def fetch_participant(host, snr, port=80):
    path = "/next_racer.php?snr=%d" % int(snr)
    status, body = http_get(host, path, port=port, timeout_s=1.0)
    if status != 200:
        raise OSError("HTTP %d" % status)
    try:
        data = ujson.loads(body)
    except:
        data = {}
    if isinstance(data, dict) and data.get("error") == "not_found": return None
    if isinstance(data, dict) and "error" in data: raise RuntimeError("Server error: "+data["error"])
    return data

def fetch_participant_from_base(base, snr):
    scheme,host,port=_parse_host_port(base)
    if scheme=="https":
        # keep using urequests for simplicity here
        root = base if base.startswith("http") else ("https://" + base.strip("/"))
        root = root.split("://",1)[0] + "://" + root.split("://",1)[1].split("/",1)[0]
        url = root.rstrip("/") + "/next_racer.php?snr=%d" % int(snr)
        return fetch_json(url, 2)
    else:
        return fetch_participant(host, snr, port)

def print_participant(row):
    if not row: print("Participant not found."); return
    print("— #%s (%s %s)  order=%s last=%s next=%s  DOB=%s  email=%s" %
          (row.get("Startnummer"), row.get("Vorname",""), row.get("Name",""),
           row.get("race_order"), row.get("last_run"), row.get("next_run"),
           row.get("Geburtsdatum",""), row.get("E-mail","")))

# ----------------------------------------------------------------------
# DB normalizer
# ----------------------------------------------------------------------
def _normalize_read_payload(payload):
    if isinstance(payload, dict):
        if "data" in payload: return {"status":payload.get("status","success"),"data":payload["data"]}
        return {"status":"success","data":[payload]}
    elif isinstance(payload,list):
        return {"status":"success","data":payload}
    return {"status":"error","data":[]}

# ----------------------------------------------------------------------
# DB reader — SINGLE, SHORT STEP (cooperative)
# ----------------------------------------------------------------------
def read_from_db_step(last_pin_state_ref):
    """
    Executes ONE quick poll step and returns the new last_pin_state.
    Keeps each call short (<= ~200ms). Caller loops & checks stop flag.
    """
    led = Pin("LED", Pin.OUT)

    # 1) Build URL (fast)
    url = build_url(credentials.READ_URL)
    params = []
    # Add lightweight filters here if needed:
    # if race_status: params.append("race_status="+race_status)
    # if device_id:   params.append("device_id="+device_id)
    if params: url += ("?" if "?" not in url else "&") + "&".join(params)

    # 2) Fetch (timed)
    data = None
    try:
        res = urequests.get(url, timeout=1)  # keep it short
        raw = res.json()
        res.close()
        data = _normalize_read_payload(raw)
    except Exception as e:
        print("DB op error:", e)
        OLED.oled_text(["DB error", str(e)[:21]])
        time.sleep(0.2)  # brief backoff
        return last_pin_state_ref[0]

    # 3) Handle STOP pin edge briefly
    current_pin_state = INPUT_PIN_stop_race.value()
    if current_pin_state != last_pin_state_ref[0]:
        last_pin_state_ref[0] = current_pin_state
        if current_pin_state == 0:
            # rising event handling: finish last record quickly
            try:
                if data["data"]:
                    first = data["data"][0]
                    try:
                        # Instead of: ok = send_db_entry(1, 2, "race_started")
                        ok = send_db_entry(startnummer = cnt, run = 1, race_status = "race_started")
                        if ok:
                            print("Event logged")
                            OLED.oled_text(["START logged", f"{startnummer} {cnt}", "Waiting..."], 0)
                            cnt += 1
                            save_state(cnt)
                        else:
                            OLED.oled_text(["START log FAIL", f"{startnummer} {cnt}"], 0)

                    except NameError:
                        pass
                    try:
                        edit_record(first['id'], "race_status", "started_and_finished")  # provided elsewhere
                    except NameError:
                        pass
            except Exception as e:
                print("finish error:", e)
        return last_pin_state_ref[0]

    # 4) Light UI/logging (fast)
    try:
        if INPUT_PIN_stop_race.value() != 0:
            if data["data"]:
                for idx, r in enumerate(data["data"][:8]):  # cap prints
                    print("core1: Data", idx, ":", r)
        latest_txt = str(data["data"][0].get("value","-")) if data["data"] else "-"
        OLED.oled_text(["DB OK core1", "Stop pin:%d" % INPUT_PIN_stop_race.value(),
                   latest_txt[:21], get_timestamp().split()[1]])
        led.on(); time.sleep(0.03); led.off()
    except Exception as e:
        print("UI error:", e)

    # 5) short cooperative sleep to yield
    time.sleep(0.05)
    return last_pin_state_ref[0]

# ----------------------------------------------------------------------
# Core1 worker (cooperative stop)
# ----------------------------------------------------------------------
class Core1Manager:
    def __init__(self):
        self._run = False
        self._done = True
        self.thread_id = None

    def start(self):
        if self._run: return
        self._run = True
        self._done = False
        self.thread_id = _thread.start_new_thread(self._thread, ())

    def stop(self, timeout_s=3.0):
        if not self._run: return
        self._run = False
        t0 = time.ticks_ms()
        while not self._done and time.ticks_diff(time.ticks_ms(), t0) < int(timeout_s*1000):
            time.sleep(0.01)
        if not self._done:
            print("WARN: core1 did not exit before timeout")

    def _thread(self):
        last_pin_state_ref = [INPUT_PIN_stop_race.value()]  # mutable box
        try:
            while self._run:
                try:
                    last_pin_state_ref[0] = read_from_db_step(last_pin_state_ref)
                except Exception as e:
                    # keep iteration short even on errors
                    # print("core1 worker error:", e)
                    time.sleep(0.1)
                # extra tiny yield
                time.sleep(0.02)
        finally:
            self._done = True

# ----------------------------------------------------------------------
# Safe shutdown
# ----------------------------------------------------------------------
def safe_shutdown(core1, wlan=None, timers=None, sockets=None, cnt=None):
    """Order: stop core1 -> stop timers -> close sockets -> save -> WiFi off -> OLED off."""
    try:
        OLED.oled_text(["Shutting down…", ""], 0)
    except:
        pass

    # 1) Stop background thread(s)
    try: core1.stop()
    except Exception as e: print("WARN: stopping core1 failed:", e)

    # 2) Stop timers/IRQs
    if timers:
        for t in timers:
            try: t.deinit()
            except: pass
    else:
        # ensure our ms timer is off as well
        try: timer.deinit()
        except: pass

    # 3) Close any open sockets
    if sockets:
        for s in sockets:
            try: s.close()
            except: pass

    # 4) Persist state
    if cnt is not None:
        save_state(cnt)

    # 5) Disconnect Wi-Fi last (avoid EHOSTUNREACH spam while stopping)
    if wlan:
        try:
            wlan.disconnect()
            wlan.active(False)
        except:
            pass

    print("Shutdown in 3 seconds.")

# ----------------------------------------------------------------------
# Helpers used by Main
# ----------------------------------------------------------------------
def get_next_startnummer():
    try:
        # This line is problematic:
        # url = _full_url(credentials.SERVER_HOST + credentials.READ_URL) + "?limit=1"
        
        # Replace with:
        url = _full_url(credentials.READ_URL) + "?limit=1"
        
        res = urequests.get(url, timeout=3)
        raw = res.json(); res.close()
        data = _normalize_read_payload(raw)
        if data["status"] == "success" and data["data"]:
            last_value = str(data["data"][0].get("value",""))
            parts = last_value.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1]) + 1
        # fallback to first valid Startnummer
        return 1
    except Exception as e:
        print("get_next_startnummer error:", e)
        return 1


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    wlan = connect_wifi()
    ip = wlan.ifconfig()[0]
    OLED.oled_init()
    print("OLED object type:", type(OLED.oled))

    oled_writer = OLED.OLEDWriter(OLED.oled)
    oled_writer.draw_text(
        "WiFi connected\n" + str(ip)
    )
    time.sleep(1)

    # # show a readable header first (optional)
    # oled_writer = OLED.OLEDWriter(OLED.oled, max_cols=16, max_lines=8, line_height=8)
    # oled_writer.draw_text(f"WiFi connected\n{ip}")
    # time.sleep(1)
    # 
    # scroller = OLED.OLEDScroller(
    #     OLED.oled, oled_lock=OLED.oled_lock,
    #     interval_ms=800,   # scroll every 1.5s
    #     loop=True,          # allow looping
    #     max_loops=1,        # stop after 3 full scroll cycles
    #     max_cols=16,        # 16 chars per line
    #     max_lines=8,        # 8 lines total
    #     line_height=8       # font height
    # )
    # 
    # # now load all the text to be auto-wrapped + auto-scrolled
    # scroller.set_text([
    #     "IP-Adresse: ",     # separate line
    #     str(ip),            # separate line
    #     "Syncing time...",  # separate line
    #     # long paragraph that will wrap to 16 chars/line and scroll 
    #     ("This is a longer line, \nthat will automatically wrap to the next lines "
    #      "and then start to scroll up every 800 ms until the end, then loop.")
    # ])
    # 
    # while not scroller.done:
    #     scroller.tick()
    #     time.sleep(0.05)

    
    # # starting second core 
    # core1 = Core1Manager()
    # core1.start()

    # Startnummer
    cnt = get_next_startnummer()
    startnummer = "Startnummer:"
    print("Starting from Startnummer", cnt)
    OLED.oled_text(["Ready", f"{startnummer} {cnt}", "Waiting START..."], 0)

    # Example: single fetch wrapped
    try:
        participant = fetch_participant_from_base(_full_url("/"), 3)  # base only
        print_participant(participant)
    except Exception as e:
        print("Fetch failed:", e)

    print("Monitoring… (pull START pin to GND)")

    # keep references to cleanup targets
    timers = [timer]     # our ms ticker
    sockets = []         # append any sockets you open in main

    # In your main function, replace the main loop with:
    try:
        while True:
            state = INPUT_PIN_start_race.value()
            if state == 0:
                message = f"{startnummer} {cnt}"
                OLED.oled_text(["START detected", message, "logging..."], 0)
                
                print("START detected", message, "logging...")
                
                ok = False
                time.sleep(1)
                try:
                    # Fix this call:
                    ok = send_db_entry(cnt, 1, "race_started")
                except Exception as e:
                    print("send_db_entry error:", e)
    
                if ok:
                    print("Event logged")
                    OLED.oled_text(["START logged", message, "Waiting..."], 0)
                    cnt += 1
                    save_state(cnt)  # periodic persist
                else:
                    OLED.oled_text(["START log FAIL", message], 0)
                time.sleep(0.8)
            else:
                OLED.oled_text(["Idle", f"Next: {startnummer} {cnt}",
                           f"Start pin:{state}", get_timestamp().split()[1]], 0)
                time.sleep(0.15)

    except KeyboardInterrupt:
        print("KeyboardInterrupt: shutting down…")
        safe_shutdown(core1, wlan=wlan, timers=timers, sockets=sockets, cnt=cnt)
        
        # Clear/turn off OLED
        try:
            oled_writer = OLED.OLEDWriter(OLED.oled)
            oled_writer.draw_text(
                "Shutting down\n\nOLED display turning off in 3 secondes!"
            )
            time.sleep(3)
            OLED.oled_clear()
        except:
            pass
        
    except Exception as e:
        print("Unhandled error:", e)
        safe_shutdown(core1, wlan=wlan, timers=timers, sockets=sockets, cnt=cnt)
        raise
    else:
        safe_shutdown(core1, wlan=wlan, timers=timers, sockets=sockets, cnt=cnt)

if __name__ == "__main__":
    main()

