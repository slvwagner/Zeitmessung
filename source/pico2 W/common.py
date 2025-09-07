# common.py — shared helpers + big-digit rendering (MicroPython)
import time, json, sys, os, ubinascii
import network
try:
    import ntptime
except Exception:
    ntptime = None

try:
    import urequests as requests
except Exception:
    requests = None

# Your OLED module must expose: oled_init(), oled, oled_text(lines), OLEDWriter, OLEDScroller
import OLED

# ----------------------------
# Ring logger + dbg()
# ----------------------------
_LOG = []; _MAX_LOG = 120
def dbg(*parts):
    s = " ".join(str(x) for x in parts)
    try: print(s)
    except: pass
    try:
        _LOG.append((time.ticks_ms(), s))
        if len(_LOG) > _MAX_LOG: _LOG.pop(0)
    except: pass

def recent_log(n=8): return [m for _, m in _LOG[-n:]]

def log_to_file(path="last_log.txt", head_lines=None):
    try:
        with open(path, "w") as f:
            if head_lines:
                for ln in head_lines: f.write(str(ln) + "\n")
            for _, m in _LOG[-60:]: f.write(m + "\n")
        dbg("Log saved:", path)
    except Exception as e:
        dbg("Save log failed:", e)

# ----------------------------
# UI queue → only Core 0 renders OLED
# ----------------------------
try:
    import _thread
    _ui_lock = _thread.allocate_lock()
except Exception:
    class _DummyLock:
        def __enter__(self): pass
        def __exit__(self, *a): pass
    _ui_lock = _DummyLock()

_ui_queue = []   # list of (lines, until_ms)
_notice_until_ms = 0

def ui_post(lines, hold_ms=1200, replace=True, max_cols=21):
    if not isinstance(lines, (list, tuple)): lines = [str(lines)]
    lines = [str(x)[:max_cols] for x in lines]
    with _ui_lock:
        if replace: _ui_queue[:] = []
        _ui_queue.append((lines, time.ticks_add(time.ticks_ms(), hold_ms)))

def ui_drain_once():
    """Core 0: render one queued message if any."""
    global _notice_until_ms
    item = None
    with _ui_lock:
        if _ui_queue: item = _ui_queue.pop(0)
    if not item: return False
    lines, until_ms = item
    try: OLED.oled_text(lines)
    except Exception: pass
    _notice_until_ms = until_ms
    return True

def notice_active():
    return time.ticks_diff(_notice_until_ms, time.ticks_ms()) > 0

def show_error(where, exc):
    try:
        if hasattr(sys, "print_exception"): sys.print_exception(exc)
    except Exception: pass
    ui_post(["ERR @" + str(where), (str(exc)[:21] if exc else "")])

# ----------------------------
# Wi-Fi + time
# ----------------------------
def wifi_connect(ssid, password, timeout_ms=15000):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        sta.connect(ssid, password)
        t0 = time.ticks_ms()
        while not sta.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                raise RuntimeError("WiFi timeout")
            time.sleep(0.2)
    dbg("WiFi:", sta.ifconfig()); return sta

_epoch_anchor_s=None; _ticks_anchor_ms=None
def time_sync_ntp(hosts=("pool.ntp.org","time.google.com","129.6.15.28")):
    global _epoch_anchor_s,_ticks_anchor_ms
    if ntptime is None:
        _epoch_anchor_s=time.time(); _ticks_anchor_ms=time.ticks_ms()
        ui_post(["NTP unsupported"], 900); return False
    for h in hosts:
        try:
            ntptime.host=h; ntptime.settime()
            _epoch_anchor_s=time.time(); _ticks_anchor_ms=time.ticks_ms()
            dbg("NTP synced:", h); ui_post(["NTP synced", h], 800); return True
        except Exception as e:
            dbg("NTP fail:", h, e)
    _epoch_anchor_s=time.time(); _ticks_anchor_ms=time.ticks_ms()
    ui_post(["NTP FAILED","Check WiFi/DNS"], 1100); return False

