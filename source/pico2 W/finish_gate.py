# finish_gate.py — streamlined Finish Gate using common helpers
import time
from machine import Pin
import credentials
import common as C

DEVICE_NAME = getattr(credentials, "DEVICE_NAME", "FinishGate")
TZ_H        = int(getattr(credentials, "TIMEZONE_OFFSET", 0))
API_KEY     = getattr(credentials, "API_KEY", "")

PIN_BEAM   = Pin(2, Pin.IN, Pin.PULL_UP)
PIN_CANCEL = Pin(3, Pin.IN, Pin.PULL_UP)
PIN_LED    = Pin(15, Pin.OUT)

SERVER_BASE   = C.build_root(credentials.SERVER_HOST)
INSERT_EP     = getattr(credentials, "INSERT", "/insert_race.php")
READ_EP       = getattr(credentials, "READ_URL", "/read.php")
OPEN_RUNS_EP  = getattr(credentials, "OPEN_RUNS", "/open_runs.php")

START_SET = {"started", "race_started"}
FIN_SET   = {"finished", "finish time", "time confirmed"}

_open = []                   # [{'Startnummer':int,'run':int,'started_at':str}]
_last_fetch = 0
OPEN_MS = 3000

DEVICE_ID = ""

def url(p):
    return SERVER_BASE + (p if p.startswith("/") else ("/" + p))

def fetch_open(force=False):
    global _open, _last_fetch
    if (not force) and time.ticks_diff(time.ticks_ms(), _last_fetch) < OPEN_MS:
        return
    headers = {"X-API-Key": API_KEY} if API_KEY else {}

    # 1) preferred: open_runs.php
    res = C.http_get_json(url(OPEN_RUNS_EP), headers=headers)
    if res and res.get("status") == "success" and isinstance(res.get("data"), list):
        cleaned = []
        for r in res["data"]:
            try:
                cleaned.append({"Startnummer": int(r["Startnummer"]),
                                "run": int(r["run"]),
                                "started_at": r.get("started_at","")})
            except: pass
        _open = cleaned
        _last_fetch = time.ticks_ms()
        return

    # 2) fallback: derive from read.php
    res2 = C.http_get_json(url(READ_EP) + "?limit=400&order=asc", headers=headers)
    tmp = []
    if res2 and res2.get("status") == "success":
        last = {}
        first_started = {}
        for e in res2["data"]:
            try:
                sn = int(e.get("Startnummer")); rn = int(e.get("run",1))
                st = (e.get("race_status") or "").strip().lower()
                ts = e.get("timestamp_ms", "")
                key = (sn, rn)
                if st in START_SET and key not in first_started:
                    first_started[key] = ts
                last[key] = st
            except: pass
        for (sn, rn), st in last.items():
            if st in START_SET:
                tmp.append({"Startnummer": sn, "run": rn, "started_at": first_started.get((sn,rn),"")})
        tmp.sort(key=lambda r: r.get("started_at",""))
    _open = tmp
    _last_fetch = time.ticks_ms()

def scroller_text():
    if not _open:
        return ["No open runs", "Waiting...", "", "Beam: idle"]
    head = ["Expected:",
            " SN #%s  Run %s" % (str(_open[0]["Startnummer"]), str(_open[0]["run"])),
            "On track: %d" % len(_open), ""]
    queue = []
    for i, r in enumerate(_open[:24]):
        queue.append(("%s#%s r%s" % (">" if i==0 else " ", r["Startnummer"], r["run"]))[:21])
    return head + queue

def post_finish(ts_ms):
    if not _open: return False, "empty"
    cur = _open[0]
    ts_str = C.format_local(ts_ms, TZ_H)
    payload = {
        "Startnummer": cur["Startnummer"],
        "run": cur["run"],
        "timestamp_ms": ts_str,
        "device_id": DEVICE_ID,
        "device_name": DEVICE_NAME,
        "race_status": getattr(credentials, "FINISH_STATUS", "finished"),
        "timezone_offset": TZ_H
    }
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    res = C.http_post_json(url(INSERT_EP), payload, headers=headers)
    ok = bool(res and res.get("status") == "success")
    msg = ("OK id=%s" % (res.get("data",{}).get("id"))) if ok else ("ERR %s" % (res,))
    if ok:
        del _open[0]
    return ok, msg

