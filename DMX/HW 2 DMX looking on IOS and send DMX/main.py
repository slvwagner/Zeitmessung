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

class UltraDMXController:
    def __init__(self, sm_id=0, tx_pin=0, de_pin=2):
        self.tx_pin = tx_pin
        self.de_pin = Pin(de_pin, Pin.OUT)
        self.dmx_data = bytearray(513)
        self.dmx_data[0] = 0
        
        # PIO program optimized for 250kbps
        @asm_pio(out_init=PIO.OUT_HIGH, out_shiftdir=PIO.SHIFT_RIGHT, 
                 autopull=True, pull_thresh=8, fifo_join=PIO.JOIN_TX)
        def dmx_pio():
            wrap_target()
            out(pins, 8)               # Output 8 bits
            set(x, 17)                  # Wait for stop bits (36 cycles)
            label("wait_stop")
            nop()                       [0]
            jmp(x_dec, "wait_stop")
            wrap()
        
        # 8MHz gives exactly 4μs per instruction = 250kbps
        self.sm = StateMachine(sm_id, dmx_pio, freq=8_000_000, out_base=Pin(tx_pin))
        self.setup_dma()
        
    def setup_dma(self):
        """Setup DMA for automatic transmission"""
        # Create 32-bit aligned buffer for DMA
        self.dma_buffer = array.array('I', [0] * 513)
        
    def send_frame(self):
        """Send DMX frame with hardware acceleration"""
        # Generate break with precise timing (not in PIO)
        self.de_pin.value(1)
        
        # Manual break - much more precise than time.sleep_us()
        break_pin = Pin(self.tx_pin, Pin.OUT)
        break_start = time.ticks_us()
        break_pin.low()
        while time.ticks_diff(time.ticks_us(), break_start) < 88:
            pass
        
        # Mark after break
        break_pin.high()
        mark_start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), mark_start) < 8:
            pass
        
        # Start PIO state machine
        self.sm.active(1)
        
        # Feed data as fast as possible
        # This is the bottleneck - we need to optimize this loop
        for i in range(513):
            self.sm.put(self.dmx_data[i])
        
        # Calculate precise wait time: 513 bytes * 44μs = 22572μs
        transmit_start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), transmit_start) < 22572:
            pass
        
        self.sm.active(0)
        self.de_pin.value(0)
    
    def set_channel(self, channel, value):
        if 1 <= channel <= 512:
            self.dmx_data[channel] = value & 0xFF

# Ultra-fast input polling
class UltraFastInputMapper:
    def __init__(self):
        self.digital_pins = [Pin(i, Pin.IN, Pin.PULL_UP) for i in [3, 4, 5]]
        self.analog_adcs = [ADC(i) for i in range(3)]
        self.last_values = [0, 0, 0, 0, 0, 0]
    
    def read_inputs(self):
        """Extremely fast input reading"""
        digital = []
        for pin in self.digital_pins:
            digital.append(not pin.value())  # Single read, no debounce
        
        analog = []
        for i, adc in enumerate(self.analog_adcs):
            # Single read only - maximum speed
            raw = adc.read_u16()
            value = (raw * 255) // 65535
            # Very coarse hysteresis
            if abs(value - self.last_values[i + 3]) > 5:
                self.last_values[i + 3] = value
            analog.append(self.last_values[i + 3])
        
        return digital, analog

# High-performance application targeting 44Hz
class DMX44HzApp:
    def __init__(self):
        self.dmx = UltraDMXController(sm_id=0, tx_pin=0, de_pin=2)
        self.inputs = UltraFastInputMapper()
        self.status_led = Pin("LED", Pin.OUT)
        
        self.frame_count = 0
        self.start_time = time.ticks_ms()
        self.target_us = 22727  # 44Hz = 22.727ms per frame
        
        print("DMX 44Hz Ultra Performance App")
        print("Target: 44Hz refresh rate")
    
    def run(self):
        last_frame_time = time.ticks_us()
        led_state = False
        
        try:
            while True:
                current_time = time.ticks_us()
                
                # Read inputs and update DMX in the tightest loop possible
                digital, analog = self.inputs.read_inputs()
                for i, state in enumerate(digital):
                    self.dmx.set_channel(i + 1, 255 if state else 0)
                for i, value in enumerate(analog):
                    self.dmx.set_channel(i + 4, value)
                
                # Send frame at exact intervals
                if time.ticks_diff(current_time, last_frame_time) >= self.target_us:
                    frame_start = time.ticks_us()
                    self.dmx.send_frame()
                    last_frame_time = current_time
                    self.frame_count += 1
                    
                    # Toggle LED
                    led_state = not led_state
                    self.status_led.value(led_state)
                    
                    # Print stats occasionally
                    if self.frame_count % 44 == 0:  # Every second at 44Hz
                        self.print_stats()
                
        except KeyboardInterrupt:
            self.cleanup()
    
    def print_stats(self):
        elapsed = time.ticks_diff(time.ticks_ms(), self.start_time) / 1000
        fps = self.frame_count / elapsed if elapsed > 0 else 0
        print(f"Frames: {self.frame_count}, Rate: {fps:.1f} Hz")
        
        if fps >= 42:
            print("✅ Excellent! Near target 44Hz")
        elif fps >= 35:
            print("⚠️  Good, but below target")
        else:
            print("❌ Needs optimization")
    
    def cleanup(self):
        # Set all channels to 0
        for i in range(1, 513):
            self.dmx.set_channel(i, 0)
        self.dmx.send_frame()
        self.status_led.off()
        print("Clean shutdown")

# Alternative: Precompute entire frame for maximum speed
class PrecomputedDMXController(UltraDMXController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entire_frame = bytes([0]) + bytes([0] * 512)  # Start code + data
    
    def update_frame(self, digital, analog):
        """Update the entire frame at once"""
        # Convert to bytes for faster processing
        frame = bytearray(513)
        frame[0] = 0  # Start code
        
        # Digital channels
        for i, state in enumerate(digital):
            frame[i + 1] = 255 if state else 0
        
        # Analog channels  
        for i, value in enumerate(analog):
            frame[i + 4] = value
        
        self.entire_frame = bytes(frame)
    
    def send_frame_optimized(self):
        """Optimized send using precomputed frame"""
        self.de_pin.value(1)
        
        # Manual break
        break_pin = Pin(self.tx_pin, Pin.OUT)
        break_start = time.ticks_us()
        break_pin.low()
        while time.ticks_diff(time.ticks_us(), break_start) < 88:
            pass
        
        break_pin.high()
        mark_start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), mark_start) < 8:
            pass
        
        # Send entire frame
        self.sm.active(1)
        for byte in self.entire_frame:
            self.sm.put(byte)
        
        # Wait for transmission
        transmit_start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), transmit_start) < 22572:
            pass
        
        self.sm.active(0)
        self.de_pin.value(0)

# Run the optimized version
if __name__ == "__main__":
    print("Starting 44Hz DMX Optimized...")
    app = DMX44HzApp()
    app.run()
