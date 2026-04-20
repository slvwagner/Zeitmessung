
# --- Imports (MicroPython) ---
try:
    import utime as time          # MicroPython
except ImportError:
    import time                   # fallback (CPython)

from machine import Pin, I2C
import framebuf
import sys
import _thread  # only if you want thread-safe drawing (you do)

# --- Globals / defaults used in your code ---
I2C_ID     = 0
# I2C_FREQ   = 400_000 # save speed: 50_000
I2C_FREQ   = 50_000 # save speed: 50_000
OLED_WIDTH = 128
OLED_HEIGHT= 64
OLED_CONTROLLER = "SSD1309"

# I2C and OLED handles
i2c  = None
oled = None

# lock for thread-safe OLED access
oled_lock = _thread.allocate_lock()

# Debounce/diff state for oled_text()
_last_oled_frame = None
_last_oled_ts    = 0

# --- I2C recovery + safe write helpers ---
def _i2c_bus_recover(scl_pin=5, sda_pin=4):
    scl = Pin(scl_pin, Pin.OPEN_DRAIN, value=1)
    sda = Pin(sda_pin, Pin.OPEN_DRAIN, value=1)
    time.sleep_ms(1)
    if sda.value() == 0:
        # clock SCL to release a stuck slave
        for _ in range(18):
            scl.value(0); time.sleep_us(5)
            scl.value(1); time.sleep_us(5)
        # generate a STOP
        sda.value(0); time.sleep_us(5)
        scl.value(1); time.sleep_us(5)
        sda.value(1); time.sleep_us(5)

def _i2c_write_with_retries(i2c, addr, payload, retries=3, pause_us=50):
    for _ in range(retries):
        try:
            i2c.writeto(addr, payload)
            if pause_us: time.sleep_us(pause_us)
            return True
        except OSError:
            time.sleep_ms(2)
    return False

