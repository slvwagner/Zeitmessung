# Script to measure time between events
# xampp server with PHP and mySQL Database will be handled via php script 

import network
import ntptime
import time
import urequests
from machine import Pin, Timer
import json
import _thread  # For running on second core
import credentials  # Import credentials from a separate file

import machine, ubinascii
DEVICE_ID = ubinascii.hexlify(machine.unique_id()).decode()
DEVICE_NAME = "Start"

# --- Input Pins Setup ---
INPUT_PIN_start_race = Pin(2, Pin.IN, Pin.PULL_UP)
INPUT_PIN_stop_race = Pin(3, Pin.IN, Pin.PULL_UP)

# Output Pins
OUTPUT_PIN_time_synced = Pin(12, Pin.OUT)

# --- Millisecond Counter Setup ---
ms_counter = 0
timer = Timer()

# --- Functions ---
def update_ms(timer):
    global ms_counter
    ms_counter = (ms_counter + 1) % 1000

# Time stamp
def get_timestamp():
    seconds = time.time()
    adjusted_time = time.localtime(seconds + credentials.TIMEZONE_OFFSET * 3600)
    year, month, mday, hour, minute, second, _, _ = adjusted_time
    return f"{year}-{month:02d}-{mday:02d} {hour:02d}:{minute:02d}:{second:02d}.{ms_counter:03d}"

# connect to WiFi
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(credentials.SSID, credentials.PASSWORD)
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
            OUTPUT_PIN_time_synced.on()
            time.sleep(1)  # Allow time for the pin to be set
            OUTPUT_PIN_time_synced.off()
            return True
        except OSError as e:
            print(f"Failed with {server}: {e}")
    print("Time sync failed with all servers")
    return False

# Send data to server
def send_data(value, race_status):
    data = {
        "value": value,
        "timestamp": get_timestamp(),
        "device_id": DEVICE_ID,
        "device_name": DEVICE_NAME,
        "race_status": race_status
    }
    print("Data to send:", data)
    try:
        res = urequests.post(credentials.SERVER_URL, json=data, timeout=5)
        print("Server response:", res.text)
        res.close()
        return True
    except Exception as e:
        print("Error sending data:", e)
        return False

# Edit a record in the database
def edit_record(record_id, field, new_value):
    data = {
        "id": record_id,
        "field": field,
        "new_value": new_value
    }
    
    print(f"Attempting to edit record {record_id}: setting {field} to '{new_value}'")
    
    try:
        # First test if we can encode the data properly
        json_data = json.dumps(data)
        print("JSON payload:", json_data)
        
        # Make the request
        res = urequests.post(
            credentials.EDIT_URL,
            data=json_data,  # Using data instead of json parameter
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        # Print raw response for debugging
        raw_response = res.text
        print("Raw response:", raw_response)
        
        # Try to parse JSON
        try:
            response = res.json()
        except ValueError as e:
            print(f"JSON decode error: {e}")
            print("Response might be:", raw_response)
            return False
            
        res.close()
        
        if response.get('status') == 'success':
            print(f"Success! Edited record {record_id}. Affected rows: {response.get('affected_rows', 1)}")
            return True
        else:
            print(f"Edit failed. Server says: {response.get('message', 'No error message')}")
            return False
            
    except Exception as e:
        print(f"Network/request error: {str(e)}")
        return False
    
# --- Read URL ---
def read_from_db(race_status=None, device_id=None):
    # Runs on core 1 to periodically fetch latest entries from DB
    led = Pin("LED", Pin.OUT)
    old = None
    last_pin_state = INPUT_PIN_stop_race.value()  # Track previous pin state

    while True:
        try:
            # Normal DB reading operations
            url = credentials.READ_URL
            params = []
            
            if race_status is not None:
                params.append(f"race_status={race_status}")
            if device_id is not None:
                params.append(f"device_id={device_id}")
            
            if params:
                url += "?" + "&".join(params)


            res = urequests.get(url, timeout=2)  # Shorter timeout
            data = res.json()
            res.close()

            # Check pin state first - immediate response
            current_pin_state = INPUT_PIN_stop_race.value()
            if current_pin_state != last_pin_state:
                print(f"Stop race pin changed to: {current_pin_state}")
                last_pin_state = current_pin_state
                
                if current_pin_state == 0:  # Pin pulled down
                    print("Race stop detected! Processing...")
                    # Example actions when stop detected:
                    first_record = data["data"][0]  # Get first record
                    first_id = first_record['id']   # Access its ID
                    first_value = first_record['value']   # Access its ID
                    send_data(first_value, "finished")
                    edit_record(first_id , "race_status", "started_and_finished")

                    # Add any other immediate actions here
                    continue  # Skip the rest of this loop iteration

            # Process data only if pin hasn't changed
            if INPUT_PIN_stop_race.value() != 0:
                if len(data["data"]) > 0:
                    print("******************")    
                    for idx, record in enumerate(data["data"]):
                        print(f"core1: Data {idx}:", record)

                if data.get("status") == "success":
                    latest = data["data"][0] if data["data"] else None

                    if old != latest:
                        print("core1: New DB entry:", latest)
                        # Fast blink for new data
                        for _ in range(2):
                            led.on()
                            time.sleep(0.1)
                            led.off()
                            time.sleep(0.1)
                    else:
                        # Single blink
                        led.on()
                        time.sleep(0.05)
                        led.off()

                    old = latest

            # Brief sleep to allow pin checks
            time.sleep(0.1)

        except Exception as e:
            print("DB operation error:", e)
            time.sleep(1)  # Wait after error

def get_last_startnummer():
    try:
        url = credentials.READ_URL + "?limit=1"  # if you add limit in PHP
        res = urequests.get(url, timeout=5)
        data = res.json()
        res.close()
        if data["status"] == "success" and len(data["data"]) > 0:
            last_value = data["data"][0]["value"]
            # Assuming value is stored like "Startnummer: 12"
            parts = last_value.split()
            if parts[-1].isdigit():
                return int(parts[-1]) + 1  # continue with next number
        return 0
    except Exception as e:
        print("Error fetching last startnummer:", e)
        return 0




# --- Main ---
def main():
    wlan = connect_wifi()
    sync_time()
    
    # Start reading DB by core 1
    _thread.start_new_thread(read_from_db, ("race_started", None))

    cnt = get_last_startnummer()  # <-- start from last saved
    startnummer = "Startnummer:"

    print(f"Starting from Startnummer {cnt}")

    print("Starting monitoring...\n")
    print("core0: Waiting for pin start race pin to go LOW")
    
    while True:
        current_state = INPUT_PIN_start_race.value()
        if current_state == 0:
            print("Pin pulled down detected!")
            message = f"{startnummer} {cnt}"
            if send_data(message, "race_started"):
                print("Event logged successfully")
                print("core0: Waiting for pin start race pin to go LOW\n")
                cnt += 1
            else:
                print("Failed to log event")
            time.sleep(1)
        time.sleep(0.01)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Shutdown / Stopped.")


