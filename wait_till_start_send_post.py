# Script to measure time between events
# xampp server with PHP and mySQL Database will be handled via php script 

import network
import ntptime
import time
import urequests
from machine import Pin, Timer
import json
import _thread  # For running on second core

import machine, ubinascii
DEVICE_ID = ubinascii.hexlify(machine.unique_id()).decode()
DEVICE_NAME = "Start"

# --- WiFi credentials ---
SSID = "WN-888F40"
PASSWORD = "pdn8f428vk"

# --- Configuration ---
SERVER_URL = "http://wagnius/insert.php"
READ_URL = "http://wagnius/read.php"
TIMEZONE_OFFSET = 2  # UTC+2
INPUT_PIN = 0  # Change to your actual GPIO pin number
DEBOUNCE_MS = 50  # Debounce time in milliseconds

# --- Millisecond Counter Setup ---
ms_counter = 0
last_pin_state = None
last_pin_change = 0
timer = Timer()

# --- Functions ---
def update_ms(timer):
    global ms_counter
    ms_counter = (ms_counter + 1) % 1000

# Time stamp
def get_timestamp():
    seconds = time.time()
    adjusted_time = time.localtime(seconds + TIMEZONE_OFFSET * 3600)
    year, month, mday, hour, minute, second, _, _ = adjusted_time
    return f"{year}-{month:02d}-{mday:02d} {hour:02d}:{minute:02d}:{second:02d}.{ms_counter:03d}"

# connect to WiFi
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    while not wlan.isconnected():
        time.sleep(0.5)
    print("Connected to WiFi:", wlan.ifconfig())
    return wlan

# Sync time with NTP server
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

# Send data to server
def send_data(value):
    data = {
        "value": value,
        "timestamp": get_timestamp(),
        "device_id": DEVICE_ID,
        "device_name": DEVICE_NAME
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


# --- Read URL by core 1 ---
# --- Read URL by core 1 ---
def read_from_db():
    # Runs on core 1 to periodically fetch latest entries from DB
    led = Pin("LED", Pin.OUT)

    old = None

    while True:
        try:
            res = urequests.get(READ_URL, timeout=5)
            data = res.json()
            res.close()

            if data.get("status") == "success":
                latest = data["data"][0] if data["data"] else None

                if old != latest:
                    print("core1: Latest from DB:", latest, "\n", get_timestamp())

                    # Fast blink for new data
                    for _ in range(2):
                        led.toggle()
                        time.sleep(0.1)
                        led.toggle()
                        time.sleep(0.1)
                        led.toggle()
                        time.sleep(0.1)
                else:
                    # Normal single blink every 2 seconds
                    led.on()
                    time.sleep(0.1)
                    led.off()

                old = latest
            else:
                print("DB read error:", data)

        except Exception as e:
            print("Error reading DB:", e)

        time.sleep(2)  # poll every 2 seconds


# Start reading DB by core 1
_thread.start_new_thread(read_from_db, ())

# --- Main ---
def main():
    input_pin = Pin(INPUT_PIN, Pin.IN, Pin.PULL_UP)
    wlan = connect_wifi()
    sync_time()
    cnt = 0
    startnummer = "Startnummer:"
    
    print("Starting monitoring...\n")
    print(f"core0: Waiting for pin {INPUT_PIN} to go LOW")
    
    while True:
        current_state = input_pin.value()
        if current_state == 0:
            print("Pin pulled down detected!")
            message = f"{startnummer} {cnt}"
            if send_data(message):
                print("Event logged successfully")
                print(f"core0: Waiting for pin {INPUT_PIN} to go LOW\n")
                cnt += 1
            else:
                print("Failed to log event")
            time.sleep(1)
        time.sleep(0.01)

# Start the main program on core0
if __name__ == "__main__":
    main()
