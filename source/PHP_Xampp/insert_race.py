# main.py — MicroPython (Pico W / Pico2 W)
import network, time, ujson, sys
import usocket as socket
import credentials

# Optional, for non-blocking USB keyboard input
try:
    import uselect
except ImportError:
    uselect = None

try:
    from machine import USB_VCP
    _usb = USB_VCP()
except Exception:
    _usb = None

SSID = credentials.SSID
PASS = credentials.PASSWORD

HOST = credentials.SERVER_HOST       # e.g. "192.168.0.50" or "kinoklub.ch"
PATH = credentials.INSERT            # e.g. "/insert.php"
API_KEY = credentials.API_KEY

STARTNUMMER = 2                      # <-- change if needed
DEVICE_ID   = "PICO2W_ABC123"
DEVICE_NAME = "StartGate"
RACE_STATUS = "START"                # or "FINISH", etc.

def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(SSID, PASS)
        for _ in range(150):  # ~15s
            if wlan.isconnected():
                break
            time.sleep(0.1)
    return wlan.isconnected()

def http_post_json(host, path, obj):
    addr = socket.getaddrinfo(host, 80)[0][-1]
    s = socket.socket()
    s.connect(addr)
    try:
        payload = ujson.dumps(obj)
        req  = "POST {} HTTP/1.0\r\n".format(path)
        req += "Host: {}\r\n".format(host)
        req += "Content-Type: application/json\r\n"
        req += "Content-Length: {}\r\n".format(len(payload))
        req += "Connection: close\r\n"
        req += "X-API-Key: {}\r\n".format(API_KEY)
        req += "\r\n"
        s.send(req.encode("utf-8"))
        s.send(payload.encode("utf-8"))
        buf = b""
        while True:
            data = s.recv(1024)
            if not data:
                break
            buf += data
        return buf
    finally:
        s.close()

# High-resolution wall-clock milliseconds
try:
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
except AttributeError:
    # fallback (shouldn't be needed on Pico)
    ticks_ms = lambda: int(time.time() * 1000)
    ticks_diff = lambda a, b: a - b

_BASE_EPOCH_MS = None
_BASE_TICKS_MS = None

def _init_epoch_ms():
    # Optional: sync RTC once if you use ntptime elsewhere
    # import ntptime; 
    # try: ntptime.settime()
    # except: pass
    global _BASE_EPOCH_MS, _BASE_TICKS_MS
    # time.time() is seconds (integer); we use it as the wall-clock anchor
    _BASE_EPOCH_MS = int(time.time()) * 1000
    _BASE_TICKS_MS = ticks_ms()

def epoch_ms():
    """Return wall-clock ms with sub-second precision using ticks_ms()."""
    if _BASE_EPOCH_MS is None:
        _init_epoch_ms()
    return _BASE_EPOCH_MS + int(ticks_diff(ticks_ms(), _BASE_TICKS_MS))


# ---- Non-blocking single-character input from USB serial ----
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

def send_db_entry(run_value):
    body = {
        "Startnummer": STARTNUMMER,
        "run": run_value,
        "timestamp_ms": epoch_ms(),
        "device_id": DEVICE_ID,
        "device_name": DEVICE_NAME,
        "race_status": RACE_STATUS
    }
    resp = http_post_json(HOST, PATH, body)
    head, _, tail = resp.partition(b"\r\n\r\n")
    try:
        print("Status+Headers:\n", head.decode("utf-8", "ignore"))
        print("Body:\n", tail.decode("utf-8", "ignore"))
    except Exception:
        print("Raw response bytes length:", len(resp))

def main():
    if not wifi_connect():
        print("WiFi connect failed")
        return

    print("WiFi connected.")
    print("Press 'x' to create a new DB entry, 'q' to quit.")
    run_counter = 1

    # Optional initial post (comment out if not wanted)
    # send_db_entry(run_counter); run_counter += 1

    while True:
        ch = read_char_nonblocking()
        if ch:
            if ch in ('x', 'X'):
                print("-> Sending entry (run=%d)..." % run_counter)
                # Reconnect if WiFi dropped
                if not network.WLAN(network.STA_IF).isconnected():
                    print("WiFi dropped, reconnecting...")
                    if not wifi_connect():
                        print("Reconnect failed.")
                        time.sleep(0.5)
                        continue
                send_db_entry(run_counter)
                run_counter += 1
            elif ch in ('q', 'Q'):
                print("******************\nShutdown\n******************")
                break
            # ignore other keys
        time.sleep(0.03)  # tiny idle to be kind to CPU

if __name__ == "__main__":
    main()

