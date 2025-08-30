# === Race logger + SAFE SSD1306 OLED (no external ssd1306.py needed) ===
# Pico / Pico W / Pico2 W, MicroPython
# I2C OLED on GP4 (SDA) and GP5 (SCL), 128x64, address 0x3C (auto-detect)
# Uses a robust SSD1306 driver with small I2C chunks and 50 kHz clock.

import network, ntptime, time, urequests, json, _thread, machine, ubinascii
from machine import Pin, Timer, I2C
import credentials  # must define: SSID, PASSWORD, SERVER_URL, EDIT_URL, READ_URL, TIMEZONE_OFFSET

import usocket as socket
import ujson, framebuf, os

# ----------------------------------------------------------------------
# App state
# ----------------------------------------------------------------------
DEVICE_ID = ubinascii.hexlify(machine.unique_id()).decode()
DEVICE_NAME = "Start"

INPUT_PIN_start_race = Pin(2, Pin.IN, Pin.PULL_UP)
INPUT_PIN_stop_race  = Pin(3, Pin.IN, Pin.PULL_UP)
OUTPUT_PIN_time_synced = Pin(12, Pin.OUT)

ms_counter = 0
timer = Timer()  # main ms-ticker

def update_ms(_):
    global ms_counter
    ms_counter = (ms_counter + 1) % 1000

OLED_WIDTH, OLED_HEIGHT = 128, 64
I2C_ID = 0
I2C_FREQ = 50_000
i2c = None
oled = None
oled_lock = _thread.allocate_lock()
_last_oled_frame = None
_last_oled_ts = 0