def epoch_ms():
    global _epoch_anchor_s,_ticks_anchor_ms
    if _epoch_anchor_s is None:
        _epoch_anchor_s=time.time(); _ticks_anchor_ms=time.ticks_ms()
    return int(_epoch_anchor_s*1000 + time.ticks_diff(time.ticks_ms(), _ticks_anchor_ms))

def ts_local_ms(tz_hours): return epoch_ms()+int(tz_hours*3600*1000)

def format_local(ts_ms, tz_hours):
    ms_local = (epoch_ms() if ts_ms is None else ts_ms) + int(tz_hours*3600*1000)
    sec = ms_local//1000; mmm = ms_local%1000; tm=time.gmtime(sec)
    return "%04d-%02d-%02d %02d:%02d:%02d.%03d"%(tm[0],tm[1],tm[2],tm[3],tm[4],tm[5],mmm)

# ----------------------------
# Device id
# ----------------------------
def build_device_id():
    try:
        sta = network.WLAN(network.STA_IF); mac = sta.config('mac') or b''
    except Exception: mac = b''
    if mac: return ubinascii.hexlify(mac[-6:]).decode()
    try:
        return ubinascii.hexlify(network.WLAN().config('mac')[-6:]).decode()
    except Exception: pass
    try:
        import machine
        return ubinascii.hexlify(machine.unique_id())[-12:].decode()
    except Exception: return "deviceid"

# ----------------------------
# URL + HTTP
# ----------------------------
def build_root(base):
    s = str(base).strip()
    if not s.startswith(("http://","https://")): s = "http://" + s
    return s.rstrip("/")

def http_get_json(url, headers=None, timeout=4):
    if requests is None: return None
    r=None
    try:
        r=requests.get(url, headers=headers or {}, timeout=timeout)
        if r.status_code!=200: dbg("GET",url,"->",r.status_code); return None
        return r.json()
    except Exception as e:
        dbg("GET EXC",url,e); return None
    finally:
        try: r and r.close()
        except: pass

def http_post_json(url, payload, headers=None, timeout=6):
    if requests is None: return {"status":"error","data":{"exception":"no requests"}}
    r=None
    try:
        h={"Content-Type":"application/json"}
        if headers: h.update(headers)
        r=requests.post(url, data=json.dumps(payload), headers=h, timeout=timeout)
        if r.status_code!=200: dbg("POST",url,"->",r.status_code); return {"status":"error","data":{"http":r.status_code}}
        return r.json()
    except Exception as e:
        dbg("POST EXC",url,e); return {"status":"error","data":{"exception":str(e)}}
    finally:
        try: r and r.close()
        except: pass

# ----------------------------
# Outbox (offline POST queue)
# ----------------------------
_outbox=[]
def outbox_queue(payload): _outbox.append(payload); dbg("OUTBOX queued", payload.get("race_status"), payload.get("Startnummer"))
def outbox_flush(post_callable):
    if not _outbox: return
    dbg("OUTBOX flushing", len(_outbox))
    keep=[]
    for p in list(_outbox):
        try:
            ok=post_callable(p)
            if not ok: keep.append(p)
        except Exception: keep.append(p)
    _outbox[:]=keep

# ----------------------------
# Big seven-segment rendering
# ----------------------------
# Segment geometry (for scale s): seg_w=3*s, seg_l=20*s, seg_v=14*s
_SEG_MAP = {
    '0': ('a','b','c','d','e','f'),
    '1': ('b','c'),
    '2': ('a','b','g','e','d'),
    '3': ('a','b','g','c','d'),
    '4': ('f','g','b','c'),
    '5': ('a','f','g','c','d'),
    '6': ('a','f','g','e','c','d'),
    '7': ('a','b','c'),
    '8': ('a','b','c','d','e','f','g'),
    '9': ('a','b','c','d','f','g'),
}

