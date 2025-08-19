from machine import Pin, ADC
from rp2 import PIO, StateMachine, asm_pio
import time
import array

# DMX Controller with corrected PIO timing
class DMXController:
    def __init__(self, sm_id=0, tx_pin=0, de_pin=2):
        self.tx_pin = tx_pin
        self.de_pin = Pin(de_pin, Pin.OUT)
        self.dmx_data = bytearray(513)  # 512 channels + start code
        self.dmx_data[0] = 0  # Start code
        
        # Initialize PIO State Machine with proper timing
        self.sm = StateMachine(sm_id, self.dmx_tx_pio, freq=1_000_000, out_base=Pin(tx_pin))
        
    @asm_pio(out_init=PIO.OUT_LOW, out_shiftdir=PIO.SHIFT_RIGHT, autopull=True, pull_thresh=8)
    def dmx_tx_pio():
        # DMX transmitter PIO program - simplified timing
        wrap_target()
        # Send data bytes (break and mark are handled outside PIO)
        out(pins, 8)          # Output 8 bits
        # Wait for stop bits (44 cycles at 1MHz = 44μs for 2 stop bits at 250kbps)
        set(x, 43)            
        label("wait_stop")
        nop()                [0]
        jmp(x_dec, "wait_stop")
        wrap()
    
    def send_frame(self):
        """Send DMX frame with proper break timing"""
        # Generate break and mark manually for precise timing
        self.de_pin.value(1)  # Enable transmitter
        
        # Manual break generation (more reliable)
        break_pin = Pin(self.tx_pin, Pin.OUT)
        break_pin.low()
        time.sleep_us(88)     # DMX break duration (88μs)
        break_pin.high()
        time.sleep_us(8)      # Mark after break (8μs)
        
        # Start PIO for data transmission
        self.sm.active(1)
        for i in range(513):
            self.sm.put(self.dmx_data[i])
        
        # Wait for transmission completion (approx 22ms for 513 bytes)
        time.sleep_ms(25)
        
        self.de_pin.value(0)  # Disable transmitter
        self.sm.active(0)
    
    def set_channel(self, channel, value):
        """Set DMX channel value (1-512)"""
        if 1 <= channel <= 512:
            self.dmx_data[channel] = value & 0xFF
    
    def set_multiple_channels(self, start_channel, values):
        """Set multiple channels"""
        for i, value in enumerate(values):
            channel = start_channel + i
            if 1 <= channel <= 512:
                self.dmx_data[channel] = value & 0xFF
    
    def blackout(self):
        """Set all channels to 0"""
        for i in range(1, 513):
            self.dmx_data[i] = 0

# Input Polling and DMX Mapping Class
class InputDMXMapper:
    def __init__(self):
        # Digital Inputs (Buttons/Switches)
        self.digital_inputs = [
            Pin(3, Pin.IN, Pin.PULL_UP),   # GPIO3
            Pin(4, Pin.IN, Pin.PULL_UP),   # GPIO4  
            Pin(5, Pin.IN, Pin.PULL_UP)    # GPIO5
        ]
        
        # Analog Inputs (Pots/ Sensors)
        self.analog_inputs = [
            ADC(0),  # GPIO26
            ADC(1),  # GPIO27
            ADC(2)   # GPIO28
        ]
        
        # DMX output channels for each input
        self.dmx_mapping = {
            'digital': [1, 2, 3],    # DMX channels for digital inputs
            'analog': [4, 5, 6]      # DMX channels for analog inputs
        }
        
        # Input states
        self.last_digital_states = [False, False, False]
        self.last_analog_values = [0, 0, 0]
        
        # Debouncing
        self.debounce_counters = [0, 0, 0]
        self.debounce_threshold = 3
        
    def read_digital_inputs(self):
        """Read digital inputs with debouncing"""
        current_states = []
        for i, pin in enumerate(self.digital_inputs):
            current_state = not pin.value()  # Invert since we're using pull-up
            
            # Simple debounce logic
            if current_state != self.last_digital_states[i]:
                self.debounce_counters[i] += 1
                if self.debounce_counters[i] >= self.debounce_threshold:
                    self.last_digital_states[i] = current_state
                    self.debounce_counters[i] = 0
            else:
                self.debounce_counters[i] = 0
            
            current_states.append(self.last_digital_states[i])
        
        return current_states
    
    def read_analog_inputs(self):
        """Read analog inputs with smoothing"""
        current_values = []
        for i, adc in enumerate(self.analog_inputs):
            # Simple average for smoothing
            value1 = adc.read_u16()
            time.sleep_us(100)
            value2 = adc.read_u16()
            time.sleep_us(100)
            value3 = adc.read_u16()
            
            avg_value = (value1 + value2 + value3) // 3
            # Scale 16-bit ADC to 8-bit DMX (0-255)
            dmx_value = (avg_value * 255) // 65535
            
            # Simple hysteresis to prevent jitter
            if abs(dmx_value - self.last_analog_values[i]) > 3:
                self.last_analog_values[i] = dmx_value
            
            current_values.append(self.last_analog_values[i])
        
        return current_values
    
    def map_to_dmx(self, dmx_controller):
        """Map input values to DMX channels"""
        # Read inputs
        digital_states = self.read_digital_inputs()
        analog_values = self.read_analog_inputs()
        
        # Map digital inputs (on/off)
        for i, state in enumerate(digital_states):
            channel = self.dmx_mapping['digital'][i]
            value = 255 if state else 0  # Full on or off
            dmx_controller.set_channel(channel, value)
        
        # Map analog inputs (0-255)
        for i, value in enumerate(analog_values):
            channel = self.dmx_mapping['analog'][i]
            dmx_controller.set_channel(channel, value)
        
        return digital_states, analog_values

