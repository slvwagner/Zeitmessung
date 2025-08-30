# === Race logger + SAFE SSD1306 OLED (no external ssd1306.py needed) ===
# Pico / Pico W, MicroPython
# I2C OLED on GP4 (SDA) and GP5 (SCL), 128x64, address 0x3C (auto-detect)
# Uses a robust SSD1306 driver with small I2C chunks and 50 kHz clock.

import network, ntptime, time, urequests, json, _thread, machine, ubinascii
from machine import Pin, Timer, I2C
import credentials  # must define: SSID, PASSWORD, SERVER_URL, EDIT_URL, READ_URL, TIMEZONE_OFFSET

# ----------------------------------------------------------------------
# SAFE SSD1306 driver (I2C) — small write chunks, slow bus 
# ----------------------------------------------------------------------
import framebuf

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

    # low-level helpers
    def _cmd(self, *cmds):
        for c in cmds:
            # 0x80: Co=1, D/C#=0 (command)
            self.i2c.writeto(self.addr, bytes([0x80, c]))

    def _data(self, buf):
        # 0x40: Co=0, D/C#=1 (data); send in small chunks (16 bytes)
        CHUNK = 16
        i = 0
        b_len = len(buf)
        while i < b_len:
            n = CHUNK if (i + CHUNK) <= b_len else (b_len - i)
            self.i2c.writeto(self.addr, bytes([0x40]) + buf[i:i+n])
            i += n

    def _init_display(self):
        # horizontal addressing mode
        self._cmd(0xAE)                      # display off
        self._cmd(0xD5, 0x80)                # clock divide
        self._cmd(0xA8, self.height - 1)     # multiplex
        self._cmd(0xD3, 0x00)                # display offset
        self._cmd(0x40)                      # start line = 0
        self._cmd(0x8D, 0x14)                # charge pump on
        self._cmd(0x20, 0x00)                # memory mode: horizontal
        self._cmd(0xA1)                      # segment remap
        self._cmd(0xC8)                      # COM scan dec
        self._cmd(0xDA, 0x12)                # COM pins
        self._cmd(0x81, 0xCF)                # contrast
        self._cmd(0xD9, 0xF1)                # pre-charge
        self._cmd(0xDB, 0x40)                # VCOM detect
        self._cmd(0xA4)                      # resume
        self._cmd(0xA6)                      # normal (not inverted)
        self.fill(0)
        self.show()
        time.sleep_ms(10)
        self._cmd(0xAF)                      # display on

    def show(self):
        # set full-window then stream buffer
        self._cmd(0x21, 0, self.width - 1)   # column range
        self._cmd(0x22, 0, self.pages - 1)   # page range
        self._data(self.buffer)

class SSD1306_I2C_SAFE(SSD1306_SLOW):
    pass

# ----------------------------------------------------------------------
# App configuration / state
# ----------------------------------------------------------------------
DEVICE_ID = ubinascii.hexlify(machine.unique_id()).decode()
DEVICE_NAME = "Start"

INPUT_PIN_start_race = Pin(2, Pin.IN, Pin.PULL_UP)
INPUT_PIN_stop_race  = Pin(3, Pin.IN, Pin.PULL_UP)
OUTPUT_PIN_time_synced = Pin(12, Pin.OUT)

ms_counter = 0
timer = Timer()

def update_ms(_):
    global ms_counter
    ms_counter = (ms_counter + 1) % 1000

# OLED globals + lock/rate-limit
OLED_WIDTH, OLED_HEIGHT = 128, 64
I2C_ID = 0                         # I2C0 -> GP4 SDA, GP5 SCL
I2C_FREQ = 50_000                  # slow & safe
i2c = None
oled = None
oled_lock = _thread.allocate_lock()
_last_oled_frame = None
_last_oled_ts = 0

def get_timestamp():
    seconds = time.time()
    adjusted = time.localtime(seconds + credentials.TIMEZONE_OFFSET * 3600)
    y, m, d, hh, mm, ss, _, _ = adjusted
    return f"{y}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d}.{ms_counter:03d}"