def _seg_draw(oled, x, y, s, on):
    seg_w=3*s; seg_l=20*s; seg_v=14*s
    def H(x0,y0): oled.fill_rect(x0,y0, seg_l, seg_w, on)
    def V(x0,y0): oled.fill_rect(x0,y0, seg_w, seg_v, on)
    a=lambda: H(x+seg_w,           y+0)
    g=lambda: H(x+seg_w,           y+seg_v+seg_w)
    d=lambda: H(x+seg_w,           y+2*seg_v+2*seg_w)
    f=lambda: V(x+0,               y+seg_w)
    b=lambda: V(x+seg_l+seg_w,     y+seg_w)
    e=lambda: V(x+0,               y+seg_v+2*seg_w)
    c=lambda: V(x+seg_l+seg_w,     y+seg_v+2*seg_w)
    return a,b,c,d,e,f,g, seg_w,seg_l,seg_v

def _draw_big_digit(oled, x, y, s, ch, color=1, clear_box=True):
    a,b,c,d,e,f,g, seg_w,seg_l,seg_v = _seg_draw(oled,x,y,s,color)
    box_w = seg_l + 2*seg_w
    box_h = 2*seg_v + 3*seg_w
    if clear_box: oled.fill_rect(x,y,box_w,box_h,0)
    segs = _SEG_MAP.get(ch)
    if not segs: return box_w, box_h
    for name in segs:
        if   name=='a': a()
        elif name=='b': b()
        elif name=='c': c()
        elif name=='d': d()
        elif name=='e': e()
        elif name=='f': f()
        elif name=='g': g()
    return box_w, box_h

def _best_scale_for(txt, avail_h, spacing=4):
    # try large to small
    for s in (3,2,1):
        seg_w, seg_l, seg_v = 3*s, 20*s, 14*s
        height = 2*seg_v + 3*seg_w
        if height > avail_h: continue
        box_w = seg_l + 2*seg_w
        total_w = len(txt)*box_w + max(0,len(txt)-1)*spacing
        if total_w <= 128:
            return s
    return 1

def render_big_number(value, top_lines=1, spacing=4):
    """
    Draw big number occupying as much of the remaining screen as possible.
    top_lines = number of 8px text lines already used at the top.
    """
    try: txt = str(int(value))
    except: txt = str(value)
    oled = OLED.oled
    y0 = top_lines * 8
    avail_h = max(0, 64 - y0)
    s = _best_scale_for(txt, avail_h, spacing=spacing)
    seg_w, seg_l, seg_v = 3*s, 20*s, 14*s
    box_w = seg_l + 2*seg_w
    total = len(txt)*box_w + max(0,len(txt)-1)*spacing
    x = max(0, (128 - total)//2)
    # clear drawing area only
    oled.fill_rect(0, y0, 128, 64 - y0, 0)
    for ch in txt:
        _draw_big_digit(oled, x, y0, s, ch, 1, clear_box=False)
        x += box_w + spacing
    try: oled.show()
    except Exception: pass

def render_locked_startnummer(sn, subtitle=None):
    """
    Clears screen, prints 1-2 lines of small text, then renders big SN.
    """
    lines = ["Startnummer #%s is armed" % str(sn)]
    if subtitle: lines.append(str(subtitle)[:21])
    try:
        # Clear screen, print header lines
        OLED.oled.fill(0)
        OLED.oled_text(lines)
    except Exception:
        pass
    render_big_number(sn, top_lines=len(lines), spacing=4)

# ----------------------------
# Safe shutdown
# ----------------------------
def safe_shutdown(extra_lines=None, sta=None, led_pin=None):
    try:
        ui_post(["Shutting down...", *(extra_lines or [])], 900)
        OLED.oled_text(["Shutting down...", *(extra_lines or [])])
        time.sleep(0.6)
    except Exception: pass
    if led_pin:
        try: led_pin.value(0)
        except: pass
    if sta:
        try: sta.active(False)
        except: pass
    try:
        if hasattr(OLED.oled, "_cmd"): OLED.oled._cmd(0xAE)
    except Exception: pass
    raise SystemExit
