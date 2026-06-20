"""Squarewave generator"""
from machine import Pin
import rp2
import time
import math

signal_pin_num = 2  # send square wave on Pin?


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)
def square_wave_precise():
    """PIO program for square wave generation with dynamic frequency updates"""
    pull(block)                     # Initial delay value from FIFO
    mov(x, osr)                     # Store in X register
    wrap_target()                   # Try to get new value without blocking (keeps old value if FIFO empty)
    pull(noblock)
    mov(x, osr)                     # Update X with new value (old value remains if pull failed)
    set(pins, 1)                    # Set pin high
    mov(y, x)                       # Copy delay value to y
    label('high_loop')
    jmp(y_dec, 'high_loop')         # Delay for high period
    set(pins, 0)                    # Set pin low
    mov(y, x)                       # Copy delay value to y again
    label('low_loop')
    jmp(y_dec, 'low_loop')          # Delay for low period
    wrap()


class PreciseSquareWaveGenerator:
    def __init__(self, pin_num, base_freq=1000):
        self.pin = Pin(pin_num, Pin.OUT)
        self.system_freq = 150_000_000
        self.pin_num = pin_num
        self.sweeping = False
        self.sweep_thread = None
        print(f"Debug: Creating precise state machine for pin {pin_num}")
        # Create state machine
        self.sm = rp2.StateMachine(0, square_wave_precise, set_base=self.pin)
        # Set the base frequency
        self.current_freq = base_freq
        self.delay_value = None
        self.set_frequency(base_freq)

    def set_frequency(self, freq_hz):
        if freq_hz <= 0:
            raise ValueError("Frequency must be positive")
        # Calculate delay value
        # Formula: freq = sys_freq / (2 * (N + 2))
        # So N = (sys_freq / (2 * freq)) - 2
        N = int((self.system_freq / (2 * freq_hz)) - 2)
        print(f"Debug: Calculated delay value N = {N}")

        if N < 1:
            N = 1
            actual_freq = self.system_freq / (2 * (N + 2))
            print(f"Warning: Requested frequency too high. Using max: {actual_freq:.2f} Hz")
        elif N > 65535:
            N = 65535
            actual_freq = self.system_freq / (2 * (N + 2))
            print(f"Warning: Requested frequency too low. Using min: {actual_freq:.2f} Hz")
        else:
            actual_freq = self.system_freq / (2 * (N + 2))

        # If state machine is running, just push the new value to FIFO
        if self.sm.active():
            # Put new delay value into FIFO
            self.sm.put(N)
            print(f"Debug: Pushed new N={N} into FIFO while running")
        else:
            # If not running, put the value
            self.sm.put(N)
            print(f"Debug: Put N={N} into FIFO")
            self.delay_value = N

        self.current_freq = actual_freq
        print(f"N set to {N}")
        print(f"✓ Frequency set to {freq_hz:.2f} Hz (actual: {actual_freq:.2f} Hz)")

    def get_min_max_freq(self):
        """Get minimum and maximum achievable frequencies"""
        max_freq = self.system_freq / (2 * (1 + 2))      # N=1
        min_freq = self.system_freq / (2 * (65535 + 2))  # N=65535
        return min_freq, max_freq

    def sweep_linear(self, start_freq, end_freq, step_hz, step_time_ms=100):
        """Linear frequency sweep
        start_freq: Starting frequency in Hz
        end_freq: Ending frequency in Hz
        step_hz: Frequency step size in Hz
        step_time_ms: Time between steps in milliseconds
        """
        min_freq, max_freq = self.get_min_max_freq()
        # Clamp frequencies to achievable range
        start_freq = max(min_freq, min(start_freq, max_freq))
        end_freq = max(min_freq, min(end_freq, max_freq))

        print(f"\nLinear sweep from {start_freq:.2f} Hz to {end_freq:.2f} Hz")
        print(f"Step: {step_hz} Hz, Step time: {step_time_ms} ms")

        # Determine direction
        if start_freq <= end_freq:
            frequencies = range(int(start_freq), int(end_freq) + 1, int(step_hz))
        else:
            frequencies = range(int(start_freq), int(end_freq) - 1, -int(step_hz))

        total_steps = len(list(frequencies))
        print(f"Total steps: {total_steps}, Estimated time: {total_steps * step_time_ms / 1000:.2f} seconds")

        for i, freq in enumerate(frequencies):
            if not self.sweeping:  # Check if sweep was cancelled
                print("\nSweep cancelled")
                break
            self.set_frequency(float(freq))
            print(f"Step {i+1}/{total_steps}: {freq} Hz")
            time.sleep(step_time_ms / 1000)

    def sweep_logarithmic(self, start_freq, end_freq, steps, step_time_ms=100):
        """Logarithmic frequency sweep (better for audio applications)
        start_freq: Starting frequency in Hz
        end_freq: Ending frequency in Hz
        steps: Number of steps in the sweep
        step_time_ms: Time between steps in milliseconds
        """
        min_freq, max_freq = self.get_min_max_freq()
        # Clamp frequencies to achievable range
        start_freq = max(min_freq, min(start_freq, max_freq))
        end_freq = max(min_freq, min(end_freq, max_freq))

        print(f"\nLogarithmic sweep from {start_freq:.2f} Hz to {end_freq:.2f} Hz")
        print(f"Steps: {steps}, Step time: {step_time_ms} ms")
        print(f"Estimated time: {steps * step_time_ms / 1000:.2f} seconds")

        # Generate logarithmic steps
        log_start = math.log10(start_freq)
        log_end = math.log10(end_freq)
        log_step = (log_end - log_start) / (steps - 1)

        for i in range(steps):
            if not self.sweeping:  # Check if sweep was cancelled
                print("\nSweep cancelled")
                break
            log_freq = log_start + i * log_step
            freq = 10 ** log_freq
            self.set_frequency(freq)
            print(f"Step {i+1}/{steps}: {freq:.2f} Hz")
            time.sleep(step_time_ms / 1000)

    def sweep_sine_modulated(self, center_freq, modulation_depth, modulation_rate, duration):
        """Sine wave frequency modulation
        center_freq: Center frequency in Hz
        modulation_depth: Maximum frequency deviation in Hz
        modulation_rate: Modulation rate in Hz
        duration: Sweep duration in seconds
        """
        min_freq, max_freq = self.get_min_max_freq()

        print(f"\nSine modulated sweep")
        print(f"Center: {center_freq} Hz, Depth: {modulation_depth} Hz")
        print(f"Rate: {modulation_rate} Hz, Duration: {duration} seconds")

        start_time = time.ticks_ms()
        end_time = start_time + (duration * 1000)
        step_time_ms = 20  # 50Hz update rate
        steps = int(duration * 1000 / step_time_ms)

        for i in range(steps):
            if not self.sweeping or time.ticks_ms() > end_time:
                break

            # Calculate modulated frequency
            t = i * step_time_ms / 1000
            freq = center_freq + modulation_depth * math.sin(2 * math.pi * modulation_rate * t)

            # Clamp to achievable range
            freq = max(min_freq, min(freq, max_freq))

            self.set_frequency(freq)
            print(f"Time: {t:.2f}s, Frequency: {freq:.2f} Hz")
            time.sleep(step_time_ms / 1000)

    def start_sweep(self, sweep_type="linear", **kwargs):
        """Start a frequency sweep in a non-blocking way
        sweep_type: "linear", "log", or "sine"
        kwargs: Parameters for the specific sweep type
        """
        if self.sweeping:
            print("Sweep already in progress")
            return

        self.sweeping = True

        # Start the sweep in a separate thread
        import _thread
        self.sweep_thread = _thread.start_new_thread(self._sweep_thread_func, (sweep_type, kwargs))
        print(f"Started {sweep_type} sweep")

    def _sweep_thread_func(self, sweep_type, kwargs):
        """Internal function to run sweep in thread"""
        try:
            if sweep_type == "linear":
                self.sweep_linear(**kwargs)
            elif sweep_type == "log":
                self.sweep_logarithmic(**kwargs)
            elif sweep_type == "sine":
                self.sweep_sine_modulated(**kwargs)
        except Exception as e:
            print(f"Sweep error: {e}")
        finally:
            self.sweeping = False
            print("Sweep completed")

    def stop_sweep(self):
        """Stop the current sweep"""
        self.sweeping = False
        if self.sweep_thread:
            # Wait a bit for thread to finish
            time.sleep(0.5)
            self.sweep_thread = None
        print("Sweep stopped")

    def start(self):
        if not self.sm.active():
            # Make sure there's a value in FIFO before starting
            if self.delay_value is not None:
                self.sm.put(self.delay_value)
            self.sm.active(True)
            time.sleep_ms(10)  # Give it time to start
            print(f"✓ Started on GPIO{self.pin_num}")
            print(f"Debug: State machine active = {self.sm.active()}")
            print(f"Debug: FIFO status - TX: {self.sm.tx_fifo()}, RX: {self.sm.rx_fifo()}")

    def stop(self):
        if self.sm.active():
            # Stop any ongoing sweep
            if self.sweeping:
                self.stop_sweep()
            self.sm.active(False)
            print(f"✓ Stopped on GPIO{self.pin_num}")

    def help(self):
        print("\nCommands:")
        print("  f <freq>               - Set frequency")
        print("  status                  - Show current status")
        print("  range                   - Show frequency range")
        print("  start                   - Start square wave")
        print("  stop                    - Stop square wave")
        print("  sweep linear <start> <end> <step> <time_ms> - Linear frequency sweep")
        print("  sweep log <start> <end> <steps> <time_ms> - Logarithmic frequency sweep")
        print("  sweep sine <center> <depth> <rate> <duration> - Sine modulated sweep")
        print("  sweep_start linear ... - Start non-blocking linear sweep")
        print("  sweep_start log ...    - Start non-blocking logarithmic sweep")
        print("  sweep_start sine ...   - Start non-blocking sine modulated sweep")
        print("  sweep_stop             - Stop current sweep")
        print("  help                    - Show this help message")
        print("  exit                    - Exit program")


