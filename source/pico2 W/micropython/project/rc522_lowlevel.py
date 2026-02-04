# rc522_lowlevel_v2.py — Ultra-stable MFRC522 driver
import time
import gc
from machine import Pin, SPI

# Register definitions
PCD_IDLE = 0x00
PCD_CALCCRC = 0x03
PCD_TRANSCEIVE = 0x0C
PCD_SOFTRESET = 0x0F

CommandReg = 0x01
ComIEnReg = 0x02
ComIrqReg = 0x04
DivIrqReg = 0x05
ErrorReg = 0x06
FIFODataReg = 0x09
FIFOLevelReg = 0x0A
ControlReg = 0x0C
BitFramingReg = 0x0D
CollReg = 0x0E
ModeReg = 0x11
TxModeReg = 0x12
RxModeReg = 0x13
TxControlReg = 0x14
TxASKReg = 0x15
RFCfgReg = 0x26
TModeReg = 0x2A
TPrescalerReg = 0x2B
TReloadRegH = 0x2C
TReloadRegL = 0x2D
CRCResultRegH = 0x21
CRCResultRegL = 0x22
VersionReg = 0x37

# PICC commands
PICC_CMD_REQA = 0x26
PICC_SEL_CL1 = 0x93
PICC_SEL_CL2 = 0x95
PICC_SEL_CL3 = 0x97

# Status codes
MI_OK = 0
MI_ERR = 2


