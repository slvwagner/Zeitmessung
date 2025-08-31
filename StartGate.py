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
OUTPUT_PIN_time_synced = Pin(12, Pin.OUT)

# ----------------------------------------------------------------------
# light barrier, time measurment via IRQ - GLOBAL VARIABLES
# ----------------------------------------------------------------------
# Initialize these global variables at the module level
start_race_time = None
race_start_detected = False

# Add these imports at the top of your file
from machine import SPI, Pin

# RC522 Pin Configuration - adjust these based on your wiring
RFID_SPI_ID = 0
RFID_SCK_PIN = 6
RFID_MOSI_PIN = 7
RFID_MISO_PIN = 4
RFID_CS_PIN = 5
RFID_RST_PIN = 22

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
# Initialize SPI and RC522
# ----------------------------------------------------------------------
try:
    spi = SPI(RFID_SPI_ID, baudrate=100000, polarity=0, phase=0, sck=Pin(RFID_SCK_PIN), mosi=Pin(RFID_MOSI_PIN), miso=Pin(RFID_MISO_PIN))
    cs = Pin(RFID_CS_PIN, Pin.OUT)
    rst = Pin(RFID_RST_PIN, Pin.OUT)
    
    # RC522 register commands
    PCD_IDLE = 0x00
    PCD_AUTHENT = 0x0E
    PCD_RECEIVE = 0x08
    PCD_TRANSMIT = 0x04
    PCD_TRANSCEIVE = 0x0C
    PCD_RESETPHASE = 0x0F
    PCD_CALCCRC = 0x03
    
    # RC522 registers
    CommandReg = 0x01
    ComIEnReg = 0x02
    DivIEnReg = 0x03
    ComIrqReg = 0x04
    DivIrqReg = 0x05
    ErrorReg = 0x06
    Status1Reg = 0x07
    Status2Reg = 0x08
    FIFODataReg = 0x09
    FIFOLevelReg = 0x0A
    WaterLevelReg = 0x0B
    ControlReg = 0x0C
    BitFramingReg = 0x0D
    CollReg = 0x0E
    ModeReg = 0x11
    TxModeReg = 0x12
    RxModeReg = 0x13
    TxControlReg = 0x14
    TxASKReg = 0x15
    TxSelReg = 0x16
    RxSelReg = 0x17
    RxThresholdReg = 0x18
    DemodReg = 0x19
    MfTxReg = 0x1C
    MfRxReg = 0x1D
    SerialSpeedReg = 0x1F
    CRCResultReg = 0x21
    ModWidthReg = 0x24
    RFCfgReg = 0x26
    GsNReg = 0x27
    CWGsPReg = 0x28
    ModGsPReg = 0x29
    TModeReg = 0x2A
    TPrescalerReg = 0x2B
    TReloadRegH = 0x2C
    TReloadRegL = 0x2D
    TCounterValueRegH = 0x2E
    TCounterValueRegL = 0x2F
    TestSel1Reg = 0x31
    TestSel2Reg = 0x32
    TestPinEnReg = 0x33
    TestPinValueReg = 0x34
    TestBusReg = 0x35
    AutoTestReg = 0x36
    VersionReg = 0x37
    AnalogTestReg = 0x38
    TestDAC1Reg = 0x39
    TestDAC2Reg = 0x3A
    TestADCReg = 0x3B
    
    # Mifare commands
    PICC_CMD_REQA = 0x26
    PICC_CMD_ANTICOLL = 0x93
    PICC_CMD_SELECT = 0x93
    PICC_CMD_AUTHENT1A = 0x60
    PICC_CMD_AUTHENT1B = 0x61
    PICC_CMD_READ = 0x30
    PICC_CMD_WRITE = 0xA0
    PICC_CMD_DECREMENT = 0xC0
    PICC_CMD_INCREMENT = 0xC1
    PICC_CMD_RESTORE = 0xC2
    PICC_CMD_TRANSFER = 0xB0
    PICC_CMD_HALT = 0x50
    
    # Status codes
    MI_OK = 0
    MI_NOTAGERR = 1
    MI_ERR = 2
    
    # Initialize RC522
    def rfid_init():
        rst.value(0)
        time.sleep_ms(100)
        rst.value(1)
        time.sleep_ms(100)
        
        rfid_write_register(ModeReg, 0x3D)  # Define the 3Dh value as the soft reset command
        rfid_write_register(TPrescalerReg, 0xA9)  # Timer: TPrescaler*TreloadVal/6.78MHz = 25ms
        rfid_write_register(TReloadRegL, 0xE8)
        rfid_write_register(TReloadRegH, 0x03)
        rfid_write_register(TxASKReg, 0x40)  # 100%ASK
        rfid_write_register(ModeReg, 0x3D)  # CRC initial value 0x6363
        
        rfid_antenna_on()  # Enable antenna
    
    def rfid_write_register(addr, val):
        cs.value(0)
        spi.write(bytearray([(addr << 1) & 0x7E, val]))
        cs.value(1)
    
    def rfid_read_register(addr):
        cs.value(0)
        spi.write(bytearray([((addr << 1) & 0x7E) | 0x80, 0]))
        result = bytearray(1)
        spi.readinto(result)
        cs.value(1)
        return result[0]
    
    def rfid_set_bitmask(reg, mask):
        rfid_write_register(reg, rfid_read_register(reg) | mask)
    
    def rfid_clear_bitmask(reg, mask):
        rfid_write_register(reg, rfid_read_register(reg) & (~mask))
    
    def rfid_antenna_on():
        value = rfid_read_register(TxControlReg)
        if ~(value & 0x03):
            rfid_set_bitmask(TxControlReg, 0x03)
    
    def rfid_antenna_off():
        rfid_clear_bitmask(TxControlReg, 0x03)
    
    def rfid_to_card(command, send_data):
        back_data = []
        back_len = 0
        status = MI_ERR
        irq_en = 0x00
        wait_irq = 0x00
        last_bits = None
        n = 0
        
        if command == PCD_AUTHENT:
            irq_en = 0x12
            wait_irq = 0x10
        if command == PCD_TRANSCEIVE:
            irq_en = 0x77
            wait_irq = 0x30
        
        rfid_write_register(ComIEnReg, irq_en | 0x80)
        rfid_clear_bitmask(ComIrqReg, 0x80)
        rfid_set_bitmask(FIFOLevelReg, 0x80)
        
        rfid_write_register(CommandReg, PCD_IDLE)
        
        for i in range(len(send_data)):
            rfid_write_register(FIFODataReg, send_data[i])
        
        rfid_write_register(CommandReg, command)
        
        if command == PCD_TRANSCEIVE:
            rfid_set_bitmask(BitFramingReg, 0x80)
        
        i = 2000
        while True:
            n = rfid_read_register(ComIrqReg)
            i -= 1
            if ~((i != 0) and ~(n & 0x01) and ~(n & wait_irq)):
                break
        
        rfid_clear_bitmask(BitFramingReg, 0x80)
        
        if i != 0:
            if (rfid_read_register(ErrorReg) & 0x1B) == 0x00:
                status = MI_OK
                
                if n & irq_en & 0x01:
                    status = MI_NOTAGERR
                
                if command == PCD_TRANSCEIVE:
                    n = rfid_read_register(FIFOLevelReg)
                    last_bits = rfid_read_register(ControlReg) & 0x07
                    if last_bits != 0:
                        back_len = (n - 1) * 8 + last_bits
                    else:
                        back_len = n * 8
                    
                    if n == 0:
                        n = 1
                    if n > 16:
                        n = 16
                    
                    for i in range(n):
                        back_data.append(rfid_read_register(FIFODataReg))
            else:
                status = MI_ERR
        
        return status, back_data, back_len
    
    def rfid_request(req_mode):
        status = None
        back_bits = None
        tag_type = []
        
        rfid_write_register(BitFramingReg, 0x07)
        
        tag_type.append(req_mode)
        (status, back_data, back_bits) = rfid_to_card(PCD_TRANSCEIVE, tag_type)
        
        if (status != MI_OK) | (back_bits != 0x10):
            status = MI_ERR
        
        return status, back_bits
    
    def rfid_anticoll():
        ser_num = []
        ser_num_check = 0
        
        rfid_write_register(BitFramingReg, 0x00)
        
        ser_num.append(PICC_CMD_ANTICOLL)
        ser_num.append(0x20)
        
        (status, back_data, back_bits) = rfid_to_card(PCD_TRANSCEIVE, ser_num)
        
        if status == MI_OK:
            if len(back_data) == 5:
                for i in range(4):
                    ser_num_check = ser_num_check ^ back_data[i]
                if ser_num_check != back_data[4]:
                    status = MI_ERR
            else:
                status = MI_ERR
        
        return status, back_data
    
    def rfid_get_uid():
        (status, back_data) = rfid_anticoll()
        if status == MI_OK:
            uid = 0
            for i in range(4):
                uid = (uid << 8) | back_data[i]
            return uid
        return None
    
    # Initialize the RFID reader
    rfid_init()
    print("RFID Reader initialized")
    
