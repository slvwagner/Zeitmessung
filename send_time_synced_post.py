import urequests
import time

# Example data
value = "Startnummer 1"

# Adjust for your timezone (UTC+2 example)
timezone_offset = 2  # hours
year, month, mday, hour, minute, second, _, _ = time.localtime(time.time() + timezone_offset * 3600)
#year, month, mday, hour, minute, second, _, _ = time.localtime()
timestamp = f"{year}-{month:02d}-{mday:02d} {hour:02d}:{minute:02d}:{second:02d}"

url = "http://wagnius/insert.php"
print("host url: ", url)
data = {"value": value, "timestamp": timestamp}
print("Data to send:", data)

try:
    # Option 1: Send as form-encoded data
    res = urequests.post(url, json=data)  # or use data=data for form-encoded
    print("Server response:", res.text)
    res.close()
except Exception as e:
    print("Error sending data:", e)

print("Data sent attempt completed")  # Changed from "successfully" since we might have failed