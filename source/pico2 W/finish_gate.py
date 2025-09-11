# finish_gate.py — Finish Gate with PIO-timestamped edge capture (buffer-safe + RAM-friendly)
import time, micropython, gc
from machine import Pin
from rp2 import StateMachine, asm_pio

import credentials
import common as C

# --- Config / constants ---
DEVICE_NAME   = getattr(credentials, "DEVICE_NAME", "FinishGate")
TZ_H          = int(getattr(credentials, "TIMEZONE_OFFSET", 0))
API_KEY       = getattr(credentials, "API_KEY", "")

PIN_BEAM      = 2    # Active-LOW when beam is broken
PIN_CANCEL    = 3
PIN_LED_NUM   = 15

SERVER_BASE   = C.build_root(credentials.SERVER_HOST)
INSERT_EP     = "/insert_race.php"
READ_EP       = "/read.php"
OPEN_RUNS_EP  = "/open_runs.php"

START_SET = {"started", "race_started"}
FIN_SET   = {"finished", "finish time", "time confirmed"}

OPEN_MS         = 3000     # cache open runs this long
LOG_HOLD_MS     = 1200
SHUT_HOLD_MS    = 4000
LED_BLINK_MS    = 500

# Input hardening
REFRACTORY_US       = 300_000   # ignore triggers for X µs after a capture

# --- State ---
_open = []
_open_ver = 0
_last_fetch = 0
DEVICE_ID = ""

micropython.alloc_emergency_exception_buf(128)

# IRQ ring buffer (µs timestamps)
_Q_SIZE = 16
_ev_buf  = [0] * _Q_SIZE
_ev_head = 0
_ev_tail = 0
_last_evt_us = -1

# Stats (debug)
beam_irq_count = 0
dropped_events = 0
processed_fin  = 0
ignored_bounce = 0

# Timebase (wrap-safe conversion)
_BASE_TICKS_US = 0
_BASE_EPOCH_MS = 0

# GPIOs
PIN_LED        = Pin(PIN_LED_NUM, Pin.OUT, value=0)
PIN_BEAM_SIO   = Pin(PIN_BEAM, Pin.IN, Pin.PULL_UP)
PIN_CANCEL_SIO = Pin(PIN_CANCEL, Pin.IN, Pin.PULL_UP)

def url(p): return SERVER_BASE + (p if p.startswith("/") else ("/" + p))

def _set_open(new_list):
    """Replace _open and bump version only when it actually changes."""
    global _open, _open_ver
    changed = (len(new_list) != len(_open))
    if not changed and new_list:
        changed = (_open[0] != new_list[0]) or (_open[-1] != new_list[-1])
    if changed:
        _open = new_list
        _open_ver += 1

def fetch_open(force=False):
    """Populate _open with currently 'on track' (started, not finished)."""
    global _last_fetch
    now = time.ticks_ms()
    if not force and time.ticks_diff(now, _last_fetch) < OPEN_MS:
        return

    headers = {"X-API-Key": API_KEY} if API_KEY else {}

    # Preferred: dedicated endpoint
    try:
        res = C.http_get_json(url(OPEN_RUNS_EP), headers=headers)
    except Exception:
        res = None

    if res and res.get("status") == "success" and isinstance(res.get("data"), list):
        cleaned = []
        for r in res["data"]:
            try:
                cleaned.append({
                    "Startnummer": int(r["Startnummer"]),
                    "run": int(r["run"]),
                    "started_at": r.get("started_at", "")
                })
            except Exception:
                pass
        _set_open(cleaned)
        _last_fetch = now
        gc.collect()
        return

    # Fallback: progressively smaller limits to avoid OOM
    for lim in (120, 60, 30):
        try:
            res2 = C.http_get_json(url(READ_EP) + f"?limit={lim}&order=asc", headers=headers)
        except Exception:
            res2 = None
        if res2 and res2.get("status") == "success":
            tmp = []
            last = {}
            first_started = {}
            for e in res2["data"]:
                try:
                    sn = int(e.get("Startnummer"))
                    rn = int(e.get("run", 1))
                    st = (e.get("race_status") or "").strip().lower()
                    ts = e.get("timestamp_ms", "")
                    key = (sn, rn)
                    if st in START_SET and key not in first_started:
                        first_started[key] = ts
                    last[key] = st
                except Exception:
                    pass
            for (sn, rn), st in last.items():
                if st in START_SET:
                    tmp.append({
                        "Startnummer": sn,
                        "run": rn,
                        "started_at": first_started.get((sn, rn), "")
                    })
            tmp.sort(key=lambda r: r.get("started_at", ""))
            _set_open(tmp)
            _last_fetch = now
            gc.collect()
            return
    _last_fetch = now  # keep previous _open

