# DMX512 transmitter using PIO
# MicroPython v1.27.0 — Raspberry Pi Pico W (RP2350)

import rp2
from machine import Pin, Timer, mem32
import time
import math

DMX_CHANNELS = 8            # Number of channels (need to multiple of 4 for 32-bit word packing)
DMX_REFRESH_RATE = 50       # Hz
DMX_TX_PIN = 0              # GPIO pin for DMX output

start_code = 0x00
SM0_ID = 0
SMblock = SM0_ID // 4  # PIO block index (0-2)
print(f"Using SM{SM0_ID} in PIO block {SMblock}")
SM1_ID = 1
SM2_ID = 2

# PIO program for DMX data transmission
# Handles the 250kbps serial transmission with precise bit timing

# ============================================================================
# PIO Program 1: Control SM (14 instructions)
# ============================================================================
@rp2.asm_pio()
def dmx_control_PIO():
    """
    Control SM: Orchestrates DMX frame sequence.
    Uses IRQ1 to trigger break and data SMs.
    Waits on IRQ2 for completion signals.
    """
    # Initial setup (executes once at start)
    wait(1, irq, 0)         # 1: Wait for CPU trigger
    irq(clear, 0)           # 2: Clear CPU trigger
    pull()                  # 3: Get num_words from FIFO
    mov(x, osr)             # 4: Store in x (word counter)
    mov(y, x)               # 5: Copy to y (reset value)
    
    # Main frame loop (repeats continuously)
    wrap_target()           # Loop start
    wait(1, irq, 0)         # 6: Wait for next CPU trigger
    irq(clear, 0)           # 7: Clear CPU trigger
    
    irq(block, 1)           # 8: Trigger break SM (blocks until cleared)
    wait(1, irq, 2)         # 9: Wait for break completion (IRQ2)
    
    mov(x, y)               # 10: Reset word counter for this frame
    
    label("word_loop")
    irq(block, 1)           # 11: Trigger data SM (blocks until cleared)
    wait(1, irq, 2)         # 12: Wait for data completion (IRQ2)
    irq(clear, 2)           # 13: Clear completion flag for next wait
    jmp(x_dec, "word_loop") # 14: Loop for all words
    
    wrap()

# ============================================================================
# PIO Program 2: Data SM (10 instructions)
# ============================================================================
@rp2.asm_pio(
    out_init=rp2.PIO.OUT_HIGH,
    autopull=True,
    pull_thresh=32,
    out_shiftdir=rp2.PIO.SHIFT_RIGHT
)
def send_dmx_Byte_PIO():
    """
    Data SM: Sends 32-bit word as 4 DMX bytes.
    Triggered by IRQ1, signals completion with IRQ2.
    """
    wrap_target()
    wait(1, irq, 1)         # 1: Wait for trigger from control SM
    mov(y, 3)               # 2: 4 bytes to send (3 down to 0)
    
    label("byte_loop")
    mov(x, 7)               # 3: 8 bits to send (7 down to 0)
    set(pins, 0)            # 4: Start bit (low)
    
    label("bit_loop")
    out(pins, 1)            # 5: Output 1 data bit
    jmp(x_dec, "bit_loop")  # 6: Loop for all 8 bits
    
    set(pins, 1)            # 7: Stop bit 1 (high)
    nop()                   # 8: Stop bit 2 (high)
    jmp(y_dec, "byte_loop") # 9: Next byte
    
    irq(block, 2)           # 10: Signal word completion (blocks until cleared)
    wrap()