# ----------------------------------------------------------------------
# OLED helpers
# ----------------------------------------------------------------------
def oled_init():
    global i2c, oled
    try:
        i2c = I2C(I2C_ID, sda=Pin(4), scl=Pin(5), freq=I2C_FREQ)
        devices = i2c.scan()
        print("I2C scan ->", [hex(d) for d in devices] if devices else "[] (none)")
        if not devices:
            print("No I2C devices on GP4/GP5")
            oled = None
            return
        addr = 0x3C if 0x3C in devices else (0x3D if 0x3D in devices else devices[0])
        print("Using OLED addr:", hex(addr))
        oled = SSD1306_I2C_SAFE(OLED_WIDTH, OLED_HEIGHT, i2c, addr=addr)
        _oled_force_text(["OLED ready", f"Addr {hex(addr)}", "I2C0 GP4/GP5"])
        time.sleep(2)
    except Exception as e:
        print("OLED init error:", e)
        oled = None

def _oled_force_text(lines):
    """Immediate draw (no lock/rate-limit). Use only during init or when safe."""
    global oled
    if not oled:
        return
    oled.fill(0)
    y = 0
    for s in lines[:8]:
        oled.text(str(s)[:21], 0, y)
        y += 8
    oled.show()

def _frames_equal(a, b):
    if a is None or b is None: return False
    if len(a) != len(b): return False
    for i in range(len(a)):
        if a[i] != b[i]: return False
    return True

def oled_text(lines, y0=0, min_interval_ms=120):
    """Thread-safe, rate-limited, duplicate-frame skipping draw."""
    global _last_oled_frame, _last_oled_ts
    if not oled:
        return
    now = time.ticks_ms()
    if time.ticks_diff(now, _last_oled_ts) < min_interval_ms:
        return
    frame = [str(s)[:21] for s in lines[:8]]
    if _frames_equal(frame, _last_oled_frame):
        return
    oled_lock.acquire()
    try:
        oled.fill(0)
        y = y0
        for s in frame:
            oled.text(s, 0, y)
            y += 8
        oled.show()
        _last_oled_frame = frame
        _last_oled_ts = now
    finally:
        oled_lock.release()

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
            ntptime.host = server
            ntptime.settime()
            timer.init(period=1, mode=Timer.PERIODIC, callback=update_ms)
            OUTPUT_PIN_time_synced.on()
            print("Time synced:", server)
            oled_text(["NTP synced", server, get_timestamp().split()[1]], min_interval_ms=0)
            time.sleep(2)
            return True
        except OSError as e:
            print("NTP fail:", server, e)
            oled_text(["NTP fail", server, str(e)[:21]], min_interval_ms=0)
            time.sleep(2)
    oled_text(["NTP FAILED", "Check WiFi/DNS"], min_interval_ms=0)
    return False

# ----------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------
def send_data(value, race_status):
    data = {
        "value": value,
        "timestamp": get_timestamp(),
        "device_id": DEVICE_ID,
        "device_name": DEVICE_NAME,
        "race_status": race_status
    }
    print("Data to send:", data)
    oled_text(["Sending...", race_status, str(value)])
    try:
        res = urequests.post(credentials.SERVER_URL, json=data, timeout=5)
        txt = res.text
        print("Server response:", txt)
        res.close()
        oled_text(["Sent OK", race_status, get_timestamp().split()[1]])
        return True
    except Exception as e:
        print("Send error:", e)
        oled_text(["Send ERROR", str(e)[:20]])
        return False

