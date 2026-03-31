# MicroPython v1.27.0 on 2025-12-09; 
# RP2350/ Raspberry Pico 2 W  Deveopemt board
# DMX512 Controller

import rp2
from machine import Pin, Timer, mem32
import time

# DMX Configuration
DMX_CHANNELS = 512      # Number of DMX channels to transmit (1-512)
DMX_REFRESH_RATE = 44   # Desired refresh rate in Hz (DMX standard is 44Hz for 512 channels)
DMX_TX_PIN = 0          # DMX signal output pin (GPIO0)
PIN_TRIGGER = 1         # Pin to trigger scope (GPIO1)
start_code = 0x00

DEBUG = False

# State Machine IDs - All in PIO0 for IRQ communication
PIO_BLOCK = 0
SM_CTRL = 0
SM_CTRL_CLOCK_HZ = 6_000_000
SM_DATA = 1
SM1_DATA_CLOCK_HZ = 1_500_000

# ============================================================================
# PIO Program 1: Control SM (18 instructions)
# ============================================================================
@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, sideset_init=rp2.PIO.OUT_HIGH)
def sm_DMX_control():
    """
    SM0: Wait for CPU IRQ0 trigger, then handshake with SM1.
    """
    BREAK = 21
    MAB = 21

    wait(1, irq, 0)         .side(0)    # 1 wait for CPU-triggered IRQ0 in PIO block // Trigger pin low
    pull()                              # 2 Pull number of words (one word = 4 DMX channels) from TX FIFO       
    mov(y, osr)                         # 3 Save Nummber of words from FIFO to y register (one word = 4 DMX channels)

    wrap_target()
    wait(1, irq, 0)         .side(0)    # 4 wait for CPU-triggered IRQ0 in PIO block // Trigger pin low 

    set(x, BREAK)                       # 5 loop count for Break duration (92us @ 6MHz)
    set(pins, 0)            [5]         # 6 Break low
    label("Break")              
    nop()                   [7]         # 7 
    nop()                   [7]         # 8 
    nop()                   [7]         # 9              
    jmp(x_dec,"Break")                  # 10 Loop for Break duration       

    set(pins, 0)                        # 11 Mark after Break high duration loop (12us @ 6MHz)// Trigger pin high
    set(x, MAB)             [7]         # 12 loop count for Mark After Break duration
    label("MAB")
    set(pins, 1)            [1]         # 13 Mark After Break low    
    jmp(x_dec, "MAB")                   # 14 Mark After Break duration loop  

    mov(x, y)               .side(1)    # 15 loop count, number of words @ 4DMX channels
    label("channel_loop")
    irq(4)                              # 16 signal SM1 via IRQ 4 to send 4 Channels so one word @ 4 x 8Bit's
    wait(1, irq, 5)                     # 17 wait for SM1 response via IRQ 5
    jmp(x_dec, "channel_loop")          # 18 loop back if x > 0
    nop()                   .side(0)    # 19 2 x stop bit and trigger low // Trigger pin low
    wrap()

# ============================================================================
# PIO Program 2: Data SM (10 instructions )
# ============================================================================
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_HIGH, sideset_init=rp2.PIO.OUT_HIGH, 
             out_shiftdir=rp2.PIO.SHIFT_LEFT, autopull=True, pull_thresh=32, fifo_join=rp2.PIO.JOIN_TX)