# ----------------------------------------------------------------------
# SAFE OLED drivers
# ----------------------------------------------------------------------
class OLED_I2C_SLOW:
    def __init__(self, width, height, i2c, addr=0x3C, external_vcc=False):
        self.width = width
        self.height = height
        self.pages = self.height // 8
        self.i2c = i2c
        self.addr = addr
        self.external_vcc = external_vcc
        self.buffer = bytearray(self.width * self.pages)
        self.fb = framebuf.FrameBuffer(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self._init_display()

    # framebuf passthrough
    def fill(self, c): self.fb.fill(c)
    def pixel(self, x, y, c): self.fb.pixel(x, y, c)
    def hline(self, x, y, w, c): self.fb.hline(x, y, w, c)
    def vline(self, x, y, h, c): self.fb.vline(x, y, h, c)
    def line(self, x1, y1, x2, y2, c): self.fb.line(x1, y1, x2, y2, c)
    def rect(self, x, y, w, h, c): self.fb.rect(x, y, w, h, c)
    def fill_rect(self, x, y, w, h, c): self.fb.fill_rect(x, y, w, h, c)
    def text(self, s, x, y, c=1): self.fb.text(s, x, y, c)

    def _cmd(self, *cmds):
        for c in cmds:
            ok = _i2c_write_with_retries(self.i2c, self.addr, bytes([0x80, c]), retries=3, pause_us=50)
            if not ok:
                raise OSError("I2C CMD timeout")

    def _data(self, buf):
        CHUNK = 16  # was 64 — smaller chunks are safer
        i = 0
        b_len = len(buf)
        while i < b_len:
            n = CHUNK if (i + CHUNK) <= b_len else (b_len - i)
            payload = b"\x40" + buf[i:i+n]
            ok = _i2c_write_with_retries(self.i2c, self.addr, payload, retries=3, pause_us=50)
            if not ok:
                raise OSError("I2C DATA timeout")
            i += n

    def _init_display(self):
        raise NotImplementedError("OLED_I2C_SLOW subclasses must implement _init_display()")

    def show(self):
        self._cmd(0x21, 0, self.width - 1)
        self._cmd(0x22, 0, self.pages - 1)
        self._data(self.buffer)

class SSD1306_I2C_SAFE(OLED_I2C_SLOW):
    def _init_display(self):
        self._cmd(0xAE, 0xD5, 0x80, 0xA8, self.height - 1, 0xD3, 0x00,
                  0x40, 0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12,
                  0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6)
        self.fill(0); self.show(); time.sleep_ms(10)
        self._cmd(0xAF)

class SSD1309_I2C_SAFE(OLED_I2C_SLOW):
    def _init_display(self):
        # SSD1309 is largely SSD1306-compatible, but 4-pin breakout boards
        # typically provide the panel high-voltage rail on-board, so we unlock
        # the controller first and skip the SSD1306 charge-pump command.
        self._cmd(0xFD, 0x12, 0xAE, 0xD5, 0x70, 0xA8, self.height - 1, 0xD3, 0x00,
                  0x40, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0x7F,
                  0xD9, 0x22, 0xDB, 0x34, 0xA4, 0xA6, 0x2E)
        self.fill(0); self.show(); time.sleep_ms(10)
        self._cmd(0xAF)

OLED_DRIVERS = {
    "SSD1306": SSD1306_I2C_SAFE,
    "SSD1309": SSD1309_I2C_SAFE,
}

def _oled_driver_order():
    preferred = OLED_CONTROLLER.upper()
    order = [preferred] if preferred in OLED_DRIVERS else []
    for name in OLED_DRIVERS:
        if name not in order:
            order.append(name)
    return order

def _build_oled_driver(i2c_handle, addr):
    last_error = None
    for controller in _oled_driver_order():
        driver_cls = OLED_DRIVERS[controller]
        try:
            print("Trying OLED controller:", controller)
            return driver_cls(OLED_WIDTH, OLED_HEIGHT, i2c_handle, addr=addr), controller
        except Exception as e:
            last_error = e
            print("OLED controller failed:", controller, e)
    if last_error:
        raise last_error
    raise OSError("No OLED driver available")

# ----------------------------------------------------------------------
# OLED helpers
# ----------------------------------------------------------------------
# ---- Add near your OLED helpers ----
class NullOLED:
    def fill(self, *_): pass
    def text(self, *_, **__): pass
    def show(self): pass
    def hline(self, *_, **__): pass
    def vline(self, *_, **__): pass
    def line(self, *_, **__): pass
    def rect(self, *_, **__): pass
    def fill_rect(self, *_, **__): pass

def have_real_oled():
    return (oled is not None) and (not isinstance(oled, NullOLED))

def oled_init():
    global i2c, oled
    try:
        # try once
        i2c = I2C(I2C_ID, sda=Pin(4), scl=Pin(5), freq=I2C_FREQ)  # 50 kHz already set
        time.sleep_ms(50)
        devices = i2c.scan()
        print("I2C scan ->", [hex(d) for d in devices] if devices else "[]")
        if 0x3C not in devices and not devices:
            # try recovery if nothing responds
            print("I2C recover…")
            _i2c_bus_recover(5, 4)
            i2c = I2C(I2C_ID, sda=Pin(4), scl=Pin(5), freq=I2C_FREQ)
            time.sleep_ms(50)
            devices = i2c.scan()
            print("I2C scan(retry) ->", [hex(d) for d in devices] if devices else "[]")

        if not devices:
            print("No OLED detected; continuing without display.")
            oled = NullOLED()
            return

        addr = 0x3C if 0x3C in devices else devices[0]
        print("Using OLED addr:", hex(addr))
        oled, controller = _build_oled_driver(i2c, addr)

        # quick test pattern
        oled.fill(0); oled.text("OLED ready", 0, 0); oled.show(); time.sleep_ms(80)
        _oled_force_text(["OLED ready", controller, f"Addr {hex(addr)}", "I2C0 GP4/GP5"])

    except Exception as e:
        print("OLED init error:", e)
        try:
            # one more attempt after recovery
            print("Retry after recover…")
            _i2c_bus_recover(5, 4)
            i2c = I2C(I2C_ID, sda=Pin(4), scl=Pin(5), freq=I2C_FREQ)
            time.sleep_ms(50)
            devices = i2c.scan(); print("I2C scan(2) ->", [hex(d) for d in devices] if devices else "[]")
            addr = 0x3C if 0x3C in devices else (devices[0] if devices else 0x3C)
            oled, controller = _build_oled_driver(i2c, addr)
            _oled_force_text(["OLED recovered", controller, f"Addr {hex(addr)}"])
        except Exception as e2:
            print("OLED still not responding:", e2)
            oled = NullOLED()

def _oled_force_text(lines):
    if not oled: return
    oled.fill(0); y = 0
    for s in lines[:8]:
        oled.text(str(s)[:21], 0, y); y += 8
    oled.show()

def oled_clear():
    if not oled: return
    oled.fill(0); oled.show()

def _frames_equal(a, b): return a is not None and b is not None and a == b

def oled_text(lines, y0=0, min_interval_ms=120):
    global _last_oled_frame, _last_oled_ts
    if not oled: return
    now = time.ticks_ms()
    if time.ticks_diff(now, _last_oled_ts) < min_interval_ms: return
    frame = [str(s)[:21] for s in lines[:8]]
    if _frames_equal(frame, _last_oled_frame): return
    oled_lock.acquire()
    try:
        oled.fill(0); y = y0
        for s in frame:
            oled.text(s, 0, y); y += 8
        oled.show()
        _last_oled_frame, _last_oled_ts = frame, now
    finally:
        oled_lock.release()

class OLEDWriter:
    def __init__(self, oled, max_cols=21, max_lines=8, line_height=8):
        self.oled = oled
        self.max_cols = max_cols
        self.max_lines = max_lines
        self.line_height = line_height

    def draw_text(self, text, x=0, y=0):
        """
        Writes text with auto-wrapping to OLED.
        Supports \n newlines in text.
        """

        if self.oled is None or isinstance(self.oled, NullOLED):
          # No real OLED; just skip drawing.
          return

        lines = []

        # split incoming text into words
        for rawline in text.split("\n"):
            line = ""
            for word in rawline.split(" "):
                if not line:
                    line = word
                elif len(line) + 1 + len(word) <= self.max_cols:
                    line += " " + word
                else:
                    lines.append(line)
                    line = word
            if line: lines.append(line)

        # render to OLED
        self.oled.fill(0)
        yy = y
        for idx, l in enumerate(lines[:self.max_lines]):
            self.oled.text(l[:self.max_cols], x, yy)
            yy += self.line_height
        self.oled.show()

# Thread save OLED text scroller
# Thread-safe OLED text scroller with word-aware wrapping
class OLEDScroller:
    def __init__(self, oled, oled_lock=None, max_cols=16, max_lines=8, line_height=8,
                 interval_ms=1500, loop=True, max_loops=None,
                 break_long_words=True, hyphenate=False, collapse_spaces=True):
        """
        oled        : your SSD1306 object
        oled_lock   : optional _thread lock for safe access
        max_cols    : chars per line (16 for 128px with default 8px font)
        max_lines   : lines per screen (8 for 64px height)
        line_height : pixels per line (8 for default font)
        interval_ms : scroll interval in milliseconds
        loop        : whether to loop when reaching end
        max_loops   : how many full scroll cycles before stopping (None = infinite)
        break_long_words : if True, words longer than max_cols are split
        hyphenate        : if True, add '-' when breaking long words
        collapse_spaces  : if True, collapse consecutive spaces/tabs into single spaces
        """
        # imports for both MP/CP
        try:
            import utime as _time
        except ImportError:
            import time as _time
        self._time = _time

        self.oled = oled
        self.oled_lock = oled_lock
        self.max_cols = max_cols
        self.max_lines = max_lines
        self.line_height = line_height
        self.interval_ms = interval_ms
        self.loop = loop
        self.max_loops = max_loops
        self.break_long_words = break_long_words
        self.hyphenate = hyphenate
        self.collapse_spaces = collapse_spaces

        self._lines = []
        self._offset = 0
        self._last_ts = self._time.ticks_ms()
        self._loops_done = 0
        self._done = False
        self._y0 = 0

    def set_text(self, text_or_list, y0=0):
        """Set new text (string or list of strings). Resets scroller state."""
        self._y0 = y0
        text = "\n".join(text_or_list) if isinstance(text_or_list, (list, tuple)) else str(text_or_list)
        self._lines = self._wrap_text(text)
        self._offset = 0
        self._last_ts = self._time.ticks_ms()
        self._loops_done = 0
        self._done = (len(self._lines) <= self.max_lines)
        self._draw()

    def tick(self):
        """Call this frequently (e.g., in your main loop). Advances every interval_ms."""
        if self._done:
            return
        if len(self._lines) <= self.max_lines:
            self._done = True
            return

        now = self._time.ticks_ms()
        if self._time.ticks_diff(now, self._last_ts) >= self.interval_ms:
            self._last_ts = now
            if self._offset + self.max_lines < len(self._lines):
                self._offset += 1
            else:
                self._loops_done += 1
                if self.max_loops is not None and self._loops_done >= self.max_loops:
                    self._done = True
                    return
                if self.loop:
                    self._offset = 0
            self._draw()

    @property
    def done(self):
        """True if scrolling is finished."""
        return self._done

    # ---- internal helpers ----
    def _wrap_text(self, text):
        """Greedy word-wrap with support for explicit newlines."""
        # Split on explicit newlines first (preserve empty lines)
        paragraphs = text.split("\n")
        lines = []

        for p in paragraphs:
            if self.collapse_spaces:
                # collapse tabs and multiple spaces
                p = " ".join(p.replace("\t", " ").split())

            if p == "":
                # preserve blank line
                lines.append("")
                continue

            words = p.split(" ")
            current = ""

            for w in words:
                if not w:
                    continue  # skip accidental empties

                # If current line is empty, try to place word directly
                if current == "":
                    if len(w) <= self.max_cols:
                        current = w
                    else:
                        # word longer than line
                        self._emit_broken_word(lines, w)
                        current = ""
                    continue

                # Consider adding " word"
                if len(current) + 1 + len(w) <= self.max_cols:
                    current += " " + w
                else:
                    # current line is full—emit it
                    lines.append(current)
                    # start new line with w (or break if too long)
                    if len(w) <= self.max_cols:
                        current = w
                    else:
                        self._emit_broken_word(lines, w)
                        current = ""

            # flush remainder
            if current != "":
                lines.append(current)

        # ensure at least one line
        if not lines:
            lines = [""]

        return lines

    def _emit_broken_word(self, lines, word):
        """Split a single long word across lines according to settings."""
        if not self.break_long_words:
            # best-effort: place as its own line truncated
            lines.append(word[:self.max_cols])
            return

        # chunk the word
        if self.hyphenate and self.max_cols > 1:
            width = self.max_cols - 1  # leave space for '-'
            i = 0
            n = len(word)
            while i < n:
                chunk = word[i:i+width]
                i += width
                if i < n:
                    lines.append(chunk + "-")
                else:
                    lines.append(chunk)
        else:
            for i in range(0, len(word), self.max_cols):
                lines.append(word[i:i+self.max_cols])

    def _draw(self):
        """Draw current window of lines to OLED."""
        if not self.oled:
            return
        window = self._lines[self._offset:self._offset + self.max_lines]
        # pad window so the screen is always filled
        while len(window) < self.max_lines:
            window.append("")

        if self.oled_lock:
            self.oled_lock.acquire()
        try:
            self.oled.fill(0)
            y = self._y0
            for l in window:
                # defensive: truncate to max_cols so we never overflow
                self.oled.text(l[:self.max_cols], 0, y)
                y += self.line_height
            self.oled.show()
        finally:
            if self.oled_lock:
                self.oled_lock.release()
