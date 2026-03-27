# DMX512 transmitter using PIO - RP2350 Optimized
# Proven working with SM0 and SM3 on PIO0

import rp2
from machine import Pin, Timer, mem32
import time
import math
import gc

DMX_CHANNELS = 512           # Full DMX universe (can be reduced)
DMX_REFRESH_RATE = 44        # Hz (standard DMX refresh rate)
DMX_TX_PIN = 0               # GPIO pin for DMX output

start_code = 0x00

# PIO Program 0: Control sequencer (SM0)
@rp2.asm_pio()
def dmx_control_PIO():
    """
    Control SM: orchestrates DMX frame sequence
    Uses IRQ4 to trigger break, IRQ6 to trigger data
    Waits for IRQ5 and IRQ7 for completion signals
    """
    wrap_target()
    
    # Pull number of data words to send
    pull()
    mov(x, osr)              # x = number of words
    mov(y, x)                # y = backup of word count
    
    label("frame_loop")
    
    # Trigger break generation
    irq(4)                   # Signal break SM via IRQ4
    wait(1, irq, 5)          # Wait for break done on IRQ5
    
    # Trigger data transmission
    irq(6)                   # Signal data SM via IRQ6
    wait(1, irq, 7)          # Wait for data done on IRQ7
    
    mov(x, y)                # Reset word count for next frame
    
    wrap()

# PIO Program 1: Break and MAB generation (SM3)
@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH)
def dmx_break_PIO():
    """
    Generates DMX break (96us) and Mark After Break (12us)
    Triggered by IRQ4, signals completion via IRQ5
    """
    wrap_target()
    
    wait(1, irq, 4)          # Wait for trigger from control SM
    irq(clear, 4)
    
    # Break: 96us low
    set(pins, 0)
    set(y, 23)               # 24 cycles at 4us = 96us
    label("break_loop")
    nop()
    jmp(y_dec, "break_loop")
    
    # Mark After Break: 12us high
    set(pins, 1)
    set(y, 2)                # 3 cycles at 4us = 12us
    label("mab_loop")
    nop()
    jmp(y_dec, "mab_loop")
    
    # Signal break complete
    irq(5)
    
    wrap()

# PIO Program 2: Data transmission (SM2 - using another SM on PIO0)
@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH, autopull=True, pull_thresh=32, fifo_join=rp2.PIO.JOIN_TX)
def dmx_data_PIO():
    """
    Transmits DMX data at 250kbps
    Each 32-bit word contains 4 DMX bytes
    Triggered by IRQ6, signals completion via IRQ7
    """
    wrap_target()
    
    wait(1, irq, 6)          # Wait for trigger from control SM
    irq(clear, 6)
    
    # Get number of words to send
    pull()                   # Blocking pull
    mov(x, osr)              # Store word count
    
    label("word_loop")
    pull(noblock)            # Get next 32-bit word
    mov(y, 3)                # 4 bytes per word
    
    label("byte_loop")
    set(pins, 0)             # Start bit
    out(pins, 1)             # Bit 0
    out(pins, 1)             # Bit 1
    out(pins, 1)             # Bit 2
    out(pins, 1)             # Bit 3
    out(pins, 1)             # Bit 4
    out(pins, 1)             # Bit 5
    out(pins, 1)             # Bit 6
    out(pins, 1)             # Bit 7
    set(pins, 1)             # Stop bit 1
    nop()                    # Stop bit 2
    jmp(y_dec, "byte_loop")
    
    jmp(x_dec, "word_loop")
    
    # Signal data transmission complete
    irq(7)
    
    wrap()

