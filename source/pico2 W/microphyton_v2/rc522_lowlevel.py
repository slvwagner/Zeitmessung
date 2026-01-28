# rc522_lowlevel.py — compact MFRC522 low-level (SPI1) for Pico
import time
from machine import Pin, SPI

PCD_IDLE=0x00; PCD_CALCCRC=0x03; PCD_TRANSCEIVE=0x0C; PCD_SOFTRESET=0x0F
CommandReg=0x01; ComIEnReg=0x02; ComIrqReg=0x04; DivIrqReg=0x05; ErrorReg=0x06
FIFODataReg=0x09; FIFOLevelReg=0x0A; ControlReg=0x0C; BitFramingReg=0x0D; CollReg=0x0E
ModeReg=0x11; TxModeReg=0x12; RxModeReg=0x13; TxControlReg=0x14; TxASKReg=0x15
RFCfgReg=0x26; TModeReg=0x2A; TPrescalerReg=0x2B; TReloadRegH=0x2C; TReloadRegL=0x2D
CRCResultRegH=0x21; CRCResultRegL=0x22; VersionReg=0x37
PICC_CMD_REQA=0x26; PICC_SEL_CL1=0x93; PICC_SEL_CL2=0x95; PICC_SEL_CL3=0x97
MI_OK=0; MI_ERR=2

