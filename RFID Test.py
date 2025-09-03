# === RC522 UID reader (robust, no OLED) — Pico2 W, MicroPython ===
from machine import Pin, SPI
import time

# --- SPI1 pins (RC522) ---
SCK, MOSI, MISO, CS, RST = 10, 11, 12, 13, 22
spi = SPI(1, baudrate=50_000, polarity=0, phase=0,
          sck=Pin(SCK), mosi=Pin(MOSI), miso=Pin(MISO))
cs  = Pin(CS,  Pin.OUT, value=1)
rst = Pin(RST, Pin.OUT, value=1)

# --- Registers / commands ---
PCD_IDLE        = 0x00
PCD_CALCCRC     = 0x03
PCD_TRANSCEIVE  = 0x0C
PCD_SOFTRESET   = 0x0F

CommandReg      = 0x01
ComIEnReg       = 0x02
ComIrqReg       = 0x04
DivIrqReg       = 0x05
ErrorReg        = 0x06
FIFODataReg     = 0x09
FIFOLevelReg    = 0x0A
ControlReg      = 0x0C
BitFramingReg   = 0x0D
CollReg         = 0x0E
ModeReg         = 0x11
TxModeReg       = 0x12
RxModeReg       = 0x13
TxControlReg    = 0x14
TxASKReg        = 0x15
RFCfgReg        = 0x26
TModeReg        = 0x2A
TPrescalerReg   = 0x2B
TReloadRegH     = 0x2C
TReloadRegL     = 0x2D
CRCResultRegH   = 0x21
CRCResultRegL   = 0x22
VersionReg      = 0x37

# PICC
PICC_CMD_REQA   = 0x26
PICC_SEL_CL1    = 0x93
PICC_SEL_CL2    = 0x95
PICC_SEL_CL3    = 0x97

MI_OK=0; MI_ERR=2
PRINT_DEBUG = False   # set True to see SELECT retries

def _wr(r,v): cs.value(0); spi.write(bytearray([(r<<1)&0x7E, v&0xFF])); cs.value(1)
def _rd(r): cs.value(0); spi.write(bytearray([((r<<1)&0x7E)|0x80])); v=spi.read(1)[0]; cs.value(1); return v
def _set(r,m): _wr(r, (_rd(r)|m)&0xFF)
def _clr(r,m): _wr(r,  _rd(r) & (~m & 0xFF))

def antenna_on():
    if (_rd(TxControlReg) & 0x03) != 0x03:
        _set(TxControlReg, 0x03)

def init_rfid():
    rst.value(0); time.sleep_ms(10); rst.value(1); time.sleep_ms(10)
    _wr(CommandReg, PCD_SOFTRESET); time.sleep_ms(50)
    _wr(TModeReg, 0x8D)
    _wr(TPrescalerReg, 0x3E)
    _wr(TReloadRegL, 30)
    _wr(TReloadRegH, 0)
    _wr(TxASKReg, 0x40)     # 100% ASK
    _wr(ModeReg, 0x3D)      # CRC preset 0x6363
    _wr(RFCfgReg, 0x70)     # max RX gain
    _wr(TxModeReg, 0x00)    # CRC via FIFO only
    _wr(RxModeReg, 0x00)    # ISO14443A defaults
    antenna_on()
    _wr(CollReg, 0x80)      # clear collisions

def _to_card(send, wait_loops=12000, settle_us=0):
    _wr(ComIEnReg, 0x77 | 0x80)
    _clr(ComIrqReg, 0x80)
    _set(FIFOLevelReg, 0x80)     # flush FIFO
    _wr(CommandReg, PCD_IDLE)
    for b in send: _wr(FIFODataReg, b)
    if settle_us: time.sleep_us(settle_us)
    _wr(CommandReg, PCD_TRANSCEIVE)
    _set(BitFramingReg, 0x80)    # start send
    for _ in range(wait_loops):
        n = _rd(ComIrqReg)
        if (n & 0x01) or (n & 0x30):
            break
    _clr(BitFramingReg, 0x80)
    if (_rd(ErrorReg) & 0x1B) != 0:
        return MI_ERR, [], 0
    n = _rd(FIFOLevelReg)
    last = _rd(ControlReg) & 0x07
    bitlen = (n-1)*8 + last if last else n*8
    resp = [_rd(FIFODataReg) for _ in range(min(n, 16))]
    return MI_OK, resp, bitlen

