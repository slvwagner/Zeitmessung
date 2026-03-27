# MicroPython v1.27.0 on 2025-12-09; 
# RP2350/Pico 2 W  
# DMX512 Controller

import rp2
from machine import Pin, Timer, mem32
import time

# DMX Configuration
DMX_CHANNELS = 60  # Number of DMX channels to transmit (1-512)
DMX_REFRESH_RATE = 50
DMX_TX_PIN = 0
start_code = 0x00

# State Machine IDs - All in PIO0 for IRQ communication
SM_CTRL = 0
SM_BREAK = 1
SM_DATA = 2
PIO_BLOCK = 0

print(f"Using PIO{PIO_BLOCK}, SMs: CTRL={SM_CTRL}, BREAK={SM_BREAK}, DATA={SM_DATA}")

# ============================================================================
# PIO Program 1: Control SM (11 instructions)
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
    pull()                  # 2: Get num_words from FIFO
    mov(x, osr)             # 3: Store in x (word counter)
    mov(y, x)               # 4: Copy to y (reset value)
    
    # Main frame loop (repeats continuously)
    wrap_target()           # Loop start
    wait(1, irq, 0)         # 5: Wait for next CPU trigger
    
    irq(1)                  # 6: Trigger break SM (blocks until cleared)
    wait(1, irq, 2)         # 7: Wait for break completion (IRQ2)
    
    mov(x, y)               # 8: Reset word counter for this frame
    
    label("word_loop")
    irq(1)                  # 9: Trigger data SM (blocks until cleared)
    wait(1, irq, 2)         # 10: Wait for data completion (IRQ2)
    jmp(x_dec, "word_loop") # 11: Loop for all words
    
    wrap()

# ============================================================================
# PIO Program 2: Data SM (10 instructions )
# ============================================================================
@rp2.asm_pio(
    out_init=rp2.PIO.OUT_HIGH,
    autopull=True,
    pull_thresh=32,
    fifo_join=rp2.PIO.JOIN_TX,
    out_shiftdir=rp2.PIO.SHIFT_RIGHT
)
def send_dmx_Byte_PIO():
    """
    Data SM: Sends 32-bit word as 4 DMX bytes.
    Triggered by IRQ1 (blocking), signals completion with IRQ2 (blocking).
    """
    wrap_target()           # no need to pull => auto-pull is enabled
    wait(1, irq, 1)         # 1: Wait for trigger from control SM

    mov(y, 3)               # 2: 4 bytes to send (3 down to 0) so on 32bit word
    mov(x, 7)               # 3: 8 bits to send (7 down to 0)
    label("byte_loop")  
    set(pins, 0)            # 4: Start bit (low)
    label("bit_loop")
    out(pins, 1)            # 5: Output 1 data bit
    jmp(x_dec, "bit_loop")  # 6: Loop for all 8 bits
    set(pins, 1)            # 7: Stop bit 1 (high)
    mov(x, 7)               # 8: Stop bit 2 (high), refill loop counter x for next byte
    jmp(y_dec, "byte_loop") # 9: Next byte
    
    irq(2)                  # 10: Signal word completion
    wrap()

# ============================================================================
# PIO Program 3: Break SM (6 instructions )
# ============================================================================
@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH)
def send_dmx_break_PIO():
    """
    Break SM: Generates DMX break (96us) + MAB (12us).
    Triggered by IRQ1 (blocking), signals completion with IRQ2 (blocking).
    """
    wrap_target()
    wait(1, irq, 1)         # 1: Wait for trigger from control SM

    set(pins, 0)            # 2: Break start (low)
    nop() [23]              # 3: 23 cycles + 1 from set = 24 cycles = 96us @ 250kHz
    
    set(pins, 1)            # 4: MAB start (high)
    nop() [2]               # 5: 2 cycles + 1 from set = 3 cycles = 12us @ 250kHz
    
    irq(2)                  # 6: Signal break completion
    wrap()