class RC522LL:
    def __init__(self, spi_id=1, sck=10, mosi=11, miso=12, cs=13, rst=22, baud=50_000):
        self.spi = SPI(spi_id, baudrate=baud, polarity=0, phase=0,
                       sck=Pin(sck), mosi=Pin(mosi), miso=Pin(miso))
        self.cs  = Pin(cs, Pin.OUT, value=1)
        self.rst = Pin(rst, Pin.OUT, value=1)
        self._init_chip()

    def _wr(self, r, v):
        self.cs.value(0); self.spi.write(bytearray([(r<<1)&0x7E, v&0xFF])); self.cs.value(1)
    def _rd(self, r):
        self.cs.value(0); self.spi.write(bytearray([((r<<1)&0x7E)|0x80])); v=self.spi.read(1)[0]; self.cs.value(1); return v
    def _set(self, r, m): self._wr(r, (self._rd(r)|m)&0xFF)
    def _clr(self, r, m): self._wr(r,  self._rd(r) & (~m & 0xFF))

    def _init_chip(self):
        self.rst.value(0); time.sleep_ms(10); self.rst.value(1); time.sleep_ms(10)
        self._wr(CommandReg, PCD_SOFTRESET); time.sleep_ms(50)
        self._wr(TModeReg, 0x8D); self._wr(TPrescalerReg, 0x3E)
        self._wr(TReloadRegL, 30); self._wr(TReloadRegH, 0)
        self._wr(TxASKReg, 0x40); self._wr(ModeReg, 0x3D)
        self._wr(RFCfgReg, 0x70); self._wr(TxModeReg, 0x00); self._wr(RxModeReg, 0x00)
        if (self._rd(TxControlReg) & 0x03) != 0x03: self._set(TxControlReg, 0x03)
        self._wr(CollReg, 0x80)

    def _to_card(self, send, wait_loops=12000, settle_us=0):
        self._wr(ComIEnReg, 0x77 | 0x80)
        self._clr(ComIrqReg, 0x80)
        self._set(FIFOLevelReg, 0x80)
        self._wr(CommandReg, PCD_IDLE)
        for b in send: self._wr(FIFODataReg, b)
        if settle_us: time.sleep_us(settle_us)
        self._wr(CommandReg, PCD_TRANSCEIVE)
        self._set(BitFramingReg, 0x80)
        for _ in range(wait_loops):
            n = self._rd(ComIrqReg)
            if (n & 0x01) or (n & 0x30): break
        self._clr(BitFramingReg, 0x80)
        if (self._rd(ErrorReg) & 0x1B) != 0: return MI_ERR, [], 0
        n = self._rd(FIFOLevelReg)
        last = self._rd(ControlReg) & 0x07
        bitlen = (n-1)*8 + last if last else n*8
        resp = [self._rd(FIFODataReg) for _ in range(min(n, 16))]
        return MI_OK, resp, bitlen

    def _calc_crc(self, data_bytes):
        self._wr(CommandReg, PCD_IDLE); self._set(FIFOLevelReg, 0x80)
        for b in data_bytes: self._wr(FIFODataReg, b)
        self._wr(CommandReg, PCD_CALCCRC)
        for _ in range(5000):
            if self._rd(DivIrqReg) & 0x04: break
        return (self._rd(CRCResultRegL), self._rd(CRCResultRegH))

    def _reqa(self):
        self._wr(BitFramingReg, 0x07); self._wr(CollReg, 0x80)
        return self._to_card([PICC_CMD_REQA])

    def _anticoll_lvl(self, sel_code):
        self._wr(BitFramingReg, 0x00); self._wr(CollReg, 0x80)
        return self._to_card([sel_code, 0x20], wait_loops=8000, settle_us=20)

    def _anticoll_retry(self, sel_code, attempts=4):
        for _ in range(attempts):
            s, data, _ = self._anticoll_lvl(sel_code)
            if s == MI_OK and len(data) == 5 and ((data[0]^data[1]^data[2]^data[3]) == data[4]):
                return MI_OK, data
            time.sleep_ms(10)
        return MI_ERR, []

    def _select_crc(self, sel_code, uid4, tries=6):
        if len(uid4) != 4: return MI_ERR, 0
        for _ in range(tries):
            self._wr(BitFramingReg, 0x00)
            bcc  = uid4[0]^uid4[1]^uid4[2]^uid4[3]
            core = [sel_code, 0x70] + list(uid4) + [bcc]
            crcl, crch = self._calc_crc(core)
            frame = core + [crcl, crch]
            s, resp, _ = self._to_card(frame, wait_loops=12000, settle_us=80)
            if s == MI_OK and len(resp) >= 1: return MI_OK, resp[0]
            time.sleep_ms(3)
        return MI_ERR, 0

    def get_uid(self):
        s, _, bits = self._reqa()
        if s != MI_OK or bits != 0x10: return None
        s, part = self._anticoll_retry(PICC_SEL_CL1)
        if s != MI_OK: return None
        uid = bytearray()
        if part[0] == 0x88:
            uid += bytes(part[1:4])
            s, _ = self._select_crc(PICC_SEL_CL1, [0x88, uid[0], uid[1], uid[2]])
            if s != MI_OK: return None
            s, part2 = self._anticoll_retry(PICC_SEL_CL2)
            if s != MI_OK: return None
            if part2[0] == 0x88:
                uid += bytes(part2[1:4])
                s, _ = self._select_crc(PICC_SEL_CL2, [0x88, part2[1], part2[2], part2[3]])
                if s != MI_OK: return None
                s, part3 = self._anticoll_retry(PICC_SEL_CL3)
                if s != MI_OK: return None
                uid += bytes(part3[0:4])
                s, _ = self._select_crc(PICC_SEL_CL3, [uid[-4], uid[-3], uid[-2], uid[-1]])
                if s != MI_OK: return None
            else:
                uid += bytes(part2[0:4])
                s, _ = self._select_crc(PICC_SEL_CL2, [uid[3], uid[4], uid[5], uid[6]])
                if s != MI_OK: return None
        else:
            uid += bytes(part[0:4])
            s, _ = self._select_crc(PICC_SEL_CL1, [uid[0], uid[1], uid[2], uid[3]])
            if s != MI_OK: return None
        return bytes(uid)

def uid4_display_hex(uid_bytes):
    b = bytes(uid_bytes or b"")
    if len(b) < 4: return None
    return "%02X:%02X:%02X:%02X" % (b[0], b[1], b[2], b[3])