except Exception as e:
    print("RFID initialization failed:", e)
    RFID_AVAILABLE = False
else:
    RFID_AVAILABLE = True

# ----------------------------------------------------------------------
# get RFID (Startnummer) from DB
# ----------------------------------------------------------------------
def read_RFID(last_pin_state_ref=None):
    """
    Read RFID card and return the UID as Startnummer.
    Returns None if no card is detected.
    """
    if not RFID_AVAILABLE:
        OLED.oled_text(["RFID not available", "Check connection"], 0)
        time.sleep(2)
        return None
    
    try:
        # Check if a card is present
        (status, back_bits) = rfid_request(PICC_CMD_REQA)
        if status == MI_OK:
            # Card detected, get UID
            uid = rfid_get_uid()
            if uid:
                print(f"RFID detected: {uid}")
                OLED.oled_text(["RFID detected", f"UID: {uid}", "Processing..."], 0)
                return uid
            else:
                OLED.oled_text(["RFID read error", "Try again"], 0)
                time.sleep(1)
        else:
            # No card detected
            pass
            
    except Exception as e:
        print("RFID read error:", e)
        OLED.oled_text(["RFID error", str(e)[:21]], 0)
        time.sleep(1)
    
    return None

# ----------------------------------------------------------------------
# light barrier, time measurment via IRQ
# ----------------------------------------------------------------------
start_race_time = None
race_start_detected = False