# ============================================================================
# DMX Controller Class
# ============================================================================
class DMXControllerPIO:
    def __init__(self, tx_pin=0, channels=512, refresh_rate=44):
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = refresh_rate
        self.tx_pin = tx_pin
        
        # Initialize TX pin
        self.tx = Pin(tx_pin, Pin.OUT)
        self.tx.value(1)
        
        # DMX data buffers
        self.dmx_data = bytearray([0] * self.channels)
        self.frame = bytearray([start_code]) + bytearray([0] * self.channels)
        
        # PIO clock for DMX timing (250kHz = 4us per bit)
        DMX_CLOCK = 250_000
        
        # Create all three state machines in PIO0
        self.sm_ctrl = rp2.StateMachine(SM_CTRL, dmx_control_PIO)
        self.sm_break = rp2.StateMachine(SM_BREAK, send_dmx_break_PIO, freq=DMX_CLOCK, set_base=self.tx)
        self.sm_data = rp2.StateMachine(SM_DATA, send_dmx_Byte_PIO, freq=DMX_CLOCK, set_base=self.tx)
        
        self.transmitting = False
        self.timer = Timer()
        
        print(f"DMX Controller initialized: {self.channels} channels, {refresh_rate}Hz")
        print(f"SMs: CTRL={SM_CTRL}, BREAK={SM_BREAK}, DATA={SM_DATA} all in PIO0")
        print(f"Total instructions in PIO0: 14 + 10 + 7 = 31 (fits within 32 limit)")
        print(f"Data SM: FIFO joined (8-word TX buffer) for smooth data flow")
    
    def force_pio_irq0(self):
        """Force PIO IRQ0 on PIO block 0 to trigger control SM."""
        pio_base = 0x50200000  # PIO0 base address
        mem32[pio_base + 0x34] = 1 << 0
        time.sleep_us(1)  # Small delay for IRQ propagation
    
    def start(self):
        """Start continuous DMX transmission."""
        if self.transmitting:
            print("DMX transmission already running")
            return
        
        # Start all state machines
        self.sm_break.active(1)
        self.sm_data.active(1)
        self.sm_ctrl.active(1)
    
        time.sleep_ms(100)  # Allow SMs to initialize
        
        self.transmitting = True
        
        # Calculate number of 32-bit words needed (including start code)
        n_words = (len(self.frame) + 3) // 4
        print(f"Starting DMX transmission: {self.channels} channels, {n_words} words per frame")

        print(f"check the words in fifo: {self.sm_ctrl.tx_fifo()}")

        # Load word count into control SM's TX FIFO
        try:
            self.sm_ctrl.put(n_words)  # Load word count for the first frame
            print(f"check the words in fifo: {self.sm_ctrl.tx_fifo()}")

        except Exception as e:
            print(f"Error loading control SM: {e}")
            self.transmitting = False
            return
        
        print("DMX transmission initialized")
        
        # Start timer for frame updates
        self.timer.init(freq=self.refresh_rate, mode=Timer.PERIODIC, callback=self._update_frame)
    
    def _update_frame(self, timer):
        # Timer callback: Load new frame data and trigger transmission.
        if not self.transmitting:
            return
        
        start_time = time.ticks_us()
        print(f"\n[DEBUG] === Frame Update Started at {time.ticks_ms()} ms ===")
        
        try:
            
            # Preload first 8 words (fill the 8-word JOIN_TX buffer)
            preload_start = time.ticks_us()
            words_loaded = 0
            fifo_level = self.sm_data.tx_fifo()
            print(f"[DEBUG] FIFO level before preload: {fifo_level}/8")
            
            for i in range(0, min(8 * 4, len(self.frame)), 4):
                word = 0
                for j in range(4):
                    if i + j < len(self.frame):
                        word |= self.frame[i + j] << (8 * j)
                self.sm_data.put(word)
                words_loaded += 1
                print(f"[DEBUG]   Preloaded word {words_loaded}: 0x{word:08X} (bytes {i}-{i+3})")
            
            preload_time = time.ticks_diff(time.ticks_us(), preload_start)
            print(f"[DEBUG] Preloaded {words_loaded} words (took {preload_time} us)")
            print(f"[DEBUG] FIFO level after preload: {self.sm_data.tx_fifo()}/8")
            
            # Trigger control SM to start transmission
            trigger_start = time.ticks_us()
            self.force_pio_irq0()
            trigger_time = time.ticks_diff(time.ticks_us(), trigger_start)
            print(f"[DEBUG] Triggered control SM via IRQ0 (took {trigger_time} us)")
            
            # Continue loading remaining words while transmission is running
            remaining_words = total_words - words_loaded
            print(f"[DEBUG] Remaining words to load: {remaining_words}")
            
            if remaining_words > 0:
                load_start = time.ticks_us()
                words_loaded_now = words_loaded
                
                for idx in range(words_loaded, total_words):
                    i = idx * 4
                    word = 0
                    for j in range(4):
                        if i + j < len(self.frame):
                            word |= self.frame[i + j] << (8 * j)
                    
                    # Check FIFO level and wait if needed
                    fifo_level = self.sm_data.tx_fifo()
                    if fifo_level >= 7:
                        wait_start = time.ticks_us()
                        print(f"[DEBUG]   FIFO full ({fifo_level}/8), waiting for space...")
                        while self.sm_data.tx_fifo() >= 7:
                            time.sleep_us(10)
                        wait_time = time.ticks_diff(time.ticks_us(), wait_start)
                        print(f"[DEBUG]   Waited {wait_time} us for FIFO space")
                    
                    self.sm_data.put(word)
                    words_loaded_now += 1
                    
                    # Print progress every 10 words
                    if words_loaded_now % 10 == 0:
                        elapsed = time.ticks_diff(time.ticks_us(), load_start)
                        print(f"[DEBUG]   Loaded {words_loaded_now}/{total_words} words (FIFO: {self.sm_data.tx_fifo()}/8, elapsed: {elapsed} us)")
                
                load_time = time.ticks_diff(time.ticks_us(), load_start)
                print(f"[DEBUG] Loaded {remaining_words} remaining words (took {load_time} us)")
            
            # Final status
            final_fifo = self.sm_data.tx_fifo()
            total_time = time.ticks_diff(time.ticks_us(), start_time)
            print(f"[DEBUG] Frame update complete!")
            print(f"[DEBUG]   Total words: {total_words}")
            print(f"[DEBUG]   Final FIFO level: {final_fifo}/8")
            print(f"[DEBUG]   Total time: {total_time} us ({total_time/1000:.2f} ms)")
            
            # Calculate estimated frame time
            frame_bytes = len(self.frame)
            estimated_frame_us = frame_bytes * 44  # 44us per byte at 250kbps
            print(f"[DEBUG]   Estimated DMX frame time: {estimated_frame_us} us ({estimated_frame_us/1000:.2f} ms)")
            
            if total_time > estimated_frame_us:
                print(f"[WARNING] Loading time ({total_time/1000:.2f} ms) exceeds frame time ({estimated_frame_us/1000:.2f} ms)!")
            
        except Exception as e:
            print(f"[ERROR] Frame update error at {time.ticks_ms()} ms: {e}")
            import sys
            sys.print_exception(e)
            """Timer callback: Load new frame data and trigger transmission."""
            if not self.transmitting:
                return
            
            try:
                # Clear any pending data in data SM FIFO
                while self.sm_data.tx_fifo():
                    try:
                        self.sm_data.get()
                    except:
                        break

                print(f"Updating frame at {time.ticks_ms()} ms, preparing to load FIFO")            
                # Pack frame bytes into 32-bit words
                words_to_load = []
                for i in range(0, len(self.frame), 4):
                    word = 0
                    for j in range(4):
                        if i + j < len(self.frame):
                            word |= self.frame[i + j] << (8 * j)
                    words_to_load.append(word)
                
                # Load all words into FIFO
                for word in words_to_load:
                    self.sm_data.put(word)
                        
                print(f"Updating frame at {time.ticks_ms()} ms, loading {len(words_to_load)} words into FIFO")

                # Load all words into FIFO (8-word buffer prevents underflow)
                for word in words_to_load:
                    self.sm_data.put(word)

                # Trigger control SM to send the frame
                self.force_pio_irq0()
                
                cnt = 0
                while self.sm_data.get() < 8:  # Wait until there's space for at least one word
                    time.sleep_us(4)
                
                    # Pack frame bytes into 32-bit words and load into FIFO
                    # With JOIN_TX, we have 8 words of buffer - load all at once
                    words_to_load = []
                    for i in range(0, len(self.frame), 4):
                        word = 0
                        for j in range(4):
                            if i + j < len(self.frame):
                                word |= self.frame[i + j] << (8 * j)
                        words_to_load.append(word)
                    
                # Load all words into FIFO (8-word buffer prevents underflow)
                for word in words_to_load:
                    self.sm_data.put(word)
                
                
                print(f"Frame updated and transmission triggered at {time.ticks_ms()} ms")
                
            except Exception as e:
                print(f"Frame update error: {e}")
        
    def set_channel(self, channel, value):
        """Set a single DMX channel (1-indexed)."""
        if 1 <= channel <= self.channels:
            value = max(0, min(255, value))
            self.dmx_data[channel - 1] = value
            self.frame[channel] = value  # +1 offset for start code
            print(f"Channel {channel} = {value}")
        else:
            print(f"Error: Channel {channel} out of range (1-{self.channels})")
    
    def set_all(self, value):
        """Set all channels to the same value."""
        value = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i] = value
            self.frame[i + 1] = value
        print(f"All channels set to {value}")
    
    def clear_all(self):
        """Set all channels to 0."""
        self.set_all(0)
    
    def stop(self):
        """Stop DMX transmission."""
        if self.transmitting:
            self.timer.deinit()
            time.sleep_ms(10)  # Allow current frame to complete
            self.sm_ctrl.active(0)
            self.sm_break.active(0)
            self.sm_data.active(0)
            self.transmitting = False
            print("DMX transmission stopped")
        
        # Set TX pin to idle high
        self.tx = Pin(self.tx_pin, Pin.OUT)
        self.tx.value(1)
    
    def status(self):
        """Display current status."""
        print("\n" + "=" * 40)
        print("DMX Controller Status")
        print("=" * 40)
        print(f"Channels: {self.channels}")
        print(f"Transmitting: {self.transmitting}")
        print(f"Refresh rate: {self.refresh_rate} Hz")
        print("\nFirst 8 channels:")
        for i in range(min(8, self.channels)):
            print(f"  Channel {i+1}: {self.dmx_data[i]}")
        
        if self.transmitting:
            try:
                print(f"\nFIFO status:")
                print(f"  Data SM TX FIFO: {self.sm_data.tx_fifo()} / 8 words (JOIN_TX)")
                print(f"  Control SM TX FIFO: {self.sm_ctrl.tx_fifo()} words")
            except:
                pass
        print("=" * 40)


