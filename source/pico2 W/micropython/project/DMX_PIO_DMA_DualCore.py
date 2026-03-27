# MicroPython v1.27.0 on 2025-12-09; 
# RP2350/Pico 2 W  
# DMX512 Controller with DMA - Corrected API

import rp2
from machine import Pin, Timer, mem32
import time
import array

# DMX Configuration
DMX_CHANNELS = 512
DMX_REFRESH_RATE = 44
DMX_TX_PIN = 0
start_code = 0x00

# State Machine IDs
SM_CTRL = 0
SM_BREAK = 1
SM_DATA = 2
PIO_BLOCK = 0

print(f"Using PIO{PIO_BLOCK}, SMs: CTRL={SM_CTRL}, BREAK={SM_BREAK}, DATA={SM_DATA}")

# ============================================================================
# PIO Programs
# ============================================================================
@rp2.asm_pio()
def dmx_control_PIO():
    wait(1, irq, 0)
    pull()
    mov(x, osr)
    mov(y, x)
    wrap_target()
    wait(1, irq, 0)
    irq(1)
    wait(1, irq, 2)
    mov(x, y)
    label("word_loop")
    irq(1)
    wait(1, irq, 2)
    jmp(x_dec, "word_loop")
    wrap()

@rp2.asm_pio(
    out_init=rp2.PIO.OUT_HIGH,
    autopull=True,
    pull_thresh=32,
    fifo_join=rp2.PIO.JOIN_TX,
    out_shiftdir=rp2.PIO.SHIFT_RIGHT
)
def send_dmx_Byte_PIO():
    wrap_target()
    wait(1, irq, 1)
    mov(y, 3)
    mov(x, 7)
    label("byte_loop")  
    set(pins, 0)
    label("bit_loop")
    out(pins, 1)
    jmp(x_dec, "bit_loop")
    set(pins, 1)
    mov(x, 7)
    jmp(y_dec, "byte_loop")
    irq(2)
    wrap()

@rp2.asm_pio(out_init=rp2.PIO.OUT_HIGH)
def send_dmx_break_PIO():
    wrap_target()
    wait(1, irq, 1)
    set(pins, 0)
    nop() [23]
    set(pins, 1)
    nop() [2]
    irq(2)
    wrap()