# ----------------------------------------------------------------------
# SAFE SSD1306 driver
# ----------------------------------------------------------------------
class SSD1306_SLOW:
    def __init__(self, width, height, i2c, addr=0x3C, external_vcc=False):
        self.width = width
        self.height = height
        self.pages = self.height // 8
        self.i2c = i2c
        self.addr = addr
        self.external_vcc = external_vcc
        self.buffer = bytearray(self.width * self.pages)
        self.fb = framebuf.FrameBuffer(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self._init_display()

    # framebuf passthrough
    def fill(self, c): self.fb.fill(c)
    def pixel(self, x, y, c): self.fb.pixel(x, y, c)
    def hline(self, x, y, w, c): self.fb.hline(x, y, w, c)
    def vline(self, x, y, h, c): self.fb.vline(x, y, h, c)
    def line(self, x1, y1, x2, y2, c): self.fb.line(x1, y1, x2, y2, c)
    def rect(self, x, y, w, h, c): self.fb.rect(x, y, w, h, c)
    def fill_rect(self, x, y, w, h, c): self.fb.fill_rect(x, y, w, h, c)
    def text(self, s, x, y, c=1): self.fb.text(s, x, y, c)

    def _cmd(self, *cmds):
        for c in cmds:
            self.i2c.writeto(self.addr, bytes([0x80, c]))

    def _data(self, buf):
        CHUNK = 64
        i = 0
        b_len = len(buf)
        while i < b_len:
            n = CHUNK if (i + CHUNK) <= b_len else (b_len - i)
            self.i2c.writeto(self.addr, bytes([0x40]) + buf[i:i+n])
            i += n

    def _init_display(self):
        self._cmd(0xAE, 0xD5, 0x80, 0xA8, self.height - 1, 0xD3, 0x00,
                  0x40, 0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12,
                  0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6)
        self.fill(0); self.show(); time.sleep_ms(10)
        self._cmd(0xAF)

    def show(self):
        self._cmd(0x21, 0, self.width - 1)
        self._cmd(0x22, 0, self.pages - 1)
        self._data(self.buffer)

class SSD1306_I2C_SAFE(SSD1306_SLOW):
    pass

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
# OLED helpers
# ----------------------------------------------------------------------
def oled_init():
    global i2c, oled
    try:
        i2c = I2C(I2C_ID, sda=Pin(4), scl=Pin(5), freq=I2C_FREQ)
        devices = i2c.scan()
        print("I2C scan ->", [hex(d) for d in devices] if devices else "[]")
        if not devices:
            oled = None; return
        addr = 0x3C if 0x3C in devices else devices[0]
        print("Using OLED addr:", hex(addr))
        oled = SSD1306_I2C_SAFE(OLED_WIDTH, OLED_HEIGHT, i2c, addr=addr)
        _oled_force_text(["OLED ready", f"Addr {hex(addr)}", "I2C0 GP4/GP5"])
        time.sleep(1.5)
    except Exception as e:
        print("OLED init error:", e); oled = None

def _oled_force_text(lines):
    if not oled: return
    oled.fill(0); y = 0
    for s in lines[:8]:
        oled.text(str(s)[:21], 0, y); y += 8
    oled.show()

def oled_clear():
    if not oled: return
    oled.fill(0); oled.show()

def _frames_equal(a, b): return a is not None and b is not None and a == b

def oled_text(lines, y0=0, min_interval_ms=120):
    global _last_oled_frame, _last_oled_ts
    if not oled: return
    now = time.ticks_ms()
    if time.ticks_diff(now, _last_oled_ts) < min_interval_ms: return
    frame = [str(s)[:21] for s in lines[:8]]
    if _frames_equal(frame, _last_oled_frame): return
    oled_lock.acquire()
    try:
        oled.fill(0); y = y0
        for s in frame:
            oled.text(s, 0, y); y += 8
        oled.show()
        _last_oled_frame, _last_oled_ts = frame, now
    finally:
        oled_lock.release()

class OLEDWriter:
    def __init__(self, oled, max_cols=21, max_lines=8, line_height=8):
        self.oled = oled
        self.max_cols = max_cols
        self.max_lines = max_lines
        self.line_height = line_height

    def draw_text(self, text, x=0, y=0):
        """
        Writes text with auto-wrapping to OLED.
        Supports \n newlines in text.
        """
        lines = []
        # split incoming text into words
        for rawline in text.split("\n"):
            line = ""
            for word in rawline.split(" "):
                if not line:
                    line = word
                elif len(line) + 1 + len(word) <= self.max_cols:
                    line += " " + word
                else:
                    lines.append(line)
                    line = word
            if line: lines.append(line)

        # render to OLED
        self.oled.fill(0)
        yy = y
        for idx, l in enumerate(lines[:self.max_lines]):
            self.oled.text(l[:self.max_cols], x, yy)
            yy += self.line_height
        self.oled.show()

# ---- Auto-wrapping & auto-scrolling text for SSD1306 ----
import time

# ---- Auto-wrapping & auto-scrolling text for SSD1306 ----
import time

# Thread save OLED text scroller
class OLEDScroller:
    def __init__(self, oled, oled_lock=None, max_cols=16, max_lines=8, line_height=8,
                 interval_ms=1500, loop=True, max_loops=None):
        """
        oled        : your SSD1306 object
        oled_lock   : optional _thread lock for safe access
        max_cols    : chars per line (16 for 128px with default font)
        max_lines   : lines per screen (8 for 64px height)
        line_height : pixels per line (8 for default font)
        interval_ms : scroll interval in milliseconds
        loop        : whether to loop when reaching end
        max_loops   : how many full scroll cycles before stopping (None = infinite)
        """
        self.oled = oled
        self.oled_lock = oled_lock
        self.max_cols = max_cols
        self.max_lines = max_lines
        self.line_height = line_height
        self.interval_ms = interval_ms
        self.loop = loop
        self.max_loops = max_loops

        self._lines = []
        self._offset = 0
        self._last_ts = time.ticks_ms()
        self._loops_done = 0
        self._done = False
        self._y0 = 0

    def set_text(self, text_or_list, y0=0):
        """
        Set new text (string or list of strings).
        Resets scroller state.
        """
        self._y0 = y0
        text = "\n".join(text_or_list) if isinstance(text_or_list, (list, tuple)) else str(text_or_list)
        self._lines = self._wrap_text(text)
        self._offset = 0
        self._last_ts = time.ticks_ms()
        self._loops_done = 0
        # If all lines fit on one screen, mark done immediately
        self._done = (len(self._lines) <= self.max_lines)
        self._draw()

    def tick(self):
        """
        Call this frequently (e.g., in your main loop).
        It will advance by one line every interval_ms.
        """
        if self._done:
            return
        if len(self._lines) <= self.max_lines:
            self._done = True
            return

        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_ts) >= self.interval_ms:
            self._last_ts = now
            if self._offset + self.max_lines < len(self._lines):
                # scroll one line
                self._offset += 1
            else:
                # reached bottom → completed one cycle
                self._loops_done += 1
                if self.max_loops is not None and self._loops_done >= self.max_loops:
                    self._done = True
                    return
                if self.loop:
                    self._offset = 0
            self._draw()

    @property
    def done(self):
        """True if scrolling is finished."""
        return self._done

    # ---- internal helpers ----
    def _wrap_text(self, text):
        """Wrap a string into lines of max_cols chars."""
        out = []
        for raw in text.split("\n"):
            while len(raw) > self.max_cols:
                out.append(raw[:self.max_cols])
                raw = raw[self.max_cols:]
            if raw:
                out.append(raw)
        if not out:
            out = [""]
        return out

    def _draw(self):
        """Draw current window of lines to OLED."""
        if not self.oled:
            return
        window = self._lines[self._offset:self._offset + self.max_lines]
        # pad window so the screen is always filled
        while len(window) < self.max_lines:
            window.append("")

        if self.oled_lock:
            self.oled_lock.acquire()
        try:
            self.oled.fill(0)
            y = self._y0
            for l in window:
                self.oled.text(l[:self.max_cols], 0, y)
                y += self.line_height
            self.oled.show()
        finally:
            if self.oled_lock:
                self.oled_lock.release()