# ============================================================================
# PIO Program 3: Break SM (7 instructions)
# ============================================================================
@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH)
def send_dmx_break_PIO():
    """
    Break SM: Generates DMX break (96us) + MAB (12us).
    Triggered by IRQ1, signals completion with IRQ2.
    """
    wrap_target()
    wait(1, irq, 1)         # 1: Wait for trigger from control SM
    irq(clear, 1)           # 2: Clear trigger (unblocks control SM)
    
    set(pins, 0)            # 3: Break start (low)
    nop() [23]              # 4: 23 cycles + 1 from set = 24 cycles = 96us @ 250kHz
    
    set(pins, 1)            # 5: MAB start (high)
    nop() [2]               # 6: 2 cycles + 1 from set = 3 cycles = 12us @ 250kHz
    
    irq(block, 2)           # 7: Signal break completion (blocks until cleared)
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

        # Initialize PIO state machines - all on PIO0 for reliable communication
        self.sm_ctrl = None
        self.sm_break = None
        self.sm_data = None

        # Use SM0 for control, SM1 for break, SM2 for data (all on PIO0)
        self.sm_ctrl = rp2.StateMachine(
            SM0_ID,
            dmx_control_PIO,
            # Run at full speed for control logic
        ) 

        self.sm_break = rp2.StateMachine(
            SM1_ID,
            send_dmx_break_PIO,
            freq=250_000,  # Run at 4us per bit for break/MAB timing
            out_base=self.tx
        )

        self.sm_data = rp2.StateMachine(
            SM2_ID,
            send_dmx_Byte_PIO,
            freq=250_000,  # Run at 4us per bit for DMX data timing
            out_base=self.tx
        )

        # Control flags
        self.transmitting = False
        self.timer = Timer()

        print(f"DMX PIO Controller initialized with {self.channels} channels")
        print(f"Refresh rate: {refresh_rate} Hz")
        print(f"TX Pin: {tx_pin}")

    def cpu_force_pio_irq0(self, state_machine_block=0):
        # Force PIO IRQ0 on RP2350 using PIO_IRQ_FORCE register.
        # This is used to trigger the control SM to start a new frame.
        pio_bases = (0x50200000, 0x50300000, 0x50400000)
        pio_base = pio_bases[state_machine_block]
        # PIO_IRQ_FORCE offset is 0x34
        mem32[pio_base + 0x34] = 1 << 0

    def start(self):
        """Start continuous DMX transmission"""
        if self.transmitting:
            print("DMX transmission already running")
            return

        self.transmitting = True
        print(f"Starting continuous DMX transmission at {self.refresh_rate} Hz")
        
        # Start state machines (all on PIO0)
        self.sm_break.active(1)
        self.sm_data.active(1)
        self.sm_ctrl.active(1)

        # Calculate number of 32-bit words needed
        n_words = ((len(self.frame) + 3) // 4) - 1  # -1 because loop in control SM decrements after sending the the first word
        
        # Load number of words into dmx_control_PIO SM's TX FIFO
        # The control SM's first instruction is pull(), so this is critical
        try:
            self.sm_ctrl.put(n_words)
        except OSError as e:
            print(f"Error loading control SM FIFO: {e}")
            return
        print(f"Control SM loaded with {n_words}")
        print(f"PIO state machines started an the FIFO is loaded with {self.sm_ctrl.tx_fifo()} word(s)")
        self.cpu_force_pio_irq0(0)  # Force IRQ0 to let state machine read number of words to be sent by the data SM
        print("control SM triggered to start DMX frame transmission")
        print(f"FIFO contains {self.sm_ctrl.tx_fifo()} word(s)")

        # Start periodic timer to send new frames
        self.timer.init(
            freq=self.refresh_rate,
            mode=Timer.PERIODIC,
            callback=self._send_frame
        )
        print("Timer started")
    
    def _send_frame(self, timer):
        """Triggered by timer to send a new DMX frame"""
        if not self.transmitting:
            return
        print("\nTimer triggered: sending new DMX frame")
        try:
            # Update the data FIFO with new frame values
            while self.sm_data.tx_fifo() > 0:
                try:
                    self.sm_data.get()
                except:
                    break
            
            # Load fresh frame data
            for i in range(0, len(self.frame), 4):
                word = 0
                for j in range(4):
                    if i + j < len(self.frame):
                        word |= self.frame[i + j] << (8 * j)
                self.sm_data.put(word)
            
            # Trigger the control SM to start a new frame
            # This is the key: using the hardware register to force IRQ0
            self.cpu_force_pio_irq0(0)  # Force IRQ0 on State Machine block 0  to activate the control SM
            
        except Exception as e:
            print(f"Error in _send_frame: {e}")

    def set_channel(self, channel, value):
        """Set a single DMX channel value"""
        if 1 <= channel <= self.channels:
            clamped = max(0, min(255, value))
            self.dmx_data[channel - 1] = clamped
            self.frame[channel] = clamped  # +1 for start code offset
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
                self.frame[channel] = clamped  # +1 for start code offset
                updated_channels.append(channel)
        print(f"Updated {len(updated_channels)} channel(s)")

    def set_all(self, value):
        """Set all DMX channels to the same value"""
        clamped = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i] = clamped
            self.frame[i + 1] = clamped  # +1 for start code offset
        print(f"All channels set to {clamped}")

    def stop(self, idle_high=True):
        """Stop continuous DMX transmission and set the TX line state after shutdown."""
        if self.transmitting:
            self.timer.deinit()
            self.sm_ctrl.active(0)
            self.sm_break.active(0)
            self.sm_data.active(0)
            self.transmitting = False
            print("DMX transmission stopped")
        else:
            print("DMX transmission not running")

        # Reset TX pin to idle state
        self.tx = Pin(self.tx_pin, Pin.OUT)
        self.tx.value(1 if idle_high else 0)

    def show_status(self):
        """Display current status"""
        print("\nDMX Status:")
        print(f"  Channels: {self.channels}")
        print(f"  Transmission: {'Running' if self.transmitting else 'Stopped'}")
        print(f"  Refresh rate: {self.refresh_rate} Hz")
        print("\n  First 10 channels:")
        for i in range(min(10, self.channels)):
            print(f"    Channel {i+1}: {self.dmx_data[i]}")
        if self.channels > 10:
            print(f"    ... and {self.channels - 10} more channels")
        
        # Show PIO status
        if self.transmitting:
            print(f"\n  PIO FIFO status:")
            print(f"    Data SM TX FIFO level: {self.sm_data.tx_fifo()}")

    def clear_all(self):
        """Set all channels to 0"""
        for i in range(self.channels):
            self.dmx_data[i] = 0
            self.frame[i + 1] = 0  # +1 for start code offset
        print("All channels cleared to 0")

    def help(self):
        """Display help information"""
        print("\nDMX Controller Help:")
        print("Commands:")
        print("  c [channel] [value]     - Set single channel")
        print("  m [ch1:val1,ch2:val2]   - Set multiple channels (e.g., m 1:255,2:128)")
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
    print("Using Programmable I/O for precise DMX timing")
    print("="*60)

    # Initialize controller with PIO
    dmx = DMXControllerPIO(tx_pin=DMX_TX_PIN, channels=DMX_CHANNELS, refresh_rate=DMX_REFRESH_RATE)

    # Show help on startup
    dmx.help()

    # DO NOT auto-start transmission; use 'start' command to avoid UI/issues
    print("\nDMX transmission is stopped. Type 'start' to begin.")

    while True:
        try:
            cmd = input("\nDMX> ").strip().lower()

            if cmd == "exit":
                dmx.stop(idle_high=False)
                print("Exiting DMX controller")
                break

            elif cmd.startswith("c "):
                parts = cmd.split()
                if len(parts) == 3:
                    try:
                        channel = int(parts[1])
                        value = int(parts[2])
                        dmx.set_channel(channel, value)
                    except ValueError:
                        print("Error: Channel and value must be numbers")

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

            else:
                print(f"\nUnknown command: {cmd}")
                dmx.help()

        except KeyboardInterrupt:
            dmx.stop(idle_high=False)
            print("\nExiting DMX controller")
            break

        except Exception as e:
            print(f"Error: {e}")

# Run the controller
if __name__ == "__main__":
    try:
        interactive_dmx()
    except Exception as e:
        print(f"Error: {e}")