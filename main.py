# Script to measure time between events + OLED status display
# MicroPython (Pico/Pico W). OLED SSD1306 via I2C on GP4 (SDA) / GP5 (SCL).
# Backend endpoints in credentials.py (SSID, PASSWORD, SERVER_URL, EDIT_URL, READ_URL, TIMEZONE_OFFSET)

import network
import ntptime
import time
import urequests
import json
import _thread
import machine, ubinascii
from machine import Pin, Timer, I2C
import credentials  # <-- provide: SSID, PASSWORD, SERVER_URL, EDIT_URL, READ_URL, TIMEZONE_OFFSET

# --- Device identity ---
DEVICE_ID = ubinascii.hexlify(machine.unique_id()).decode()
DEVICE_NAME = "Start"

# --- IO Pins ---
INPUT_PIN_start_race = Pin(2, Pin.IN, Pin.PULL_UP)
INPUT_PIN_stop_race  = Pin(3, Pin.IN, Pin.PULL_UP)
OUTPUT_PIN_time_synced = Pin(12, Pin.OUT)

# --- Millisecond Counter ---
ms_counter = 0
timer = Timer()
def update_ms(_):
    global ms_counter
    ms_counter = (ms_counter + 1) % 1000

# --- OLED globals ---
OLED_WIDTH, OLED_HEIGHT = 128, 64
I2C_ID = 0  # I2C0 -> GP4 SDA, GP5 SCL
i2c = None
oled = None
SSD1306_I2C = None  # filled after we install/import the driver

def load_ssd1306_driver():
    """Try to import the SSD1306 driver and expose SSD1306_I2C globally."""
    global SSD1306_I2C
    try:
        from ssd1306 import SSD1306_I2C as _SSD1306_I2C
        SSD1306_I2C = _SSD1306_I2C
        print("ssd1306 driver loaded.")
        return True
    except ImportError:
        print("ssd1306 driver not found (yet).")
        return False

def install_ssd1306_over_network():
    """
    Install the SSD1306 driver using mip first (micropython-lib),
    falling back to upip (micropython-ssd1306 on PyPI).
    """
    # Try mip (preferred on modern MicroPython)
    try:
        import mip
        print("Installing ssd1306 via mip...")
        mip.install("ssd1306")  # installs to /lib
        if load_ssd1306_driver():
            return True
    except Exception as e:
        print("mip install failed:", e)

    # Fallback: upip
    try:
        import upip
        print("Installing ssd1306 via upip (micropython-ssd1306)...")
        upip.install("micropython-ssd1306")  # installs to /lib
        if load_ssd1306_driver():
            return True
    except Exception as e:
        print("upip install failed:", e)

    return False

def oled_text(lines, y0=0):
    """Draw a list of strings (max 8 lines @ 8px). Safe if OLED missing."""
    if not oled:
        return
    oled.fill(0)
    y = y0
    for s in lines[:8]:
        oled.text(str(s)[:21], 0, y)
        y += 8
    oled.show()

def oled_init():
    """Initialize I2C and the OLED (after the driver is available)."""
    global i2c, oled
    if SSD1306_I2C is None:
        print("SSD1306_I2C not available; skipping OLED init.")
        return
    try:
        i2c = I2C(I2C_ID, sda=Pin(4), scl=Pin(5), freq=400_000)
        devices = i2c.scan()
        print("I2C devices:", [hex(d) for d in devices] if devices else "None")
        addr = 0x3C if 0x3C in devices else (0x3D if 0x3D in devices else 0x3C)
        oled = SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c, addr=addr)
        oled_text(["OLED ready", f"Addr {hex(addr)}", "I2C0 GP4/GP5"])
    except Exception as e:
        print("OLED init error:", e)

# --- Time helpers ---
def get_timestamp():
    seconds = time.time()
    adjusted = time.localtime(seconds + credentials.TIMEZONE_OFFSET * 3600)
    year, month, mday, hour, minute, second, _, _ = adjusted
    return f"{year}-{month:02d}-{mday:02d} {hour:02d}:{minute:02d}:{second:02d}.{ms_counter:03d}"

# --- Network / Time ---
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(credentials.SSID, credentials.PASSWORD)
        while not wlan.isconnected():
            time.sleep(0.3)
    print("Connected to WiFi:", wlan.ifconfig())
    return wlan