def edit_record(record_id, field, new_value):
    payload = {"id": record_id, "field": field, "new_value": new_value}
    print("Edit:", payload)
    oled_text(["Edit record", f"id {record_id}", f"{field}={new_value}"])
    try:
        json_data = json.dumps(payload)
        res = urequests.post(
            credentials.EDIT_URL,
            data=json_data,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        raw = res.text
        print("Edit resp raw:", raw)
        try:
            response = res.json()
        except ValueError as e:
            print("Edit JSON decode error:", e)
            oled_text(["Edit RESP ERR", str(e)[:20]])
            res.close()
            return False
        res.close()
        if response.get('status') == 'success':
            oled_text(["Edit OK", f"id {record_id}"])
            return True
        else:
            msg = response.get('message', 'No message')
            print("Edit fail:", msg)
            oled_text(["Edit FAIL", msg[:21]])
            return False
    except Exception as e:
        print("Edit NET error:", e)
        oled_text(["Edit NET ERR", str(e)[:21]])
        return False

# ----------------------------------------------------------------------
# DB reader (core1)
# ----------------------------------------------------------------------
def read_from_db(race_status=None, device_id=None):
    led = Pin("LED", Pin.OUT)
    old = None
    last_pin_state = INPUT_PIN_stop_race.value()

    while True:
        try:
            url = credentials.READ_URL
            params = []
            if race_status is not None:
                params.append(f"race_status={race_status}")
            if device_id is not None:
                params.append(f"device_id={device_id}")
            if params:
                url += "?" + "&".join(params)

            res = urequests.get(url, timeout=2)
            data = res.json()
            res.close()

            # immediate stop detection
            current_pin_state = INPUT_PIN_stop_race.value()
            if current_pin_state != last_pin_state:
                print("Stop pin ->", current_pin_state)
                last_pin_state = current_pin_state
                if current_pin_state == 0:
                    print("Race stop detected; finishing...")
                    if data.get("data"):
                        first_record = data["data"][0]
                        first_id = first_record['id']
                        first_value = first_record['value']
                        send_data(first_value, "finished")
                        edit_record(first_id, "race_status", "started_and_finished")
                    time.sleep(0.1)
                    continue

            if INPUT_PIN_stop_race.value() != 0:
                if len(data.get("data", [])) > 0:
                    print("******************")
                    for idx, record in enumerate(data["data"]):
                        print(f"core1: Data {idx}:", record)

                if data.get("status") == "success":
                    latest = data["data"][0] if data["data"] else None
                    if old != latest:
                        print("core1: New DB entry:", latest)
                        for _ in range(2):
                            led.on(); time.sleep(0.1); led.off(); time.sleep(0.1)
                    else:
                        led.on(); time.sleep(0.05); led.off()
                    old = latest

                latest_txt = "-"
                if data.get("data"):
                    try:
                        latest_txt = str(data["data"][0].get("value", "-"))
                    except Exception:
                        latest_txt = "-"
                oled_text([
                    "DB OK core1",
                    f"Stop pin:{INPUT_PIN_stop_race.value()}",
                    latest_txt[:21],
                    get_timestamp().split()[1]
                ])

            time.sleep(0.12)  # small delay keeps bus calm

        except Exception as e:
            print("DB op error:", e)
            oled_text(["DB error", str(e)[:21]])
            time.sleep(1)

def get_last_startnummer():
    try:
        url = credentials.READ_URL + "?limit=1"
        res = urequests.get(url, timeout=5)
        data = res.json()
        res.close()
        if data["status"] == "success" and len(data["data"]) > 0:
            last_value = data["data"][0]["value"]
            parts = last_value.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1]) + 1
        return 0
    except Exception as e:
        print("get_last_startnummer error:", e)
        return 0

# ----------------------------------------------------------------------
# Core1 thread manager
# ----------------------------------------------------------------------
class Core1Manager:
    def __init__(self):
        self.running = False
        self.thread_id = None
    def start(self):
        if not self.running:
            self.running = True
            self.thread_id = _thread.start_new_thread(self._thread_func, ())
    def stop(self):
        self.running = False
        time.sleep(2)
    def _thread_func(self):
        try:
            read_from_db()
        except Exception as e:
            print("Core1 thread error:", e)
            time.sleep(2)

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    # Wi-Fi
    wlan = connect_wifi()
    ip = wlan.ifconfig()[0]
    # OLED
    oled_init()
    _oled_force_text(["WiFi OK", ip, "Syncing time..."])
    # Time
    synced = sync_time()
    if not synced:
        _oled_force_text(["Time sync FAIL", "Continuing..."])
    # Start DB reader
    core1_manager = Core1Manager()
    core1_manager.start()
    # Counter
    cnt = get_last_startnummer()
    startnummer = "Startnummer:"
    print(f"Starting from Startnummer {cnt}")
    oled_text(["Ready", f"{startnummer} {cnt}", "Waiting START..."], min_interval_ms=0)

    print("Monitoring…  (pull START pin to GND)")
    while True:
        current_state = INPUT_PIN_start_race.value()
        if current_state == 0:
            print("Start pin LOW -> event!")
            message = f"{startnummer} {cnt}"
            oled_text(["START detected", message, "logging..."])
            if send_data(message, "race_started"):
                print("Event logged")
                oled_text(["START logged", message, "Waiting..."])
                cnt += 1
            else:
                print("Log failed")
                oled_text(["START log FAIL", message])
            time.sleep(1)
        else:
            oled_text([
                "Idle",
                f"Next: {startnummer} {cnt}",
                f"Start pin:{current_state}",
                get_timestamp().split()[1]
            ])
            time.sleep(0.2)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Shutdown / Stopped.")
