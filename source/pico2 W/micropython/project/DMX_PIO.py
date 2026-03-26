# DMX512 transmitter using PIO
# MicroPython v1.27.0 — Raspberry Pi Pico W (RP2350)

import rp2
from machine import Pin, Timer
import time

DMX_channels = 10       # Number of channels
DMX_refresh_rate = 50   # Hz

start_code = 0x00

# PIO program for DMX data transmission
# Handles the 250kbps serial transmission with precise bit timing

@rp2.asm_pio()
def dmx_control_PIO():
    """
    Control SM: orchestrates DMX frame sequence.
    Pulls num_words to be sent by send_dmx_data_PIO
    starts send_dmx_break_PIO via IRQ(4), waits for break done IRQ(5),
    starts send_dmx_data_PIO via IRQ(6), waits for data done IRQ(7), loops till all words are sent.
    signals the cpu that a new frame can be loaded
    """
    # init 
    wrap_target()
    pull()                  # Pull num_words (blocking by default)
    mov(x, osr)             # Store num_words in x scratch register
    
    # loop to send frames continuously
    irq(4)                  # Trigger IRQ(4) to start send_dmx_break_PIO
    wait(1, irq, 5)         # Wait for IRQ(5) to be high so we know break is done
    irq(clear, 4)           # Clear IRQ(4) for next frame
    irq(clear, 5)           # Clear IRQ(5) for next frame

    # Start data SM
    irq(6)                  # Trigger IRQ(6) to start send_dmx_data_PIO       
    wait(1, irq, 7)         # Wait for IRQ(7) to be high so we know data transmission is done
    irq(clear, 6)           # Clear IRQ(6) for next frame
    irq(clear, 7)           # Clear IRQ(7) for next frame

    wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH)
def send_dmx_break_PIO():
    """
    PIO program for DMX break + mark-after-break generation.
    Emits 96us break (low) + 12us MAB (high), then triggers IRQ.
    Then stays high until the CPU deactivates the state machine.
    """
    set(pins, 0)
    set(y, 23)                 # 24 cycles at 4us = 96us (fixed from 22)
    label("break_wait")
    nop()
    jmp(y_dec, "break_wait")

    set(pins, 1)
    set(y, 2)                  # 3 cycles at 4us = 12us (fixed from 2)
    label("mab_wait")
    nop()
    jmp(y_dec, "mab_wait")

    irq(5)      # Signal break is done


@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH, autopull=True, pull_thresh=32)
def send_dmx_data_PIO():
    """
    PIO program for DMX data transmission with frame counting.
    Loads num_words first, then sends that many 32-bit words as DMX bytes.
    Triggers IRQ(7) when frame is complete.
    """
    wrap_target()

    label("word_loop")
    pull()                  # Get next 32-bit word (blocking by default)
    mov(y, 3)               # y = 3 for 4 bytes (fixed from set(y,4))

    label("byte_loop")
    set(pins, 0)       [4]  # Start bit
    out(pins, 1)       [4]  # Bit 0
    out(pins, 1)       [4]  # Bit 1
    out(pins, 1)       [4]  # Bit 2
    out(pins, 1)       [4]  # Bit 3
    out(pins, 1)       [4]  # Bit 4
    out(pins, 1)       [4]  # Bit 5
    out(pins, 1)       [4]  # Bit 6
    out(pins, 1)       [4]  # Bit 7
    set(pins, 1)       [4]  # Stop bit 1
    nop()              [4]  # Stop bit 2
    jmp(y_dec, "byte_loop")

    jmp(x_dec, "word_loop")  # Loop for all words from x counter
    
    irq(7)                  # Signal data transmission is done
    wrap()