# ============================================================================
# Interactive Test Interface
# ============================================================================
def main():
    print("\n" + "=" * 50)
    print("DMX512 PIO Controller - RP2350")
    print("=" * 50)
    print(f"Total instructions: 14 + 10 + 7 = 31 (within 32 limit)")
    print(f"Data SM FIFO: JOIN_TX (8-word buffer)")
    print("=" * 50)
    
    # Create controller
    dmx = DMXControllerPIO(
        tx_pin=DMX_TX_PIN,
        channels=DMX_CHANNELS,
        refresh_rate=DMX_REFRESH_RATE
    )
    
    print("\nCommands:")
    print("  c <ch> <val>  - Set channel (e.g., c 1 255)")
    print("  all <val>     - Set all channels (e.g., all 128)")
    print("  clear         - Clear all channels to 0")
    print("  start         - Start transmission")
    print("  stop          - Stop transmission")
    print("  status        - Show status")
    print("  exit          - Quit")
    print()
    
    while True:
        try:
            cmd = input("DMX> ").strip().lower()
            
            if cmd == "exit":
                dmx.stop()
                print("Exiting...")
                break
                
            elif cmd == "start":
                dmx.start()
                
            elif cmd == "stop":
                dmx.stop()
                
            elif cmd == "status":
                dmx.status()
                
            elif cmd == "clear":
                dmx.clear_all()
                
            elif cmd.startswith("c "):
                parts = cmd.split()
                if len(parts) == 3:
                    try:
                        channel = int(parts[1])
                        value = int(parts[2])
                        dmx.set_channel(channel, value)
                    except ValueError:
                        print("Error: Channel and value must be numbers")
                else:
                    print("Usage: c <channel> <value>")
                    
            elif cmd.startswith("all "):
                parts = cmd.split()
                if len(parts) == 2:
                    try:
                        value = int(parts[1])
                        dmx.set_all(value)
                    except ValueError:
                        print("Error: Value must be a number")
                else:
                    print("Usage: all <value>")
                    
            else:
                print(f"Unknown command: {cmd}")
                
        except KeyboardInterrupt:
            dmx.stop()
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()