# === Race logger + SAFE SSD1306 OLED (no external ssd1306.py needed) ===
# Pico / Pico W / Pico2 W, MicroPython
# I2C OLED on GP4 (SDA) and GP5 (SCL), 128x64, address 0x3C (auto-detect)
# Uses a robust SSD1306 driver with small I2C chunks and 50 kHz clock.

import network, ntptime, time, urequests, json, _thread, machine, ubinascii, sys
from machine import Pin, Timer, I2C, SPI
import credentials  # SSID, PASSWORD, SERVER_HOST, API_KEY, INSERT, TIMEZONE_OFFSET
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

# === Server helpers ===
LOOKUP_PATH = "/participant_lookup_by_RFID.php"   # your PHP path
LOOKUP_TIMEOUT = 4                                # seconds

# --- tiny logger + error/OLED helpers ---
_LOG = []

def dbg(*parts):
    s = " ".join(str(x) for x in parts)
    try: print(s)
    except: pass
    try:
        _LOG.append((time.ticks_ms(), s))
        if len(_LOG) > 120: _LOG.pop(0)
    except: pass

# --- Locks / OLED ---
OLED_LOCK = _thread.allocate_lock()
_state_lock = _thread.allocate_lock()

# === UI queue: only main thread renders OLED ===
_ui_lock = _thread.allocate_lock()
_ui_queue = []   # list of (lines, hold_ms, when_ms)
_UI_DEFAULT_HOLD_MS = 1200


# --- server backoff after network errors ---
_SERVER_BACKOFF_UNTIL_MS = 0

# Suppress repeated server calls + UI spam for UIDs that were just denied
_uid_deny_until_ms = {}   # uid_hex -> ticks_ms when we may retry
DENY_SUPPRESS_MS   = 1500  # 1.5s; tweak to taste


def server_backing_off():
    return time.ticks_diff(_SERVER_BACKOFF_UNTIL_MS, time.ticks_ms()) > 0

def set_server_backoff(ms):
    global _SERVER_BACKOFF_UNTIL_MS
    _SERVER_BACKOFF_UNTIL_MS = time.ticks_ms() + int(ms)


def ui_post(lines, hold_ms=_UI_DEFAULT_HOLD_MS, replace=True):
    """Request a screen update from non-main threads. Main thread will render."""
    if not isinstance(lines, (list, tuple)):
        lines = [str(lines)]
    with _ui_lock:
        if replace:
            _ui_queue[:] = []
        _ui_queue.append(([str(x)[:21] for x in lines], int(hold_ms), time.ticks_ms()))

def ui_drain_once():
    """Main-thread: render at most one queued message."""
    item = None
    with _ui_lock:
        if _ui_queue:
            item = _ui_queue.pop(0)
    if not item:
        return False
    lines, hold_ms, _ = item
    with OLED_LOCK:
        OLED.oled_text(lines, 0)
    global _notice_until_ms
    _notice_until_ms = time.ticks_ms() + hold_ms
    return True

def show_error(where, e):
    try: sys.print_exception(e)
    except: pass
    msg = ("%s: %s" % (where, e))[:21]
    ui_post(["ERR @" + where, msg])

def show_recent_log(n=8):
    lines = [l for _, l in _LOG[-n:]] or ["(log empty)"]
    ui_post(["Last log:"] + lines[-7:])

# --- Shared state for RFID→Startnummer handover ---
_current_uid_hex = None
_current_startnummer = None     # int or None
_uid_to_start_cache = {}        # "AA:BB:CC:DD" -> int Startnummer
_is_locked = False
_locked_startnummer = None

# --- next_run cache per Startnummer ---
_snr_next_run_cache = {}        # int Startnummer -> int next_run

# --- Short notices on OLED ---
_notice_until_ms = 0

def show_notice(lines, hold_ms=1200):
    """Queue a short message to be rendered by main thread only."""
    ui_post(lines, hold_ms=hold_ms, replace=True)

def _get_locked_snr():
    """Return the currently locked Startnummer or None if unlocked."""
    with _state_lock:
        return int(_locked_startnummer) if _is_locked and _locked_startnummer is not None else None

def lock_selected(snr):
    global _is_locked, _locked_startnummer
    with _state_lock:
        _is_locked = True
        _locked_startnummer = int(snr) if snr is not None else None

def unlock_selected(reason=""):
    global _is_locked, _locked_startnummer, _current_startnummer
    with _state_lock:
        _is_locked = False
        _locked_startnummer = None
        _current_startnummer = None
    if reason: dbg("Unlocked:", reason)

# ----------------------------------------------------------------------
# Hardware definitions
# ----------------------------------------------------------------------
DEVICE_ID = ubinascii.hexlify(machine.unique_id()).decode()
DEVICE_NAME = "StartGate"

