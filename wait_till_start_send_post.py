import network
import ntptime
import time
import urequests
from machine import Pin, Timer
import json

# import wifi credentials
import secrets
SSID = secrets.WIFI_SSID
PASSWORD = secrets.WIFI_PASSWORD

# --- Configuration ---
SERVER_URL = "http://wagnius/insert.php"
TIMEZONE_OFFSET = 2  # UTC+2
INPUT_PIN = 0  # Change to your actual GPIO pin number
DEBOUNCE_MS = 50  # Debounce time in milliseconds

# --- Millisecond Counter Setup ---
ms_counter = 0
last_pin_state = None
last_pin_change = 0

def update_ms(timer):
    global ms_counter
    ms_counter = (ms_counter + 1) % 1000

timer = Timer()
timer.init(period=1, mode=Timer.PERIODIC, callback=update_ms)

# --- WiFi Setup ---
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    
    while not wlan.isconnected():
        time.sleep(0.5)
    print("Connected to WiFi:", wlan.ifconfig())
    return wlan

# --- NTP Sync ---
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

# --- Timestamp Generation ---
def get_timestamp():
    seconds = time.time()
    adjusted_time = time.localtime(seconds + TIMEZONE_OFFSET * 3600)
    year, month, mday, hour, minute, second, _, _ = adjusted_time
    return f"{year}-{month:02d}-{mday:02d} {hour:02d}:{minute:02d}:{second:02d}.{ms_counter:03d}"

# --- HTTP Post Function ---
def send_data(value):
    data = {
        "value": value,
        "timestamp": get_timestamp()
    }
    
    try:
        res = urequests.post(SERVER_URL, json=data, timeout=5)
        print("Server response:", res.text)
        res.close()
        return True
    except Exception as e:
        print("Error sending data:", e)
        return False

# --- Main Program ---
# Initialize hardware
input_pin = Pin(INPUT_PIN, Pin.IN, Pin.PULL_UP)

def main():
    # Initialize hardware
    input_pin = Pin(INPUT_PIN, Pin.IN, Pin.PULL_UP)
    wlan = connect_wifi()
    sync_time()
    cnt = 0
    startnummer = "start startnummer"
    
    print("Starting monitoring...")
    print(f"Waiting for pin {INPUT_PIN} to go LOW")
    
    while True:
        current_state = input_pin.value()
        
        # Detect falling edge with debounce
        if current_state == 0 :
            print("Pin pulled down detected!")
            message = (startnummer + " " + str(cnt))
            print("Sent message:", message)
            if send_data(message):
                print("Event logged successfully")
                print(f"Waiting for pin {INPUT_PIN} to go LOW")
                cnt += 1
            else:
                print("Failed to log event")

            time.sleep(1)  # Cooldown period
        
        time.sleep(0.01)  # Small delay to prevent busy-waiting

if __name__ == "__main__":
    main()