def _calc_crc(data_bytes):
    _wr(CommandReg, PCD_IDLE); _set(FIFOLevelReg, 0x80)
    for b in data_bytes: _wr(FIFODataReg, b)
    _wr(CommandReg, PCD_CALCCRC)
    for _ in range(5000):
        if _rd(DivIrqReg) & 0x04: break
    return (_rd(CRCResultRegL), _rd(CRCResultRegH))  # (lsb, msb)

def reqa():
    _wr(BitFramingReg, 0x07); _wr(CollReg, 0x80)   # 7-bit REQA, clear collisions
    return _to_card([PICC_CMD_REQA])

def anticoll_level(sel_code):
    _wr(BitFramingReg, 0x00); _wr(CollReg, 0x80)
    return _to_card([sel_code, 0x20], wait_loops=8000, settle_us=20)

def anticoll_retry(sel_code, attempts=4):
    for _ in range(attempts):
        s, data, bits = anticoll_level(sel_code)
        if s == MI_OK and len(data) == 5:
            if (data[0]^data[1]^data[2]^data[3]) == data[4]:  # BCC ok
                return MI_OK, data
        time.sleep_ms(10)
    return MI_ERR, []

def select_with_crc(sel_code, uid4, tries=6):
    if len(uid4) != 4: return MI_ERR, 0
    for t in range(tries):
        _wr(BitFramingReg, 0x00)  # full-byte framing
        bcc  = uid4[0]^uid4[1]^uid4[2]^uid4[3]
        core = [sel_code, 0x70] + list(uid4) + [bcc]
        crc_lsb, crc_msb = _calc_crc(core)
        frame = core + [crc_lsb, crc_msb]
        s, resp, bits = _to_card(frame, wait_loops=12000, settle_us=80)
        # Accept SAK (1B) or SAK+CRC_A (3B). Empty resp -> soft miss, retry quietly.
        if s == MI_OK and len(resp) >= 1:
            return MI_OK, resp[0]
        if s == MI_OK and len(resp) == 0:
            time.sleep_ms(3); continue
        if PRINT_DEBUG and len(resp) > 0:
            print("DBG: SELECT fail try", t+1, "resp=", resp, "bits=", bits)
        time.sleep_ms(3)
    return MI_ERR, 0

def get_uid_bytes():
    # Single quick presence check (don’t over-gate)
    s, _, bits = reqa()
    if s != MI_OK or bits != 0x10:
        return None

    # CL1
    s, part = anticoll_retry(PICC_SEL_CL1)
    if s != MI_OK:
        return None

    uid = bytearray()
    if part[0] == 0x88:           # cascade -> 7 or 10 bytes
        uid += bytes(part[1:4])
        s, _ = select_with_crc(PICC_SEL_CL1, [0x88, uid[0], uid[1], uid[2]])
        if s != MI_OK: return None
        s, part2 = anticoll_retry(PICC_SEL_CL2)
        if s != MI_OK: return None
        if part2[0] == 0x88:      # 10-byte UID (rare)
            uid += bytes(part2[1:4])
            s, _ = select_with_crc(PICC_SEL_CL2, [0x88, part2[1], part2[2], part2[3]])
            if s != MI_OK: return None
            s, part3 = anticoll_retry(PICC_SEL_CL3)
            if s != MI_OK: return None
            uid += bytes(part3[0:4])
            s, _ = select_with_crc(PICC_SEL_CL3, [uid[-4], uid[-3], uid[-2], uid[-1]])
            if s != MI_OK: return None
        else:                      # 7-byte UID
            uid += bytes(part2[0:4])
            s, _ = select_with_crc(PICC_SEL_CL2, [uid[3], uid[4], uid[5], uid[6]])
            if s != MI_OK: return None
    else:                          # 4-byte UID
        uid += bytes(part[0:4])
        s, _ = select_with_crc(PICC_SEL_CL1, [uid[0], uid[1], uid[2], uid[3]])
        if s != MI_OK: return None

    return bytes(uid)

def to_hex(b): return ":".join(f"{x:02X}" for x in b)

# --- Run test ---
init_rfid()
ver = _rd(VersionReg)
print("RC522 VersionReg =", hex(ver), "(0x91/0x92 typical; 0x82 common on clones)")
print("Present a tag…")

last_uid = None
while True:
    uid = get_uid_bytes()
    if uid:
        hx = to_hex(uid)
        if hx != last_uid:
            print("UID:", hx, f"({len(uid)} bytes)")
            last_uid = hx
        # wait until tag is removed so we don’t spam
        while True:
            s2, _, b2 = reqa()
            if not (s2 == MI_OK and b2 == 0x10):
                break
            time.sleep_ms(80)
    time.sleep_ms(40)