def sm_DMX_data():
    """
    SM1: Wait for IRQ 4 from SM0, generate square wave, signal back via IRQ 1.
    """
    
    wrap_target()

    wait(1, irq, 4)                     # 1 Wait for IRQ 4 from SM0  / Trigger pin high
    set(x, 3)               .side(0)[4] # 2 4 Bytes in one word // start bit low
    label("byte_loop")
    set(y, 7)                           # 3 Loop counter for Bit_loop
    label("bit_loop")
    out(pins, 1)                    [4] # 4 Output bit to pin and shift right
    jmp(y_dec, "bit_loop")              # 5 Loop for square wave duration
    jmp(x_dec, "byte_loop") .side(0)    # 6 Loop for next word in FIFO
    set(pins, 1)                        # 7 Stop bit hi
    irq(5)                  .side(1)    # 7 Signal SM0 back via IRQ 1 / Triger pin low

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
        # Pulse to show activity on scope during setup
        self.tx.value(1)
        self.tx.value(0)  
        
        # DMX data buffers
        self.dmx_data = bytearray([0] * self.channels)
        for i in range(self.channels):
            self.dmx_data[i] = i  # Initialize all channels to 0

        self.frame = bytearray([start_code]) + bytearray([0] * self.channels)
        
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
        
        self.n_words = 0
        if DEBUG:
            print(f"DMX Controller initialized: {self.channels} channels, {refresh_rate}Hz")
            print(f"SMs: CTRL={SM_CTRL}, DATA={SM_DATA} all in PIO0")
            print(f"Total instructions in PIO0: 14 + 10 + 7 = 31 (fits within 32 limit)")
            print(f"Control + Data SM: FIFO joined (8-word TX buffer)")
        
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
        
        # Start all state machines
        self.sm_data.active(1)
        self.sm_ctrl.active(1)
        time.sleep_ms(100)  # Allow SMs to initialize
        
        self.transmitting = True
        
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
        period_ms = int(1000 / self.refresh_rate)
        if DEBUG:
            print(f"[DEBUG] Starting timer with period: {period_ms} ms ({self.refresh_rate} Hz)")
        self.timer.init(period=period_ms, mode=Timer.PERIODIC, callback=self.update_frame)
        print("DMX transmission initialized")
    
    def update_frame(self, timer):
        # Timer callback: Load new frame data and trigger transmission.
        if DEBUG:
            print(f"[DEBUG] Timer callback: update_frame CALLED at {time.ticks_ms()} ms")  
            print(f"[DEBUG] FIFO level data state machine: {self.sm_data.tx_fifo()}/8")  
        if not self.transmitting:
            return
        
        start_time = time.ticks_us()
        if DEBUG:
            print(f"\n[DEBUG] === Frame Update Started at {time.ticks_ms()} ms ===")
        
        try:
            # Preload first 8 words (fill the 8-word JOIN_TX buffer)
            preload_start = time.ticks_us()
            words_loaded = 0
            fifo_level = self.sm_data.tx_fifo()
            if DEBUG:
                print(f"[DEBUG] FIFO level data state machine before preload: {fifo_level}/8")
            
            for i in range(0, min(8 * 4, len(self.frame)), 4):
                word = 0
                for j in range(4):
                    if i + j < len(self.frame):
                        word |= self.frame[i + j] << (8 * j)
                self.sm_data.put(word)
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
            remaining_words = self.n_words - words_loaded
            #print(f"[DEBUG] Remaining words to load: {remaining_words}")
            
            if remaining_words > 0:
                load_start = time.ticks_us()
                words_loaded_now = words_loaded
                
                for idx in range(words_loaded, self.n_words):
                    i = idx * 4
                    word = 0
                    for j in range(4):
                        if i + j < len(self.frame):
                            word |= self.frame[i + j] << (8 * j)
                    
                    # Check FIFO level and wait if needed
                    fifo_level = self.sm_data.tx_fifo()
                    while self.sm_data.tx_fifo() >= 7:
                        time.sleep_us(10)

                    
                    self.sm_data.put(word)
                    words_loaded_now += 1
                    
                
                load_time = time.ticks_diff(time.ticks_us(), load_start)
                if DEBUG:
                    print(f"[DEBUG] Loaded {remaining_words} remaining words (took {load_time} us)")
            
            # Final status
            final_fifo = self.sm_data.tx_fifo()
            total_time = time.ticks_diff(time.ticks_us(), start_time)
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
                print(f"  Control SM TX FIFO: {self.sm_ctrl.tx_fifo()} / 8 words (JOIN_TX)")
            except:
                pass
        print("=" * 40)


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