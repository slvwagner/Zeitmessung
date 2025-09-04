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
DEVICE_NAME = "StartGate"

INPUT_PIN_start_race = Pin(2, Pin.IN, Pin.PULL_UP)
INPUT_PIN_stop_race  = Pin(3, Pin.IN, Pin.PULL_UP)
OUTPUT_PIN_time_synced = Pin(15, Pin.OUT)   # moved to avoid SPI1 GP12

# ----------------------------------------------------------------------
# light barrier, time measurment via IRQ - GLOBAL VARIABLES
# ----------------------------------------------------------------------
start_race_time = None
race_start_detected = False

# ----------------------------------------------------------------------
# RC522 wiring (SPI1)  — verified working
# ----------------------------------------------------------------------
from machine import SPI, Pin

RFID_SPI_ID   = 1
RFID_SCK_PIN  = 10
RFID_MOSI_PIN = 11
RFID_MISO_PIN = 12
RFID_CS_PIN   = 13   # RC522 "SDA" pin
RFID_RST_PIN  = 22

# | RC522 pin | Pico2 W             | color code      |
# | --------- | ------------------- | --------------- |
# | SDA (CS)  | **GP13**            | dark green      |
# | SCK       | **GP10**            | red             |
# | MOSI      | **GP11**            | orange          |
# | MISO      | **GP12**            | yellow          |
# | IRQ       | (leave unconnected) |                 |
# | GND       | GND                 |                 |
# | RST       | **GP22**            | white           |
# | 3.3V      | 3V3                 |                 |

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
# === RC522 low-level (known-good) =====================================
# ----------------------------------------------------------------------
# Uses the exact command flow you just confirmed works (REQA -> ANTICOLL -> SELECT).
# SPI @ 50 kHz, CPOL=0, CPHA=0; accepts SAK or SAK+CRC_A; adds small settle delays.

# SPI & pins
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

# --- Registers / commands ---
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

# PICC
PICC_CMD_REQA   = 0x26
PICC_SEL_CL1    = 0x93
PICC_SEL_CL2    = 0x95
PICC_SEL_CL3    = 0x97

MI_OK=0; MI_ERR=2
PRINT_RFID_DEBUG = False  # set True if you want to see select retries

def _wr(r,v): cs.value(0); spi.write(bytearray([(r<<1)&0x7E, v&0xFF])); cs.value(1)
def _rd(r): cs.value(0); spi.write(bytearray([((r<<1)&0x7E)|0x80])); v=spi.read(1)[0]; cs.value(1); return v
def _set(r,m): _wr(r, (_rd(r)|m)&0xFF)
def _clr(r,m): _wr(r,  _rd(r) & (~m & 0xFF))

def rfid_antenna_on():
    if (_rd(TxControlReg) & 0x03) != 0x03:
        _set(TxControlReg, 0x03)

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
    _wr(TxModeReg, 0x00)    # CRC via FIFO only
    _wr(RxModeReg, 0x00)    # ISO14443A defaults
    rfid_antenna_on()
    _wr(CollReg, 0x80)      # clear collisions

def _to_card(send, wait_loops=12000, settle_us=0):
    _wr(ComIEnReg, 0x77 | 0x80)
    _clr(ComIrqReg, 0x80)
    _set(FIFOLevelReg, 0x80)     # flush FIFO
    _wr(CommandReg, PCD_IDLE)
    for b in send: _wr(FIFODataReg, b)
    if settle_us: time.sleep_us(settle_us)
    _wr(CommandReg, PCD_TRANSCEIVE)
    _set(BitFramingReg, 0x80)    # start send
    for _ in range(wait_loops):
        n = _rd(ComIrqReg)
        if (n & 0x01) or (n & 0x30): break
    _clr(BitFramingReg, 0x80)
    if (_rd(ErrorReg) & 0x1B) != 0:
        return MI_ERR, [], 0
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
    return (_rd(CRCResultRegL), _rd(CRCResultRegH))  # (lsb, msb)

def reqa():
    _wr(BitFramingReg, 0x07); _wr(CollReg, 0x80)
    return _to_card([PICC_CMD_REQA])

