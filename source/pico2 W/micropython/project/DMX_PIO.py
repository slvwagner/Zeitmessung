# MicroPython v1.27.0 on 2025-12-09; 
# RP2350/ Raspberry Pico 2 W  Deveopemt board
# DMX512 Controller

import rp2
from machine import Pin, Timer, mem32
import time

# DMX Configuration
DMX_CHANNELS = 10      # Number of DMX channels to transmit (1-512)
DMX_REFRESH_RATE = 44   # Desired refresh rate in Hz (DMX standard is 44Hz for 512 channels) This implementation only alows a max of 41Hz.
DMX_TX_PIN = 0          # GPIO pin for DMX data output (GPIO0)
PIN_TRIGGER = 1         # Pin to trigger scope (GPIO1)
start_code = 0x00       # DMX start code (0x00 is common for lighting)

DEBUG = False
PRINT_UPDATES = False
SAFE_HEADROOM_PERCENT = 5

# State Machine IDs - All in PIO0 for IRQ communication
PIO_BLOCK = 0
SM_CTRL = 0
SM_CTRL_CLOCK_HZ = 6_000_000
SM_DATA = 1
SM1_DATA_CLOCK_HZ = 3_000_000

# ============================================================================
# PIO Program 1: Control SM (18 instructions)
# ============================================================================
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, sideset_init=rp2.PIO.OUT_HIGH)
def sm_DMX_control():
    """
    SM0: Wait for CPU IRQ0 trigger, then handshake with SM1.
    """
    BREAK = 21
    MAB = 21

    wait(1, irq, 0)                     # 1 wait for CPU-triggered IRQ0 in PIO block // Trigger pin low
    pull()                              # 2 Pull number of words (one word = 4 DMX channels) from TX FIFO       
    mov(y, osr)                         # 3 Save Nummber of words from FIFO to y register (one word = 4 DMX channels)

    wrap_target()
    wait(1, irq, 0)         .side(0)    # 4 wait for CPU-triggered IRQ0 in PIO block // Trigger pin low 

    set(x, BREAK)                       # 5 loop count for Break duration (92us @ 6MHz)
    set(pins, 0)            [5]         # 6 Break low
    label("Break")              
    nop()                   [7]         # 6 
    nop()                   [7]         # 7 
    nop()                   [7]         # 8 
    jmp(x_dec,"Break")                  # 9 Loop for Break duration       

    set(pins, 0)                        # 10 Mark after Break high duration loop (12us @ 6MHz)// Trigger pin high => Trigger scope on falling edge
    set(x, MAB)             [1]         # 11 loop count for Mark After Break duration
    label("MAB")
    set(pins, 1)            [1]         # 12 Mark After Break low    
    jmp(x_dec, "MAB")                   # 13 Mark After Break duration loop  

    mov(x, y)               .side(1)    # 14 loop count, number of words @ 4DMX channels
    label("channel_loop")
    irq(4)                              # 15 signal SM1 via IRQ 4 to send 4 Channels so one word @ 4 x 8Bit's
    wait(1, irq, 5)                     # 16 wait for SM1 response via IRQ 5
    jmp(x_dec, "channel_loop")          # 17 loop back if x > 0
    set(pins, 1)            .side(0)    # 18 Signal idle high // Trigger pin low
    wrap()

# ============================================================================
# PIO Program 2: Data SM (9 instructions)
# ============================================================================
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_HIGH, sideset_init=rp2.PIO.OUT_HIGH, 
             out_shiftdir=rp2.PIO.SHIFT_RIGHT, autopull=True, pull_thresh=32, fifo_join=rp2.PIO.JOIN_TX)
def sm_DMX_data():
    """
    SM1: Wait for IRQ 4 from SM0, Bit pattern, signal back via IRQ 1.
    """
    
    wrap_target()

    wait(1, irq, 4)                     # 1 Wait for IRQ 4 from SM0  
    set(x, 3)                           # 2 4 Bytes in one word 
    label("byte_loop")
    set(y, 7)               .side(0)[5] # 3 Start bit low // Loop counter for Bit_loop 
    nop()                   [5]         # 4 Small delay before starting bit loop (allows scope to trigger on start bit)
    label("bit_loop")             
    out(pins, 1)                    [4] # 4 Output bit to pin and shift right (4us per bit at 250kbps)
    nop()                           [5] # 5 
    jmp(y_dec, "bit_loop")              # 6 Loop bit loop
    set(pins, 1)                    [4] # 7 Stop bit high
    nop()                           [5] # 8 
    nop()                           [5] # 9 Stop bit high (3 cycles delay + loop back cyle)
    nop()                           [5] # 11 
    jmp(x_dec, "byte_loop")             # 12 Loop for next word in FIFO // stop bit high
    irq(5)                  .side(1)    # 13 Signal SM0 back via IRQ 1 / Triger pin low

    wrap()