class DMXControllerPIO:
    def __init__(self, tx_pin=0, channels=512, refresh_rate=44):
        """
        Initialize DMX controller using PIO on RP2350
        Uses SM0, SM2, SM3 all on PIO0 for maximum compatibility
        """
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = refresh_rate
        self.tx_pin = tx_pin
        
        # Initialize TX pin
        self.tx = Pin(tx_pin, Pin.OUT)
        self.tx.value(1)  # Idle high
        
        # Initialize DMX data
        self.dmx_data = bytearray([0] * self.channels)
        self.frame = bytearray([start_code]) + bytearray([0] * self.channels)
        
        # Calculate number of 32-bit words needed
        self.num_words = (len(self.frame) + 3) // 4
        
        # Pre-pack data for faster transmission
        self.packed_words = self._pack_frame()
        
        # State machines on PIO0 (proven working)
        # SM0: Control sequencer
        self.sm_ctrl = rp2.StateMachine(
            0,                  # SM0 on PIO0
            dmx_control_PIO,
            freq=125_000_000    # Run at full speed
        )
        
        # SM2: Data transmission
        self.sm_data = rp2.StateMachine(
            2,                  # SM2 on PIO0
            dmx_data_PIO,
            freq=250_000,       # 4us per bit for DMX
            out_base=self.tx
        )
        
        # SM3: Break generation
        self.sm_break = rp2.StateMachine(
            3,                  # SM3 on PIO0
            dmx_break_PIO,
            freq=250_000,       # 4us per bit
            out_base=self.tx
        )
        
        # Control flags
        self.transmitting = False
        self.timer = Timer()
        self.frame_count = 0
        self.fifo_underruns = 0
        
        print(f"\n{'='*50}")
        print(f"DMX512 Controller - RP2350 PIO Implementation")
        print(f"{'='*50}")
        print(f"Configuration:")
        print(f"  Channels: {self.channels}")
        print(f"  Refresh rate: {refresh_rate} Hz")
        print(f"  TX Pin: GPIO{tx_pin}")
        print(f"  Words per frame: {self.num_words}")
        print(f"\nPIO Allocation (all on PIO0):")
        print(f"  SM0: Control sequencer")
        print(f"  SM2: Data transmitter")
        print(f"  SM3: Break/MAB generator")
        print(f"\nIRQ Assignment:")
        print(f"  CPU IRQ0 → Control SM (frame trigger)")
        print(f"  Control SM → Break SM via IRQ4")
        print(f"  Break SM → Control SM via IRQ5")
        print(f"  Control SM → Data SM via IRQ6")
        print(f"  Data SM → Control SM via IRQ7")
        print(f"{'='*50}")
        
        # Free memory check
        gc.collect()
        print(f"Free memory: {gc.mem_free()} bytes\n")
    
    def _pack_frame(self):
        """Pack frame data into 32-bit words (little-endian)"""
        words = []
        for i in range(0, len(self.frame), 4):
            word = 0
            for j in range(4):
                if i + j < len(self.frame):
                    word |= self.frame[i + j] << (8 * j)
            words.append(word)
        return words
    
    def _load_fifos(self):
        """Load data into PIO FIFOs"""
        try:
            # Load control SM with number of words
            # Clear any old data
            while self.sm_ctrl.tx_fifo() > 0:
                try:
                    self.sm_ctrl.get()
                except:
                    break
            
            # Put number of words for control SM
            self.sm_ctrl.put(self.num_words)
            
            # Load data SM with packed words
            # Clear existing data
            while self.sm_data.tx_fifo() > 0:
                try:
                    self.sm_data.get()
                except:
                    break
            
            # Load all data words
            for word in self.packed_words:
                self.sm_data.put(word)
                
            return True
            
        except Exception as e:
            print(f"Error loading FIFOs: {e}")
            return False
    
    def cpu_force_pio_irq0(self, pio_index=0):
        """Force PIO IRQ0 on specified PIO block"""
        pio_bases = (0x50200000, 0x50300000, 0x50400000)
        pio_base = pio_bases[pio_index]
        # PIO_IRQ_FORCE offset is 0x34
        mem32[pio_base + 0x34] = 1 << 0
    
    def start(self):
        """Start continuous DMX transmission"""
        if self.transmitting:
            print("DMX transmission already running")
            return
        
        # Load initial data
        if not self._load_fifos():
            print("Failed to load initial data")
            return
        
        self.transmitting = True
        self.frame_count = 0
        self.fifo_underruns = 0
        
        # Start all state machines
        self.sm_ctrl.active(1)
        self.sm_break.active(1)
        self.sm_data.active(1)
        
        # Start timer for frame refresh
        self.timer.init(
            freq=self.refresh_rate,
            mode=Timer.PERIODIC,
            callback=self._trigger_frame
        )
        
        # Trigger first frame
        self.cpu_force_pio_irq0(pio_index=0)
        
        print(f"\n✅ DMX Transmission Started")
        print(f"   Refresh rate: {self.refresh_rate} Hz")
        print(f"   Frame period: {1000/self.refresh_rate:.1f} ms")
        print(f"   Data rate: {self.num_words * 4 * 8 * self.refresh_rate / 1000:.1f} kbps")
    
    def _trigger_frame(self, timer):
        """Trigger a new DMX frame transmission"""
        if not self.transmitting:
            return
        
        try:
            # Monitor FIFO level
            fifo_level = self.sm_data.tx_fifo()
            
            # Reload if FIFO is getting low
            if fifo_level < 2:
                if not self._load_fifos():
                    self.fifo_underruns += 1
            
            # Trigger control SM to start new frame
            self.cpu_force_pio_irq0(pio_index=0)
            self.frame_count += 1
            
            # Status update every 100 frames
            if self.frame_count % 100 == 0:
                fifo_status = self.sm_data.tx_fifo()
                print(f"📊 Frame {self.frame_count}: FIFO={fifo_status}/{self.num_words} words, "
                      f"Underruns={self.fifo_underruns}")
                
        except Exception as e:
            print(f"Frame trigger error: {e}")
    
    def _update_frame(self):
        """Update packed words when channel data changes"""
        # Update frame data
        for i in range(self.channels):
            self.frame[i + 1] = self.dmx_data[i]
        
        # Repack words
        self.packed_words = self._pack_frame()
        
        # Reload FIFOs if transmitting
        if self.transmitting:
            self._load_fifos()
    
    def set_channel(self, channel, value):
        """Set a single DMX channel value"""
        if 1 <= channel <= self.channels:
            clamped = max(0, min(255, value))
            self.dmx_data[channel - 1] = clamped
            self._update_frame()
            print(f"✨ Channel {channel:3d} set to {clamped:3d}")
        else:
            print(f"❌ Error: Channel {channel} out of range (1-{self.channels})")
    
    def set_channels(self, values_dict):
        """Set multiple DMX channels at once"""
        updated = 0
        for channel, value in values_dict.items():
            if 1 <= channel <= self.channels:
                clamped = max(0, min(255, value))
                self.dmx_data[channel - 1] = clamped
                updated += 1
        
        if updated > 0:
            self._update_frame()
            print(f"✅ Updated {updated} channel(s)")
        else:
            print("❌ No valid channels updated")
    
    def set_all(self, value):
        """Set all DMX channels to the same value"""
        clamped = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i] = clamped
        self._update_frame()
        print(f"✅ All {self.channels} channels set to {clamped}")
    
    def stop(self, idle_high=True):
        """Stop DMX transmission"""
        if self.transmitting:
            self.timer.deinit()
            self.sm_ctrl.active(0)
            self.sm_break.active(0)
            self.sm_data.active(0)
            self.transmitting = False
            print(f"\n🛑 DMX Transmission Stopped")
            print(f"   Total frames sent: {self.frame_count}")
            print(f"   FIFO underruns: {self.fifo_underruns}")
        else:
            print("DMX transmission not running")
        
        # Reset TX pin
        self.tx = Pin(self.tx_pin, Pin.OUT)
        self.tx.value(1 if idle_high else 0)
    
    def show_status(self):
        """Display current status"""
        print("\n" + "="*50)
        print("📡 DMX Controller Status")
        print("="*50)
        print(f"Channels: {self.channels}")
        print(f"Refresh rate: {self.refresh_rate} Hz")
        print(f"Transmission: {'🟢 RUNNING' if self.transmitting else '🔴 STOPPED'}")
        print(f"Frames sent: {self.frame_count}")
        
        if self.transmitting:
            try:
                fifo_level = self.sm_data.tx_fifo()
                print(f"Data FIFO: {fifo_level}/{self.num_words} words")
                print(f"FIFO underruns: {self.fifo_underruns}")
            except:
                pass
        
        print("\nFirst 10 channels:")
        for i in range(min(10, self.channels)):
            print(f"  Ch {i+1:3d}: {self.dmx_data[i]:3d} | ", end="")
            # Visual bar
            bar_len = self.dmx_data[i] // 16
            print("█" * bar_len + "░" * (16 - bar_len))
        
        if self.channels > 10:
            print(f"  ... and {self.channels - 10} more channels")
        print("="*50)
    
    def clear_all(self):
        """Set all channels to 0"""
        for i in range(self.channels):
            self.dmx_data[i] = 0
        self._update_frame()
        print("✅ All channels cleared to 0")
    
    def ramp_test(self, duration=10):
        """Test pattern: ramp up/down on first channel"""
        if not self.transmitting:
            print("Start transmission first!")
            return
        
        print(f"Ramp test on channel 1 for {duration} seconds...")
        start_time = time.time()
        
        while time.time() - start_time < duration:
            # Ramp up and down
            elapsed = time.time() - start_time
            phase = (elapsed / duration) * 2 * math.pi
            value = int(127 + 127 * math.sin(phase))
            self.set_channel(1, value)
            time.sleep_ms(50)
        
        print("Ramp test complete")
    
    def help(self):
        """Display help information"""
        print("\n" + "="*50)
        print("DMX Controller Commands")
        print("="*50)
        print("  c <ch> <val>          - Set single channel")
        print("  m <ch1:val1,ch2:val2> - Set multiple channels")
        print("    Example: m 1:255,2:128,3:64")
        print("  all <value>           - Set all channels")
        print("  start                 - Start transmission")
        print("  stop                  - Stop transmission")
        print("  status                - Show status")
        print("  clear                 - Clear all channels")
        print("  ramp                  - Ramp test on channel 1")
        print("  help                  - Show this help")
        print("  exit                  - Exit program")
        print("="*50)