def anticoll_level(sel_code):
    _wr(BitFramingReg, 0x00); _wr(CollReg, 0x80)
    return _to_card([sel_code, 0x20], wait_loops=8000, settle_us=20)

def anticoll_retry(sel_code, attempts=4):
    for _ in range(attempts):
        s, data, bits = anticoll_level(sel_code)
        if s == MI_OK and len(data) == 5:
            if (data[0]^data[1]^data[2]^data[3]) == data[4]:  # BCC ok
                return MI_OK, data
        time.sleep_ms(10)
    return MI_ERR, []

def select_with_crc(sel_code, uid4, tries=6):
    if len(uid4) != 4: return MI_ERR, 0
    for t in range(tries):
        _wr(BitFramingReg, 0x00)  # full-byte framing
        bcc  = uid4[0]^uid4[1]^uid4[2]^uid4[3]
        core = [sel_code, 0x70] + list(uid4) + [bcc]
        crc_lsb, crc_msb = _calc_crc(core)
        frame = core + [crc_lsb, crc_msb]
        s, resp, bits = _to_card(frame, wait_loops=12000, settle_us=80)
        # Accept SAK (1B) or SAK+CRC_A (3B). Empty resp -> soft miss, retry quietly.
        if s == MI_OK and len(resp) >= 1:
            return MI_OK, resp[0]
        if s == MI_OK and len(resp) == 0:
            time.sleep_ms(3); continue
        if PRINT_RFID_DEBUG and len(resp) > 0:
            print("DBG: SELECT fail try", t+1, "resp=", resp, "bits=", bits)
        time.sleep_ms(3)
    return MI_ERR, 0

def get_uid_bytes():
    # Quick presence check
    s, _, bits = reqa()
    if s != MI_OK or bits != 0x10:
        return None

    # CL1
    s, part = anticoll_retry(PICC_SEL_CL1)
    if s != MI_OK:
        return None

    uid = bytearray()
    if part[0] == 0x88:           # cascade -> 7 or 10 bytes
        uid += bytes(part[1:4])
        s, _ = select_with_crc(PICC_SEL_CL1, [0x88, uid[0], uid[1], uid[2]])
        if s != MI_OK: return None
        s, part2 = anticoll_retry(PICC_SEL_CL2)
        if s != MI_OK: return None
        if part2[0] == 0x88:      # 10-byte UID (rare)
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

# Initialize the RFID reader
rfid_init()
print("RC522 VersionReg =", hex(_rd(VersionReg)), "(0x91/0x92 typical; 0x82 common on clones)")
print("RFID Reader initialized")

RFID_AVAILABLE = True  # if we got here without exception

# ----------------------------------------------------------------------
# get RFID (Startnummer) from DB — NOW returns UID hex string
# ----------------------------------------------------------------------
_last_uid_hex = None

def _oled_show(lines, rf_quiet=True):
    """Draw lines to OLED; optionally mute RF field during the write."""
    if rf_quiet:
        try: rfid_antenna_off()
        except: pass
    try:
        OLED.oled_text(lines, 0)
    except Exception as e:
        print("OLED draw err:", e)
    finally:
        if rf_quiet:
            try: rfid_antenna_on()
            except: pass

def read_RFID(last_pin_state_ref=None):
    """
    Poll for an RFID tag and, if present, show it and return the UID as HEX string.
    Returns None if no tag is detected.
    """
    global _last_uid_hex
    if not RFID_AVAILABLE:
        _oled_show(["RFID not available", "Check connection"], True)
        time.sleep(0.2)
        return None

    try:
        uid_bytes = get_uid_bytes()
        if uid_bytes:
            uid_hex = _uid_hex(uid_bytes)
            if uid_hex != _last_uid_hex:
                print("RFID UID:", uid_hex, f"({len(uid_bytes)} bytes)")
                _oled_show(["RFID detected", uid_hex], True)
                _last_uid_hex = uid_hex
            # Wait until tag is removed before reporting another
            # (Main loop keeps running; Core1 keeps polling)
            return uid_hex
        else:
            # No card or unstable read; optional tiny heartbeat could be added here
            return None
    except Exception as e:
        print("RFID read error:", e)
        _oled_show(["RFID error", str(e)[:21]], True)
        time.sleep(0.2)
        return None

