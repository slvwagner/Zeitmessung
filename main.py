# === Race logger + SAFE SSD1306 OLED (no external ssd1306.py needed) ===
# Pico / Pico W, MicroPython
# I2C OLED on GP4 (SDA) and GP5 (SCL), 128x64, address 0x3C (auto-detect)
# Uses a robust SSD1306 driver with small I2C chunks and 50 kHz clock.

import network, ntptime, time, urequests, json, _thread, machine, ubinascii
from machine import Pin, Timer, I2C
import credentials  # must define: SSID, PASSWORD, SERVER_URL, EDIT_URL, READ_URL, TIMEZONE_OFFSET

import socket, ujson, framebuf

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
        CHUNK = 16
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

# ----------------------------------------------------------------------
# App state
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

OLED_WIDTH, OLED_HEIGHT = 128, 64
I2C_ID = 0
I2C_FREQ = 50_000
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
        print("I2C scan ->", [hex(d) for d in devices] if devices else "[]")
        if not devices:
            oled = None; return
        addr = 0x3C if 0x3C in devices else devices[0]
        print("Using OLED addr:", hex(addr))
        oled = SSD1306_I2C_SAFE(OLED_WIDTH, OLED_HEIGHT, i2c, addr=addr)
        _oled_force_text(["OLED ready", f"Addr {hex(addr)}", "I2C0 GP4/GP5"])
        time.sleep(2)
    except Exception as e:
        print("OLED init error:", e); oled = None

def _oled_force_text(lines):
    if not oled: return
    oled.fill(0); y = 0
    for s in lines[:8]:
        oled.text(str(s)[:21], 0, y); y += 8
    oled.show()

def _frames_equal(a, b):
    return a is not None and b is not None and a == b

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
            time.sleep(2); return True
        except OSError as e:
            print("NTP fail:", server, e)
            oled_text(["NTP fail", server, str(e)[:21]], 0); time.sleep(2)
    oled_text(["NTP FAILED", "Check WiFi/DNS"], 0); return False

# ----------------------------------------------------------------------
# HTTP helpers
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

def http_get_json(host, path, port=80, timeout=6):
    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket(); s.settimeout(timeout); s.connect(addr)
    try:
        req = "GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n"%(path,host)
        s.send(req.encode())
        chunks=[]; 
        while True:
            d=s.recv(1024)
            if not d: break
            chunks.append(d)
        raw=b"".join(chunks)
    finally: s.close()
    header,body=raw.split(b"\r\n\r\n",1)
    first_line=header.split(b"\r\n",1)[0]
    if not (first_line.startswith(b"HTTP/1.1 200") or first_line.startswith(b"HTTP/1.0 200")):
        try: return ujson.loads(body)
        except: raise RuntimeError("HTTP not OK: "+first_line.decode())
    return ujson.loads(body)

def fetch_participant(host, snr, port=80):
    path = "/next_racer.php?snr=%d"%int(snr)
    data = http_get_json(host, path, port=port)
    if isinstance(data,dict) and data.get("error")=="not_found": return None
    if isinstance(data,dict) and "error" in data: raise RuntimeError("Server error: "+data["error"])
    return data