INPUT_PIN_start_race = Pin(2, Pin.IN, Pin.PULL_UP)
INPUT_PIN_stop_race  = Pin(3, Pin.IN, Pin.PULL_UP)
stop_pin = INPUT_PIN_stop_race
OUTPUT_PIN_time_synced = Pin(15, Pin.OUT)

# ----------------------------------------------------------------------
# light barrier / IRQ globals
# ----------------------------------------------------------------------
start_race_time = None
race_start_detected = False

def setup_irq():
    INPUT_PIN_start_race.irq(trigger=Pin.IRQ_FALLING, handler=start_race_isr)

def start_race_isr(pin):
    global start_race_time, race_start_detected
    start_race_time = get_timestamp()  # string "YYYY-MM-DD HH:MM:SS.mmm"
    race_start_detected = True
    pin.irq(handler=None)  # simple debounce; re-armed in main loop

# ----------------------------------------------------------------------
# RC522 wiring (SPI1) — known-good low-level
# ----------------------------------------------------------------------
RFID_SPI_ID   = 1
RFID_SCK_PIN  = 10
RFID_MOSI_PIN = 11
RFID_MISO_PIN = 12
RFID_CS_PIN   = 13   # RC522 "SDA" pin
RFID_RST_PIN  = 22

spi = SPI(
    RFID_SPI_ID,
    baudrate=50_000,
    polarity=0,
    phase=0,
    sck=Pin(RFID_SCK_PIN),
    mosi=Pin(RFID_MOSI_PIN),
    miso=Pin(RFID_MISO_PIN),
)
cs  = Pin(RFID_CS_PIN,  Pin.OUT, value=1)
rst = Pin(RFID_RST_PIN, Pin.OUT, value=1)

PCD_IDLE        = 0x00
PCD_CALCCRC     = 0x03
PCD_TRANSCEIVE  = 0x0C
PCD_SOFTRESET   = 0x0F

CommandReg      = 0x01
ComIEnReg       = 0x02
ComIrqReg       = 0x04
DivIrqReg       = 0x05
ErrorReg        = 0x06
FIFODataReg     = 0x09
FIFOLevelReg    = 0x0A
ControlReg      = 0x0C
BitFramingReg   = 0x0D
CollReg         = 0x0E
ModeReg         = 0x11
TxModeReg       = 0x12
RxModeReg       = 0x13
TxControlReg    = 0x14
TxASKReg        = 0x15
RFCfgReg        = 0x26
TModeReg        = 0x2A
TPrescalerReg   = 0x2B
TReloadRegH     = 0x2C
TReloadRegL     = 0x2D
CRCResultRegH   = 0x21
CRCResultRegL   = 0x22
VersionReg      = 0x37

PICC_CMD_REQA   = 0x26
PICC_SEL_CL1    = 0x93
PICC_SEL_CL2    = 0x95
PICC_SEL_CL3    = 0x97

MI_OK=0; MI_ERR=2
PRINT_RFID_DEBUG = False

def _wr(r,v): cs.value(0); spi.write(bytearray([(r<<1)&0x7E, v&0xFF])); cs.value(1)
def _rd(r): cs.value(0); spi.write(bytearray([((r<<1)&0x7E)|0x80])); v=spi.read(1)[0]; cs.value(1); return v
def _set(r,m): _wr(r, (_rd(r)|m)&0xFF)
def _clr(r,m): _wr(r,  _rd(r) & (~m & 0xFF))

def rfid_antenna_on():
    if (_rd(TxControlReg) & 0x03) != 0x03: _set(TxControlReg, 0x03)

def rfid_antenna_off():
    _clr(TxControlReg, 0x03)

def rfid_init():
    rst.value(0); time.sleep_ms(10); rst.value(1); time.sleep_ms(10)
    _wr(CommandReg, PCD_SOFTRESET); time.sleep_ms(50)
    _wr(TModeReg, 0x8D)
    _wr(TPrescalerReg, 0x3E)
    _wr(TReloadRegL, 30)
    _wr(TReloadRegH, 0)
    _wr(TxASKReg, 0x40)     # 100% ASK
    _wr(ModeReg, 0x3D)      # CRC preset 0x6363
    _wr(RFCfgReg, 0x70)     # max RX gain
    _wr(TxModeReg, 0x00)
    _wr(RxModeReg, 0x00)
    rfid_antenna_on()
    _wr(CollReg, 0x80)

def _to_card(send, wait_loops=12000, settle_us=0):
    _wr(ComIEnReg, 0x77 | 0x80)
    _clr(ComIrqReg, 0x80)
    _set(FIFOLevelReg, 0x80)
    _wr(CommandReg, PCD_IDLE)
    for b in send: _wr(FIFODataReg, b)
    if settle_us: time.sleep_us(settle_us)
    _wr(CommandReg, PCD_TRANSCEIVE)
    _set(BitFramingReg, 0x80)
    for _ in range(wait_loops):
        n = _rd(ComIrqReg)
        if (n & 0x01) or (n & 0x30): break
    _clr(BitFramingReg, 0x80)
    if (_rd(ErrorReg) & 0x1B) != 0: return MI_ERR, [], 0
    n = _rd(FIFOLevelReg)
    last = _rd(ControlReg) & 0x07
    bitlen = (n-1)*8 + last if last else n*8
    resp = [_rd(FIFODataReg) for _ in range(min(n, 16))]
    return MI_OK, resp, bitlen

