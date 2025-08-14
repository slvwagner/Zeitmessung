import network
import ntptime
import time
import urequests
from machine import Pin, Timer
import json
import _thread  # For running on second core

# --- WiFi credentials ---
SSID = "WN-888F40"
PASSWORD = "pdn8f428vk"

# --- Configuration ---
SERVER_URL = "http://wagnius/insert.php"
TIMEZONE_OFFSET = 2  # UTC+2
INPUT_PIN = 0  # Change to your actual GPIO pin number
DEBOUNCE_MS = 50  # Debounce time in milliseconds

# --- Millisecond Counter Setup ---
ms_counter = 0
last_pin_state = None
last_pin_change = 0
timer = Timer()

# --- LED heartbeat function (runs on core 1) ---
def heartbeat_led():
    led = Pin("LED", Pin.OUT)  # Internal LED
    while True:
        led.toggle()
        time.sleep(1)  # 1 second blink interval

# Start heartbeat on core 1
_thread.start_new_thread(heartbeat_led, ())

# --- Functions ---
def update_ms(timer):
    global ms_counter
    ms_counter = (ms_counter + 1) % 1000

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    while not wlan.isconnected():
        time.sleep(0.5)
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
            return True
        except OSError as e:
            print(f"Failed with {server}: {e}")
    print("Time sync failed with all servers")
    return False

def get_timestamp():
    seconds = time.time()
    adjusted_time = time.localtime(seconds + TIMEZONE_OFFSET * 3600)
    year, month, mday, hour, minute, second, _, _ = adjusted_time
    return f"{year}-{month:02d}-{mday:02d} {hour:02d}:{minute:02d}:{second:02d}.{ms_counter:03d}"

def send_data(value):
    data = {
        "value": value,
        "timestamp": get_timestamp()
    }
    print("Data to send:", data)
    try:
        res = urequests.post(SERVER_URL, json=data, timeout=5)
        print("Server response:", res.text)
        res.close()
        return True
    except Exception as e:
        print("Error sending data:", e)
        return False

# --- Main ---
def main():
    input_pin = Pin(INPUT_PIN, Pin.IN, Pin.PULL_UP)
    wlan = connect_wifi()
    sync_time()
    cnt = 0
    startnummer = "start startnummer"
    
    print("Starting monitoring...\n")
    print(f"Waiting for pin {INPUT_PIN} to go LOW")
    
    while True:
        current_state = input_pin.value()
        if current_state == 0:
            print("Pin pulled down detected!")
            message = f"{startnummer} {cnt}"
            if send_data(message):
                print("Event logged successfully")
                print(f"Waiting for pin {INPUT_PIN} to go LOW\n")
                cnt += 1
            else:
                print("Failed to log event")
            time.sleep(1)
        time.sleep(0.01)

if __name__ == "__main__":
    main()