# Main Application
class DMXControlApp:
    def __init__(self):
        # Initialize DMX controller
        self.dmx = DMXController(sm_id=0, tx_pin=0, de_pin=2)
        
        # Initialize input mapper
        self.input_mapper = InputDMXMapper()
        
        # Status LED
        self.status_led = Pin("LED", Pin.OUT)
        
        # Transmission rate (Hz)
        self.transmit_rate = 30  # DMX frames per second
        self.transmit_interval = 1000 // self.transmit_rate
        
        # Statistics
        self.frame_count = 0
        self.start_time = time.ticks_ms()
        
        print("DMX Control Application Initialized")
        print("Digital inputs: GPIO3,4,5")
        print("Analog inputs: GPIO26,27,28")
        print("DMX output: GPIO0 (TX) with GPIO2 (DE)")
        print(f"Transmission rate: {self.transmit_rate} Hz")
    
    def run(self):
        """Main application loop"""
        last_transmit_time = time.ticks_ms()
        led_state = False
        
        try:
            while True:
                current_time = time.ticks_ms()
                
                # Read inputs and map to DMX
                digital_states, analog_values = self.input_mapper.map_to_dmx(self.dmx)
                
                # Transmit DMX at specified rate
                if time.ticks_diff(current_time, last_transmit_time) >= self.transmit_interval:
                    self.dmx.send_frame()
                    last_transmit_time = current_time
                    self.frame_count += 1
                    
                    # Toggle status LED
                    led_state = not led_state
                    self.status_led.value(led_state)
                
                # Print status periodically
                if self.frame_count % 30 == 0:
                    self.print_status(digital_states, analog_values)
                
                # Small delay to prevent CPU hogging
                time.sleep_ms(10)
                
        except KeyboardInterrupt:
            self.cleanup()
        except Exception as e:
            print(f"Unexpected error: {e}")
            self.cleanup()
    
    def print_status(self, digital_states, analog_values):
        """Print current status"""
        elapsed = time.ticks_diff(time.ticks_ms(), self.start_time) / 1000
        fps = self.frame_count / elapsed if elapsed > 0 else 0
        
        print("\n=== DMX Control Status ===")
        print(f"Frames: {self.frame_count}, Rate: {fps:.1f} Hz")
        print("Digital: ", end="")
        for i, state in enumerate(digital_states):
            print(f"{i+1}:{'ON' if state else 'OFF'} ", end="")
        print("\nAnalog:  ", end="")
        for i, value in enumerate(analog_values):
            print(f"{i+1}:{value:3d} ", end="")
        print("\nDMX:     ", end="")
        for i in range(1, 7):
            value = self.dmx.dmx_data[i]
            print(f"{i}:{value:3d} ", end="")
        print()
    
    def cleanup(self):
        """Clean up before exit"""
        print("\nShutting down...")
        self.dmx.blackout()
        self.dmx.send_frame()
        time.sleep(0.1)  # Ensure last frame is sent
        self.status_led.off()
        print("All channels set to 0. Safe to disconnect.")

# Alternative simpler version without PIO if still having issues
class SimpleDMXController:
    def __init__(self, tx_pin=0, de_pin=2):
        self.tx_pin = Pin(tx_pin, Pin.OUT)
        self.de_pin = Pin(de_pin, Pin.OUT)
        self.dmx_data = bytearray(513)
        self.dmx_data[0] = 0
        
    def send_frame(self):
        """Send DMX frame using bit-banging"""
        self.de_pin.value(1)  # Enable transmitter
        
        # Send break (88μs low)
        self.tx_pin.low()
        time.sleep_us(88)
        
        # Send mark after break (8μs high)
        self.tx_pin.high()
        time.sleep_us(8)
        
        # Send data (250kbps = 4μs per bit)
        for byte in self.dmx_data:
            # Start bit (low)
            self.tx_pin.low()
            time.sleep_us(4)
            
            # Data bits (LSB first)
            for bit in range(8):
                self.tx_pin.value((byte >> bit) & 1)
                time.sleep_us(4)
            
            # Stop bits (2 bits high)
            self.tx_pin.high()
            time.sleep_us(8)
        
        self.de_pin.value(0)  # Disable transmitter
    
    def set_channel(self, channel, value):
        if 1 <= channel <= 512:
            self.dmx_data[channel] = value & 0xFF
    
    def blackout(self):
        for i in range(1, 513):
            self.dmx_data[i] = 0

# Test the application
if __name__ == "__main__":
    print("Starting DMX Control Application...")
    # Test pattern - uncomment to verify DMX output
    # self.dmx.set_channel(1, 255)  # Channel 1 always on
    # self.dmx.set_channel(7, 128)  # Channel 7 at 50%
    # Try PIO version first, fall back to simple version if needed
    try:
        app = DMXControlApp()
        app.run()
    except Exception as e:
        print(f"PIO version failed: {e}")
        print("Falling back to simple bit-banging version...")
        
        # Use simple version
        simple_dmx = SimpleDMXController(tx_pin=0, de_pin=2)
        input_mapper = InputDMXMapper()
        
        try:
            while True:
                digital, analog = input_mapper.map_to_dmx(simple_dmx)
                simple_dmx.send_frame()
                time.sleep(0.033)  # ~30Hz
        except KeyboardInterrupt:
            simple_dmx.blackout()
            simple_dmx.send_frame()
            print("Simple version stopped")