# ----------------------------------------------------------------------
# light barrier, time measurment via IRQ
# ----------------------------------------------------------------------
start_race_time = None
race_start_detected = False

def setup_irq():
    # Configure interrupt on falling edge (when pin goes from HIGH to LOW)
    INPUT_PIN_start_race.irq(trigger=Pin.IRQ_FALLING, handler=start_race_isr)

def start_race_isr(pin):
    global start_race_time, race_start_detected
    start_race_time = get_timestamp()  # Use high-resolution time
    race_start_detected = True
    pin.irq(handler=None)  # Disable interrupt temporarily to prevent bounce

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
try:
    import utime as time
except ImportError:
    import time

try:
    from machine import Timer, Pin
    _HAVE_MACHINE = True
except Exception:
    Timer = None
    Pin = None
    _HAVE_MACHINE = False

try:
    import ntptime
except Exception:
    ntptime = None

try:
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
except AttributeError:
    def ticks_ms():
        try:
            return int(time.monotonic_ns() // 1_000_000)
        except AttributeError:
            return int(time.monotonic() * 1000)
    def ticks_diff(a, b):
        return a - b

_BASE_EPOCH_MS = None
_BASE_TICKS_MS = None

def _init_epoch_ms():
    global _BASE_EPOCH_MS, _BASE_TICKS_MS
    if ntptime is not None:
        try:
            ntptime.settime()
        except Exception:
            pass
    _BASE_EPOCH_MS = int(time.time()) * 1000  # seconds -> ms
    _BASE_TICKS_MS = ticks_ms()

def epoch_ms():
    global _BASE_EPOCH_MS, _BASE_TICKS_MS
    if _BASE_EPOCH_MS is None:
        _init_epoch_ms()
    return _BASE_EPOCH_MS + int(ticks_diff(ticks_ms(), _BASE_TICKS_MS))

def get_timestamp():
    tz_offset = getattr(credentials, "TIMEZONE_OFFSET", 0)
    ms_utc = epoch_ms()
    ms_local = ms_utc + (tz_offset * 3600 * 1000)
    sec = ms_local // 1000
    mmm = ms_local % 1000
    tm = time.localtime(sec)
    return "%04d-%02d-%02d %02d:%02d:%02d.%03d" % (tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], mmm)

def _maybe(fn_name, *args, **kwargs):
    g = globals().get(fn_name)
    if callable(g):
        try:
            return g(*args, **kwargs)
        except Exception:
            pass
    obj = globals().get(fn_name)
    if obj is not None:
        try:
            return getattr(obj, "on")()
        except Exception:
            pass
    return None

def sync_time(ntp_servers=None):
    if ntptime is None:
        _maybe("OLED.oled_text", ["NTP unsupported", "ntptime not found"], 0)
        return False
    if ntp_servers is None:
        ntp_servers = ["pool.ntp.org", "time.google.com", "129.6.15.28"]
    for server in ntp_servers:
        try:
            ntptime.host = server
            ntptime.settime()
            _init_epoch_ms()
            _maybe("OUTPUT_PIN_time_synced")
            print("Time synced:", server)
            _maybe("OLED.oled_text", ["NTP synced", server, get_timestamp().split()[1]], 0)
            try: time.sleep(1.5)
            except Exception: pass
            return True
        except Exception as e:
            print("NTP fail:", server, e)
            _maybe("OLED.oled_text", ["NTP fail", server, str(e)[:21]], 0)
            try: time.sleep(1.0)
            except Exception: pass
    _maybe("OLED.oled_text", ["NTP FAILED", "Check WiFi/DNS"], 0)
    return False

def get_timestamp_ms_utc():
    return epoch_ms()

def get_timestamp_ms_local(tz_hours=0):
    return epoch_ms() + int(tz_hours * 3600 * 1000)