def sync_time():
    ntp_servers = ["pool.ntp.org", "time.google.com", "129.6.15.28"]
    for server in ntp_servers:
        try:
            ntptime.host = server
            ntptime.settime()
            timer.init(period=1, mode=Timer.PERIODIC, callback=update_ms)
            print(f"Time synced with {server}")
            OUTPUT_PIN_time_synced.on()
            oled_text(["NTP synced", server, get_timestamp().split()[1]])
            time.sleep(0.6)
            return True
        except OSError as e:
            print(f"Failed with {server}: {e}")
            oled_text(["NTP fail", server, str(e)[:21]])
            time.sleep(0.3)
    oled_text(["NTP FAILED", "Check WiFi/DNS"])
    return False

# --- HTTP helpers ---
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
        print("Error sending data:", e)
        oled_text(["Send ERROR", str(e)[:20]])
        return False

def edit_record(record_id, field, new_value):
    payload = {"id": record_id, "field": field, "new_value": new_value}
    print(f"Attempting to edit record {record_id}: set {field}='{new_value}'")
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
        print("Raw response:", raw)
        try:
            response = res.json()
        except ValueError as e:
            print(f"JSON decode error: {e}")
            oled_text(["Edit RESP ERR", str(e)[:20]])
            res.close()
            return False
        res.close()
        if response.get('status') == 'success':
            print(f"Success! Edited record {record_id}.")
            oled_text(["Edit OK", f"id {record_id}"])
            return True
        else:
            msg = response.get('message', 'No error message')
            print("Edit failed:", msg)
            oled_text(["Edit FAIL", msg[:21]])
            return False
    except Exception as e:
        print("Network/request error:", str(e))
        oled_text(["Edit NET ERR", str(e)[:21]])
        return False

# --- DB read (core1) ---
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

            current_pin_state = INPUT_PIN_stop_race.value()
            if current_pin_state != last_pin_state:
                print(f"Stop race pin changed to: {current_pin_state}")
                last_pin_state = current_pin_state
                if current_pin_state == 0:
                    print("Race stop detected! Processing...")
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

            time.sleep(0.1)

        except Exception as e:
            print("DB operation error:", e)
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
        print("Error fetching last startnummer:", e)
        return 0

# --- Core1 thread manager ---
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

# --- Main ---
def main():
    # Connect Wi-Fi first, so we can install the driver if needed
    wlan = connect_wifi()
    ip = wlan.ifconfig()[0]
    print("IP:", ip)

    # Ensure SSD1306 driver is available (try import, then install if missing)
    if not load_ssd1306_driver():
        print("Attempting to install ssd1306 driver over network...")
        if install_ssd1306_over_network():
            print("ssd1306 installed successfully.")
        else:
            print("ssd1306 could not be installed. OLED will be disabled.")

    # Now initialize the OLED (if driver present)
    oled_init()
    oled_text(["WiFi OK", ip, "Syncing time..."])

    # Sync time
    synced = sync_time()
    if not synced:
        oled_text(["Time sync FAIL", "Continuing..."])

    # Start DB reader on core1
    core1_manager = Core1Manager()
    core1_manager.start()

    cnt = get_last_startnummer()
    startnummer = "Startnummer:"
    print(f"Starting from Startnummer {cnt}")
    oled_text(["Ready", f"{startnummer} {cnt}", "Waiting START..."])

    print("Starting monitoring...\n")
    print("core0: Waiting for pin start race pin to go LOW")

    while True:
        current_state = INPUT_PIN_start_race.value()
        if current_state == 0:
            print("Pin pulled down detected!")
            message = f"{startnummer} {cnt}"
            oled_text(["START detected", message, "logging..."])
            if send_data(message, "race_started"):
                print("Event logged successfully")
                oled_text(["START logged", message, "Waiting..."])
                cnt += 1
            else:
                print("Failed to log event")
                oled_text(["START log FAIL", message])
            time.sleep(1)
        else:
            oled_text([
                "Idle",
                f"Next: {startnummer} {cnt}",
                f"Start pin:{current_state}",
                get_timestamp().split()[1]
            ])
            time.sleep(0.5)

    # Unreached
    core1_manager.stop()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Shutdown / Stopped.")
