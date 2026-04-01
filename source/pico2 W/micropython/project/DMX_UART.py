# DMX512 transmitter
# MicroPython v1.27.0 — Raspberry Pi Pico W (RP2350)

from machine import Pin, UART, Timer
import time

DMX_channels = 320		# Number of channels 
DMX_refresh_rate = 50 	# Hz
DMX_BREAK_US = 92
DMX_MAB_US = 12

start_code = 0x00

class DMXController:
    def __init__(self, tx_pin=0, channels=512, refresh_rate=44):
        """
        Initialize DMX controller with proper UART handling
        """
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = refresh_rate
        
        # Store TX pin number for break generation
        self.tx_pin_num = tx_pin
        self.break_us = DMX_BREAK_US
        self.mab_us = DMX_MAB_US
        
        # Initialize UART once - this is the proper way
        # DMX uses 250000 baud, 8 data bits, 2 stop bits, no parity
        self.uart = UART(0, baudrate=250000, bits=8, stop=2, parity=None, tx=Pin(tx_pin))
        
        # Initialize channel data (all channels start at 0)
        self.dmx_data = bytearray([0] * (self.channels))
                
        # Add start code at beginning (0 for DMX512-A)
        self.frame = bytearray([start_code]) + bytearray([0] * self.channels)
        
        # Control flags
        self.transmitting = False
        self.timer = Timer()
        
        # Pre-allocate break pattern for direct UART control
        # We'll use a different method to generate break without UART deinit
        
        print(f"DMX Controller initialized with {self.channels} channels")
        print(f"Refresh rate: {refresh_rate} Hz")
        print(f"TX Pin: {tx_pin}")

    def _delay_us_exact(self, duration_us):
        """Busy-wait delay for tighter timing than sleep_us() in callbacks."""
        t0 = time.ticks_us()
        while time.ticks_diff(time.ticks_us(), t0) < duration_us:
            pass
        
    def set_channel(self, channel, value):
        """Set a single DMX channel value"""
        if 1 <= channel <= self.channels:
            clamped = max(0, min(255, value))
            self.dmx_data[channel - 1] = clamped
            self._update_frame([channel])
            print(f"Channel {channel} set to {value}")
        else:
            print(f"Error: Channel {channel} out of range (1-{self.channels})")
    
    def set_channels(self, values_dict):
        """Set multiple DMX channels at once"""
        updated_channels = []
        for channel, value in values_dict.items():
            if 1 <= channel <= self.channels:
                self.dmx_data[channel - 1] = max(0, min(255, value))
                updated_channels.append(channel)
        if updated_channels:
            self._update_frame(updated_channels)
        print(f"Updated {len(updated_channels)} channel(s)")

    def set_all(self, value):
        """Set all DMX channels to the same value"""
        clamped = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i] = clamped
        self._update_frame()
        print(f"All channels set to {clamped}")
    
    def _update_frame(self, channels=None):
        """Update the DMX frame with current channel data"""
        if channels is None:
            for i in range(self.channels):
                self.frame[i + 1] = self.dmx_data[i]
        else:
            for channel in channels:
                if 1 <= channel <= self.channels:
                    self.frame[channel] = self.dmx_data[channel - 1]

        self.frame[0] = start_code
    
    def _send_break(self):
        """
        Generate DMX break + MAB with explicit pin control for predictable timing.
        """
        # Force TX low as GPIO for break duration.
        self.uart.deinit()
        Pin(self.tx_pin_num, Pin.OUT, value=0)
        self._delay_us_exact(self.break_us)

        # Re-enable UART; idle-high transition ends break and starts MAB.
        self.uart.init(baudrate=250000, bits=8, stop=2, parity=None, tx=Pin(self.tx_pin_num))
        self._delay_us_exact(self.mab_us)
    
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
    
    def set_all(self, value):
        """Set all DMX channels to the same value"""
        clamped = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i] = clamped
            self.frame[i + 1] = clamped
        print(f"All channels set to {clamped}")

    def clear_all(self):
        """Set all channels to 0"""
        for i in range(self.channels):
            self.dmx_data[i] = 0
        self._update_frame()
        print("All channels cleared to 0")

    def help(self):
        """Display help information"""
        print("\nDMX Controller Help:")
        print("Commands:")
        print("  c [channel] [value]     - Set single channel")
        print("  m [ch1:val1,ch2:val2]   - Set multiple channels")
        print("  all [value]             - Set all channels to value")
        print("  start                   - Start continuous transmission")
        print("  stop                    - Stop continuous transmission")
        print("  status                  - Show current status")
        print("  all [value]             - Set all channels to value")
        print("  clear                   - Clear all channels")
        print("  help                    - Show this help message")
        print("  exit                    - Exit program")

# Interactive interface
def interactive_dmx():
    """Interactive DMX controller with proper UART handling"""
    print("\n" + "="*60)
    print("DMX512 Controller - Proper UART Implementation")
    print("="*60)
    print("UART is initialized once for reliable operation")
    print("="*60)
    
    # Initialize controller with single UART init
    dmx = DMXController(tx_pin=0, channels=DMX_channels, refresh_rate=DMX_refresh_rate)
    
    # Show help on startup
    dmx.help()

    # Auto-start transmission
    dmx.start()

    
    while True:
        try:
            cmd = input("\nDMX> ").strip().lower()
            
            if cmd == "exit":
                dmx.stop()
                print("Exiting DMX controller")
                break
                
            elif cmd.startswith("c "):
                parts = cmd.split()
                if len(parts) == 3:
                    channel = int(parts[1])
                    value = int(parts[2])
                    dmx.set_channel(channel, value)
                    
            elif cmd.startswith("mc "):
                try:
                    values_str = cmd[3:]
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

            elif cmd.startswith("all "):
                try:
                    value = int(cmd.split()[1])
                    dmx.set_all(value)
                except Exception as e:
                    print(f"Error: {e}")
                
            elif cmd == "clear":
                dmx.clear_all()
            elif cmd == "help":
                dmx.help()

        except KeyboardInterrupt:
            dmx.stop()
            print("\nExiting DMX controller")
            break
                
        except Exception as e:
            print(f"Error: {e}")

# Run the controller
if __name__ == "__main__":
    interactive_dmx()
    