# Add this function to set up the IRQ
def setup_irq():
    # Configure interrupt on falling edge (when pin goes from HIGH to LOW)
    INPUT_PIN_start_race.irq(trigger=Pin.IRQ_FALLING, handler=start_race_isr)

# Add the interrupt service routine
def start_race_isr(pin):
    global start_race_time, race_start_detected
    # Get the precise time immediately when interrupt occurs
    start_race_time = get_timestamp()  # Use high-resolution time
    race_start_detected = True
    # Disable interrupt temporarily to prevent bounce
    pin.irq(handler=None)

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
    tz_offset = getattr(credentials, "TIMEZONE_OFFSET", 0)
    print(f"Timezone offset: {tz_offset} hours")
    
    ms_utc = epoch_ms()
    print(f"UTC ms: {ms_utc}")
    
    ms_local = ms_utc + (tz_offset * 3600 * 1000)
    print(f"Local ms: {ms_local}")
    
    sec = ms_local // 1000
    mmm = ms_local % 1000
    tm = time.localtime(sec)
    
    result = "%04d-%02d-%02d %02d:%02d:%02d.%03d" % (
        tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], mmm
    )
    print(f"Final timestamp: {result}")
    
    return result

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
            print(f"Server returned error: {r.status_code}")
            return False
        data = r.json()
        print(data)
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
    
    print("get_next_startnummer: ", int(parts[-1]) + int(1))

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    # Make sure to declare these as global in main function
    global start_race_time, race_start_detected
    
    wlan = connect_wifi()
    ip = wlan.ifconfig()[0]
    
    print("Timezone offset:" ,getattr(credentials, "TIMEZONE_OFFSET", 0), " hours.")
    
    # 🔧 Make sure the RTC is correct *before* any call to get_timestamp()/epoch_ms()
    if not sync_time():
        print("WARNING: NTP sync failed — timestamps will be wrong.")
    
    OLED.oled_init()
    print("OLED object type:", type(OLED.oled))

    oled_writer = OLED.OLEDWriter(OLED.oled)
    oled_writer.draw_text(
        "WiFi connected\n" + str(ip)
    )
    time.sleep(1)

    # Set up the interrupt
    setup_irq()
    
    # starting second core
    core1 = Core1Manager()
    core1.start()

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

    try:
        while True:
            # Check if race start was detected via IRQ
            if race_start_detected:
                measured_time = start_race_time
                print("\n--------------------\nIRQ Measured time for Startnummer", str(cnt),":", measured_time)
                
                message = f"{cnt}"
                OLED.oled_text(["START detected", message, "logging..."], 0)
                
                print("START detected via IRQ", message, "logging...")
                
                ok = False
                try:
                    ok = send_db_entry(
                        startnummer=cnt, 
                        run=1, 
                        race_status="race_started", 
                        timestamp=measured_time
                    )
                except Exception as e:
                    print("send_db_entry error:", e)
                
                if ok:
                    print("*********************\nEvent logged via IRQ")
                    OLED.oled_text(["START logged", message, "Waiting..."], 0)
                    cnt += 1
                    save_state(cnt)
                else:
                    OLED.oled_text(["START log FAIL", message], 0)
                
                # Reset flag and re-enable interrupt
                race_start_detected = False
                start_race_time = None
                setup_irq()  # Re-enable the interrupt
                
                # Small delay to avoid immediate re-trigger
                time.sleep(0.1)
            
            # Your normal display update
            else:
                OLED.oled_text([f"Startnummer: {cnt}", f"Timestamp", get_timestamp().split()[1]], 0)
                time.sleep(0.001)

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