def _calc_crc(data_bytes):
    _wr(CommandReg, PCD_IDLE); _set(FIFOLevelReg, 0x80)
    for b in data_bytes: _wr(FIFODataReg, b)
    _wr(CommandReg, PCD_CALCCRC)
    for _ in range(5000):
        if _rd(DivIrqReg) & 0x04: break
    return (_rd(CRCResultRegL), _rd(CRCResultRegH))

def reqa():
    _wr(BitFramingReg, 0x07); _wr(CollReg, 0x80)
    return _to_card([PICC_CMD_REQA])

def anticoll_level(sel_code):
    _wr(BitFramingReg, 0x00); _wr(CollReg, 0x80)
    return _to_card([sel_code, 0x20], wait_loops=8000, settle_us=20)

def anticoll_retry(sel_code, attempts=4):
    for _ in range(attempts):
        s, data, bits = anticoll_level(sel_code)
        if s == MI_OK and len(data) == 5 and ((data[0]^data[1]^data[2]^data[3]) == data[4]):
            return MI_OK, data
        time.sleep_ms(10)
    return MI_ERR, []

def select_with_crc(sel_code, uid4, tries=6):
    if len(uid4) != 4: return MI_ERR, 0
    for _ in range(tries):
        _wr(BitFramingReg, 0x00)
        bcc  = uid4[0]^uid4[1]^uid4[2]^uid4[3]
        core = [sel_code, 0x70] + list(uid4) + [bcc]
        crc_lsb, crc_msb = _calc_crc(core)
        frame = core + [crc_lsb, crc_msb]
        s, resp, bits = _to_card(frame, wait_loops=12000, settle_us=80)
        if s == MI_OK and len(resp) >= 1: return MI_OK, resp[0]
        if s == MI_OK and len(resp) == 0: time.sleep_ms(3); continue
        time.sleep_ms(3)
    return MI_ERR, 0

def get_uid_bytes():
    s, _, bits = reqa()
    if s != MI_OK or bits != 0x10: return None
    s, part = anticoll_retry(PICC_SEL_CL1)
    if s != MI_OK: return None
    uid = bytearray()
    if part[0] == 0x88:           # cascade -> 7 or 10 bytes
        uid += bytes(part[1:4])
        s, _ = select_with_crc(PICC_SEL_CL1, [0x88, uid[0], uid[1], uid[2]])
        if s != MI_OK: return None
        s, part2 = anticoll_retry(PICC_SEL_CL2)
        if s != MI_OK: return None
        if part2[0] == 0x88:      # 10-byte UID
            uid += bytes(part2[1:4])
            s, _ = select_with_crc(PICC_SEL_CL2, [0x88, part2[1], part2[2], part2[3]])
            if s != MI_OK: return None
            s, part3 = anticoll_retry(PICC_SEL_CL3)
            if s != MI_OK: return None
            uid += bytes(part3[0:4])
            s, _ = select_with_crc(PICC_SEL_CL3, [uid[-4], uid[-3], uid[-2], uid[-1]])
            if s != MI_OK: return None
        else:                      # 7-byte UID
            uid += bytes(part2[0:4])
            s, _ = select_with_crc(PICC_SEL_CL2, [uid[3], uid[4], uid[5], uid[6]])
            if s != MI_OK: return None
    else:                          # 4-byte UID
        uid += bytes(part[0:4])
        s, _ = select_with_crc(PICC_SEL_CL1, [uid[0], uid[1], uid[2], uid[3]])
        if s != MI_OK: return None
    return bytes(uid)

def _uid_hex(b): return ":".join("{:02X}".format(x) for x in b)

rfid_init()
print("RC522 VersionReg =", hex(_rd(VersionReg)), "(0x91/0x92 typical; 0x82 common on clones)")
print("RFID Reader initialized")
RFID_AVAILABLE = True

# ----------------------------------------------------------------------
# Wi-Fi + wall-clock
# ----------------------------------------------------------------------
def ensure_wifi():
    sta = network.WLAN(network.STA_IF)
    if not sta.active(): sta.active(True)
    if not sta.isconnected():
        sta.connect(credentials.SSID, credentials.PASSWORD)
        t0 = time.ticks_ms()
        while (not sta.isconnected()) and time.ticks_diff(time.ticks_ms(), t0) < 8000:
            time.sleep_ms(200)
    return sta.isconnected()

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