def main():
    print("RP2350 PIO Square Wave Generator - WITH SWEEP FUNCTIONS")
    print("=======================================================")
    print(f"\nCreating precise square wave generator on GPIO{signal_pin_num}")

    precise_gen = PreciseSquareWaveGenerator(signal_pin_num, 2000)
    precise_gen.start()

    min_freq, max_freq = precise_gen.get_min_max_freq()
    print(f"\nAchievable frequency range: {min_freq:.2f} Hz to {max_freq:.2f} Hz")

    print("\nCommands:")
    print("  f <freq> - Set frequency")
    print("  status - Show status")
    print("  stop - Stop")
    print("  start - Start")
    print("  range - Show frequency range")
    print("\nSweep commands (blocking):")
    print("  sweep linear <start> <end> <step> <time_ms>")
    print("  sweep log <start> <end> <steps> <time_ms>")
    print("  sweep sine <center> <depth> <rate> <duration>")
    print("\nSweep commands (non-blocking):")
    print("  sweep_start linear <start> <end> <step> <time_ms>")
    print("  sweep_start log <start> <end> <steps> <time_ms>")
    print("  sweep_start sine <center> <depth> <rate> <duration>")
    print("  sweep_stop - Stop current sweep")
    print("  exit - Exit")
    print()

    while True:
        try:
            cmd = input("> ").strip().lower()

            if cmd == "exit":
                precise_gen.stop()
                break

            elif cmd == "start":
                precise_gen.start()

            elif cmd == "stop":
                precise_gen.stop()

            elif cmd == "range":
                min_f, max_f = precise_gen.get_min_max_freq()
                print(f"Min frequency: {min_f:.2f} Hz")
                print(f"Max frequency: {max_f:.2f} Hz")

            elif cmd == "status":
                print(f"Active: {precise_gen.sm.active()}")
                print(f"Frequency: {precise_gen.current_freq:.2f} Hz")
                print(f"Delay value: {precise_gen.delay_value}")
                print(f"TX FIFO level: {precise_gen.sm.tx_fifo()}")
                print(f"RX FIFO level: {precise_gen.sm.rx_fifo()}")
                print(f"Sweeping: {precise_gen.sweeping}")

            elif cmd.startswith("f "):
                try:
                    freq = float(cmd[2:])
                    precise_gen.set_frequency(freq)
                except ValueError:
                    print("Invalid frequency - please enter a number")
                except Exception as e:
                    print(f"Error setting frequency: {e}")

            elif cmd == "help":
                precise_gen.help()

            # Sweep commands
            elif cmd.startswith("sweep linear"):
                try:
                    parts = cmd.split()
                    _, _, start, end, step, time_ms = parts
                    precise_gen.sweep_linear(float(start), float(end), float(step), float(time_ms))
                except Exception as e:
                    print(f"Error: {e}")
                    print("Usage: sweep linear <start> <end> <step> <time_ms>")

            elif cmd.startswith("sweep log"):
                try:
                    parts = cmd.split()
                    _, _, start, end, steps, time_ms = parts
                    precise_gen.sweep_logarithmic(float(start), float(end), int(steps), float(time_ms))
                except Exception as e:
                    print(f"Error: {e}")
                    print("Usage: sweep log <start> <end> <steps> <time_ms>")

            elif cmd.startswith("sweep sine"):
                try:
                    parts = cmd.split()
                    _, _, center, depth, rate, duration = parts
                    precise_gen.sweep_sine_modulated(float(center), float(depth), float(rate), float(duration))
                except Exception as e:
                    print(f"Error: {e}")
                    print("Usage: sweep sine <center> <depth> <rate> <duration>")

            # Non-blocking sweep commands
            elif cmd.startswith("sweep_start linear"):
                try:
                    parts = cmd.split()
                    _, _, start, end, step, time_ms = parts
                    precise_gen.start_sweep("linear", start_freq=float(start), end_freq=float(end),
                                            step_hz=float(step), step_time_ms=float(time_ms))
                except Exception as e:
                    print(f"Error: {e}")

            elif cmd.startswith("sweep_start log"):
                try:
                    parts = cmd.split()
                    _, _, start, end, steps, time_ms = parts
                    precise_gen.start_sweep("log", start_freq=float(start), end_freq=float(end),
                                            steps=int(steps), step_time_ms=float(time_ms))
                except Exception as e:
                    print(f"Error: {e}")

            elif cmd.startswith("sweep_start sine"):
                try:
                    parts = cmd.split()
                    _, _, center, depth, rate, duration = parts
                    precise_gen.start_sweep("sine", center_freq=float(center),
                                            modulation_depth=float(depth),
                                            modulation_rate=float(rate), duration=float(duration))
                except Exception as e:
                    print(f"Error: {e}")

            elif cmd == "sweep_stop":
                precise_gen.stop_sweep()

            elif cmd:
                print("Unknown command")

        except KeyboardInterrupt:
            print("\nExiting...")
            precise_gen.stop()
            break


if __name__ == "__main__":
    main()
    set_pin = Pin(signal_pin_num, Pin.OUT)
    set_pin.value(0)