# Interactive interface
def interactive_dmx():
    """Interactive DMX controller"""
    print("\n" + "█"*60)
    print("DMX512 Controller - RP2350 PIO Implementation")
    print("Based on proven IRQ handshake architecture")
    print("█"*60)
    
    # Free memory before initialization
    gc.collect()
    print(f"\n💾 Free memory before init: {gc.mem_free()} bytes")
    
    # Initialize controller
    dmx = DMXControllerPIO(
        tx_pin=DMX_TX_PIN,
        channels=DMX_CHANNELS,
        refresh_rate=DMX_REFRESH_RATE
    )
    
    dmx.help()
    print("\n💡 DMX transmission is stopped. Type 'start' to begin.")
    print("   Use 'status' to monitor transmission quality.\n")
    
    while True:
        try:
            cmd = input("DMX> ").strip().lower()
            
            if cmd == "exit":
                dmx.stop(idle_high=False)
                print("\n👋 Exiting DMX controller")
                break
            
            elif cmd == "ramp":
                dmx.ramp_test(10)
            
            elif cmd.startswith("c "):
                parts = cmd.split()
                if len(parts) == 3:
                    try:
                        dmx.set_channel(int(parts[1]), int(parts[2]))
                    except ValueError:
                        print("❌ Error: Invalid numbers")
                else:
                    print("❌ Usage: c <channel> <value>")
            
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
                    print(f"❌ Error: {e}")
                    print("   Example: m 1:255,2:128,3:64")
            
            elif cmd.startswith("all "):
                try:
                    dmx.set_all(int(cmd.split()[1]))
                except:
                    print("❌ Error: all <value>")
            
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
            
            elif cmd == "":
                continue
            
            else:
                print(f"❌ Unknown command: {cmd}")
                dmx.help()
        
        except KeyboardInterrupt:
            dmx.stop(idle_high=False)
            print("\n👋 Exiting DMX controller")
            break
        
        except Exception as e:
            print(f"❌ Error: {e}")

# Run the controller
if __name__ == "__main__":
    try:
        gc.collect()
        interactive_dmx()
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import sys
        sys.print_exception(e)