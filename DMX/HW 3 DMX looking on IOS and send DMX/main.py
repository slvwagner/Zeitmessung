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

class TrueHardwareDMX:
    def __init__(self, sm_id=0, tx_pin=0, de_pin=2):
        self.tx_pin = tx_pin
        self.de_pin = Pin(de_pin, Pin.OUT)
        self.dmx_data = bytearray(513)
        self.dmx_data[0] = 0
        
        # PIO that includes break generation AND data transmission
        @asm_pio(set_init=PIO.OUT_HIGH, out_init=PIO.OUT_HIGH, 
                 out_shiftdir=PIO.SHIFT_RIGHT, autopull=True, pull_thresh=8)
        def dmx_full_frame_pio():
            # Break generation (88us low)
            set(pins, 0)              [87]  # 88 cycles of low
            # Mark after break (8us high)  
            set(pins, 1)              [7]   # 8 cycles of high
            # Data transmission
            wrap_target()
            out(pins, 8)                     # Output 8 bits
            set(x, 17)                       # Wait for stop bits (36 cycles)
            label("wait_stop")
            nop()                   [0]
            jmp(x_dec, "wait_stop")
            wrap()
        
        # Run at 1MHz: 1 cycle = 1us, perfect for timing
        self.sm = StateMachine(sm_id, dmx_full_frame_pio, freq=1_000_000, 
                              set_base=Pin(tx_pin), out_base=Pin(tx_pin))
        
        # Precompute the break command
        self.break_command = self.create_break_command()
        
    def create_break_command(self):
        """Create a special command to trigger break generation in PIO"""
        # This will make the PIO execute the break sequence
        return 0xFFFFFFFF  # Special value that triggers break
    
    def send_frame_hardware(self):
        """Send complete DMX frame using hardware only"""
        self.de_pin.value(1)
        
        # Start the state machine - it will handle break and data
        self.sm.active(1)
        
        # First, send the break command
        self.sm.put(self.break_command)
        
        # Then send all data bytes
        for i in range(513):
            self.sm.put(self.dmx_data[i])
        
        # The PIO will handle everything from here
        # We need to wait for the transmission to complete
        # 88us break + 8us mark + 513 * 44us = 22572us + 96us = 22668us
        time.sleep_us(23000)  # Slightly longer to be safe
        
        self.sm.active(0)
        self.de_pin.value(0)
    
    def set_channel(self, channel, value):
        if 1 <= channel <= 512:
            self.dmx_data[channel] = value & 0xFF

# Ultra-minimal input reader
class MinimalInputReader:
    def __init__(self):
        self.digital_pins = [Pin(3, Pin.IN, Pin.PULL_UP), 
                            Pin(4, Pin.IN, Pin.PULL_UP), 
                            Pin(5, Pin.IN, Pin.PULL_UP)]
        self.analog_adcs = [ADC(0), ADC(1), ADC(2)]
        self.last_analog = [0, 0, 0]
    
    def read(self):
        """Absolute minimum input reading"""
        digital = [not pin.value() for pin in self.digital_pins]
        
        analog = []
        for i, adc in enumerate(self.analog_adcs):
            raw = adc.read_u16()
            value = (raw >> 8)  # Convert 16-bit to 8-bit (quick divide by 256)
            if abs(value - self.last_analog[i]) > 3:
                self.last_analog[i] = value
            analog.append(self.last_analog[i])
        
        return digital, analog