def get_timestamp():
    seconds = time.time()
    adjusted = time.localtime(seconds + credentials.TIMEZONE_OFFSET * 3600)
    y, m, d, hh, mm, ss, _, _ = adjusted
    return f"{y}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d}.{ms_counter:03d}"

# ----------------------------------------------------------------------
# Wi-Fi / NTP
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

def sync_time():
    ntp_servers = ["pool.ntp.org", "time.google.com", "129.6.15.28"]
    for server in ntp_servers:
        try:
            ntptime.host = server; ntptime.settime()
            timer.init(period=1, mode=Timer.PERIODIC, callback=update_ms)
            OUTPUT_PIN_time_synced.on()
            print("Time synced:", server)
            oled_text(["NTP synced", server, get_timestamp().split()[1]], 0)
            time.sleep(1.5); return True
        except OSError as e:
            print("NTP fail:", server, e)
            oled_text(["NTP fail", server, str(e)[:21]], 0); time.sleep(1.0)
    oled_text(["NTP FAILED", "Check WiFi/DNS"], 0); return False

# ----------------------------------------------------------------------
# HTTP helpers (short, stoppable operations)
# ----------------------------------------------------------------------
def build_url(base):
    s = str(base).strip()
    if not s: raise ValueError("credentials.READ_URL empty")
    if s.startswith("http://") or s.startswith("https://"):
        if s.rstrip("/").endswith("/read.php"): return s
        return s.rstrip("/") + "/read.php"
    return "http://" + s.strip("/").rstrip("/") + "/read.php"

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
        oled_text(["DB error", str(e)[:21]])
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
                        send_data(first['value'], "finished")  # provided elsewhere
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
        oled_text(["DB OK core1", "Stop pin:%d" % INPUT_PIN_stop_race.value(),
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
        oled_text(["Shutting down…", ""], 0)
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

    # 6) Clear/turn off OLED
    try:
        oled_writer = OLEDWriter(oled)
        oled_writer.draw_text(
            "Systemshutdown\n"
            "Timers shutdown\nAll sockets closed\nWLAN disconnected"
        )
        time.sleep(1)
        oled_clear()
    except:
        pass

    print("Shutdown complete.")

# ----------------------------------------------------------------------
# Helpers used by Main
# ----------------------------------------------------------------------
def get_last_startnummer():
    try:
        url = build_url(credentials.READ_URL) + "?limit=1"
        res = urequests.get(url, timeout=2)
        raw = res.json(); res.close()
        data = _normalize_read_payload(raw)
        if data["status"]=="success" and data["data"]:
            last_value = str(data["data"][0].get("value",""))
            parts = last_value.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1]) + 1
        return 0
    except Exception as e:
        print("get_last_startnummer error:", e); return 0

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    wlan = connect_wifi()
    ip = wlan.ifconfig()[0]
    oled_init()
    
    oled_writer = OLEDWriter(oled)
    oled_writer.draw_text(
        "WiFi connected\n" + str(ip)
    )
    time.sleep(1)

    # show a readable header first (optional)
    oled_writer = OLEDWriter(oled, max_cols=16, max_lines=8, line_height=8)
    oled_writer.draw_text(f"WiFi connected\n{ip}")
    time.sleep(1)
    
    scroller = OLEDScroller(
        oled, oled_lock=oled_lock,
        interval_ms=1500,   # scroll every 1.5s
        loop=True,          # allow looping
        max_loops=2,        # stop after 3 full scroll cycles
        max_cols=16,        # 16 chars per line
        max_lines=8,        # 8 lines total
        line_height=8       # font height
    )
    
    # now load all the text to be auto-wrapped + auto-scrolled
    scroller.set_text([
        "WiFi OK",          # separate line
        str(ip),            # separate line
        "Syncing time...",  # separate line
        # long paragraph that will wrap to 16 chars/line and scroll
        ("This is a longer line that will automatically wrap to the next lines "
         "and then start to scroll up every 800 ms until the end, then loop.")
    ])

    while not scroller.done:
        scroller.tick()
        time.sleep(0.05)

    
    # starting second core 
    core1 = Core1Manager()
    core1.start()

    # restore last counter safely
    cnt = load_state(default_cnt=get_last_startnummer())
    startnummer = "Startnummer:"
    print("Starting from Startnummer", cnt)
    oled_text(["Ready", f"{startnummer} {cnt}", "Waiting START..."], 0)

    # Example: single fetch wrapped
    try:
        participant = fetch_participant_from_base(credentials.READ_URL, 2)
        print_participant(participant)
    except Exception as e:
        print("Fetch failed:", e)

    print("Monitoring… (pull START pin to GND)")

    # keep references to cleanup targets
    timers = [timer]     # our ms ticker
    sockets = []         # append any sockets you open in main

    try:
        while True:
            state = INPUT_PIN_start_race.value()
            if state == 0:
                message = f"{startnummer} {cnt}"
                oled_text(["START detected", message, "logging..."], 0)
                ok = False
                try:
                    ok = send_data(message, "race_started")  # defined elsewhere
                except NameError:
                    # optional: remove this if provided in your codebase
                    print("send_data not defined; skipping")
                    ok = True
                except Exception as e:
                    print("send_data error:", e)

                if ok:
                    print("Event logged")
                    oled_text(["START logged", message, "Waiting..."], 0)
                    cnt += 1
                    save_state(cnt)  # periodic persist
                else:
                    oled_text(["START log FAIL", message], 0)
                time.sleep(0.8)
            else:
                oled_text(["Idle", f"Next: {startnummer} {cnt}",
                           f"Start pin:{state}", get_timestamp().split()[1]], 0)
                time.sleep(0.15)

    except KeyboardInterrupt:
        print("KeyboardInterrupt: shutting down…")
        safe_shutdown(core1, wlan=wlan, timers=timers, sockets=sockets, cnt=cnt)
        
    except Exception as e:
        print("Unhandled error:", e)
        safe_shutdown(core1, wlan=wlan, timers=timers, sockets=sockets, cnt=cnt)
        raise
    else:
        safe_shutdown(core1, wlan=wlan, timers=timers, sockets=sockets, cnt=cnt)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Shutdown / Stopped.")
