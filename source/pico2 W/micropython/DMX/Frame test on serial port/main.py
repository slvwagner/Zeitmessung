from machine import Pin, UART, Timer
import time

DMX_channels = 10		# Numer of chanels 
DNX_refresch_rate = 50 	# Hz

start_code = 0x00

class DMXController:
    def __init__(self, tx_pin=0, channels=512, refresh_rate=44):
        """
        Initialize DMX controller with continuous transmission
        Uses single UART initialization for better reliability
        """
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = refresh_rate
        
        # Store TX pin number for break generation
        self.tx_pin_num = tx_pin
        
        # Initialize UART once - this is the proper way
        # DMX uses 250000 baud, 8 data bits, 2 stop bits, no parity
        self.uart = UART(0, baudrate=250000, bits=8, stop=2, parity=None, tx=Pin(tx_pin))
        
        # Initialize channel data (all channels start at 0)
        self.dmx_data = bytearray([0] * (self.channels))
                
        # Add start code at beginning (0 for DMX512-A)
        self.frame = bytearray([0]) + self.dmx_data
        self.frame[0] = start_code
        
        # Control flags
        self.transmitting = False
        self.timer = Timer()
        
        # Pre-allocate break pattern for direct UART control
        # We'll use a different method to generate break without UART deinit
        
        print(f"DMX Controller initialized with {self.channels} channels")
        print(f"Refresh rate: {refresh_rate} Hz")
        print(f"TX Pin: {tx_pin}")
        
    def set_channel(self, channel, value):
        """Set a single DMX channel value"""
        if 1 <= channel <= self.channels:
            self.dmx_data[channel - 1] = max(0, min(255, value))
            self._update_frame()
            print(f"Channel {channel} set to {value}")
        else:
            print(f"Error: Channel {channel} out of range (1-{self.channels})")
    
    def set_channels(self, values_dict):
        """Set multiple DMX channels at once"""
        for channel, value in values_dict.items():
            if 1 <= channel <= self.channels:
                self.dmx_data[channel - 1] = max(0, min(255, value))
        self._update_frame()
        print(f"Updated {len(values_dict)} channel(s)")
    
    def _update_frame(self):
        """Update the DMX frame with current channel data"""
        self.frame = bytearray([0]) + self.dmx_data
        
        self.frame[0] = start_code
    
    def _send_break(self):
        """
        Generate DMX break using UART break control
        This is the proper way without deinitializing UART
        """
        # Method 1: Use UART break if available
        # Some MicroPython implementations have sendbreak()
        if hasattr(self.uart, 'sendbreak'):
            # Send break (typically 92-100us at 250kbps)
            self.uart.sendbreak()
            # Small delay after break
            time.sleep_us(5)  # Mark-after-break
        else:
            # Method 2: Alternative using baudrate change (less reliable)
            # This is a workaround but still better than full reinit
            original_baud = self.uart.baudrate
            self.uart.baudrate = 50000  # Lower baudrate to create longer bits
            # Write 0x00 to create a longer low period
            self.uart.write(b'\x00')
            self.uart.baudrate = original_baud
            time.sleep_us(10)
    
    def _send_frame(self, timer):
        """
        Send a complete DMX frame
        Called by timer for continuous transmission
        """
        # Send break and mark-after-break
        self._send_break()
        
        # Send the complete frame (start code + channel data)
        # UART is already configured and ready
        self.uart.write(self.frame)
    
    def start(self):
        """Start continuous DMX transmission"""
        if self.transmitting:
            print("DMX transmission already running")
            return
        
        self.transmitting = True
        print(f"Starting continuous DMX transmission at {self.refresh_rate} Hz")
        
        # Start timer to trigger frame transmission
        self.timer.init(
            freq=self.refresh_rate,
            mode=Timer.PERIODIC,
            callback=self._send_frame
        )
    
    def stop(self):
        """Stop continuous DMX transmission"""
        if not self.transmitting:
            print("DMX transmission not running")
            return
        
        self.timer.deinit()
        self.transmitting = False
        print("DMX transmission stopped")
    
    def show_status(self):
        """Display current status"""
        print("\nDMX Status:")
        for i in range(min(10, self.channels)):
            print(f"  Channel {i+1}: {self.dmx_data[i]}")
        if self.channels > 10:
            print(f"  ... and {self.channels - 10} more channels")
        print(f"Transmission: {'Running' if self.transmitting else 'Stopped'}")
        print(f"Refresh rate: {self.refresh_rate} Hz")
    
    def clear_all(self):
        """Set all channels to 0"""
        for i in range(self.channels):
            self.dmx_data[i] = 0
        self._update_frame()
        print("All channels cleared to 0")


# Interactive interface
def interactive_dmx():
    """Interactive DMX controller with proper UART handling"""
    print("\n" + "="*60)
    print("DMX512 Controller - Proper UART Implementation")
    print("="*60)
    print("UART is initialized once for reliable operation")
    print("="*60)
    
    # Initialize controller with single UART init
    dmx = DMXController(tx_pin=0, channels=DMX_channels, refresh_rate=DNX_refresch_rate)
    
    print("\nCommands:")
    print("  s [channel] [value]     - Set single channel")
    print("  m [ch1:val1,ch2:val2]  - Set multiple channels")
    print("  start                   - Start continuous transmission")
    print("  stop                    - Stop continuous transmission")
    print("  status                  - Show current status")
    print("  clear                   - Clear all channels")
    print("  exit                    - Exit program")
    print("="*60)
    
    # Auto-start transmission
    dmx.start()
    
    while True:
        try:
            cmd = input("\nDMX> ").strip().lower()
            
            if cmd == "exit":
                dmx.stop()
                print("Exiting DMX controller")
                break
                
            elif cmd.startswith("s "):
                parts = cmd.split()
                if len(parts) == 3:
                    channel = int(parts[1])
                    value = int(parts[2])
                    dmx.set_channel(channel, value)
                    
            elif cmd.startswith("m "):
                try:
                    values_str = cmd[2:]
                    pairs = values_str.split(',')
                    values = {}
                    for pair in pairs:
                        ch, val = pair.split(':')
                        values[int(ch)] = int(val)
                    dmx.set_channels(values)
                except Exception as e:
                    print(f"Error: {e}")
                    
            elif cmd == "start":
                dmx.start()
                
            elif cmd == "stop":
                dmx.stop()
                
            elif cmd == "status":
                dmx.show_status()
                
            elif cmd == "clear":
                dmx.clear_all()
                
        except Exception as e:
            print(f"Error: {e}")

# Run the controller
if __name__ == "__main__":
    print("DMX512 Controller - Single UART Initialization")
    print("UART is configured once for reliable DMX transmission")
    interactive_dmx()
    