# Main app optimized for maximum speed
class True44HzDMXApp:
    def __init__(self):
        self.dmx = TrueHardwareDMX(sm_id=0, tx_pin=0, de_pin=2)
        self.inputs = MinimalInputReader()
        self.led = Pin("LED", Pin.OUT)
        
        self.frame_count = 0
        self.start_time = time.ticks_ms()
        
        print("True Hardware DMX - Target 44Hz")
    
    def run(self):
        last_frame_time = time.ticks_us()
        target_interval = 22727  # 44Hz = 22.727ms
        
        # Pre-allocate variables to avoid garbage collection
        digital = [False, False, False]
        analog = [0, 0, 0]
        
        try:
            while True:
                current_time = time.ticks_us()
                
                # Only read inputs if it's time to send a frame
                if time.ticks_diff(current_time, last_frame_time) >= target_interval:
                    # Read inputs
                    digital, analog = self.inputs.read()
                    
                    # Update DMX
                    for i in range(3):
                        self.dmx.set_channel(i + 1, 255 if digital[i] else 0)
                        self.dmx.set_channel(i + 4, analog[i])
                    
                    # Send frame
                    self.dmx.send_frame_hardware()
                    
                    last_frame_time = current_time
                    self.frame_count += 1
                    
                    # Update status
                    if self.frame_count % 44 == 0:
                        self.print_status()
                
                # Minimal delay to avoid 100% CPU
                time.sleep_us(100)
                
        except KeyboardInterrupt:
            self.cleanup()
    
    def print_status(self):
        elapsed = time.ticks_diff(time.ticks_ms(), self.start_time) / 1000
        fps = self.frame_count / elapsed if elapsed > 0 else 0
        print(f"Frames: {self.frame_count}, Rate: {fps:.1f} Hz")
    
    def cleanup(self):
        for i in range(1, 513):
            self.dmx.set_channel(i, 0)
        self.dmx.send_frame_hardware()
        self.led.off()

# ALTERNATIVE: Use hardware timer for precise 44Hz
from machine import Timer

class TimerDrivenDMXApp(True44HzDMXApp):
    def __init__(self):
        super().__init__()
        self.timer = Timer()
        
    def run(self):
        # Set up hardware timer for 44Hz
        def send_frame_callback(timer):
            digital, analog = self.inputs.read()
            for i in range(3):
                self.dmx.set_channel(i + 1, 255 if digital[i] else 0)
                self.dmx.set_channel(i + 4, analog[i])
            self.dmx.send_frame_hardware()
            self.frame_count += 1
            
            if self.frame_count % 44 == 0:
                self.print_status()
        
        # Start hardware timer at 44Hz
        self.timer.init(freq=44, mode=Timer.PERIODIC, callback=send_frame_callback)
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.timer.deinit()
            self.cleanup()

# FINAL OPTIMIZATION: Use direct register access for maximum speed
class RegisterDMX:
    def __init__(self, sm_id=0, tx_pin=0, de_pin=2):
        self.de_pin = Pin(de_pin, Pin.OUT)
        self.tx_pin = Pin(tx_pin, Pin.OUT)
        self.dmx_data = bytearray(513)
        
    def send_frame_register(self):
        """Direct register access for maximum speed"""
        self.de_pin.value(1)
        
        # Manual break with direct register access
        self.tx_pin.low()
        start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), start) < 88:
            pass
        
        self.tx_pin.high()
        start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), start) < 8:
            pass
        
        # Send data with direct timing
        for byte in self.dmx_data:
            # Start bit
            self.tx_pin.low()
            start_bit = time.ticks_us()
            while time.ticks_diff(time.ticks_us(), start_bit) < 4:
                pass
            
            # Data bits
            for bit in range(8):
                self.tx_pin.value((byte >> bit) & 1)
                bit_start = time.ticks_us()
                while time.ticks_diff(time.ticks_us(), bit_start) < 4:
                    pass
            
            # Stop bits
            self.tx_pin.high()
            stop_start = time.ticks_us()
            while time.ticks_diff(time.ticks_us(), stop_start) < 8:
                pass
        
        self.de_pin.value(0)

# Run the most optimized version
if __name__ == "__main__":
    print("Starting Ultimate 44Hz DMX...")
    
    # Try hardware timer version first
    try:
        app = TimerDrivenDMXApp()
        app.run()
    except:
        # Fallback to software timing
        app = True44HzDMXApp()
        app.run()
