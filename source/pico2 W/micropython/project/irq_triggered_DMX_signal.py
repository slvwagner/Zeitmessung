import time

import rp2
from machine import Pin, mem32


# RP2350/Pico 2 W PIO note:
# This demo proves CPU can trigger a PIO IRQ flag that SM0 waits on.
#
# SM0: wait(1, irq, 0) <- CPU sets PIO IRQ0 via IRQ_FORCE register
# SM0: generate Break and MAB -> IRQ4 -> SM1
# SM1: wait IRQ4 -> bit pattern -> IRQ1 -> SM0

PIN_TX = 0 # Signal pin for both SMs to toggle
PIN_TRIGGER = 1 # Pin to trigger scope

DMX_CHANNELS = 8  # Number of DMX channels to send (to be multiple of 4 channels )

SM0_ID = 0
SM0_CLOCK_HZ = 6_000_000

SM1_ID = 1
SM1_CLOCK_HZ = 1_500_000


SMblock = SM0_ID // 4  # PIO block index (0-2)
print(f"Using SM{SM0_ID} in PIO block {SMblock}")

@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, sideset_init=rp2.PIO.OUT_HIGH)
def sm_DMX_control():
    """
    SM0: Wait for CPU IRQ0 trigger, then handshake with SM1.
    """
    BREAK = 21
    MAB = 21

    pull()                              # 1 Pull number of words (one word = 4 DMX channels) from TX FIFO       
    mov(y, osr)                         # 2 Save Nummber of words from FIFO to y register (one word = 4 DMX channels)

    wrap_target()
    wait(1, irq, 0)         .side(0)    # 3 wait for CPU-triggered IRQ0 in PIO block // Trigger pin low 

    set(x, BREAK)                       # 4 loop count for Break duration (92us @ 6MHz)
    set(pins, 0)            [5]         # 5 Break low
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
    set(pins, 1)            .side(0)    # 18 2 x stop bit and trigger low // Trigger pin low
    wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_HIGH, sideset_init=rp2.PIO.OUT_HIGH, 
             out_shiftdir=rp2.PIO.SHIFT_RIGHT, autopull=True, pull_thresh=32, fifo_join=rp2.PIO.JOIN_TX)
def sm_DMX_data():
    """
    SM1: Wait for IRQ 4 from SM0, generate bit pattern (DMX channels), signal back via IRQ 1.
    """
    
    wrap_target()

    wait(1, irq, 4)                     # 1 Wait for IRQ 4 from SM0  
    set(x, 3)                           # 2 4 Bytes in one word 
    label("byte_loop")
    set(y, 7)               .side(0)[4] # 3 Loop counter for Bit_loop // Start bit low
    label("bit_loop")             
    out(pins, 1)                    [4] # 4 Output bit to pin and shift right
    jmp(y_dec, "bit_loop")              # 5 Loop bit loop
    set(pins, 1)                    [4] # 6 Stop bit high
    nop()                           [3] # 7 Stop bit high
    jmp(x_dec, "byte_loop")             # 8 Loop for next word in FIFO
    irq(5)                  .side(1)    # 9 Signal SM0 back via IRQ 1 / Triger pin low

    wrap()

def main():
    sm0 = None
    sm1 = None

    try:
        sm0 = rp2.StateMachine(
            SM0_ID,
            sm_DMX_control,
            freq=SM0_CLOCK_HZ,
            set_base=Pin(PIN_TX),
            sideset_base=Pin(PIN_TRIGGER)
        )
        sm1 = rp2.StateMachine(
            SM1_ID,
            sm_DMX_data,
            freq=SM1_CLOCK_HZ,
            set_base=Pin(PIN_TX),
            out_base=Pin(PIN_TX),
            sideset_base=Pin(PIN_TX)
        )

        print("=" * 60)
        print("Commands:")
        print("  t : trigger one cycle")
        print("  auto    : trigger continuously every 2 seconds")
        print("  quit    : stop demo")
        print()
        print("(Use Ctrl+C to stop)")
        print("=" * 60)

        # Start both SMs; SM0 will block on IRQ0 until CPU forces it.
        sm0.active(1)
        sm1.active(1)
        print("Both SMs started; SM0 waiting for CPU IRQ0.")
        print()

        # Load the number of words (one word = 4 DMX channels) to send into SM0's TX FIFO
        num_words = DMX_CHANNELS // 4  # Example: send 5 words (20 DMX channels
        sm0.put(num_words)
        print(f"Loaded {num_words} words (for {DMX_CHANNELS} DMX channels) into SM0 TX FIFO.")

        try:
            cycle = 0
            while True:
                cmd = input("cmd> ").strip().lower()

                if cmd == "t":
                    print("Forcing CPU -> PIO IRQ0 trigger...")
                    cpu_force_pio_irq0(statmachine_block=SMblock)  
                    cycle += 1
                    print("CPU forced PIO0 IRQ0 (cycle {})".format(cycle))
                    print(f"FIFO level: {sm1.tx_fifo()}")

                elif cmd == "l":
                    for ii in range(4):
                        if sm1.tx_fifo() < 8:
                            sm1.put(0b00000001000000110000011100001111)  # Debug: send data to SM1 TX FIFO on unknown command
                            time.sleep_ms(20)
                            print(f"FIFO level after put: {sm1.tx_fifo()}")
                        else:
                            print("SM1 TX FIFO is full, cannot put more data.")   
                            break
                    
                elif cmd in ("q", "quit", "exit"):
                    print("Exit command received.")
                    break

                else:
                    print("Unknown command: {}".format(cmd))
                    print("Use t, auto, or quit.")               

        except KeyboardInterrupt:
            pass
    

    except KeyboardInterrupt:
        print("Stopped by user")
    finally:
        safe_stop(sm0)
        safe_stop(sm1)
        print("\nDemo stopped.")

def cpu_force_pio_irq0(statmachine_block=0):
    """Force PIO IRQ0 on RP2350 using PIO_IRQ_FORCE register."""
    # RP2350 address map (pico-sdk rp2350/addressmap.h):
    # PIO0_BASE=0x50200000, PIO1_BASE=0x50300000, PIO2_BASE=0x50400000
    pio_bases = (0x50200000, 0x50300000, 0x50400000)
    pio_base = pio_bases[statmachine_block]  # Ensure block index is valid (0-2)

    # rp2350 pio.h: PIO_IRQ_FORCE offset is 0x34
    mem32[pio_base + 0x34] = 1 << 0

def safe_stop(sm):
    if sm is None:
        return
    try:
        sm.active(0)
    except Exception:
        pass

if __name__ == "__main__":
    main()