# --- High-resolution wall-clock ms with optional NTP sync ---
try:
    import utime as time
except ImportError:
    import time

try:
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
except AttributeError:
    def ticks_ms():
        try: return int(time.monotonic_ns() // 1_000_000)
        except AttributeError: return int(time.monotonic() * 1000)
    def ticks_diff(a, b): return a - b

try:
    import ntptime
except Exception:
    ntptime = None

_BASE_EPOCH_MS = None
_BASE_TICKS_MS = None

def _init_epoch_ms():
    global _BASE_EPOCH_MS, _BASE_TICKS_MS
    if ntptime is not None:
        try: ntptime.settime()
        except Exception: pass
    _BASE_EPOCH_MS = int(time.time()) * 1000
    _BASE_TICKS_MS = ticks_ms()

def epoch_ms():
    global _BASE_EPOCH_MS, _BASE_TICKS_MS
    if _BASE_EPOCH_MS is None: _init_epoch_ms()
    return _BASE_EPOCH_MS + int(ticks_diff(ticks_ms(), _BASE_TICKS_MS))

def sync_time(ntp_servers=None):
    if ntptime is None:
        ui_post(["NTP unsupported", "ntptime not found"])
        return False
    if ntp_servers is None:
        ntp_servers = ["pool.ntp.org", "time.google.com", "129.6.15.28"]
    for server in ntp_servers:
        try:
            ntptime.host = server
            ntptime.settime()
            _init_epoch_ms()
            OUTPUT_PIN_time_synced.value(1)
            print("Time synced:", server)
            ui_post(["NTP synced", server], hold_ms=1000)
            return True
        except Exception as e:
            print("NTP fail:", server, e)
            ui_post(["NTP fail", server, str(e)[:21]], hold_ms=600)
    ui_post(["NTP FAILED", "Check WiFi/DNS"])
    return False

def get_timestamp_ms_utc(): return epoch_ms()
def get_timestamp_ms_local(tz_hours=0): return epoch_ms() + int(tz_hours * 3600 * 1000)

def get_timestamp_local_string(tz_hours=0):
    ms = get_timestamp_ms_local(tz_hours)
    sec = ms // 1000; mmm = ms % 1000
    tm = time.gmtime(sec)
    return "%04d-%02d-%02d %02d:%02d:%02d.%03d" % (tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], mmm)

def get_timestamp():
    return get_timestamp_local_string(tz_hours=getattr(credentials, "TIMEZONE_OFFSET", 0))

# ----------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------
def _parse_host_port(base):
    s = str(base).strip(); scheme = "http"
    if s.startswith("http://"): s = s[7:]
    elif s.startswith("https://"): s = s[8:]; scheme = "https"
    hostpart = s.split("/", 1)[0]
    if ":" in hostpart:
        host, p = hostpart.split(":", 1)
        port = int(p) if p.isdigit() else (443 if scheme=="https" else 80)
    else:
        host, port = hostpart, (443 if scheme=="https" else 80)
    return scheme, host, port

def _full_url(path):
    base = credentials.SERVER_HOST.rstrip('/')
    if not base.startswith(('http://', 'https://')): base = 'http://' + base
    p = (path or "").strip()
    if not p.startswith("/"): p = "/" + p
    result = base + p
    print("URL built:", result)
    return result

def fetch_json(url, timeout=2):
    r = None
    try:
        r = urequests.get(url, timeout=timeout)
        if r.status_code != 200: raise OSError("HTTP %s from %s"%(r.status_code,url))
        return r.json()
    finally:
        if r:
            try: r.close()
            except: pass

def fetch_participant(host, snr, port=80):
    path = "/next_racer.php?snr=%d" % int(snr)
    ai = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket(); s.settimeout(1.0)
    try:
        s.connect(ai)
        req = "GET {} HTTP/1.0\r\nHost: {}\r\nConnection: close\r\n\r\n".format(path, host)
        s.send(req.encode())
        buf = b""
        while True:
            chunk = s.recv(1024)
            if not chunk: break
            buf += chunk
    finally:
        try: s.close()
        except: pass
    sep = buf.find(b"\r\n\r\n")
    status = 0
    try:
        line = buf[:sep].split(b"\r\n", 1)[0]
        status = int(line.split()[1])
    except: status = 0
    body = buf[sep+4:] if sep >= 0 else buf
    if status != 200: raise OSError("HTTP %d" % status)
    try: data = ujson.loads(body)
    except: data = {}
    if isinstance(data, dict) and data.get("error") == "not_found": return None
    if isinstance(data, dict) and "error" in data: raise RuntimeError("Server error: "+data["error"])
    return data

def fetch_participant_from_base(base, snr):
    scheme,host,port=_parse_host_port(base)
    if scheme=="https":
        root = base if base.startswith("http") else ("https://" + base.strip("/"))
        root = root.split("://",1)[0] + "://" + root.split("://",1)[1].split("/",1)[0]
        url = root.rstrip("/") + "/next_racer.php?snr=%d" % int(snr)
        return fetch_json(url, 2)
    else:
        return fetch_participant(host, snr, port)