# ============================================================================
# DMX Controller with DMA - Using Properties API
# ============================================================================
class DMXControllerDMA:
    def __init__(self, tx_pin=0, channels=512, refresh_rate=44):
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = min(refresh_rate, 44)
        self.tx_pin = tx_pin
        
        # Initialize TX pin
        self.tx = Pin(tx_pin, Pin.OUT)
        self.tx.value(1)
        
        # Frame buffer
        self.frame_buffer = array.array('B', [start_code] + [0] * self.channels)
        self.dmx_data = bytearray([0] * self.channels)
        
        # PIO clock
        DMX_CLOCK = 250_000
        
        # Create state machines
        print("Creating state machines...")
        self.sm_ctrl = rp2.StateMachine(SM_CTRL, dmx_control_PIO, freq=DMX_CLOCK)
        self.sm_break = rp2.StateMachine(SM_BREAK, send_dmx_break_PIO, freq=DMX_CLOCK, set_base=self.tx)
        self.sm_data = rp2.StateMachine(SM_DATA, send_dmx_Byte_PIO, freq=DMX_CLOCK, set_base=self.tx)
        
        # DMA setup
        print("Setting up DMA...")
        self.pio_base = 0x50200000
        self.pio_txfifo_addr = self.pio_base + 0x10 + (SM_DATA * 0x04)
        self.dreq_tx = 4 + SM_DATA
        
        # Create DMA channel
        self.dma = rp2.DMA()
        self.dma_channel = self.dma.channel
        self.dma_active = False
        self.transmitting = False
        self.timer = Timer()
        self.frame_count = 0
        
        # Create DMA control word using pack_ctrl
        self.dma_ctrl = self.dma.pack_ctrl(
            enable=True,
            treq_sel=self.dreq_tx,
            inc_read=True,
            inc_write=False,
            size=0  # 0 = byte transfers
        )
        
        # Set the control word once
        self.dma.ctrl = self.dma_ctrl
        
        # Set completion handler
        self.dma.irq(self.dma_complete_handler)
        
        print(f"DMX Controller initialized:")
        print(f"  Channels: {self.channels}")
        print(f"  Refresh rate: {self.refresh_rate} Hz")
        print(f"  Frame size: {len(self.frame_buffer)} bytes")
        print(f"  Words per frame: {(len(self.frame_buffer) + 3) // 4}")
        print(f"  DMA Channel: {self.dma_channel}")
        print(f"  DMA DREQ: {self.dreq_tx}")
        print(f"  DMA Control: 0x{self.dma_ctrl:08X}")
        print(f"  PIO TX FIFO: 0x{self.pio_txfifo_addr:08X}")
    
    def force_pio_irq0(self):
        """Force PIO IRQ0 to trigger control SM."""
        mem32[self.pio_base + 0x34] = 1 << 0
        time.sleep_us(1)
    
    def update_frame_buffer(self):
        """Update frame buffer with current channel data."""
        self.frame_buffer[0] = start_code
        for i in range(self.channels):
            self.frame_buffer[i + 1] = self.dmx_data[i]
    
    def start_dma_transfer(self):
        """
        Start DMA transfer using property-based API.
        Set read, write, count attributes, then activate.
        """
        total_bytes = len(self.frame_buffer)
        total_words = (total_bytes + 3) // 4
        
        # Wait for previous DMA to complete
        if self.dma_active:
            timeout = 5000
            start = time.ticks_us()
            while self.dma_active and time.ticks_diff(time.ticks_us(), start) < timeout:
                time.sleep_us(10)
            
            if self.dma_active:
                self.dma.active(0)
                self.dma_active = False
        
        # Set DMA transfer parameters using properties (NOT config method)
        self.dma.read = self.frame_buffer
        self.dma.write = self.pio_txfifo_addr
        self.dma.count = total_bytes
        
        # Clear control SM FIFO
        while self.sm_ctrl.tx_fifo():
            try:
                self.sm_ctrl.get()
            except:
                break
        
        # Load word count into control SM
        self.sm_ctrl.put(total_words)
        
        # Start DMA transfer
        self.dma.active(1)
        self.dma_active = True
        
        # Trigger control SM to start frame
        self.force_pio_irq0()
        
        self.frame_count += 1
        return total_words
    
    def dma_complete_handler(self, dma):
        """DMA completion callback."""
        self.dma_active = False
    
    def start(self):
        """Start continuous DMX transmission."""
        if self.transmitting:
            print("DMX transmission already running")
            return
        
        print("Starting DMX transmission...")
        
        # Update frame buffer with initial data
        self.update_frame_buffer()
        
        # Start state machines
        self.sm_break.active(1)
        self.sm_data.active(1)
        self.sm_ctrl.active(1)
        
        time.sleep_ms(10)
        
        self.transmitting = True
        
        # Start first frame
        words = self.start_dma_transfer()
        print(f"First frame sent: {words} words (DMA)")
        
        # Start timer for frame updates
        self.timer.init(freq=self.refresh_rate, mode=Timer.PERIODIC, callback=self._send_frame)
        print(f"DMX transmission running at {self.refresh_rate} Hz")
    
    def _send_frame(self, timer):
        """Timer callback: Update frame and start DMA transfer."""
        if not self.transmitting:
            return
        
        # Update frame buffer with latest channel data
        self.update_frame_buffer()
        
        # Start DMA transfer
        words = self.start_dma_transfer()
        
        # Print status every 100 frames
        if self.frame_count % 100 == 0:
            fifo_level = self.sm_data.tx_fifo()
            print(f"Frame {self.frame_count}: {words} words, FIFO: {fifo_level}/8")
    
    def set_channel(self, channel, value):
        """Set a single DMX channel (1-indexed)."""
        if 1 <= channel <= self.channels:
            value = max(0, min(255, value))
            self.dmx_data[channel - 1] = value
            print(f"Channel {channel} = {value}")
        else:
            print(f"Error: Channel {channel} out of range (1-{self.channels})")
    
    def set_all(self, value):
        """Set all channels to the same value."""
        value = max(0, min(255, value))
        for i in range(self.channels):
            self.dmx_data[i] = value
        print(f"All channels set to {value}")
    
    def clear_all(self):
        """Clear all channels to 0."""
        self.set_all(0)
    
    def stop(self):
        """Stop DMX transmission."""
        if self.transmitting:
            self.timer.deinit()
            time.sleep_ms(10)
            self.sm_ctrl.active(0)
            self.sm_break.active(0)
            self.sm_data.active(0)
            if self.dma_active:
                self.dma.active(0)
            self.transmitting = False
            print("DMX transmission stopped")
        
        self.tx = Pin(self.tx_pin, Pin.OUT)
        self.tx.value(1)
    
    def status(self):
        """Display current status."""
        print("\n" + "=" * 50)
        print("DMX DMA Controller Status")
        print("=" * 50)
        print(f"Channels: {self.channels}")
        print(f"Refresh rate: {self.refresh_rate} Hz")
        print(f"Transmitting: {self.transmitting}")
        print(f"DMA active: {self.dma_active}")
        print(f"DMA Channel: {self.dma_channel}")
        print(f"Frames sent: {self.frame_count}")
        print("\nFirst 8 channels:")
        for i in range(min(8, self.channels)):
            print(f"  Channel {i+1}: {self.dmx_data[i]}")
        
        if self.transmitting:
            try:
                print(f"\nFIFO status:")
                print(f"  Data SM TX FIFO: {self.sm_data.tx_fifo()} / 8 words")
                print(f"  Control SM TX FIFO: {self.sm_ctrl.tx_fifo()} words")
            except:
                pass
        print("=" * 50)


# ============================================================================
# Main
# ============================================================================
def main():
    print("\n" + "=" * 50)
    print("DMX512 DMA Controller - RP2350")
    print("=" * 50)
    print("Features:")
    print("  - DMA transfers using property-based API")
    print("  - Supports full 512 channels at 44Hz")
    print("  - Hardware-accelerated DMX timing")
    print("=" * 50)
    
    dmx = DMXControllerDMA(
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