# ============================================================================
# PIO Program instructions: 18 + 13 = 31 instructions 
# ============================================================================

# ============================================================================
# DMX Controller Class
# ============================================================================
class DMXControllerPIO:
    def __init__(self, tx_pin=0, channels=512, refresh_rate=41):
        self.channels = min(max(1, channels), 512)
        if refresh_rate <= 41:
            self.refresh_rate = refresh_rate
        else:
            self.refresh_rate = 41
        self.tx_pin = tx_pin
        
        # Initialize TX pin
        self.tx = Pin(tx_pin, Pin.OUT)
        # Pulse to show activity on scope during setup
        self.tx.value(1)
        self.tx.value(0)  
        
        # DMX data buffers
        self.dmx_data = bytearray([0] * self.channels)
        for i in range(self.channels):
            self.dmx_data[i] = 0  # Initialize all channels to 0

        self.frame = bytearray([start_code]) + bytearray([0] * self.channels)
        # Packed 32-bit words (little-endian) sent to PIO data SM.
        self.packed_words = [0] * ((len(self.frame) + 3) // 4)
        self.word_dirty = bytearray(len(self.packed_words))
        self.all_words_dirty = True
        self._pack_all_words()
        
        # PIO clock for DMX timing (250kHz = 4us per bit)
        DMX_CLOCK = 250_000
        
        # Create the state machines in PIO Block 0
        self.sm_ctrl = rp2.StateMachine(
            SM_CTRL, 
            sm_DMX_control, 
            freq=SM_CTRL_CLOCK_HZ,
            set_base=Pin(DMX_TX_PIN),
            sideset_base=Pin(PIN_TRIGGER)
            )

        self.sm_data = rp2.StateMachine(
            SM_DATA, 
            sm_DMX_data, 
            freq=SM1_DATA_CLOCK_HZ,
            set_base=Pin(DMX_TX_PIN),
            out_base=Pin(DMX_TX_PIN),
            sideset_base=Pin(DMX_TX_PIN)
            )
        
        self.transmitting = False
        self.timer = Timer()
        self._frame_in_progress = False
        self.print_updates = PRINT_UPDATES
        self.active_refresh_rate = self.refresh_rate
        self.data_version = 0
        self.last_sent_version = 0
        self.frame_count = 0
        self.skipped_callbacks = 0
        self.max_update_us = 0
        self.sum_update_us = 0
        
        self.n_words = 0
        if DEBUG:
            print(f"DMX Controller initialized: {self.channels} channels, {refresh_rate}Hz")
            print(f"SMs: CTRL={SM_CTRL}, DATA={SM_DATA} all in PIO0")
            print(f"Total instructions in PIO0: 14 + 10 + 7 = 31 (fits within 32 limit)")
            print(f"Control + Data SM: FIFO joined (8-word TX buffer)")

    def _pack_word(self, word_idx):
        """Pack one 32-bit word from 4 frame bytes (little-endian)."""
        i = word_idx * 4
        word = 0
        for j in range(4):
            if i + j < len(self.frame):
                word |= self.frame[i + j] << (8 * j)
        self.packed_words[word_idx] = word

    def _pack_all_words(self):
        """Pack the whole DMX frame into 32-bit words."""
        for idx in range(len(self.packed_words)):
            self._pack_word(idx)

    def _pack_dirty_words(self):
        """Pack only modified words to reduce setter-side latency."""
        if self.all_words_dirty:
            self._pack_all_words()
            self.all_words_dirty = False
            for i in range(len(self.word_dirty)):
                self.word_dirty[i] = 0
            return

        for idx in range(len(self.word_dirty)):
            if self.word_dirty[idx]:
                self._pack_word(idx)
                self.word_dirty[idx] = 0
        
    def force_pio_irq0(self):
        # Force PIO IRQ0 on PIO block 0 to trigger control SM.
        pio_base = 0x50200000  # PIO0 base address
        mem32[pio_base + 0x34] = 1 << 0
        time.sleep_us(1)  # Small delay for IRQ propagation
    
    def start(self):
        # Start continuous DMX transmission.
        if self.transmitting:
            print("DMX transmission already running")
            return

        # Clamp requested refresh to a realistic value for this frame size.
        frame_bytes = len(self.frame)
        min_frame_us = (frame_bytes * 44) + 88 + 8  # 250 kbps byte time + break + MAB
        safe_frame_us = (min_frame_us * (100 + SAFE_HEADROOM_PERCENT)) // 100
        safe_max_hz = max(1, 1_000_000 // safe_frame_us)
        self.active_refresh_rate = self.refresh_rate
        if self.active_refresh_rate > safe_max_hz:
            print(f"Refresh {self.active_refresh_rate}Hz too high for {frame_bytes} bytes in Python path.")
            print(f"Using safe refresh {safe_max_hz}Hz to keep REPL responsive.")
            self.active_refresh_rate = safe_max_hz
        
        # Start all state machines
        self.sm_data.active(1)
        self.sm_ctrl.active(1)
        time.sleep_ms(100)  # Allow SMs to initialize
        
        self.transmitting = True
        self.frame_count = 0
        self.skipped_callbacks = 0
        self.max_update_us = 0
        self.sum_update_us = 0
        
        # Calculate number of 32-bit words needed for the DMX frame (start code + channel data)
        self.n_words = ((len(self.frame) + 3) // 4) - 1  # Total words minus one because statemachine only decrement the counter after sending a word, so we preload with total-1
        print(f"Starting DMX transmission: {self.channels} channels, {self.n_words} words per frame")

        # Load word count into control SM's TX FIFO
        try:
            print(f"Put the number of words in FIFO, atual FIFO level: {self.sm_ctrl.tx_fifo()}")
            self.sm_ctrl.put(self.n_words)  # Load word count into control SM FIFO
            print(f"check the words in fifo: {self.sm_ctrl.tx_fifo()}")

        except Exception as e:
            print(f"Error loading control SM: {e}")
            self.transmitting = False
            return         
   
        # Signal statemachine to read FIFO filled with number of words
        if DEBUG:
            print("[DEBUG]  Trigger to read FIFO with number of words")
        self.force_pio_irq0()
        print(f"check the words in fifo: {self.sm_ctrl.tx_fifo()}")
    
        # Start timer for frame updates
        # Use period in milliseconds instead of freq to be explicit
        period_ms = int(1000 / self.active_refresh_rate)
        if DEBUG:
            print(f"[DEBUG] Starting timer with period: {period_ms} ms ({self.active_refresh_rate} Hz)")
        self.timer.init(period=period_ms, mode=Timer.PERIODIC, callback=self.update_frame)
        print("DMX transmission initialized")
    
    def update_frame(self, timer):
        # Timer callback: Load new frame data and trigger transmission.
        if DEBUG:
            print(f"[DEBUG] Timer callback: update_frame CALLED at {time.ticks_ms()} ms")  
            print(f"[DEBUG] FIFO level data state machine: {self.sm_data.tx_fifo()}/8")  
        if not self.transmitting:
            return
        # Prevent re-entrant timer callbacks from stacking up under load.
        if self._frame_in_progress:
            self.skipped_callbacks += 1
            return
        self._frame_in_progress = True
        
        start_time = time.ticks_us()
        if DEBUG:
            print(f"\n[DEBUG] === Frame Update Started at {time.ticks_ms()} ms ===")
        
        try:
            self._pack_dirty_words()
            self.last_sent_version = self.data_version
            # Preload first 8 words (fill the 8-word JOIN_TX buffer)
            preload_start = time.ticks_us()
            words_loaded = 0
            fifo_level = self.sm_data.tx_fifo()
            if DEBUG:
                print(f"[DEBUG] FIFO level data state machine before preload: {fifo_level}/8")
            
            preload_count = min(8, len(self.packed_words))
            for idx in range(preload_count):
                self.sm_data.put(self.packed_words[idx])
                words_loaded += 1
                #print(f"[DEBUG]   Preloaded word {words_loaded}: 0x{word:08X} (bytes {i}-{i+3})")
            
            preload_time = time.ticks_diff(time.ticks_us(), preload_start)
            if DEBUG:
                print(f"[DEBUG] FIFO level data state machine after preload: {self.sm_data.tx_fifo()}/8")
            
            # Trigger control SM to start transmission
            #trigger_start = time.ticks_us()
            self.force_pio_irq0()
            #trigger_time = time.ticks_diff(time.ticks_us(), trigger_start)
            #print(f"[DEBUG] Triggered control SM via IRQ0 (took {trigger_time} us)")
            
            # Continue loading remaining words while transmission is running
            total_words = self.n_words + 1
            remaining_words = total_words - words_loaded
            #print(f"[DEBUG] Remaining words to load: {remaining_words}")
            
            if remaining_words > 0:
                load_start = time.ticks_us()
                words_loaded_now = words_loaded
                
                for idx in range(words_loaded, total_words):
                    # Blocking put waits in C code until FIFO has room; cheaper than Python polling.
                    self.sm_data.put(self.packed_words[idx])
                    words_loaded_now += 1
                    
                
                load_time = time.ticks_diff(time.ticks_us(), load_start)
                if DEBUG:
                    print(f"[DEBUG] Loaded {remaining_words} remaining words (took {load_time} us)")
            
            # Final status
            final_fifo = self.sm_data.tx_fifo()
            total_time = time.ticks_diff(time.ticks_us(), start_time)
            self.frame_count += 1
            self.sum_update_us += total_time
            if total_time > self.max_update_us:
                self.max_update_us = total_time
            if DEBUG:
                print(f"[DEBUG] Frame update complete!")
                print(f"[DEBUG]   Total words: {self.n_words} for {DMX_CHANNELS} channels.")
                print(f"[DEBUG]   Final FIFO level data state machine: {final_fifo}/8")
                print(f"[DEBUG]   Total time: {total_time} us ({total_time/1000:.2f} ms)")
            
            # Calculate estimated frame time
            frame_bytes = len(self.frame)
            estimated_frame_us = (frame_bytes * 44)  + 88 + 8 # 44us per byte at 250kbps
            if DEBUG:
                print(f"[DEBUG]   Estimated DMX frame time @ {self.channels} channels: {estimated_frame_us} us ({estimated_frame_us/1000:.2f} ms)")    
                print(f"[DEBUG]   Loading time ({total_time/1000:.2f} ms) ({total_time/1000:.2f} ms) ")
                print(f"[DEBUG]   Update frequency: {1/(total_time/1000000):.2f} Hz")

        except Exception as e:
            print(f"[ERROR] Frame update error at {time.ticks_ms()} ms: {e}")
            print(f"{e}")
        finally:
            self._frame_in_progress = False
        
    def set_channel(self, channel, value):
        """Set a single DMX channel (1-indexed)."""
        if 1 <= channel <= self.channels:
            value = max(0, min(255, value))
            self.dmx_data[channel - 1] = value
            self.frame[channel] = value  # +1 offset for start code
            self.word_dirty[channel // 4] = 1
            self.data_version += 1
            if self.print_updates:
                print(f"Channel {channel} = {value}")
        else:
            print(f"Error: Channel {channel} out of range (1-{self.channels})")
    
    def set_all(self, value):
        """Set all channels to the same value."""
        value = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i] = value
            self.frame[i + 1] = value
        self.all_words_dirty = True
        self.data_version += 1
        if self.print_updates:
            print(f"All channels set to {value}")

    def set_channels_bulk(self, values):
        """Set many channels at once from bytes/bytearray/list/tuple."""
        n = min(len(values), self.channels)
        if n <= 0:
            return

        if isinstance(values, (bytes, bytearray)):
            self.dmx_data[:n] = values[:n]
            self.frame[1:n + 1] = values[:n]
        else:
            for i in range(n):
                v = max(0, min(255, values[i]))
                self.dmx_data[i] = v
                self.frame[i + 1] = v

        # Mark packed words that include start-code + updated channels.
        first_word = 0
        last_word = n // 4
        for w in range(first_word, min(last_word + 1, len(self.word_dirty))):
            self.word_dirty[w] = 1
        self.data_version += 1

        if self.print_updates:
            print(f"Bulk update applied to {n} channels")

    def benchmark_updates(self):
        """Measure update-path cost for set_all and per-channel loop."""
        timer_was_running = self.transmitting
        restore_refresh = self.active_refresh_rate
        if timer_was_running:
            # Pause periodic ISR load so benchmark reflects setter cost.
            self.timer.deinit()
            self.transmitting = False

        old_verbose = self.print_updates
        self.print_updates = False

        t0 = time.ticks_us()
        self.set_all(255)
        t_all = time.ticks_diff(time.ticks_us(), t0)

        t1 = time.ticks_us()
        for ch in range(1, self.channels + 1):
            self.set_channel(ch, 0)
        t_single_loop = time.ticks_diff(time.ticks_us(), t1)

        t2 = time.ticks_us()
        bulk_values = bytearray(self.channels)
        for i in range(self.channels):
            bulk_values[i] = 128
        self.set_channels_bulk(bulk_values)
        t_bulk = time.ticks_diff(time.ticks_us(), t2)

        self.print_updates = old_verbose

        if timer_was_running:
            period_ms = int(1000 / restore_refresh)
            self.timer.init(period=period_ms, mode=Timer.PERIODIC, callback=self.update_frame)
            self.transmitting = True

        print("Benchmark (CPU-side update only):")
        print(f"  set_all():          {t_all / 1000:.3f} ms")
        print(f"  512x set_channel(): {t_single_loop / 1000:.3f} ms")
        print(f"  set_channels_bulk():{t_bulk / 1000:.3f} ms")

    def benchmark_live_latency(self, value=255, timeout_ms=2000):
        """Measure command-to-next-sent-frame latency while transmitting."""
        if not self.transmitting:
            print("Start transmission first")
            return

        self.set_all(value)
        target_version = self.data_version
        t0 = time.ticks_us()
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)

        while self.last_sent_version < target_version:
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                print("Live latency timeout")
                return
            time.sleep_ms(1)

        dt_us = time.ticks_diff(time.ticks_us(), t0)
        print(f"Live command->sent latency: {dt_us / 1000:.3f} ms")
    
    def clear_all(self):
        """Set all channels to 0."""
        self.set_all(0)

    def set_lsb_test_pattern(self):
        """Load known bytes into first channels to verify on-wire bit order."""
        if self.channels < 3:
            print("Need at least 3 channels for lsbtest pattern")
            return

        self.set_channel(1, 0x01)
        self.set_channel(2, 0x80)
        self.set_channel(3, 0x55)
        print("LSB test pattern loaded: CH1=0x01 CH2=0x80 CH3=0x55")
        print("Expected LSB-first bits: 0x01 -> 10000000, 0x80 -> 00000001")
    
    def stop(self):
        """Stop DMX transmission."""
        if self.transmitting:
            self.timer.deinit()
            time.sleep_ms(10)  # Allow current frame to complete
            self.sm_ctrl.active(0)
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
        print(f"Refresh rate (requested/active): {self.refresh_rate} / {self.active_refresh_rate} Hz")
        print(f"Frame count: {self.frame_count}")
        print(f"Skipped callbacks: {self.skipped_callbacks}")
        if self.frame_count > 0:
            avg_us = self.sum_update_us / self.frame_count
            print(f"Update time avg/max: {avg_us/1000:.3f} / {self.max_update_us/1000:.3f} ms")
        print("\nFirst 8 channels:")
        for i in range(min(8, self.channels)):
            print(f"  Channel {i+1}: {self.dmx_data[i]}")
        
        if self.transmitting:
            try:
                print(f"\nFIFO status:")
                print(f"  Data SM TX FIFO: {self.sm_data.tx_fifo()} / 8 words (JOIN_TX)")
                print(f"  Control SM TX FIFO: {self.sm_ctrl.tx_fifo()} / 8 words (JOIN_TX)")
            except:
                pass
        print("=" * 40)
    
    def help(self):
        """Display available commands."""
        print("\nAvailable commands:")
        print("  start           - Start DMX transmission")
        print("  stop            - Stop DMX transmission")
        print("  status          - Show current status")
        print("  clear           - Clear all channels to 0")
        print("  c <ch> <val>    - Set channel <ch> to value <val> (1-indexed)")
        print("  all <val>       - Set all channels to value <val>")
        print("  bench           - Benchmark update methods")
        print("  benchlive       - Benchmark live command-to-sent latency")
        print("  lsbtest         - Load LSB test pattern into first channels")
        print("  verbose on/off  - Toggle update prints")
        print("  help            - Show this help message")
        print("  exit            - Exit the program\n")


# ============================================================================
# Interactive Test Interface
# ============================================================================
def main():
    print("=" * 50)
    print("DMX512 PIO Controller - RP2350")
    print("=" * 50)
    
    # Create controller
    dmx = DMXControllerPIO(
        tx_pin=DMX_TX_PIN,
        channels=DMX_CHANNELS,
        refresh_rate=DMX_REFRESH_RATE
    )
    
    dmx.help()

    
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

            elif cmd == "bench":
                dmx.benchmark_updates()

            elif cmd == "benchlive":
                dmx.benchmark_live_latency()

            elif cmd == "lsbtest":
                dmx.set_lsb_test_pattern()

            elif cmd in ("verbose on", "verbose off"):
                dmx.print_updates = (cmd == "verbose on")
                print(f"Update prints: {'ON' if dmx.print_updates else 'OFF'}")
                
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
            
            elif cmd == "help":
                dmx.help()
                    
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