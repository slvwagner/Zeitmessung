import network
import ntptime
import time
import socket


# Connect to WiFi
ssid = "WN-888F40"
password = "pdn8f428vk"

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(ssid, password)

# Wait for connection
print("Connected to WiFi:", wlan.ifconfig())
while not wlan.isconnected():
    time.sleep(1)

print("Connected to WiFi:", wlan.ifconfig())


import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("8.8.8.8", 123))
    print("UDP reachable")
except:
    print("UDP blocked")
finally:
    s.close()


# List of NTP servers (IP and hostname)
ntp_servers = [
    "pool.ntp.org",
    "time.google.com",
    "129.6.15.28",  # time.nist.gov (direct IP)
]

# Try each NTP server
synced = False
for server in ntp_servers:
    try:
        ntptime.host = server
        ntptime.settime()
        print(f"Time sync successful with {server}")
        synced = True
        break
    except OSError as e:
        print(f"Failed with {server}: {e}")

if not synced:
    print("Time sync failed with all servers")

