import time

import rp2
from machine import Pin, mem32


# RP2350/Pico 2 W PIO note:
# This demo proves CPU can trigger a PIO IRQ flag that SM0 waits on.
#
# SM0: wait(1, irq, 0) <- CPU sets PIO IRQ0 via IRQ_FORCE register
# SM0: generate square wave -> IRQ4 -> SM1
# SM1: wait IRQ4 -> generate square wave -> IRQ1 -> SM0

PIN_TEST = 0
SM0_ID = 0
SM1_ID = 3

SM0_CLOCK_HZ = 250_000
SM1_CLOCK_HZ = 250_000


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)
def sm0_irq_handshake_and_squarewave():
    """
    SM0: Wait for CPU IRQ0 trigger, then handshake with SM1.
    """
    wrap_target()
    wait(1, irq, 0)         # wait for CPU-triggered IRQ0 in PIO block
    irq(clear, 0)

    set(y, 5)               # loop count
    label("loop")
    set(pins, 1)            # toggle high
    nop()
    set(pins, 0)            # toggle low
    jmp(y_dec, "loop")

    irq(4)                  # signal SM1 via IRQ 4

    wait(1, irq, 1)         # wait for SM1 response via IRQ 1
    irq(clear, 1)

    wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)
def sm1_irq_handshake_and_squarewave():
    """
    SM1: Wait for IRQ 4 from SM0, generate square wave, signal back via IRQ 1.
    """
    wrap_target()
    
    wait(1, irq, 4)         # Wait for IRQ 4 from SM0
    irq(clear, 4)           # Clear IRQ 4
    
    set(y, 5)               # loop
    label("loop")
    set(pins, 1)            # toggle high
    nop()
    set(pins, 0)            # toggle low
    
    jmp(y_dec, "loop")  [5]

    irq(1)                  # Signal SM0 back via IRQ 1
    
    wrap()


def safe_stop(sm):
    if sm is None:
        return
    try:
        sm.active(0)
    except Exception:
        pass


def cpu_force_pio_irq0(pio_index=0):
    """Force PIO IRQ0 on RP2350 using PIO_IRQ_FORCE register."""
    # RP2350 address map (pico-sdk rp2350/addressmap.h):
    # PIO0_BASE=0x50200000, PIO1_BASE=0x50300000, PIO2_BASE=0x50400000
    pio_bases = (0x50200000, 0x50300000, 0x50400000)
    pio_base = pio_bases[pio_index]

    # rp2350 pio.h: PIO_IRQ_FORCE offset is 0x34
    mem32[pio_base + 0x34] = 1 << 0


def main():
    pin = Pin(PIN_TEST, Pin.OUT)
    pin.value(0)

    sm0 = None
    sm1 = None

    try:
        sm0 = rp2.StateMachine(
            SM0_ID,
            sm0_irq_handshake_and_squarewave,
            freq=SM0_CLOCK_HZ,
            set_base=pin,
        )
        sm1 = rp2.StateMachine(
            SM1_ID,
            sm1_irq_handshake_and_squarewave,
            freq=SM1_CLOCK_HZ,
            set_base=pin,
        )

        print("=" * 60)
        print("CPU -> PIO IRQ TRIGGER DEMO (RP2350)")
        print("=" * 60)
        print("Both SMs: GP{}".format(PIN_TEST))
        print("SM0 at {} Hz PIO clock".format(SM0_CLOCK_HZ))
        print("SM1 at {} Hz PIO clock".format(SM1_CLOCK_HZ))
        print()
        print("IRQ Architecture:")
        print("  CPU: write PIO_IRQ_FORCE bit0 (PIO0 + 0x34)")
        print("  SM0: wait IRQ0 -> wave -> IRQ4")
        print("  SM1: wait IRQ4 -> wave -> IRQ1")
        print("  SM0: wait IRQ1 complete, then waits for next CPU trigger")
        print()
        print("Commands:")
        print("  t : trigger one square-wave cycle")
        print("  auto    : trigger continuously every 2 seconds")
        print("  quit    : stop demo")
        print()
        print("(Use Ctrl+C to stop)")
        print("=" * 60)
        print()

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
                    cpu_force_pio_irq0(0)
                    cycle += 1
                    print("CPU forced PIO0 IRQ0 (cycle {})".format(cycle))

                elif cmd == "auto":
                    print("Entering auto-trigger mode. Press Ctrl+C to return to cmd prompt.")
                    try:
                        while True:
                            print("Forcing CPU -> PIO IRQ0 trigger...")
                            cpu_force_pio_irq0(0)
                            cycle += 1
                            print("CPU forced PIO0 IRQ0 (cycle {})".format(cycle))
                            time.sleep(1)
                    except KeyboardInterrupt:
                        print("Auto-trigger mode stopped.")

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
        pin.value(0)
        print("\nDemo stopped.")


if __name__ == "__main__":
    main()