def get_timestamp_local_string(tz_hours=0):
    ms = get_timestamp_ms_local(tz_hours)
    sec = ms // 1000
    mmm = ms % 1000
    tm = time.gmtime(sec)
    return "%04d-%02d-%02d %02d:%02d:%02d.%03d" % (tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], mmm)

def get_timestamp():
    return get_timestamp_local_string(tz_hours = getattr(credentials, "TIMEZONE_OFFSET"))

# ----------------------------------------------------------------------
# Non-blocking single-character input from USB serial
# ----------------------------------------------------------------------
if uselect:
    _poll = uselect.poll()
    _poll.register(sys.stdin, uselect.POLLIN)
else:
    _poll = None

def read_char_nonblocking():
    if _usb:
        if _usb.any():
            b = _usb.read(1)
            return b.decode() if b else None
        return None
    if _poll and _poll.poll(0):
        ch = sys.stdin.read(1)
        return ch
    return None

# ----------------------------------------------------------------------
# HTTP helpers (short, stoppable operations)
# ----------------------------------------------------------------------
def _full_url(path):
    base = credentials.SERVER_HOST.rstrip('/')
    if not base.startswith(('http://', 'https://')):
        base = 'http://' + base
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    result = base + p
    print("URL built:", result)
    return result

def send_db_entry(startnummer, run, race_status, timestamp):
    print("send_db_entry: timestamp", str(timestamp))
    url = _full_url(credentials.INSERT)
    payload = {
        "Startnummer": int(startnummer),
        "run": int(run),
        "timestamp_ms": str(timestamp),
        "timezone_offset": getattr(credentials, "TIMEZONE_OFFSET"),
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
            print("Server returned error:", r.status_code)
            return False
        data = r.json()
        print(data)
        return isinstance(data, dict) and data.get("status") == "success"
    except Exception as e:
        print("POST error details:", e)
        import sys
        sys.print_exception(e)
        return False
    finally:
        if r:
            try: r.close()
            except: pass

def build_url(base):
    s = str(base).strip()
    if not s: 
        raise ValueError("credentials.READ_URL empty")
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
    status = 0
    try:
        line = head.split(b"\r\n", 1)[0]
        status = int(line.split()[1])
    except:
        status = 0
    return status, body

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
                    last_pin_state_ref[0] = read_RFID(last_pin_state_ref)
                except Exception as e:
                    time.sleep(0.1)
                time.sleep(0.02)
        finally:
            self._done = True

# ----------------------------------------------------------------------
# Safe shutdown
# ----------------------------------------------------------------------
def safe_shutdown(core1, wlan=None, timers=None, sockets=None, cnt=None):
    try:
        OLED.oled_text(["Shutting down…", ""], 0)
    except:
        pass
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
    if cnt is not None:
        save_state(cnt)
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
        url = _full_url(credentials.READ_URL) + "?limit=1"
        res = urequests.get(url, timeout=3)
        raw = res.json(); res.close()
        data = _normalize_read_payload(raw)
        if data["status"] == "success" and data["data"]:
            last_value = str(data["data"][0].get("value",""))
            parts = last_value.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1]) + 1
        return 1
    except Exception as e:
        print("get_next_startnummer error:", e)
        return 1

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    global start_race_time, race_start_detected
    
    wlan = connect_wifi()
    ip = wlan.ifconfig()[0]
    
    print("Timezone offset:" ,getattr(credentials, "TIMEZONE_OFFSET", 0), " hours.")
    if not sync_time():
        print("WARNING: NTP sync failed — timestamps will be wrong.")
    
    OLED.oled_init()
    print("OLED object type:", type(OLED.oled))

    oled_writer = OLED.OLEDWriter(OLED.oled)
    oled_writer.draw_text("WiFi connected\n" + str(ip))
    time.sleep(1)

    setup_irq()
    
    core1 = Core1Manager()
    core1.start()

    cnt = get_next_startnummer()
    startnummer = "Startnummer:"
    print("Starting from Startnummer", cnt)
    OLED.oled_text(["Ready", f"{startnummer} {cnt}", "Waiting START..."], 0)

    try:
        participant = fetch_par_
