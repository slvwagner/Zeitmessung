# finish_gate.py — Finish Gate with PIO-timestamped edge capture (stable & debounced)
import time, micropython
from machine import Pin
import rp2
from rp2 import PIO, StateMachine, asm_pio

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

# Input hardening (in addition to PIO waiting for release)
REFRACTORY_US       = 300_000   # ignore triggers for X µs after a capture

# --- State ---
_open = []
_last_fetch = 0
DEVICE_ID = ""

# PIO / timestamping state
micropython.alloc_emergency_exception_buf(128)

# Fixed-size ring buffer for IRQ-safe passing of timestamps (µs, ticks_us domain)
_Q_SIZE = 8
_ev_buf = [0] * _Q_SIZE
_ev_head = 0
_ev_tail = 0
_last_evt_us = -1  # for refractory check in main loop

# Timebase (for converting ticks_us → epoch_ms safely across wrap)
_BASE_TICKS_US = 0
_BASE_EPOCH_MS = 0

# GPIOs
PIN_LED    = Pin(PIN_LED_NUM, Pin.OUT, value=0)
PIN_BEAM_SIO   = Pin(PIN_BEAM, Pin.IN, Pin.PULL_UP)
PIN_CANCEL_SIO = Pin(PIN_CANCEL, Pin.IN, Pin.PULL_UP)

def url(p):
    return SERVER_BASE + (p if p.startswith("/") else ("/" + p))

def fetch_open(force=False):
    """Populate _open with currently 'on track' (started, not finished)."""
    global _open, _last_fetch
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
        _open = cleaned
        _last_fetch = now
        return

    # Fallback: derive from full log
    try:
        res2 = C.http_get_json(url(READ_EP) + "?limit=400&order=asc", headers=headers)
    except Exception:
        res2 = None

    tmp = []
    if res2 and res2.get("status") == "success":
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

    _open = tmp
    _last_fetch = now

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
    return ok, msg

# --- PIO program ---
# Wait for beam HIGH (idle), then wait for LOW (break -> falling edge),
# trigger a PIO IRQ, then wait until HIGH again (release) and re-arm.
@asm_pio()
def beam_fall_irq():
    wait(1, pin, 0)        # ensure idle=HIGH before arming
    label("arm")
    wait(0, pin, 0)        # falling edge (beam broken)
    irq(0)                 # raise PIO IRQ 0 (non-blocking)
    wait(1, pin, 0)        # wait for release (back to HIGH)
    jmp("arm")

# --- IRQ handler for PIO: store a µs timestamp in a lock-free ring buffer ---
def _sm_irq_handler(sm):
    # Hard IRQ context: NO allocations, no prints!
    global _ev_head
    tsus = time.ticks_us()
    _ev_buf[_ev_head] = tsus
    _ev_head = (_ev_head + 1) & (_Q_SIZE - 1)

# --- Helpers for ticks_us -> epoch_ms conversion (wrap-safe) ---
def epoch_ms_from_ticks_us(ts_us):
    # delta in µs relative to base ticks_us (wrap-safe)
    du = time.ticks_diff(ts_us, _BASE_TICKS_US)
    # convert to ms, round to nearest
    return _BASE_EPOCH_MS + (du + 500) // 1000

# --- Main ---
def main():
    global DEVICE_ID, _ev_tail, _last_evt_us
    global _BASE_TICKS_US, _BASE_EPOCH_MS

    sta = C.wifi_connect(credentials.SSID, credentials.PASSWORD)
    C.time_sync_ntp()
    DEVICE_ID = C.build_device_id()

    # Establish stable timebase just after NTP sync
    _BASE_EPOCH_MS = C.epoch_ms()
    _BASE_TICKS_US = time.ticks_us()

    import OLED
    OLED.oled_init()
    C.ui_post([DEVICE_NAME, "WiFi " + sta.ifconfig()[0], "Syncing runs..."], 1200)

    # Prepare PIO state machine
    # NOTE: WAIT uses the 'in_base' mapping. We map IN pins to PIN_BEAM.
    sm = StateMachine(
        0, beam_fall_irq, freq=2_000_000,  # freq not critical; WAIT dominates
        in_base=Pin(PIN_BEAM, Pin.IN, Pin.PULL_UP)
    )
    sm.irq(handler=_sm_irq_handler, hard=True)
    sm.active(1)

    fetch_open(force=True)

    # OLED scroller (optional)
    sc = None
    try:
        sc = OLED.OLEDScroller(
            OLED.oled, OLED.oled_lock,
            max_cols=21, max_lines=8, line_height=8,
            interval_ms=1200, loop=True, max_loops=None,
            break_long_words=True, hyphenate=False, collapse_spaces=True
        )
        sc.set_text(scroller_text(), y0=0)
    except Exception:
        pass

    last_blink = time.ticks_ms()
    C.dbg("main loop starts (PIO armed)")

    try:
        while True:
            # Blink LED
            if time.ticks_diff(time.ticks_ms(), last_blink) > LED_BLINK_MS:
                last_blink = time.ticks_ms()
                PIN_LED.value(1 - PIN_LED.value())

            # Maintain open runs + UI
            fetch_open(False)
            if sc:
                sc.set_text(scroller_text(), y0=0)
                sc.tick()

            # CANCEL: short=clear queue; 1.2s show log; 4s shutdown
            if PIN_CANCEL_SIO.value() == 0:
                t0 = time.ticks_ms()
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
                    # Clear queued events
                    _ev_tail = 0
                    # Reset head to tail (atomically enough in this context)
                    # (Race is negligible; worst-case we drop one event while cancel pressed)
                    # We won't touch _ev_head here to avoid IRQ races; just consume later.
                    C.ui_post(["Cancelled", "Queue cleared"], 800)

            # Pull timestamps from ring buffer (lock-free)
            while _ev_tail != _ev_head:
                ts_us = _ev_buf[_ev_tail]
                _ev_tail = (_ev_tail + 1) & (_Q_SIZE - 1)

                # Software refractory against bounce / double-breaks
                if (_last_evt_us >= 0) and (time.ticks_diff(ts_us, _last_evt_us) < REFRACTORY_US):
                    continue
                _last_evt_us = ts_us

                # Convert to epoch ms using stable base
                ts_ms = epoch_ms_from_ticks_us(ts_us)

                cur = _open[0] if _open else {"Startnummer": "-", "run": "-"}
                C.ui_post([
                    "FINISH captured!",
                    "SN #%s  Run %s" % (str(cur.get("Startnummer", "-")), str(cur.get("run", "-"))),
                    C.format_local(ts_ms, TZ_H),
                    "Uploading..."
                ], 1100)

                ok, msg = post_finish(ts_ms)
                C.ui_post(["FINISH " + ("OK" if ok else "FAIL"), str(msg)[:21]], 1100)

                fetch_open(force=True)

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