def scroller_text():
    if not _open:
        return ["No open runs", "Waiting...", "", "Beam: idle"]
    head = [
        "Expected:",
        " SN #%s  Run %s" % (str(_open[0]["Startnummer"]), str(_open[0]["run"])),
        "On track: %d" % len(_open),
        ""
    ]
    queue = []
    for i, r in enumerate(_open[:24]):
        queue.append(("%s#%s r%s" % (">" if i == 0 else " ", r["Startnummer"], r["run"]))[:21])
    return head + queue

def post_finish(ts_ms):
    """Post finish for the currently expected runner."""
    if not _open:
        return False, "empty"
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

    C.dbg("FINISH measured: SNr %s  Run %s  @ %s" %
          (cur["Startnummer"], cur["run"], ts_str))

    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    res = C.http_post_json(url(INSERT_EP), payload, headers=headers)
    ok = bool(res and res.get("status") == "success")
    msg = ("OK id=%s" % (res.get("data", {}).get("id"))) if ok else ("ERR %s" % (res,))
    if ok:
        del _open[0]
        _set_open(_open[:])  # bump version to refresh scroller
    return ok, msg

# --- PIO program ---
@asm_pio()
def beam_fall_irq():
    wait(1, pin, 0)        # idle=HIGH before arming
    label("arm")
    wait(0, pin, 0)        # falling edge (beam broken)
    irq(0)                 # raise PIO IRQ 0
    wait(1, pin, 0)        # wait for release (HIGH)
    jmp("arm")

# --- IRQ handler (overflow-safe) ---
def _sm_irq_handler(sm):
    # HARD IRQ: no allocations / prints!
    global _ev_head, dropped_events, beam_irq_count
    tsus = time.ticks_us()
    nxt = (_ev_head + 1) & (_Q_SIZE - 1)
    if nxt == _ev_tail:
        dropped_events += 1   # queue full → drop newest
        return
    _ev_buf[_ev_head] = tsus
    _ev_head = nxt
    beam_irq_count += 1

def epoch_ms_from_ticks_us(ts_us):
    du = time.ticks_diff(ts_us, _BASE_TICKS_US)      # µs since base
    return _BASE_EPOCH_MS + (du + 500) // 1000       # ms (rounded)

