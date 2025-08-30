import network, time, ujson
import usocket as socket
import credentials

SSID = credentials.SSID
PASS = credentials.PASSWORD

HOST = credentials.SERVER_HOST
PATH = "/"+ credentials.INSERT
API_KEY = credentials.API_KEY

def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(SSID, PASS)
        for _ in range(100):  # ~10s
            if wlan.isconnected():
                break
            time.sleep(0.1)
    return wlan.isconnected()

def http_post_json(host, path, obj):
    addr = socket.getaddrinfo(host, 80)[0][-1]
    s = socket.socket()
    s.connect(addr)
    payload = ujson.dumps(obj)
    req = "POST {} HTTP/1.0\r\n".format(path)
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
    s.close()
    return buf

def epoch_ms():
    try:
        return int(time.time() * 1000)
    except:
        return None

if wifi_connect():
    print("WiFi connected.")
    body = {
        "Startnummer": 2,
        "run": 1,
        "timestamp_ms": epoch_ms(),
        "device_id": "PICO2W_ABC123",
        "device_name": "StartGate",
        "race_status": "START"
    }
    resp = http_post_json(HOST, PATH, body)
    head, _, tail = resp.partition(b"\r\n\r\n")
    print("Status+Headers:\n", head.decode("utf-8", "ignore"))
    print("Body:\n", tail.decode("utf-8", "ignore"))
else:
    print("WiFi connect failed")

