import time

import rp2
from machine import Pin, mem32


# RP2350/Pico 2 W PIO note:
# This demo proves CPU can trigger a PIO IRQ flag that SM0 waits on.
#
# SM0: wait(1, irq, 0) <- CPU sets PIO IRQ0 via IRQ_FORCE register
# SM0: generate square wave -> IRQ4 -> SM1
# SM1: wait IRQ4 -> generate square wave -> IRQ1 -> SM0

PIN_TX = 0 # Signal pin for both SMs to toggle
PIN_TRIGGER = 1 # Pin to trigger scope

SM0_ID = 0
SMblock = SM0_ID // 4  # PIO block index (0-2)
print(f"Using SM{SM0_ID} in PIO block {SMblock}")
SM1_ID = 1
SM2_ID = 2

SM0_CLOCK_HZ = 150_000_000
SM1_CLOCK_HZ = 1_000_000
SM2_CLOCK_HZ = 100_000

@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, sideset_init=rp2.PIO.OUT_HIGH)
def sm0_irq_handshake_and_squarewave():
    """
    SM0: Wait for CPU IRQ0 trigger, then handshake with SM1.
    """
    wrap_target()
    wait(1, irq, 0)             # 1 wait for CPU-triggered IRQ0 in PIO block

    set(y, 2)       .side(1)    # 3 loop count
    irq(4)                      # 8 signal SM1 via IRQ 4
    wait(1, irq, 1)             # 9 wait for SM1 response via IRQ 1
    nop()           .side(0)    # 11 toggle low
   
    wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_HIGH, sideset_init=rp2.PIO.OUT_HIGH, out_shiftdir=rp2.PIO.SHIFT_LEFT, autopull=True, pull_thresh=32, fifo_join=rp2.PIO.JOIN_TX)
def sm1_irq_handshake_test():
    """
    SM1: Wait for IRQ 4 from SM0, generate square wave, signal back via IRQ 1.
    """
    wrap_target()
    
    wait(1, irq, 4)             # 1 Wait for IRQ 4 from SM0  / Trigger pin high
    set(x, 3)
    label("word_loop")
    set(y, 7)                   # 2 Loop counter for Bit_loop
    label("bit_loop")
    out(pins, 1)                # 3 Output bit to pin and shift right
    jmp(y_dec, "bit_loop")      # 5 Loop for square wave duration
    jmp(x_dec, "word_loop")     # 6 Loop for next word in FIFO
    irq(1)         .side(1)     # 7 Signal SM0 back via IRQ 1 / Triger pin low

    wrap()

def main():
    sm0 = None
    sm1 = None

    try:
        sm0 = rp2.StateMachine(
            SM0_ID,
            sm0_irq_handshake_and_squarewave,
            freq=SM0_CLOCK_HZ,
            set_base=Pin(PIN_TX),
            sideset_base=Pin(PIN_TRIGGER)
        )
        sm1 = rp2.StateMachine(
            SM1_ID,
            sm1_irq_handshake_test,
            freq=SM1_CLOCK_HZ,
            set_base=Pin(PIN_TX),
            out_base=Pin(PIN_TX),
            sideset_base=Pin(PIN_TX)
            
        )

        print("=" * 60)
        print("Commands:")
        print("  t : trigger one square-wave cycle")
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
                    if sm1.tx_fifo() < 8:
                        sm1.put(0b10101010111101111111001111110001)  # Debug: send data to SM1 TX FIFO on unknown command
                        print(f"FIFO level after put: {sm1.tx_fifo()}")
                    else:
                        print("SM1 TX FIFO is full, cannot put more data.")   
                    
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