def get_next_run_from_race_table(snr, limit=500):
    """Scan latest events and return next run number for SNr."""
    try:
        url = _full_url(credentials.READ_URL) + "?limit=%d" % int(limit)
        r = urequests.get(url, timeout=4)
        data = r.json(); r.close()
        rows = (data.get("data") or []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        max_run = 0
        for row in rows:
            try:
                if int(row.get("Startnummer")) == int(snr):
                    rno = int(row.get("run", 1))
                    if rno > max_run: max_run = rno
            except: pass
        return max_run + 1 if max_run > 0 else 1
    except Exception as e:
        print("next_run scan failed:", e)
        return 1

def send_db_entry(startnummer, run, race_status, timestamp):
    """POST a race row. Returns True/False."""
    print("send_db_entry: timestamp", str(timestamp))
    url = _full_url(credentials.INSERT)
    payload = {
        "Startnummer": int(startnummer),
        "run": int(run),
        "timestamp_ms": str(timestamp),
        "timezone_offset": getattr(credentials, "TIMEZONE_OFFSET", 0),
        "device_id": DEVICE_ID[:32],
        "device_name": DEVICE_NAME[:50],
        "race_status": str(race_status)[:50],
    }
    r = None
    try:
        headers = {"X-API-Key": getattr(credentials, "API_KEY", "")} if getattr(credentials, "API_KEY", "") else {}
        r = urequests.post(url, json=payload, headers=headers, timeout=5)
        print("HTTP", r.status_code, r.text)
        if r.status_code != 200: return False
        data = r.json()
        return isinstance(data, dict) and data.get("status") == "success"
    except Exception as e:
        print("POST error details:", e)
        try: sys.print_exception(e)
        except: pass
        return False
    finally:
        if r:
            try: r.close()
            except: pass

# === UID cooldown to avoid OLED spam / repeated calls when card is held ===
_last_uid_seen_ms = {}
RELOCK_COOLDOWN_MS = 1200  # adjust to taste

def should_ignore_uid(uid_full):
    now = time.ticks_ms()
    last = _last_uid_seen_ms.get(uid_full, 0)
    if time.ticks_diff(now, last) < RELOCK_COOLDOWN_MS:
        return True
    _last_uid_seen_ms[uid_full] = now
    return False

# ----------------------------------------------------------------------
# Lookup Startnummer by RFID UID (first 4 bytes, display order)
# ----------------------------------------------------------------------
def uid_bytes_to_le4_hex(uid_bytes):
    b = bytes(uid_bytes)
    if len(b) < 4: return None
    return "{:02X}:{:02X}:{:02X}:{:02X}".format(b[0], b[1], b[2], b[3])

def lookup_startnummer_by_rfid(uid_hex):
    """
    Always query server for lock permission. Returns Startnummer or None.
    Refuses locking if racer is on track. Retries with backoff on timeouts.
    """
    if not uid_hex:
        return None

    # If we recently timed out, pause a bit to avoid hammering server
    if server_backing_off():
        ui_post(["Server busy", "retrying shortly"], hold_ms=800)
        return None

    try:
        ensure_wifi()
    except:
        pass

    enc_uid = uid_hex.upper().replace(":", "%3A")
    url = _full_url(LOOKUP_PATH) + "?rfid=" + enc_uid
    print("GET", url)

    timeouts = (2, 3, 5)  # seconds
    r = None
    for attempt, to_s in enumerate(timeouts, 1):
        try:
            r = urequests.get(url, timeout=to_s)
            if r.status_code != 200:
                txt = None
                try: txt = r.text
                except: pass
                print("LOOKUP HTTP", r.status_code, (txt[:200] if txt else ""))
                ui_post(["HTTP %d" % r.status_code, "RFID " + uid_hex], hold_ms=1200)
                return None

            # Parse robustly (strip BOM/whitespace)
            try:
                raw = r.text or ""
                clean = raw.lstrip("\ufeff").strip()
                data = ujson.loads(clean)
            except Exception as je:
                print("JSON parse error:", je)
                print("RAW <<", (raw[:200] if raw else ""), ">>")
                ui_post(["JSON parse error", "see console"], hold_ms=1200)
                return None

            if not isinstance(data, dict) or data.get("status") not in ("ok", "success"):
                print("LOOKUP bad envelope:", (clean[:200] if 'clean' in locals() else ""))
                return None

            payload = data.get("data") or {}
            participant = payload.get("participant")
            allowed = bool(payload.get("allowed_to_lock", False))
            ontrk   = bool(payload.get("on_track", False))
            cur_run = payload.get("current_run", None)

            if not participant:
                ui_post(["RFID unknown", uid_hex], hold_ms=1200)
                return None

            if not allowed:
                line2 = "on track" if ontrk else "not allowed"
                line3 = ("Run %s" % cur_run) if cur_run is not None else ""
                ui_post(["LOCK REFUSED", line2, line3], hold_ms=1200)
                dbg("LOCK REFUSED for", uid_hex, "on_track=", ontrk, "run=", cur_run)
                # NEW: suppress further lookups for this UID for ~1.5s
                try:
                    _uid_deny_until_ms[uid_hex] = time.ticks_ms() + DENY_SUPPRESS_MS
                except:
                    pass
                return None


            # Approved – return Startnummer
            try:
                snr = int(participant.get("Startnummer"))
            except:
                snr = None
            if snr is None:
                return None

            _uid_to_start_cache[uid_hex] = snr
            return snr

        except OSError as e:
            # Common timeout errno on Pico: 110 (ETIMEDOUT)
            print("LOOKUP error (attempt %d/%d, %ss):" % (attempt, len(timeouts), to_s), e)
            if attempt == len(timeouts):
                # Final failure — back off a bit and inform UI
                set_server_backoff(3000)  # 3s global backoff
                ui_post(["Server timeout", "Try again"], hold_ms=1200)
                return None
            # small delay before next retry
            time.sleep_ms(120)

        finally:
            if r:
                try: r.close()
                except: pass
            r = None


# ----------------------------------------------------------------------
# Big-digit UI
# ----------------------------------------------------------------------
def _seg_draw(oled, x, y, s, on):
    seg_w = 3*s; seg_l = 20*s; seg_v = 14*s
    def H(x0, y0): oled.fill_rect(x0, y0, seg_l, seg_w, on)
    def V(x0, y0): oled.fill_rect(x0, y0, seg_w, seg_v, on)
    a = lambda: H(x + seg_w,            y + 0)
    g = lambda: H(x + seg_w,            y + seg_v + seg_w)
    d = lambda: H(x + seg_w,            y + 2*seg_v + 2*seg_w)
    f = lambda: V(x + 0,                y + seg_w)
    b = lambda: V(x + seg_l + seg_w,    y + seg_w)
    e = lambda: V(x + 0,                y + seg_v + 2*seg_w)
    c = lambda: V(x + seg_l + seg_w,    y + seg_v + 2*seg_w)
    return a,b,c,d,e,f,g, seg_w, seg_l, seg_v

_SEG_MAP = {
    '0': ('a','b','c','d','e','f'),
    '1': ('b','c'),
    '2': ('a','b','g','e','d'),
    '3': ('a','b','g','c','d'),
    '4': ('f','g','b','c'),
    '5': ('a','f','g','c','d'),
    '6': ('a','f','g','e','c','d'),
    '7': ('a','b','c'),
    '8': ('a','b','c','d','e','f','g'),
    '9': ('a','b','c','d','f','g'),
}

def draw_big_digit(oled, x, y, s, ch, color=1, clear_box=True):
    a,b,c,d,e,f,g, seg_w, seg_l, seg_v = _seg_draw(oled, x, y, s, color)
    box_w = seg_l + 2*seg_w
    box_h = 2*seg_v + 3*seg_w
    if clear_box: oled.fill_rect(x, y, box_w, box_h, 0)
    segs = _SEG_MAP.get(ch)
    if not segs: return box_w
    for name in segs:
        if name == 'a': a()
        elif name == 'b': b()
        elif name == 'c': c()
        elif name == 'd': d()
        elif name == 'e': e()
        elif name == 'f': f()
        elif name == 'g': g()
    return box_w

def choose_scale_for(value, line=3, spacing=4):
    txt = str(value); avail_h = 64 - line*8
    for s in (2, 1):
        seg_w, seg_l, seg_v = 3*s, 20*s, 14*s
        height = 2*seg_v + 3*seg_w
        if height > avail_h: continue
        box_w = seg_l + 2*seg_w
        total_w = len(txt)*box_w + max(0, len(txt)-1)*spacing
        if total_w <= 128: return s
    return 1

def render_startnummer_big(oled, value, line=3, scale=2, spacing=4):
    try: txt = str(int(value))
    except: txt = str(value)
    y = line * 8
    seg_w = 3*scale; seg_l = 20*scale; box_w = seg_l + 2*seg_w
    total = len(txt)*box_w + max(0, len(txt)-1)*spacing
    x = max(0, (128 - total)//2)
    oled.fill_rect(0, y, 128, 64 - y, 0)
    for ch in txt:
        draw_big_digit(oled, x, y, scale, ch, 1, clear_box=False)
        x += box_w + spacing
    oled.show()

def draw_status_unlocked():
    with OLED_LOCK:
        OLED.oled_text(["Startnummer: --", "Timestamp", get_timestamp().split()[1]])
        render_startnummer_big(OLED.oled, "--", line=3, scale=1)

def draw_status_locked(sn, run_no=1):
    with OLED_LOCK:
        OLED.oled_text([f"LOCKED Startnummer: {sn}", f"Run {run_no}", get_timestamp().split()[1]])
        s = choose_scale_for(sn, line=3)
        render_startnummer_big(OLED.oled, sn, line=3, scale=s)

def draw_status_and_big(_unused, _cnt):
    """Compat wrapper so older calls still work."""
    sn = _get_locked_snr()
    if sn is None: draw_status_unlocked()
    else: draw_status_locked(sn, _snr_next_run_cache.get(int(sn), 1))

# ----------------------------------------------------------------------
# Core1 worker (RFID polling)
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
        while (not self._done) and time.ticks_diff(time.ticks_ms(), t0) < int(timeout_s*1000):
            time.sleep(0.01)
        if not self._done: print("WARN: core1 did not exit before timeout")

    def _thread(self):
        last_uid_full = None
        try:
            while self._run:
                try:
                    uid_bytes = get_uid_bytes()
                    if uid_bytes:
                        uid_full = _uid_hex(uid_bytes)
                        # ignore if card is still hovering
                        if should_ignore_uid(uid_full):
                            time.sleep_ms(30)
                            continue
                        uid_le4  = uid_bytes_to_le4_hex(uid_bytes)
                        with _state_lock:
                            locked_now = _is_locked
                        if uid_full != last_uid_full:
                            last_uid_full = uid_full
                            if locked_now:
                                continue  # ignore new cards while locked
                            now = time.ticks_ms()
                            if time.ticks_diff(_uid_deny_until_ms.get(uid_le4 or "", 0), now) > 0:
                                # recently denied; don't query server again yet
                                time.sleep_ms(30)
                                continue
                            snr = lookup_startnummer_by_rfid(uid_le4)
                            with _state_lock:
                                global _current_uid_hex, _current_startnummer
                                _current_uid_hex = uid_le4
                                _current_startnummer = snr
                            if snr is not None:
                                lock_selected(snr)
                                print("RFID selected + LOCKED:", uid_le4, "→", snr)
                                try:
                                    int_snr = int(snr)
                                    if _snr_next_run_cache.get(int_snr) is None:
                                        nr = None
                                        try:
                                            if 'get_next_run_from_race_table' in globals() and callable(get_next_run_from_race_table):
                                                nr = int(get_next_run_from_race_table(int_snr))
                                        except Exception as _e:
                                            print("next_run lookup err:", _e)
                                        if nr is None: nr = 1
                                        _snr_next_run_cache[int_snr] = int(nr)
                                        print("next_run cached for SNr %d → %d" % (int_snr, int(nr)))
                                except Exception as e:
                                    print("next_run cache seed err:", e)
                    else:
                        last_uid_full = None
                except Exception as e:
                    print("Core1 loop err:", e)
                    time.sleep(0.1)
        finally:
            self._done = True

# ----------------------------------------------------------------------
# Safe shutdown + simple state file (optional)
# ----------------------------------------------------------------------
STATE_FILE = "race_state.json"
timer = Timer()  # global timer (deinit on shutdown)

def save_state(cnt):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(ujson.dumps({"cnt": cnt}))
        if hasattr(os, "sync"): os.sync()
    except Exception as e:
        print("WARN: could not save state:", e)

def safe_shutdown(core1, wlan=None, timers=None, sockets=None, cnt=None):
    ui_post(["Shutting down…", ""], hold_ms=500)
    try: core1.stop()
    except Exception as e: print("WARN: stopping core1 failed:", e)
    if timers:
        for t in timers:
            try: t.deinit()
            except: pass
    else:
        try: timer.deinit()
        except: pass
    if sockets:
        for s in sockets:
            try: s.close()
            except: pass
    if cnt is not None: save_state(cnt)
    if wlan:
        try:
            wlan.disconnect()
            wlan.active(False)
        except: pass
    print("Shutdown in 3 seconds.")

# ----------------------------------------------------------------------
# Offline outbox (queue failed POSTs)
# ----------------------------------------------------------------------
_outbox = []

def outbox_append(payload):
    try:
        _outbox.append(payload)
        dbg("OUTBOX queued:", payload)
    except: pass

def outbox_flush():
    if not _outbox: return
    dbg("OUTBOX flush try:", len(_outbox))
    still = []
    for p in list(_outbox):
        try:
            ok = send_db_entry(
                startnummer=p["Startnummer"],
                run=p["run"],
                race_status=p["race_status"],
                timestamp=p["timestamp_ms"]
            )
            if not ok: still.append(p)
        except Exception:
            still.append(p)
    _outbox[:] = still

# ----------------------------------------------------------------------
# Duplicate protection
# ----------------------------------------------------------------------
MIN_START_INTERVAL_MS = 800   # tweak as needed
_last_sn_start_ms = {}        # snr -> last start ticks

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    global race_start_detected, start_race_time, wlan

    # ----- Wi-Fi + time + OLED boot -----
    wlan = connect_wifi()
    ip = wlan.ifconfig()[0]

    print("Timezone offset:", getattr(credentials, "TIMEZONE_OFFSET", 0), " hours.")
    try:
        sync_time()
    except Exception:
        print("WARNING: NTP sync failed — timestamps may be wrong.")

    OLED.oled_init()
    print("OLED object type:", type(OLED.oled))
    try:
        with OLED_LOCK:
            ow = OLED.OLEDWriter(OLED.oled)
            ow.draw_text("WiFi connected\n" + str(ip))
        time.sleep(1)
    except Exception:
        pass

    # Arm the **START** IRQ (once)
    setup_irq()

    # Start RFID polling thread (once)
    core1 = Core1Manager()
    core1.start()

    # Initial idle screen
    cnt = 1
    draw_status_and_big(None, cnt)   # shows "--" unlocked
    print("Monitoring… (pull START pin to GND)")

    sockets = []
    last_ts_ms = time.ticks_ms()
    LOG_HOLD_MS  = 1200
    SHUT_HOLD_MS = 4000
    last_outbox  = time.ticks_ms()

    try:
        while True:
            # ---- STOP button handling ----
            if stop_pin.value() == 0:
                t0 = time.ticks_ms()
                logs_shown = False
                while stop_pin.value() == 0:
                    dt = time.ticks_diff(time.ticks_ms(), t0)
                    if (not logs_shown) and dt >= LOG_HOLD_MS and dt < SHUT_HOLD_MS:
                        show_recent_log(7)   # queue log to UI
                        logs_shown = True
                    if dt >= SHUT_HOLD_MS:
                        safe_shutdown(core1, wlan=wlan)  # never returns
                    time.sleep_ms(20)
                # released
                if logs_shown:
                    time.sleep_ms(800)
                    draw_status_and_big(None, cnt)
                else:
                    unlock_selected("STOP short-press")

            # ---- drain one UI message if any ----
            if ui_drain_once():
                # Just rendered a notice; delay idle repaint a bit
                last_ts_ms = time.ticks_ms()

            # ---- periodic: try to flush any offline events ----
            if time.ticks_diff(time.ticks_ms(), last_outbox) > 2500:
                outbox_flush()
                last_outbox = time.ticks_ms()

            # ---- periodic: idle repaint of time ----
            if time.ticks_diff(time.ticks_ms(), last_ts_ms) > 500:
                # Skip idle redraw if a notice is still active
                if time.ticks_diff(_notice_until_ms, time.ticks_ms()) <= 0:
                    draw_status_and_big(None, cnt)
                last_ts_ms = time.ticks_ms()

            # ---- Beam event -> log 'started' for the locked racer ----
            if race_start_detected:
                race_start_detected = False
                ts = start_race_time
                start_race_time = None

                sn = _get_locked_snr()
                if sn is None:
                    dbg("START ignored: no SNr locked @ %s" % ts)
                    show_notice(["START ignored", "No SNr locked", "Tap card to lock"], 1100)
                    setup_irq()
                    continue

                now = time.ticks_ms()
                last = _last_sn_start_ms.get(int(sn), 0)
                if time.ticks_diff(now, last) < MIN_START_INTERVAL_MS:
                    show_notice(["Ignored duplicate", "SNr %s too soon" % sn], 1000)
                    setup_irq()
                    continue
                _last_sn_start_ms[int(sn)] = now

                run_no = int(_snr_next_run_cache.get(int(sn), 1))
                with OLED_LOCK:
                    OLED.oled_text(["START detected",
                                    "SNr %s  Run %s" % (sn, run_no),
                                    "logging..."])
                    render_startnummer_big(OLED.oled, sn, line=3)

                ok = send_db_entry(sn, run_no, "started", ts)
                if not ok:
                    outbox_append({
                        "Startnummer": sn,
                        "run": run_no,
                        "race_status": "started",
                        "timestamp_ms": ts
                    })
                    with OLED_LOCK:
                        OLED.oled_text(["START queued (offline)",
                                        "SNr %s  Run %s" % (sn, run_no)])
                else:
                    with OLED_LOCK:
                        OLED.oled_text(["START logged",
                                        "SNr %s  Run %s" % (sn, run_no),
                                        "Ready"])
                    try:
                        _snr_next_run_cache[int(sn)] = run_no + 1
                    except Exception:
                        pass
                    unlock_selected("start logged")

                time.sleep_ms(300)
                setup_irq()

            time.sleep_ms(15)

    except KeyboardInterrupt:
        safe_shutdown(core1, wlan=wlan)
    except Exception as e:
        show_error("main", e)
        safe_shutdown(core1, wlan=wlan)

if __name__ == "__main__":
    main()