class DMXControllerPIO:
    def __init__(self, tx_pin=0, channels=512, refresh_rate=44):
        """
        Initialize DMX controller using PIO for precise data transmission
        """
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = refresh_rate
        self.tx_pin = tx_pin

        # Initialize TX pin for break/mark-after-break generation
        self.tx = Pin(tx_pin, Pin.OUT)
        self.tx.value(1)  # Idle high

        # Initialize channel data (all channels start at 0)
        self.dmx_data = bytearray([0] * self.channels)

        # Create DMX frame: start code + channel data
        self.frame = bytearray([start_code]) + bytearray([0] * self.channels)

        # Initialize PIO state machines 
        self.sm_ctrl = rp2.StateMachine(
            0, 
            dmx_control_PIO
            )   # Run at full speed
        self.sm_break = rp2.StateMachine(
            1, 
            send_dmx_break_PIO, 
            freq=250_000, # Run at defined speed
            out_base=self.tx
            )
        self.sm_data = rp2.StateMachine(
            2, 
            send_dmx_data_PIO, 
            freq=250_000, # Run at defined speed
            out_base=self.tx
            )

        # Calculate number of 32-bit words needed for the frame (start code + channels)
        self.DMX_words = math.ceil((len(self.frame) + 3) // 4)

        # Control flags
        self.transmitting = False
        self.timer = Timer()

        print(f"DMX PIO Controller initialized with {self.channels} channels")
        print(f"Refresh rate: {refresh_rate} Hz")
        print(f"TX Pin: {tx_pin}")

    def start(self):
        """Start continuous DMX transmission"""
        if self.transmitting:
            print("DMX transmission already running")
            return

        self.transmitting = True
        print(f"Starting continuous DMX transmission at {self.refresh_rate} Hz")

        # Start state machines
        self.sm_ctrl.active(1)
        self.sm_break.active(1)
        self.sm_data.active(1)
        
         # Send word count to control SM
        self.sm_ctrl.put(self.DMX_words) 

        # Start periodic timer to send new frames
        self.timer.init(
            freq=self.refresh_rate,
            mode=Timer.PERIODIC,
            callback=self._send_frame
        )
    
    def _send_frame(self, timer):
        """Triggered by timer: send num_words to control SM for next frame."""
        if not self.transmitting:
            return
        
        try:
            self._load_frame_into_fifo()  # Load frame data into data SM FIFO
            self.sm_ctrl.put(self.DMX_words)  # Send word count to start next frame
        except OSError:
            # FIFO full; stop feeding
            print("Data FIFO full, stopping frame load")

    def _load_frame_into_fifo(self):
        """Load DMX frame into data SM FIFO as 32-bit words"""
        for i in range(0, len(self.frame), 4):
            word = 0
            for j in range(4):
                if i + j < len(self.frame):
                    word |= self.frame[i + j] << (8 * j)
            try:
                self.sm_data.put(word)
            except OSError:
                # FIFO full but pass anyway to trigger data SM; it will block until space is available
                pass
      
    def set_channel(self, channel, value):
        """Set a single DMX channel value"""
        if 1 <= channel <= self.channels:
            clamped = max(0, min(255, value))
            self.dmx_data[channel - 1] = clamped
            self.frame[channel] = clamped
            print(f"Channel {channel} set to {clamped}")
        else:
            print(f"Error: Channel {channel} out of range (1-{self.channels})")

    def set_channels(self, values_dict):
        """Set multiple DMX channels at once"""
        updated_channels = []
        for channel, value in values_dict.items():
            if 1 <= channel <= self.channels:
                clamped = max(0, min(255, value))
                self.dmx_data[channel - 1] = clamped
                self.frame[channel] = clamped
                updated_channels.append(channel)
        print(f"Updated {len(updated_channels)} channel(s)")

    def set_all(self, value):
        """Set all DMX channels to the same value"""
        clamped = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i] = clamped
            self.frame[i + 1] = clamped
        print(f"All channels set to {clamped}")

    def stop(self):
        """Stop continuous DMX transmission"""
        if not self.transmitting:
            print("DMX transmission not running")
            return

        self.timer.deinit()
        self.sm_ctrl.active(0)
        self.sm_break.active(0)
        self.sm_data.active(0)
        self.transmitting = False
        print("DMX transmission stopped")
        self.tx.value(1)  # Idle high

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
            self.frame[i + 1] = 0
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
        print("  clear                   - Clear all channels")
        print("  help                    - Show this help message")
        print("  exit                    - Exit program")

# Interactive interface
def interactive_dmx():
    """Interactive DMX controller using PIO for precise timing"""
    print("\n" + "="*60)
    print("DMX512 Controller - PIO Implementation")
    print("="*60)
    print("Using Programmable I/O for precise DMX timing")
    print("="*60)

    # Initialize controller with PIO
    dmx = DMXControllerPIO(tx_pin=0, channels=DMX_channels, refresh_rate=DMX_refresh_rate)

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

            elif cmd.startswith("all "):
                try:
                    value = int(cmd.split()[1])
                    dmx.set_all(value)
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