# --- Main ---
def main():
    global DEVICE_ID, _ev_tail, _last_evt_us, _BASE_TICKS_US, _BASE_EPOCH_MS
    # FIX: declare mutated counters as globals
    global processed_fin, ignored_bounce

    sta = C.wifi_connect(credentials.SSID, credentials.PASSWORD)
    C.time_sync_ntp()
    DEVICE_ID = C.build_device_id()

    # Stable timebase right after NTP
    _BASE_EPOCH_MS = C.epoch_ms()
    _BASE_TICKS_US = time.ticks_us()

    import OLED
    OLED.oled_init()
    C.ui_post([DEVICE_NAME, "WiFi " + sta.ifconfig()[0], "Syncing runs..."], 1200)

    # PIO state machine
    sm = StateMachine(
        0, beam_fall_irq, freq=2_000_000,
        in_base=Pin(PIN_BEAM, Pin.IN, Pin.PULL_UP)
    )
    sm.irq(handler=_sm_irq_handler, hard=True)
    sm.active(1)

    fetch_open(force=True)

    # OLED scroller (update on change or cadence)
    sc = None
    last_scroller_ver = -1
    last_scroller_set_ms = time.ticks_ms()
    try:
        sc = OLED.OLEDScroller(
            OLED.oled, OLED.oled_lock,
            max_cols=21, max_lines=8, line_height=8,
            interval_ms=1200, loop=True, max_loops=None,
            break_long_words=True, hyphenate=False, collapse_spaces=True
        )
        sc.set_text(scroller_text(), y0=0)
        last_scroller_ver = _open_ver
    except Exception:
        pass

    last_blink = time.ticks_ms()
    last_dbg   = time.ticks_ms()
    last_gc    = time.ticks_ms()
    C.dbg("main loop starts (PIO armed)")

    try:
        while True:
            now_ms = time.ticks_ms()

            # Alive LED
            if time.ticks_diff(now_ms, last_blink) > LED_BLINK_MS:
                last_blink = now_ms
                PIN_LED.value(1 - PIN_LED.value())

            # Maintain open runs + UI
            fetch_open(False)
            if sc:
                if (_open_ver != last_scroller_ver) or (time.ticks_diff(now_ms, last_scroller_set_ms) >= 1200):
                    sc.set_text(scroller_text(), y0=0)
                    last_scroller_ver = _open_ver
                    last_scroller_set_ms = now_ms
                sc.tick()

            # CANCEL: short=clear queue; 1.2s=show log; 4s=shutdown
            if PIN_CANCEL_SIO.value() == 0:
                t0 = now_ms
                shown = False
                while PIN_CANCEL_SIO.value() == 0:
                    dt = time.ticks_diff(time.ticks_ms(), t0)
                    if (not shown) and dt >= LOG_HOLD_MS and dt < SHUT_HOLD_MS:
                        C.ui_post(["Recent log:"] + C.recent_log(7), 1400)
                        shown = True
                    if dt >= SHUT_HOLD_MS:
                        C.log_to_file(head_lines=[DEVICE_NAME, "ID " + DEVICE_ID, "tz=" + str(TZ_H)])
                        C.safe_shutdown(["Safe to power off"], sta=sta, led_pin=PIN_LED)
                    time.sleep_ms(18)
                if shown:
                    time.sleep_ms(700)
                else:
                    # Clear queued events safely: set tail = head
                    local_head = _ev_head      # read once to reduce race window
                    _ev_tail = local_head
                    C.ui_post(["Cancelled", "Queue cleared"], 800)

            # Drain PIO events
            while _ev_tail != _ev_head:
                ts_us = _ev_buf[_ev_tail]
                _ev_tail = (_ev_tail + 1) & (_Q_SIZE - 1)

                # Software refractory (bounce)
                if (_last_evt_us >= 0) and (time.ticks_diff(ts_us, _last_evt_us) < REFRACTORY_US):
                    ignored_bounce += 1
                    continue
                _last_evt_us = ts_us

                ts_ms = epoch_ms_from_ticks_us(ts_us)

                cur = _open[0] if _open else {"Startnummer": "-", "run": "-"}
                C.ui_post([
                    "FINISH captured!",
                    "SN #%s  Run %s" % (str(cur.get("Startnummer", "-")), str(cur.get("run", "-"))),
                    C.format_local(ts_ms, TZ_H),
                    "Uploading..."
                ], 1100)

                ok, msg = post_finish(ts_ms)
                processed_fin += 1
                C.ui_post(["FINISH " + ("OK" if ok else "FAIL"), str(msg)[:21]], 1100)

                fetch_open(force=True)

            # Light periodic GC
            if time.ticks_diff(now_ms, last_gc) > 5000:
                gc.collect()
                last_gc = now_ms

            # Periodic debug
            if time.ticks_diff(now_ms, last_dbg) > 3000:
                last_dbg = now_ms
                qlen = (_ev_head - _ev_tail) & (_Q_SIZE - 1)
                C.dbg("PIO seen=", beam_irq_count,
                      " processed=", processed_fin,
                      " ignored(bounce)=", ignored_bounce,
                      " dropped=", dropped_events,
                      " qlen=", qlen,
                      " head/tail=", _ev_head, "/", _ev_tail)

            C.ui_drain_once()
            time.sleep_ms(10)

    except KeyboardInterrupt:
        C.safe_shutdown(["KeyboardInterrupt"], sta=sta, led_pin=PIN_LED)
    except Exception as e:
        C.show_error("main", e)
        C.log_to_file(head_lines=[DEVICE_NAME, "ID " + DEVICE_ID])
        C.safe_shutdown(["Error exit"], sta=sta, led_pin=PIN_LED)

if __name__ == "__main__":
    main()
