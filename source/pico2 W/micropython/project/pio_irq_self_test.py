from rp2 import PIO, StateMachine, asm_pio
import time


IRQ0 = getattr(PIO, "IRQ0", getattr(PIO, "IRQ_SM0", 0x100))
IRQ1 = getattr(PIO, "IRQ1", getattr(PIO, "IRQ_SM1", 0x200))
IRQ2 = getattr(PIO, "IRQ2", getattr(PIO, "IRQ_SM2", 0x400))
IRQ3 = getattr(PIO, "IRQ3", getattr(PIO, "IRQ_SM3", 0x800))
EXPECTED_MASK = IRQ0 | IRQ1 | IRQ2 | IRQ3


@asm_pio()
def _pio_irq_cycle():
    wrap_target()
    irq(0)
    irq(1)
    irq(2)
    irq(3)
    wrap()


class PioIrqSelfTest:
    def __init__(self, pio_id=0, sm_id=0, freq=10000):
        self.pio_id = pio_id
        self.sm_id = sm_id
        self.freq = freq
        self._seen_mask = 0
        self._events = []
        self._pio = None
        self._sm = None

    def _irq_handler(self, pio):
        flags = pio.irq().flags() & EXPECTED_MASK
        self._seen_mask |= flags
        self._events.append(flags)
        print("PIO IRQ callback flags=0x{:02x} seen=0x{:02x}".format(flags, self._seen_mask))

    def run(self, timeout_ms=1000):
        print("Starting PIO IRQ self-test on PIO {} SM {}".format(self.pio_id, self.sm_id))
        print("Expecting flags: 0x{:02x}".format(EXPECTED_MASK))

        self._seen_mask = 0
        self._events = []
        self._pio = PIO(self.pio_id)
        try:
            self._sm = StateMachine(self.sm_id, _pio_irq_cycle, freq=self.freq)
        except ValueError as exc:
            if "freq out of range" in str(exc):
                print("SELF-TEST CONFIG ERROR: freq={} is too low for this board".format(self.freq))
                print("Try again with a higher freq, for example 10000 or 20000")
            raise
        self._pio.irq(handler=self._irq_handler, trigger=EXPECTED_MASK)

        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        self._sm.active(1)
        try:
            while self._seen_mask != EXPECTED_MASK:
                if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                    break
                time.sleep_ms(10)
        finally:
            self._sm.active(0)
            self._pio.irq(handler=None)

        missing = EXPECTED_MASK & ~self._seen_mask
        if missing:
            print("SELF-TEST FAILED missing=0x{:02x}".format(missing))
            print("Observed events:", self._events)
            return False

        print("SELF-TEST PASSED")
        print("Observed events:", self._events)
        return True


def run(timeout_ms=1000, pio_id=0, sm_id=0, freq=10000):
    return PioIrqSelfTest(pio_id=pio_id, sm_id=sm_id, freq=freq).run(timeout_ms=timeout_ms)


if __name__ == "__main__":
    run()
