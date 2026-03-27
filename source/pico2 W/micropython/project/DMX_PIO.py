# DMX512 transmitter using PIO
# MicroPython v1.27.0 — Raspberry Pi Pico W (RP2350)

import rp2
from machine import Pin, Timer, mem32
import time
import math

DMX_CHANELS = 10           # Number of channels
DMX_REFRESH_RATE = 50       # Hz
DMX_TX_PIN = 0              # GPIO pin for DMX output

# Single-SM design to fit RP2350 PIO instruction memory and avoid ENOMEM.
SM_FRAME_ID = 0

PIO0_BASE = 0x50200000
PIO_IRQ_OFFSET = 0x30
PIO_IRQ_FORCE_OFFSET = 0x34
IRQ_START = 0
IRQ_DONE = 1

start_code = 0x00

# PIO program for DMX data transmission
# Handles the 250kbps serial transmission with precise bit timing

@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH, fifo_join=rp2.PIO.JOIN_TX)
def dmx_frame_PIO():
    """
    Single PIO program for one full DMX frame:
    - wait for CPU IRQ0 trigger
    - pull word_count_minus_1
    - generate break + MAB
    - transmit all words (4 bytes/word, 8N2)
    """
    wrap_target()
    # asm_pio programs require literal constants in IRQ operands.
    wait(1, irq, 0)  # CPU start trigger (IRQ_START)
    irq(clear, 0)
    irq(clear, 1)    # IRQ_DONE

    pull()                  # word_count_minus_1
    mov(x, osr)

    # Break: 96us at 250kHz => 24 cycles
    set(pins, 0)
    set(y, 23)
    label("break_wait")
    nop()
    jmp(y_dec, "break_wait")

    # Mark-after-break: 12us => 3 cycles
    set(pins, 1)
    set(y, 2)
    label("mab_wait")
    nop()
    jmp(y_dec, "mab_wait")

    # Send all words loaded by CPU into TX FIFO
    label("word_loop")
    pull()                  # next 32-bit word (4 DMX bytes)
    mov(y, 3)               # 4 bytes per word
    label("byte_loop")
    set(pins, 0)            # start bit
    out(pins, 1)            # bit 0
    out(pins, 1)            # bit 1
    out(pins, 1)            # bit 2
    out(pins, 1)            # bit 3
    out(pins, 1)            # bit 4
    out(pins, 1)            # bit 5
    out(pins, 1)            # bit 6
    out(pins, 1)            # bit 7
    set(pins, 1)            # stop bit 1
    nop()                   # stop bit 2
    jmp(y_dec, "byte_loop")
    jmp(x_dec, "word_loop")

    irq(1)                   # Frame complete (IRQ_DONE)

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
        self.frame_words = []

        # Initialize PIO state machines 
        self.sm_frame = None


        self.sm_frame = rp2.StateMachine(
            SM_FRAME_ID,
            dmx_frame_PIO,
            freq=250_000,  # Run at 4us per bit for break/MAB timing
            out_base=self.tx
        )


        # Calculate number of 32-bit words needed for the frame (start code + channels)
        self.DMX_words = math.ceil(len(self.frame) / 4)
        self._rebuild_frame_words()

        # Control flags
        self.transmitting = False
        self.timer = Timer()
        self.dropped_frames = 0
        self.frame_active = False

        print(f"DMX PIO Controller initialized with {self.channels} channels")
        print(f"Refresh rate: {refresh_rate} Hz")
        print(f"TX Pin: {tx_pin}")

    def _cpu_trigger_frame_irq0(self):
        """Force PIO IRQ0 for frame SM start on RP2350 PIO0."""
        mem32[PIO0_BASE + PIO_IRQ_FORCE_OFFSET] = 1 << IRQ_START

    def _clear_start_done_irqs(self):
        """Clear stale start/done IRQ flags before starting transmission."""
        mem32[PIO0_BASE + PIO_IRQ_OFFSET] = (1 << IRQ_START) | (1 << IRQ_DONE)

    def _read_and_clear_done_irq(self):
        """Check and clear done IRQ flag; return True when a frame completed."""
        done_mask = 1 << IRQ_DONE
        flags = mem32[PIO0_BASE + PIO_IRQ_OFFSET]
        if flags & done_mask:
            mem32[PIO0_BASE + PIO_IRQ_OFFSET] = done_mask
            return True
        return False

    def _rebuild_frame_words(self):
        """Pack current frame bytes into little-endian 32-bit words."""
        words = []
        for i in range(0, len(self.frame), 4):
            word = 0
            for j in range(4):
                if i + j < len(self.frame):
                    word |= self.frame[i + j] << (8 * j)
            words.append(word)
        self.frame_words = words

    def start(self):
        """Start continuous DMX transmission"""
        if self.transmitting:
            print("DMX transmission already running")
            return

        self.transmitting = True
        print(f"Starting continuous DMX transmission at {self.refresh_rate} Hz")

        # Start frame state machine
        self.sm_frame.active(1)
        self._clear_start_done_irqs()
        self.frame_active = False
        
        # Start periodic timer to send new frames
        self.timer.init(
            freq=self.refresh_rate,
            mode=Timer.PERIODIC,
            callback=self._send_frame
        )
    
    def _send_frame(self, timer):
        """Triggered by timer: load frame data FIFO, then trigger frame SM."""
        if not self.transmitting:
            return
        
        try:
            # Don't retrigger while a frame is still in progress.
            if self.frame_active:
                if self._read_and_clear_done_irq():
                    self.frame_active = False
                else:
                    self.dropped_frames += 1
                    return

            # Non-blocking policy: only queue a frame if there is enough FIFO space.
            # JOIN_TX gives 8 words deep on RP2 PIO.
            required_words = self.DMX_words + 1  # count + payload words
            if self.sm_frame.tx_fifo() > (8 - required_words):
                self.dropped_frames += 1
                return

            self._load_frame_into_fifo()  # First word is count, rest are frame data words
            self._cpu_trigger_frame_irq0()
            self.frame_active = True

        except Exception:
            # Avoid printing from timer callback context.
            self.dropped_frames += 1

    def _load_frame_into_fifo(self):
        """Load DMX frame into frame SM FIFO: [word_count_minus_1, data words...]"""
        self.sm_frame.put(self.DMX_words - 1)
        for word in self.frame_words:
            self.sm_frame.put(word)
      
    def set_channel(self, channel, value):
        """Set a single DMX channel value"""
        if 1 <= channel <= self.channels:
            clamped = max(0, min(255, value))
            self.dmx_data[channel - 1] = clamped
            self.frame[channel] = clamped
            self._rebuild_frame_words()
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
        if updated_channels:
            self._rebuild_frame_words()
        print(f"Updated {len(updated_channels)} channel(s)")

    def set_all(self, value):
        """Set all DMX channels to the same value"""
        clamped = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i] = clamped
            self.frame[i + 1] = clamped
        self._rebuild_frame_words()
        print(f"All channels set to {clamped}")

    def stop(self, idle_high=True):
        """Stop continuous DMX transmission and set the TX line state after shutdown."""
        if self.transmitting:
            self.timer.deinit()
            self.sm_frame.active(0)
            self.transmitting = False
            self.frame_active = False
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
        print(f"Dropped frames: {self.dropped_frames}")

    def clear_all(self):
        """Set all channels to 0"""
        for i in range(self.channels):
            self.dmx_data[i] = 0
            self.frame[i + 1] = 0
        self._rebuild_frame_words()
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