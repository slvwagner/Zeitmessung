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

# PIO program for DMX data transmission
# Handles the 250kbps serial transmission with precise bit timing

@rp2.asm_pio()
def dmx_control_PIO():
    """
    Control SM: orchestrates DMX frame sequence.
    Pulls num_words to be sent by send_dmx_Byte_PIO
    starts send_dmx_break_PIO via IRQ(4), waits for break done IRQ(5),
    starts send_dmx_Byte_PIO via IRQ(6), waits for data done IRQ(7), loops till all words are sent.
    signals the cpu that a new frame can be loaded
    """
    # first time init 
    wait(1, irq, 0)         # wait for CPU-triggered IRQ0 in PIO block
    irq(clear, 0)
    pull()                  #  1 Pull num_words (blocking by default)
    mov(x, osr)             #  2 Store num_words in x scratch register
    mov(y, x)               #  3 Copy num_words 
        
    # infinite loop to send DMX frames continuously
    wrap_target()           # 4 Loop start
    wait(1, irq, 0)         # wait for CPU-triggered IRQ0 in PIO block
    irq(clear, 0)

    # Start break SM - Using IRQ 4 (internal PIO IRQ)
    irq(4)                  # 5 Trigger IRQ(4) to start send_dmx_break_PIO
    wait(1, irq, 5)         # 6 Wait for IRQ(5) to be high so we know break is done

    # Start sending Words (4 Bytes) SM
    label("word_loop")
    irq(6)                  # 7 Trigger IRQ(6) to start send_dmx_Byte_PIO       
    wait(1, irq, 7)         # 8 Wait for IRQ(7) to be high so we know data transmission is done
    irq(clear, 7)           # 9 Clear IRQ(7) for next word
    jmp(x_dec, "word_loop") # 10 Loop to send next word 

    mov(x,y)                # 11 Reset x to original num_words for next DMX frame

    wrap()


@rp2.asm_pio(
        out_init=rp2.PIO.OUT_HIGH, 
        autopull=False, 
        pull_thresh=32, 
        fifo_join=rp2.PIO.JOIN_TX, 
        out_shiftdir=rp2.PIO.SHIFT_RIGHT
        )
def send_dmx_Byte_PIO():
    """
    PIO program for DMX word transmission (4 bytes = 32 bits).
    Sends 32-bit words as DMX bytes.
    Triggers IRQ(7) when word transmission is complete to signal the control SM.
    """
    wrap_target()
    wait(1, irq, 6)         # Wait for IRQ(6) to be high to start data transmission
    irq(clear, 6)           # Clear IRQ(6) for next frame 
    pull()                  # Get next 32-bit word (blocking)
    mov(y, 3)               # Set y to 3 for byte loop (4 bytes total)

    label("byte_loop")
    set(pins, 0)            # Start bit

    out(pins, 1)            # Bit 0
    out(pins, 1)            # Bit 1
    out(pins, 1)            # Bit 2
    out(pins, 1)            # Bit 3
    out(pins, 1)            # Bit 4
    out(pins, 1)            # Bit 5
    out(pins, 1)            # Bit 6
    out(pins, 1)            # Bit 7
    set(pins, 1)            # Stop bit 1
    nop()                   # Stop bit 2
    jmp(y_dec, "byte_loop")
    
    irq(7)                  # Signal word transmission is done
    wrap()

@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH)
def send_dmx_break_PIO():
    """
    PIO program for DMX break + mark-after-break generation.
    Emits 96us break (low) + 12us MAB (high), then triggers IRQ.
    Then stays high until the CPU deactivates the state machine.
    """
    wrap_target()
    wait(1, irq, 4)         # Wait for IRQ(4) to be high to start break/MAB sequence
    irq(clear, 4)           # Clear IRQ(4) for next frame
    
    # Break
    set(pins, 0)
    set(y, 23)              # 24 cycles at 4us = 96us 
    label("break_wait")
    nop()
    jmp(y_dec, "break_wait")

    # Mark After Break (MAB)
    set(pins, 1)
    set(y, 2)               # 3 cycles at 4us = 12us 
    label("mab_wait")
    nop()
    jmp(y_dec, "mab_wait")

    irq(5)                  # Signal break is done
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
            0,
            dmx_control_PIO,
            # Run at full speed for control logic
        ) 

        self.sm_break = rp2.StateMachine(
            3,
            send_dmx_break_PIO,
            freq=250_000,  # Run at 4us per bit for break/MAB timing
            out_base=self.tx
        )

        self.sm_data = rp2.StateMachine(
            4,
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