# Beam capture
_pending_ts = None
def _beam_isr(pin):
    global _pending_ts
    if pin.value() == 0 and _pending_ts is None:
        _pending_ts = C.epoch_ms()

def main():
    global DEVICE_ID, _pending_ts
    sta = C.wifi_connect(credentials.SSID, credentials.PASSWORD)
    C.time_sync_ntp()
    DEVICE_ID = C.build_device_id()

    import OLED
    OLED.oled_init()
    C.ui_post([DEVICE_NAME, "WiFi "+sta.ifconfig()[0], "Syncing runs..."], 1200)

    PIN_LED.value(0)
    try:
        PIN_BEAM.irq(trigger=Pin.IRQ_FALLING, handler=_beam_isr)
    except Exception:
        PIN_BEAM.irq(handler=_beam_isr, trigger=Pin.IRQ_FALLING)

    fetch_open(force=True)
    sc = None
    try:
        sc = OLED.OLEDScroller(OLED.oled, OLED.oled_lock, max_cols=21, max_lines=8,
                               line_height=8, interval_ms=1200, loop=True, max_loops=None,
                               break_long_words=True, hyphenate=False, collapse_spaces=True)
        sc.set_text(scroller_text(), y0=0)
    except Exception:
        pass

    LOG_HOLD_MS  = 1200
    SHUT_HOLD_MS = 4000
    last_blink = time.ticks_ms()

    try:
        while True:
            # Blink LED
            if time.ticks_diff(time.ticks_ms(), last_blink) > 500:
                last_blink = time.ticks_ms()
                PIN_LED.value(1 - PIN_LED.value())

            # Refresh queue + scroller
            fetch_open(False)
            if sc:
                sc.set_text(scroller_text(), y0=0)
                sc.tick()

            # CANCEL: short=clear pending / 1.2s=show logs / 4s=safe shutdown
            if PIN_CANCEL.value() == 0:
                t0 = time.ticks_ms(); shown = False
                while PIN_CANCEL.value() == 0:
                    dt = time.ticks_diff(time.ticks_ms(), t0)
                    if (not shown) and dt >= LOG_HOLD_MS and dt < SHUT_HOLD_MS:
                        C.ui_post(["Recent log:"] + C.recent_log(7), 1400)
                        shown = True
                    if dt >= SHUT_HOLD_MS:
                        C.log_to_file(head_lines=[DEVICE_NAME, "ID "+DEVICE_ID, "tz="+str(TZ_H)])
                        C.safe_shutdown(["Safe to power off"], sta=sta, led_pin=PIN_LED)
                    time.sleep_ms(18)
                if shown:
                    time.sleep_ms(700)
                else:
                    _pending_ts = None
                    C.ui_post(["Cancelled", "Pending cleared"], 800)

            # Beam → finish current
            if _pending_ts is not None:
                ts = _pending_ts; _pending_ts = None
                cur = _open[0] if _open else {"Startnummer":"-", "run":"-"}
                C.ui_post(["FINISH captured!",
                           "SN #%s  Run %s" % (str(cur.get("Startnummer","-")), str(cur.get("run","-"))),
                           C.format_local(ts, TZ_H),
                           "Uploading..."], 1100)
                ok, msg = post_finish(ts)
                C.ui_post(["FINISH " + ("OK" if ok else "FAIL"), msg[:21]], 1100)
                fetch_open(force=True)

            # Drain UI queue
            C.ui_drain_once()
            time.sleep_ms(20)

    except KeyboardInterrupt:
        C.safe_shutdown(["KeyboardInterrupt"], sta=sta, led_pin=PIN_LED)
    except Exception as e:
        C.show_error("main", e)
        C.log_to_file(head_lines=[DEVICE_NAME, "ID "+DEVICE_ID])
        C.safe_shutdown(["Error exit"], sta=sta, led_pin=PIN_LED)

if __name__ == "__main__":
    main()
