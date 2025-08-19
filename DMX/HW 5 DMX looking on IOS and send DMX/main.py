from machine import Pin, ADC, Timer
from rp2 import PIO, StateMachine, asm_pio
import time
import _thread

# Simple input reader
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

# PIO-based DMX transmitter with precise timing
class PioDMXTransmitter:
    def __init__(self, sm_id=0, tx_pin=0, de_pin=2):
        self.tx_pin = tx_pin
        self.de_pin = Pin(de_pin, Pin.OUT)
        self.dmx_data = bytearray(513)
        self.dmx_data[0] = 0
        
        # PIO program for DMX data transmission
        @asm_pio(out_init=PIO.OUT_HIGH, out_shiftdir=PIO.SHIFT_RIGHT, autopull=True, pull_thresh=8)
        def dmx_pio():
            wrap_target()
            out(pins, 8)          # Output 8 bits
            set(x, 17)             # Wait for stop bits
            label("wait_stop")
            nop()         [0]
            jmp(x_dec, "wait_stop")
            wrap()
        
        # 8MHz gives 250kbps (4μs per bit)
        self.sm = StateMachine(sm_id, dmx_pio, freq=8_000_000, out_base=Pin(tx_pin))
        
    def set_channel(self, channel, value):
        if 1 <= channel <= 512:
            self.dmx_data[channel] = value & 0xFF
            
    def blackout(self):
        for i in range(1, 513):
            self.dmx_data[i] = 0
    
    def send_frame(self):
        """Send DMX frame with proper timing"""
        self.de_pin.value(1)
        
        # Generate break manually (more reliable than PIO for this)
        break_pin = Pin(self.tx_pin, Pin.OUT)
        
        # Break - 88μs low
        break_pin.low()
        start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), start) < 88:
            pass
        
        # Mark after break - 8μs high  
        break_pin.high()
        start = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), start) < 8:
            pass
        
        # Start PIO for data transmission
        self.sm.active(1)
        for i in range(513):
            self.sm.put(self.dmx_data[i])
        
        # Wait for transmission to complete
        time.sleep_ms(23)  # 513 bytes * 44μs = ~22.6ms
        
        self.sm.active(0)
        self.de_pin.value(0)

# Accurate frame rate measurement using hardware
class AccurateDMXApp:
    def __init__(self):
        self.dmx = PioDMXTransmitter(tx_pin=0, de_pin=2)
        self.inputs = SimpleInputReader()
        self.led = Pin("LED", Pin.OUT)
        
        self.frame_count = 0
        self.real_start_time = time.ticks_us()
        self.last_frame_time = self.real_start_time
        
        print("Accurate DMX Timing Test")
        print("Using hardware-based measurement")
    
    def run(self):
        """Run and measure actual hardware performance"""
        try:
            while True:
                current_time = time.ticks_us()
                
                # Read inputs
                digital, analog = self.inputs.read()
                
                # Update DMX
                for i in range(3):
                    self.dmx.set_channel(i + 1, 255 if digital[i] else 0)
                    self.dmx.set_channel(i + 4, analog[i])
                
                # Send frame and measure actual time
                frame_start = time.ticks_us()
                self.dmx.send_frame()
                frame_end = time.ticks_us()
                
                self.frame_count += 1
                self.led.value(self.frame_count % 2)
                
                # Calculate real timing
                frame_time = time.ticks_diff(frame_end, frame_start)
                total_time = time.ticks_diff(frame_end, self.real_start_time)
                actual_fps = 1000000 * self.frame_count / total_time if total_time > 0 else 0
                
                # Print accurate measurements
                if self.frame_count % 5 == 0:
                    print(f"Frame {self.frame_count}:")
                    print(f"  Frame time: {frame_time / 1000:.1f}ms")
                    print(f"  Actual FPS: {actual_fps:.1f}Hz")
                    print(f"  Theoretical max: {1000000 / frame_time:.1f}Hz")
                    print("-" * 30)
                
                # Small delay to avoid queueing
                time.sleep_ms(5)
                
        except KeyboardInterrupt:
            self.stop()
    
    def stop(self):
        self.dmx.blackout()
        self.dmx.send_frame()
        self.led.off()
        print("Test stopped")

