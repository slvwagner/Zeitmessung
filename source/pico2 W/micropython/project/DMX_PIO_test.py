# DMX512 transmitter using PIO
# MicroPython v1.27.0 — Raspberry Pi Pico W (RP2350)

import math
import rp2
import time
from machine import Pin

DMX_channels = 10
DMX_refresh_rate = 50

start_code = 0x00


@rp2.asm_pio(
    out_init=rp2.PIO.OUT_HIGH,
    set_init=rp2.PIO.OUT_HIGH,
    out_shiftdir=rp2.PIO.SHIFT_RIGHT,
    autopull=True,
    pull_thresh=32,
)
def dmx_tx_pio():
    """Transmit one packed 32-bit word as four DMX bytes at 250 kbps."""
    wrap_target()
    set(x, 3)
    label("byte_loop")
    set(pins, 0)
    out(pins, 1)
    out(pins, 1)
    out(pins, 1)
    out(pins, 1)
    out(pins, 1)
    out(pins, 1)
    out(pins, 1)
    out(pins, 1)
    set(pins, 1)
    nop()
    jmp(x_dec, "byte_loop")
    wrap()


class DMXControllerPIO:
    def __init__(self, tx_pin=0, channels=512, refresh_rate=44):
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = refresh_rate
        self.tx_pin = tx_pin
        self.frame_interval_us = max(1, int(1_000_000 / refresh_rate))
        self.frame_time_us = 100 + 12 + (len(bytearray([start_code]) + bytearray([0] * self.channels)) * 44)

        self.tx = Pin(tx_pin, Pin.OUT, value=1)
        self.dmx_data = bytearray([0] * self.channels)
        self.frame = bytearray([start_code]) + bytearray([0] * self.channels)
        self.word_count = math.ceil(len(self.frame) / 4)

        try:
            self.sm = rp2.StateMachine(
                0,
                dmx_tx_pio,
                freq=2_500_000,
                out_base=self.tx,
                set_base=self.tx,
            )
        except OSError as e:
            print("ERROR: Failed to allocate PIO StateMachine:", e)
            raise

        self.transmitting = False

        print(f"DMX PIO Controller initialized with {self.channels} channels")
        print(f"Refresh rate: {refresh_rate} Hz")
        print(f"TX Pin: {tx_pin}")

    def _write_break_and_mab(self):
        self.tx.value(0)
        time.sleep_us(100)
        self.tx.value(1)
        time.sleep_us(12)

    def _write_frame_words(self):
        self.sm.restart()
        self.sm.active(1)
        for index in range(0, len(self.frame), 4):
            word = 0
            for offset in range(4):
                byte_index = index + offset
                if byte_index < len(self.frame):
                    word |= self.frame[byte_index] << (8 * offset)
            self.sm.put(word)
        time.sleep_us((self.word_count * 4 * 44) + 16)
        self.sm.active(0)
        self.tx.value(1)

    def send_frame(self):
        self._write_break_and_mab()
        self._write_frame_words()

    def start(self):
        if self.transmitting:
            print("DMX transmission already running")
            return
        self.transmitting = True
        print(f"Starting continuous DMX transmission at {self.refresh_rate} Hz")

    def stop(self):
        if not self.transmitting:
            print("DMX transmission not running")
            return
        self.transmitting = False
        self.sm.active(0)
        self.tx.value(1)
        print("DMX transmission stopped")

    def set_channel(self, channel, value):
        if 1 <= channel <= self.channels:
            clamped = max(0, min(255, value))
            self.dmx_data[channel - 1] = clamped
            self.frame[channel] = clamped
            print(f"Channel {channel} set to {clamped}")
        else:
            print(f"Error: Channel {channel} out of range (1-{self.channels})")

    def set_channels(self, values_dict):
        updated_channels = []
        for channel, value in values_dict.items():
            if 1 <= channel <= self.channels:
                clamped = max(0, min(255, value))
                self.dmx_data[channel - 1] = clamped
                self.frame[channel] = clamped
                updated_channels.append(channel)
        print(f"Updated {len(updated_channels)} channel(s)")

    def set_all(self, value):
        clamped = max(0, min(255, value))
        for index in range(self.channels):
            self.dmx_data[index] = clamped
            self.frame[index + 1] = clamped
        print(f"All channels set to {clamped}")

    def show_status(self):
        print("\nDMX Status:")
        for index in range(min(10, self.channels)):
            print(f"  Channel {index + 1}: {self.dmx_data[index]}")
        if self.channels > 10:
            print(f"  ... and {self.channels - 10} more channels")
        print(f"Transmission: {'Running' if self.transmitting else 'Stopped'}")
        print(f"Refresh rate: {self.refresh_rate} Hz")

    def clear_all(self):
        for index in range(self.channels):
            self.dmx_data[index] = 0
            self.frame[index + 1] = 0
        print("All channels cleared to 0")

    def help(self):
        print("\nDMX Controller Help:")
        print("Commands:")
        print("  c [channel] [value]     - Set single channel")
        print("  m [ch1:val1,ch2:val2]   - Set multiple channels")
        print("  all [value]             - Set all channels to value")
        print("  start                   - Start continuous transmission")
        print("  stop                    - Stop continuous transmission")
        print("  status                  - Show current status")
        print("  clear                   - Clear all channels")
        print("  step                    - Send exactly one frame")
        print("  help                    - Show this help message")
        print("  exit                    - Exit program")


def interactive_dmx():
    print("\n" + "=" * 60)
    print("DMX512 Controller - PIO Implementation")
    print("=" * 60)
    print("Using Programmable I/O for precise DMX timing")
    print("=" * 60)

    dmx = DMXControllerPIO(tx_pin=0, channels=DMX_channels, refresh_rate=DMX_refresh_rate)
    dmx.help()
    print("DMX transmission is stopped. Type 'start' to begin.")

    while True:
        try:
            cmd = input("\nDMX> ").strip().lower()

            if cmd == "exit":
                dmx.stop()
                print("Exiting DMX controller")
                break

            elif cmd.startswith("c "):
                parts = cmd.split()
                if len(parts) == 3:
                    dmx.set_channel(int(parts[1]), int(parts[2]))

            elif cmd.startswith("m "):
                try:
                    values = {}
                    for pair in cmd[2:].split(','):
                        channel, value = pair.split(':')
                        values[int(channel)] = int(value)
                    dmx.set_channels(values)
                except Exception as e:
                    print(f"Error: {e}")

            elif cmd.startswith("all "):
                try:
                    dmx.set_all(int(cmd.split()[1]))
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

            elif cmd == "step":
                dmx.send_frame()

            elif cmd == "help":
                dmx.help()

            else:
                print(f"Unknown command: {cmd}. Type 'help' for available commands.")

            if dmx.transmitting:
                print("Continuous transmit mode active. Press Ctrl+C to stop.")
                while dmx.transmitting:
                    dmx.send_frame()
                    wait_us = dmx.frame_interval_us - dmx.frame_time_us
                    if wait_us > 0:
                        time.sleep_us(wait_us)

        except KeyboardInterrupt:
            if dmx.transmitting:
                dmx.stop()
                print("\nTransmission interrupted")
                continue
            dmx.stop()
            print("\nExiting DMX controller")
            break

        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    interactive_dmx()