def fetch_participant_from_base(base, snr):
    scheme,host,port=_parse_host_port(base)
    if scheme=="https":
        root=base if base.startswith("http") else "https://"+base.strip("/")
        parts=root.split("://",1); host_only=parts[1].split("/",1)[0]
        root=parts[0]+"://"+host_only
        url=root.rstrip("/")+"/next_racer.php?snr=%d"%int(snr)
        return fetch_json(url,2)
    else: return fetch_participant(host,snr,port)

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
# DB reader
# ----------------------------------------------------------------------
def read_from_db(race_status=None, device_id=None):
    led=Pin("LED",Pin.OUT); old=None
    last_pin_state=INPUT_PIN_stop_race.value()
    while True:
        try:
            url=build_url(credentials.READ_URL)
            params=[]
            if race_status: params.append("race_status="+race_status)
            if device_id: params.append("device_id="+device_id)
            if params: url+=("?" if "?" not in url else "&")+"&".join(params)
            res=urequests.get(url,timeout=2); raw=res.json(); res.close()
            data=_normalize_read_payload(raw)

            current_pin_state=INPUT_PIN_stop_race.value()
            if current_pin_state!=last_pin_state:
                last_pin_state=current_pin_state
                if current_pin_state==0:
                    if data["data"]:
                        first=data["data"][0]
                        send_data(first['value'],"finished")
                        edit_record(first['id'],"race_status","started_and_finished")
                    time.sleep(0.1); continue

            if INPUT_PIN_stop_race.value()!=0:
                if data["data"]:
                    for idx,r in enumerate(data["data"]):
                        print("core1: Data",idx,":",r)
                if data["status"]=="success":
                    latest=data["data"][0] if data["data"] else None
                    if old!=latest:
                        print("core1: New DB entry:",latest)
                        for _ in range(2): led.on();time.sleep(0.1);led.off();time.sleep(0.1)
                    else: led.on();time.sleep(0.05);led.off()
                    old=latest
                latest_txt=str(data["data"][0].get("value","-")) if data["data"] else "-"
                oled_text(["DB OK core1","Stop pin:%d"%INPUT_PIN_stop_race.value(),
                           latest_txt[:21],get_timestamp().split()[1]])
            time.sleep(0.12)
        except Exception as e:
            print("DB op error:",e)
            oled_text(["DB error",str(e)[:21]]); time.sleep(1)

def get_last_startnummer():
    try:
        url=build_url(credentials.READ_URL)+"?limit=1"
        res=urequests.get(url,timeout=5); raw=res.json(); res.close()
        data=_normalize_read_payload(raw)
        if data["status"]=="success" and data["data"]:
            last_value=str(data["data"][0].get("value",""))
            parts=last_value.split()
            if parts and parts[-1].isdigit(): return int(parts[-1])+1
        return 0
    except Exception as e:
        print("get_last_startnummer error:",e); return 0

# ----------------------------------------------------------------------
# Core1 thread
# ----------------------------------------------------------------------
class Core1Manager:
    def __init__(self): self.running=False; self.thread_id=None
    def start(self):
        if not self.running:
            self.running=True; self.thread_id=_thread.start_new_thread(self._thread_func,())
    def stop(self): self.running=False; time.sleep(2)
    def _thread_func(self):
        try: read_from_db()
        except Exception as e: print("Core1 thread error:",e); time.sleep(2)

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    wlan=connect_wifi(); ip=wlan.ifconfig()[0]
    oled_init(); _oled_force_text(["WiFi OK",ip,"Syncing time..."])
    if not sync_time(): _oled_force_text(["Time sync FAIL","Continuing..."])
    core1=Core1Manager(); core1.start()
    cnt=get_last_startnummer(); startnummer="Startnummer:"
    print("Starting from Startnummer",cnt)
    oled_text(["Ready",f"{startnummer} {cnt}","Waiting START..."],0)

    try:
        participant=fetch_participant_from_base(credentials.READ_URL,2)
        print_participant(participant)
    except Exception as e: print("Fetch failed:",e)

    print("Monitoring… (pull START pin to GND)")
    while True:
        state=INPUT_PIN_start_race.value()
        if state==0:
            message=f"{startnummer} {cnt}"
            oled_text(["START detected",message,"logging..."])
            if send_data(message,"race_started"):
                print("Event logged")
                oled_text(["START logged",message,"Waiting..."])
                cnt+=1
            else:
                oled_text(["START log FAIL",message])
            time.sleep(1)
        else:
            oled_text(["Idle",f"Next: {startnummer} {cnt}",
                       f"Start pin:{state}",get_timestamp().split()[1]])
            time.sleep(0.2)

if __name__=="__main__":
    try: main()
    except KeyboardInterrupt: print("Shutdown / Stopped.")