# Single test to measure one frame accurately
def measure_single_frame():
    """Measure one DMX frame accurately"""
    dmx = PioDMXTransmitter(tx_pin=0, de_pin=2)
    inputs = SimpleInputReader()
    
    print("Measuring single DMX frame timing...")
    
    # Prepare test data
    digital, analog = inputs.read()
    for i in range(3):
        dmx.set_channel(i + 1, 255 if digital[i] else 0)
        dmx.set_channel(i + 4, analog[i])
    
    # Measure frame time
    start_time = time.ticks_us()
    dmx.send_frame()
    end_time = time.ticks_us()
    
    frame_time = time.ticks_diff(end_time, start_time)
    theoretical_fps = 1000000 / frame_time
    
    print(f"Single frame time: {frame_time}μs ({frame_time/1000:.1f}ms)")
    print(f"Theoretical maximum FPS: {theoretical_fps:.1f}Hz")
    print(f"Break+Mark: 96μs, Data: {513*44}μs = {513*44 + 96}μs total")
    
    return frame_time

# Test different approaches
def test_dmx_timing():
    """Test various DMX timing approaches"""
    print("Testing DMX Timing Methods")
    print("=" * 40)
    
    # Method 1: Direct timing
    print("1. Direct bit-banging timing:")
    dmx = PioDMXTransmitter()
    start = time.ticks_us()
    dmx.send_frame()
    end = time.ticks_us()
    direct_time = time.ticks_diff(end, start)
    print(f"   Time: {direct_time}μs, FPS: {1000000/direct_time:.1f}Hz")
    
    # Method 2: Theoretical calculation
    theoretical_time = 96 + (513 * 44)  # break+mark + data
    print(f"2. Theoretical: {theoretical_time}μs, FPS: {1000000/theoretical_time:.1f}Hz")
    
    print("=" * 40)

# Simple application that works with actual hardware limits
class WorkingDMXApp:
    def __init__(self, target_fps=30):
        self.dmx = PioDMXTransmitter(tx_pin=0, de_pin=2)
        self.inputs = SimpleInputReader()
        self.led = Pin("LED", Pin.OUT)
        
        self.target_fps = target_fps
        self.target_ms = 1000 // target_fps
        self.frame_count = 0
        self.start_time = time.ticks_ms()
        
        print(f"Working DMX App - Target: {target_fps}Hz")
    
    def run(self):
        """Simple working application"""
        try:
            while True:
                frame_start = time.ticks_ms()
                
                # Read inputs
                digital, analog = self.inputs.read()
                
                # Update DMX
                for i in range(3):
                    self.dmx.set_channel(i + 1, 255 if digital[i] else 0)
                    self.dmx.set_channel(i + 4, analog[i])
                
                # Send frame
                self.dmx.send_frame()
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
        self.dmx.send_frame()
        self.led.off()
        print("App stopped")

# Main menu
if __name__ == "__main__":
    print("DMX Timing Diagnostic Tool")
    print("1. Accurate timing measurement")
    print("2. Single frame test")
    print("3. Timing method comparison")
    print("4. Working app (30Hz target)")
    
    try:
        choice = input("Select: ").strip()
        
        if choice == "1":
            app = AccurateDMXApp()
            app.run()
        elif choice == "2":
            measure_single_frame()
        elif choice == "3":
            test_dmx_timing()
        elif choice == "4":
            app = WorkingDMXApp(target_fps=30)
            app.run()
        else:
            measure_single_frame()
            
    except KeyboardInterrupt:
        print("Stopped by user")
    except Exception as e:
        print(f"Error: {e}")4