class RC522LL:
    def __init__(self, spi_id=1, sck=10, mosi=11, miso=12, cs=13, rst=22, baud=50_000):
        self.spi_id = spi_id
        self.spi_pins = {'sck': sck, 'mosi': mosi, 'miso': miso}
        self.cs_pin = cs
        self.rst_pin = rst
        self.baud = baud
        
        self.cs = None
        self.rst = None
        self.spi = None
        
        # Memory-efficient buffers (reused)
        self._uid_buffer = bytearray(10)
        self._temp_buffer = bytearray(16)
        self._spi_write_buf = bytearray(2)
        self._spi_read_buf = bytearray(1)
        
        # Statistics
        self.scan_count = 0
        self.error_count = 0
        self.last_error = None
        
        self._init_hardware()
        gc.collect()
        
        print(f"RC522LL v2 initialized, mem_free: {gc.mem_free()}")

    def _init_hardware(self):
        """Initialize all hardware with error recovery"""
        # Initialize GPIO pins
        try:
            if self.cs:
                self.cs.init(Pin.OUT, value=1)
            else:
                self.cs = Pin(self.cs_pin, Pin.OUT, value=1)
                
            if self.rst:
                self.rst.init(Pin.OUT, value=1)
            else:
                self.rst = Pin(self.rst_pin, Pin.OUT, value=1)
        except Exception as e:
            print(f"GPIO init error: {e}")
            raise
        
        # Initialize SPI
        self._init_spi()
        
        # Initialize chip
        self._init_chip()

    def _init_spi(self):
        """Initialize or reinitialize SPI with robust error handling"""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                # Close existing SPI if any
                if self.spi:
                    try:
                        self.spi.deinit()
                        time.sleep_ms(10)
                    except:
                        pass
                
                # Create new SPI instance
                self.spi = SPI(
                    self.spi_id,
                    baudrate=self.baud,
                    polarity=0,
                    phase=0,
                    sck=Pin(self.spi_pins['sck']),
                    mosi=Pin(self.spi_pins['mosi']),
                    miso=Pin(self.spi_pins['miso'])
                )
                
                # Test SPI communication
                self.cs.value(0)
                self.spi.write(b'\x00')
                self.cs.value(1)
                
                return True
                
            except Exception as e:
                self.last_error = f"SPI init attempt {attempt+1}: {e}"
                self.error_count += 1
                
                if attempt == max_attempts - 1:
                    print(f"SPI init failed after {max_attempts} attempts")
                    raise
                
                time.sleep_ms(50)
                gc.collect()
        
        return False

    def deinit(self):
        """Clean up all resources"""
        try:
            if self.spi:
                self.spi.deinit()
        except:
            pass
        
        self.spi = None
        gc.collect()
        print("RC522LL deinitialized")

    def _reinit(self):
        """Complete reinitialization after errors"""
        print("Reinitializing RC522...")
        self.deinit()
        time.sleep_ms(100)
        self._init_hardware()
        time.sleep_ms(50)
        print("Reinitialization complete")

    def _wr(self, r, v, retries=3):
        """Write register with robust error recovery"""
        self._spi_write_buf[0] = (r << 1) & 0x7E
        self._spi_write_buf[1] = v & 0xFF
        
        for attempt in range(retries):
            try:
                self.cs.value(0)
                self.spi.write(self._spi_write_buf)
                self.cs.value(1)
                return True
                
            except Exception as e:
                self.last_error = f"SPI write error: {e}"
                self.error_count += 1
                
                if attempt == retries - 1:
                    raise
                
                # Reinitialize SPI and retry
                self._reinit()
                time.sleep_ms(10)
        
        return False

    def _rd(self, r, retries=3):
        """Read register with robust error recovery"""
        self._spi_write_buf[0] = ((r << 1) & 0x7E) | 0x80
        
        for attempt in range(retries):
            try:
                self.cs.value(0)
                self.spi.write(self._spi_write_buf[0:1])
                self.spi.readinto(self._spi_read_buf)
                self.cs.value(1)
                return self._spi_read_buf[0]
                
            except Exception as e:
                self.last_error = f"SPI read error: {e}"
                self.error_count += 1
                
                if attempt == retries - 1:
                    raise
                
                # Reinitialize SPI and retry
                self._reinit()
                time.sleep_ms(10)
        
        return 0

    def _set(self, r, m):
        """Set bits in register"""
        current = self._rd(r)
        self._wr(r, (current | m) & 0xFF)

    def _clr(self, r, m):
        """Clear bits in register"""
        current = self._rd(r)
        self._wr(r, current & (~m & 0xFF))

    def _init_chip(self):
        """Initialize MFRC522 chip with error handling"""
        try:
            # Hardware reset
            self.rst.value(0)
            time.sleep_ms(20)
            self.rst.value(1)
            time.sleep_ms(20)
            
            # Soft reset
            self._wr(CommandReg, PCD_SOFTRESET)
            time.sleep_ms(100)
            
            # Wait for oscillator stabilization
            for _ in range(100):
                if self._rd(CommandReg) & 0x10 == 0:
                    break
                time.sleep_ms(1)
            
            # Timer settings
            self._wr(TModeReg, 0x8D)
            self._wr(TPrescalerReg, 0x3E)
            self._wr(TReloadRegL, 30)
            self._wr(TReloadRegH, 0)
            
            # Modulation settings
            self._wr(TxASKReg, 0x40)
            self._wr(ModeReg, 0x3D)
            
            # RF settings
            self._wr(RFCfgReg, 0x70)
            self._wr(TxModeReg, 0x00)
            self._wr(RxModeReg, 0x00)
            
            # Enable antenna
            self._set(TxControlReg, 0x03)
            
            # Clear collision register
            self._wr(CollReg, 0x80)
            
            # Clear any pending IRQs
            self._clr(ComIrqReg, 0x80)
            
            print(f"Chip initialized, version: 0x{self.get_version():02X}")
            
        except Exception as e:
            print(f"Chip init error: {e}")
            raise

    def _to_card(self, send, wait_loops=10000, settle_us=0):
        """Send data to card and receive response"""
        # Clear buffers and IRQs
        self._wr(ComIEnReg, 0x77 | 0x80)
        self._clr(ComIrqReg, 0x80)
        self._set(FIFOLevelReg, 0x80)
        self._wr(CommandReg, PCD_IDLE)
        
        # Write data to FIFO
        for b in send:
            self._wr(FIFODataReg, b)
        
        # Settle time if needed
        if settle_us:
            time.sleep_us(settle_us)
        
        # Start transmission
        self._wr(CommandReg, PCD_TRANSCEIVE)
        self._set(BitFramingReg, 0x80)
        
        # Wait for completion with timeout
        start_time = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start_time) < 100:  # 100ms timeout
            n = self._rd(ComIrqReg)
            if (n & 0x01) or (n & 0x30):  # Timer IRQ or Tx/Rx done
                break
            time.sleep_us(100)  # Small sleep to prevent tight loop
        
        self._clr(BitFramingReg, 0x80)
        
        # Check for errors
        if (self._rd(ErrorReg) & 0x1B) != 0:
            return MI_ERR, [], 0
        
        # Read response
        n = self._rd(FIFOLevelReg)
        if n == 0:
            return MI_ERR, [], 0
            
        last = self._rd(ControlReg) & 0x07
        bitlen = (n - 1) * 8 + last if last else n * 8
        
        # Read data into temp buffer
        read_len = min(n, len(self._temp_buffer))
        for i in range(read_len):
            self._temp_buffer[i] = self._rd(FIFODataReg)
        
        return MI_OK, self._temp_buffer[:read_len], bitlen

    def _calc_crc(self, data_bytes):
        """Calculate CRC for given data"""
        self._wr(CommandReg, PCD_IDLE)
        self._set(FIFOLevelReg, 0x80)
        
        for b in data_bytes:
            self._wr(FIFODataReg, b)
        
        self._wr(CommandReg, PCD_CALCCRC)
        
        # Wait for CRC calculation with timeout
        start_time = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start_time) < 100:
            if self._rd(DivIrqReg) & 0x04:
                break
            time.sleep_us(100)
        
        return self._rd(CRCResultRegL), self._rd(CRCResultRegH)

    def _reqa(self):
        """Send REQA command (wake up cards)"""
        self._wr(BitFramingReg, 0x07)
        self._wr(CollReg, 0x80)
        return self._to_card([PICC_CMD_REQA])

    def _anticoll_lvl(self, sel_code):
        """Anti-collision for one level"""
        self._wr(BitFramingReg, 0x00)
        self._wr(CollReg, 0x80)
        return self._to_card([sel_code, 0x20], wait_loops=5000, settle_us=20)

    def _anticoll_retry(self, sel_code, attempts=3):
        """Anti-collision with retries"""
        for attempt in range(attempts):
            s, data, _ = self._anticoll_lvl(sel_code)
            if s == MI_OK and len(data) >= 5:
                # Verify BCC
                bcc = data[0] ^ data[1] ^ data[2] ^ data[3]
                if bcc == data[4]:
                    return MI_OK, data[:5]
            time.sleep_ms(10)
        return MI_ERR, []

    def _select_crc(self, sel_code, uid4, tries=4):
        """Select card with CRC check"""
        if len(uid4) != 4:
            return MI_ERR, 0
        
        for attempt in range(tries):
            self._wr(BitFramingReg, 0x00)
            bcc = uid4[0] ^ uid4[1] ^ uid4[2] ^ uid4[3]
            
            # Build select frame
            frame = [sel_code, 0x70] + list(uid4) + [bcc]
            
            # Calculate CRC
            crcl, crch = self._calc_crc(frame)
            frame += [crcl, crch]
            
            # Send frame
            s, resp, _ = self._to_card(frame, wait_loops=8000, settle_us=80)
            
            if s == MI_OK and len(resp) >= 1:
                return MI_OK, resp[0]
            
            time.sleep_ms(3)
        
        return MI_ERR, 0

    def get_uid(self):
        """Get UID of present card (returns bytes or None)"""
        self.scan_count += 1
        
        try:
            # Clear buffer
            self._uid_buffer[:] = b'\x00' * len(self._uid_buffer)
            
            # Wake up cards
            s, _, bits = self._reqa()
            if s != MI_OK or bits != 0x10:
                return None
            
            # Level 1 anti-collision
            s, part = self._anticoll_retry(PICC_SEL_CL1)
            if s != MI_OK:
                return None
            
            # Check for 7-byte UID (Cascade tag)
            if part[0] == 0x88:
                # 7-byte UID
                self._uid_buffer[0:3] = part[1:4]
                
                # Select cascade level 1
                s, _ = self._select_crc(PICC_SEL_CL1, [0x88, part[1], part[2], part[3]])
                if s != MI_OK:
                    return None
                
                # Level 2 anti-collision
                s, part2 = self._anticoll_retry(PICC_SEL_CL2)
                if s != MI_OK:
                    return None
                
                if part2[0] == 0x88:
                    # Double cascade (10-byte UID, rare)
                    self._uid_buffer[3:6] = part2[1:4]
                    
                    # Select cascade level 2
                    s, _ = self._select_crc(PICC_SEL_CL2, [0x88, part2[1], part2[2], part2[3]])
                    if s != MI_OK:
                        return None
                    
                    # Level 3 anti-collision
                    s, part3 = self._anticoll_retry(PICC_SEL_CL3)
                    if s != MI_OK:
                        return None
                    
                    self._uid_buffer[6:10] = part3[0:4]
                    
                    # Select cascade level 3
                    s, _ = self._select_crc(PICC_SEL_CL3, [self._uid_buffer[6], self._uid_buffer[7], 
                                                          self._uid_buffer[8], self._uid_buffer[9]])
                    if s != MI_OK:
                        return None
                    
                    return bytes(self._uid_buffer[0:10])
                else:
                    # 7-byte UID
                    self._uid_buffer[3:7] = part2[0:4]
                    
                    # Select cascade level 2
                    s, _ = self._select_crc(PICC_SEL_CL2, [self._uid_buffer[3], self._uid_buffer[4], 
                                                          self._uid_buffer[5], self._uid_buffer[6]])
                    if s != MI_OK:
                        return None
                    
                    return bytes(self._uid_buffer[0:7])
            else:
                # 4-byte UID
                self._uid_buffer[0:4] = part[0:4]
                
                # Select card
                s, _ = self._select_crc(PICC_SEL_CL1, [part[0], part[1], part[2], part[3]])
                if s != MI_OK:
                    return None
                
                return bytes(self._uid_buffer[0:4])
                
        except Exception as e:
            self.last_error = f"get_uid error at scan {self.scan_count}: {e}"
            self.error_count += 1
            
            # Don't crash, just return None
            if self.error_count > 10:
                print(f"Too many errors ({self.error_count}), reinitializing...")
                self._reinit()
                self.error_count = 0
            
            return None

    def get_version(self):
        """Get chip version for debugging"""
        try:
            return self._rd(VersionReg)
        except:
            return 0
    
    def get_stats(self):
        """Get statistics for debugging"""
        return {
            'scans': self.scan_count,
            'errors': self.error_count,
            'last_error': self.last_error,
            'memory_free': gc.mem_free()
        }


def uid4_display_hex(uid_bytes):
    b = bytes(uid_bytes or b"")
    if len(b) < 4: return None
    return "%02X:%02X:%02X:%02X" % (b[0], b[1], b[2], b[3])


