# DMX512 transmitter using PIO
# MicroPython v1.27.0 — Raspberry Pi Pico W (RP2350)

import rp2
from machine import Pin, Timer, mem32
import time
import math

DMX_CHANELS = 8            # Number of channels (need to mulitple of 4 for 32-bit word packing)
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
    pull()                  # Pull num_words (blocking by default)
    mov(x, osr)             # Store num_words in x scratch register
    mov(y, x)               # Copy num_words 
        
    # infinite loop to send DMX frames continuously
    wrap_target()

    # Start break SM
    irq(4)                  # Trigger IRQ(4) to start send_dmx_break_PIO
    wait(1, irq, 5)         # Wait for IRQ(5) to be high so we know break is done

    # Start sending Words (4 Bytes) SM
    label("word_loop")
    irq(6)                  # Trigger IRQ(6) to start send_dmx_Byte_PIO       
    wait(1, irq, 7)         # Wait for IRQ(7) to be high so we know data transmission is done
    irq(clear, 7)           # Clear IRQ(7) for next word
    jmp(x_dec, "word_loop") # Loop to send next word 

    mov(x,y)                # Reset x to original num_words for next DMX frame

    wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH, autopull=True, pull_thresh=32, fifo_join=rp2.PIO.JOIN_TX)
def send_dmx_Byte_PIO():
    """
    PIO program for DMX word transmission (4 bytes = 32 bits).
    Sends 32-bit words as DMX bytes.
    Triggers IRQ(7) when word transmission is complete to signal the control SM.
    """
    wrap_target()
    wait(1, irq, 6)         # Wait for IRQ(6) to be high to start data transmission
    irq(clear, 6)           # Clear IRQ(6) for next frame 
    pull(noblock)           # Get next 32-bit word (non-blocking)
    mov(y, 3)               # Set x to 3 for byte loop (4 bytes total)

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
    set(y, 23)                 # 24 cycles at 4us = 96us 
    label("break_wait")
    nop()
    jmp(y_dec, "break_wait")

    # Mark After Break (MAB)
    set(pins, 1)
    set(y, 2)                  # 3 cycles at 4us = 12us 
    label("mab_wait")
    nop()
    jmp(y_dec, "mab_wait")

    irq(5)      # Signal break is done
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
        self.sm_ctrl = None
        self.sm_break = None
        self.sm_data = None

        self.sm_ctrl = rp2.StateMachine(
            0,
            dmx_control_PIO
        )   # PIO0 SM0 runs @ full speed for control logic

        self.sm_break = rp2.StateMachine(
            1,
            send_dmx_break_PIO,
            freq=250_000,  # Run at 4us per bit for break/MAB timing
            out_base=self.tx
        )  # PIO1 SM0

        self.sm_data = rp2.StateMachine(
            2,
            send_dmx_Byte_PIO,
            freq=250_000,  # Run at 4us per bit for DMX data timing
            out_base=self.tx
        )  # PIO1 SM1

        # Control flags
        self.transmitting = False
        self.timer = Timer()

        print(f"DMX PIO Controller initialized with {self.channels} channels")
        print(f"Refresh rate: {refresh_rate} Hz")
        print(f"TX Pin: {tx_pin}")

    def cpu_force_pio_irq0(self, pio_index=0):
        # Force PIO IRQ0 on RP2350 using PIO_IRQ_FORCE register.
        # RP2350 address map (pico-sdk rp2350/addressmap.h):
        # PIO0_BASE=0x50200000, PIO1_BASE=0x50300000, PIO2_BASE=0x50400000
        pio_bases = (0x50200000, 0x50300000, 0x50400000)
        pio_base = pio_bases[pio_index]

        # rp2350 pio.h: PIO_IRQ_FORCE offset is 0x34
        mem32[pio_base + 0x34] = 1 << 0

    def start(self):
        # Start continuous DMX transmission
        if self.transmitting:
            print("DMX transmission already running")
            return

        self.transmitting = True
        print(f"Starting continuous DMX transmission at {self.refresh_rate} Hz")

        # Start state machines
        self.sm_break.active(1)
        self.sm_data.active(1)
        self.sm_ctrl.active(1)

        # Start periodic timer to send new frames
        self.timer.init(
            freq=self.refresh_rate,
            mode=Timer.PERIODIC,
            callback=self._send_frame
        )
    
    def _send_frame(self, timer):
        # Triggered by timer
        if not self.transmitting:
            return
        
        try:
            # Convert first 8 bytes to 2 words (32 bits each)
            # First word: bytes 0-3 (start code + first 3 channels)
            # Second word: bytes 4-7 (next 4 channels)
            word1 = 0
            for i in range(4):
                if i < len(self.frame):
                    word1 |= self.frame[i] << (8 * i)
            
            word2 = 0
            for i in range(4, 8):
                if i < len(self.frame):
                    word2 |= self.frame[i] << (8 * (i - 4))
            
            # Load initial words into control SM for initial transmission
            # Note: The control SM expects to pull the number of words first
            # You need to set up the initial data before starting
            
            # Calculate number of 32-bit words needed
            num_words = (len(self.frame) + 3) // 4
            
            # Load number of words into control SM's TX FIFO
            self.sm_ctrl.put(num_words)
            
            # Load all data words into data SM's TX FIFO
            for i in range(0, len(self.frame), 4):
                word = 0
                for j in range(4):
                    if i + j < len(self.frame):
                        word |= self.frame[i + j] << (8 * j)
                self.sm_data.put(word)
            
            # Trigger control SM to start a new frame.
            # You need to implement IRQ triggering properly
            # This might need to be done differently based on your PIO setup
            self.sm_ctrl.exec("irq(0)")  # Trigger IRQ 0 to start control SM

        except Exception as e:
            print(f"Error in _send_frame: {e}")
            # Triggered by timer
            if not self.transmitting:
                return
        
        try:
            # Load first 2 words (start code + first 3 channels) into control SM for initial transmission
            self.sm_data.put(self,self.frame[:8])  

            # Trigger control SM to start a new frame.
            self.sm_ctrl.irq(0)

            for i in range(7, len(self.frame), 4):
                word = 0
                for j in range(4):
                    if i + j < len(self.frame):
                        word |= self.frame[i + j] << (8 * j)
                try:
                    self.sm_data.put(word)
                except OSError:
                    print("Warning: Data FIFO full, skipping word")
                    print(self.sm_data.tx_fifo())  # check how many words are in the FIFO
                    break

        except Exception as e :
            print(e)


      
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

        self.tx = Pin(self.tx_pin, Pin.OUT)
        self.tx.value(1 if idle_high else 0)

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
    dmx = DMXControllerPIO(tx_pin=DMX_TX_PIN, channels=DMX_CHANELS, refresh_rate=DMX_REFRESH_RATE)

    # Show help on startup
    dmx.help()

    # DO NOT auto-start transmission; use 'start' command to avoid UI/issues
    print("DMX transmission is stopped. Type 'start' to begin.")

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

            else:
                print(f"\n******\nUnknown command {cmd}\n******")
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