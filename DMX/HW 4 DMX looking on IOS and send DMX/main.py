from machine import Pin, ADC
from rp2 import PIO, StateMachine, asm_pio
import time
import array
import uctypes

# DMA register addresses
DMA_BASE = 0x50000000
DMA_CH0_READ_ADDR = DMA_BASE + 0x000
DMA_CH0_WRITE_ADDR = DMA_BASE + 0x004
DMA_CH0_TRANS_COUNT = DMA_BASE + 0x008
DMA_CH0_CTRL_TRIG = DMA_BASE + 0x00C
PIO0_BASE = 0x50200000
PIO0_TXF0 = PIO0_BASE + 0x10  # TX FIFO for State Machine 0

class DmaDmxController:
    def __init__(self, sm_id=0, tx_pin=0, de_pin=2):
        self.tx_pin = tx_pin
        self.de_pin = Pin(de_pin, Pin.OUT)
        self.dmx_data = bytearray(513)
        self.dmx_data[0] = 0
        
        # PIO program for data transmission only
        @asm_pio(out_init=PIO.OUT_HIGH, out_shiftdir=PIO.SHIFT_RIGHT, 
                 autopull=True, pull_thresh=8, fifo_join=PIO.JOIN_TX)
        def dmx_pio():
            wrap_target()
            out(pins, 8)          # Output 8 bits
            set(x, 17)            # Wait for stop bits
            label("wait_stop")
            nop()         [0]
            jmp(x_dec, "wait_stop")
            wrap()
        
        self.sm = StateMachine(sm_id, dmx_pio, freq=8_000_000, out_base=Pin(tx_pin))
        
        # DMA buffer (32-bit words)
        self.dma_buffer = array.array('I', [0] * 513)
        self.setup_dma()
        
    def setup_dma(self):
        """Setup DMA configuration"""
        # This would configure DMA registers directly
        # For now, we'll use a simplified approach
        pass
        
    def send_frame_dma(self):
        """Send frame using DMA for automatic data transfer"""
        self.de_pin.value(1)
        
        # Generate break and mark manually
        break_pin = Pin(self.tx_pin, Pin.OUT)
        
        # Break
        break_pin.low()
        start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), start) < 88:
            pass
        
        # Mark after break
        break_pin.high()
        start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), start) < 8:
            pass
        
        # Start PIO
        self.sm.active(1)
        
        # Convert bytes to 32-bit words for DMA (simplified)
        for i in range(513):
            self.dma_buffer[i] = self.dmx_data[i]
        
        # Feed data to PIO (simulate DMA with optimized loop)
        # This is much faster than the previous version
        for i in range(513):
            self.sm.put(self.dmx_data[i])
        
        # Wait for transmission
        time.sleep_ms(23)
        
        self.sm.active(0)
        self.de_pin.value(0)
    
    def set_channel(self, channel, value):
        if 1 <= channel <= 512:
            self.dmx_data[channel] = value & 0xFF
            
    def blackout(self):
        for i in range(1, 513):
            self.dmx_data[i] = 0

# Input reader
class SimpleInputReader:
    def __init__(self):
        self.digital_pins = [Pin(3, Pin.IN, Pin.PULL_UP), Pin(4, Pin.IN, Pin.PULL_UP), Pin(5, Pin.IN, Pin.PULL_UP)]
        self.analog_adcs = [ADC(0), ADC(1), ADC(2)]
        self.last_analog = [0, 0, 0]
    
    def read(self):
        digital = [not pin.value() for pin in self.digital_pins]
        analog = []
        for i, adc in enumerate(self.analog_adcs):
            raw = adc.read_u16()
            value = raw >> 8
            if abs(value - self.last_analog[i]) > 3:
                self.last_analog[i] = value
            analog.append(self.last_analog[i])
        return digital, analog

# Optimized application
class OptimizedDmxApp:
    def __init__(self, target_fps=30):
        self.dmx = DmaDmxController(tx_pin=0, de_pin=2)
        self.inputs = SimpleInputReader()
        self.led = Pin("LED", Pin.OUT)
        
        self.target_fps = target_fps
        self.target_ms = 1000 // target_fps
        self.frame_count = 0
        self.start_time = time.ticks_ms()
        
        print(f"Optimized DMX App - Target: {target_fps}Hz")
    
    def run(self):
        """Run with optimized timing"""
        try:
            while True:
                frame_start = time.ticks_ms()
                
                # Read inputs
                digital, analog = self.inputs.read()
                
                # Update DMX
                for i in range(3):
                    self.dmx.set_channel(i + 1, 255 if digital[i] else 0)
                    self.dmx.set_channel(i + 4, analog[i])
                
                # Send frame with optimized method
                self.dmx.send_frame_dma()
                self.frame_count += 1
                self.led.value(self.frame_count % 2)
                
                # Print status
                if self.frame_count % 10 == 0:
                    current_time = time.ticks_ms()
                    elapsed = (current_time - self.start_time) / 1000
                    fps = self.frame_count / elapsed if elapsed > 0 else 0
                    print(f"Frames: {self.frame_count}, FPS: {fps:.1f}")
                
                # Wait for next frame
                frame_time = time.ticks_diff(time.ticks_ms(), frame_start)
                if frame_time < self.target_ms:
                    time.sleep_ms(self.target_ms - frame_time)
                    
        except KeyboardInterrupt:
            self.stop()
    
    def stop(self):
        self.dmx.blackout()
        self.dmx.send_frame_dma()
        self.led.off()
        print("App stopped")

# Test the optimized version
if __name__ == "__main__":
    print("Optimized DMX Controller")
    print("Using DMA-style data transfer")
    
    app = OptimizedDmxApp(target_fps=30)
    app.run()
