from DMX_native_wrapper import DMXControllerPIO_DMA as NativeDMXController
import time

try:
    import network
except Exception:
    network = None

try:
    import ntptime
except Exception:
    ntptime = None

try:
    from credentials import SSID, PASSWORD
except Exception:
    SSID = None
    PASSWORD = None

DMX_CHANNELS = 512
DMX_REFRESH_RATE = 20
DMX_TX_PIN = 0
DMX_TRIGGER_PIN = 1
NTP_SYNC_TIMEOUT_MS = 12000
NTP_HOSTS = ("pool.ntp.org", "time.google.com", "129.6.15.28")


class DMXControllerPIO_DMA:
    """User-facing API compatible controller using native C DMX backend."""

    def __init__(self, tx_pin=DMX_TX_PIN, trigger_pin=DMX_TRIGGER_PIN,
                 channels=DMX_CHANNELS, refresh_rate=DMX_REFRESH_RATE,
                 sm_ctrl_id=8, sm_data_id=9):
        self.tx_pin = tx_pin
        self.trigger_pin = trigger_pin
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = min(max(1, refresh_rate), 1000)
        self.sm_ctrl_id = sm_ctrl_id
        self.sm_data_id = sm_data_id

        self.print_updates = False
        self.auto_ntp_sync = False
        self.time_synced = False
        self.last_ntp_sync_s = None
        # Invert transmitted channel bytes in Python before passing to native backend.
        self.invert_data_bits = True

        self.dmx_data = bytearray(self.channels)

        self._native = NativeDMXController(
            tx_pin=self.tx_pin,
            trigger_pin=self.trigger_pin,
            channels=self.channels,
            refresh_rate=self.refresh_rate,
            sm_ctrl_id=self.sm_ctrl_id,
            sm_data_id=self.sm_data_id,
        )
        self._native.set_invert_data_bits(self.invert_data_bits)

    def start(self):
        if self.is_running():
            print("DMX transmission already running")
            return
        self._native.start()
        if self.auto_ntp_sync and not self.time_synced:
            self.sync_time_ntp()
        print("DMX transmission initialised")

    def stop(self):
        if self.is_running():
            self._native.stop()
            print("DMX transmission stopped")

    def is_running(self):
        return bool(self._native.is_running())

    def service(self):
        return self._native.service()

    def set_channel(self, channel, value):
        if 1 <= channel <= self.channels:
            v = max(0, min(255, int(value)))
            self.dmx_data[channel - 1] = v
            self._native.set_channel(channel, v)
            if self.print_updates:
                print("Channel {} = {}".format(channel, v))
        else:
            print("Error: Channel {} out of range (1-{})".format(channel, self.channels))

    def set_all(self, value):
        v = max(0, min(255, int(value)))
        self.dmx_data = bytearray(self.channels)
        payload = bytearray(self.channels)
        for i in range(self.channels):
            self.dmx_data[i] = v
            payload[i] = v
        self._native.set_channels(payload)
        if self.print_updates:
            print("All channels set to {}".format(v))

    def set_channels_bulk(self, values):
        n = min(len(values), self.channels)
        if n <= 0:
            return

        if isinstance(values, (bytes, bytearray)):
            payload = bytearray(values[:n])
        else:
            payload = bytearray(n)
            for i in range(n):
                payload[i] = max(0, min(255, int(values[i])))

        for i in range(n):
            self.dmx_data[i] = payload[i]

        written = self._native.set_channels(payload)
        if self.print_updates:
            print("Bulk update applied to {} channels".format(written))

    def set_invert_data_bits(self, enabled):
        enabled = bool(enabled)
        if self.invert_data_bits == enabled:
            return
        self.invert_data_bits = enabled
        self._native.set_invert_data_bits(enabled)

    def clear_all(self):
        self.set_all(0)

    def set_lsb_test_pattern(self):
        if self.channels < 3:
            print("Need at least 3 channels for LSB test pattern")
            return
        self.set_channel(1, 0x01)
        self.set_channel(2, 0x80)
        self.set_channel(3, 0x55)
        print("LSB test pattern loaded: CH1=0x01 CH2=0x80 CH3=0x55")
        print("Expected LSB-first bits: 0x01->10000000  0x80->00000001")

    def benchmark_updates(self):
        old_verbose = self.print_updates
        self.print_updates = False

        t0 = time.ticks_us()
        self.set_all(255)
        t_all = time.ticks_diff(time.ticks_us(), t0)

        t1 = time.ticks_us()
        for ch in range(1, self.channels + 1):
            self.set_channel(ch, 0)
        t_single = time.ticks_diff(time.ticks_us(), t1)

        t2 = time.ticks_us()
        bulk = bytearray(self.channels)
        for i in range(self.channels):
            bulk[i] = 128
        self.set_channels_bulk(bulk)
        t_bulk = time.ticks_diff(time.ticks_us(), t2)

        self.print_updates = old_verbose

        print("Benchmark (setter path only):")
        print("  set_all():           {:.3f} ms".format(t_all / 1000))
        print("  512x set_channel():  {:.3f} ms".format(t_single / 1000))
        print("  set_channels_bulk(): {:.3f} ms".format(t_bulk / 1000))

    def benchmark_live_latency(self, value=255, timeout_ms=2000):
        if not self.is_running():
            print("Start transmission first")
            return

        start_status = self._native.status()
        start_frame_count = int(start_status.get("frame_count", 0))

        self.set_all(value)

        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        t0 = time.ticks_us()
        while True:
            status = self._native.status()
            if int(status.get("frame_count", 0)) > start_frame_count:
                dt_us = time.ticks_diff(time.ticks_us(), t0)
                print("Live command->next-frame latency: {:.3f} ms".format(dt_us / 1000))
                return
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                print("Live latency timeout")
                return
            time.sleep_ms(1)

    def sync_time_ntp(self, timeout_ms=NTP_SYNC_TIMEOUT_MS):
        if network is None:
            print("[TIME] network module unavailable (non-Wi-Fi build)")
            return False
        if ntptime is None:
            print("[TIME] ntptime module unavailable")
            return False
        if not SSID or not PASSWORD:
            print("[TIME] missing Wi-Fi credentials for NTP sync")
            return False

        sta = network.WLAN(network.STA_IF)
        sta.active(True)

        if not sta.isconnected():
            print("[TIME] connecting Wi-Fi for NTP sync...")
            sta.connect(SSID, PASSWORD)
            t0 = time.ticks_ms()
            while not sta.isconnected():
                if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                    print("[TIME] Wi-Fi timeout; NTP sync skipped")
                    return False
                time.sleep_ms(200)

        for host in NTP_HOSTS:
            try:
                ntptime.host = host
                ntptime.settime()
                self.time_synced = True
                self.last_ntp_sync_s = time.time()
                print("[TIME] NTP synced via {}".format(host))
                return True
            except Exception as exc:
                print("[TIME] NTP host failed: {} {}".format(host, exc))

        print("[TIME] all NTP hosts failed")
        return False

    def status(self):
        s = self._native.status()

        print("\n" + "=" * 40)
        print("DMX Controller Status (native C backend)")
        print("=" * 40)
        print("Channels:                {}".format(s.get("channels", self.channels)))
        print("Transmitting:            {}".format(s.get("running", False)))
        print("Refresh rate:            {} Hz".format(s.get("refresh_rate", self.refresh_rate)))
        print("Invert data bits:        {}".format(self.invert_data_bits))
        print("Frame count:             {}".format(s.get("frame_count", 0)))
        print("Skipped callbacks:       {}".format(s.get("skipped_callbacks", 0)))
        print("DMA prime timeouts:      {}".format(s.get("prime_timeouts", 0)))
        print("Frame timeouts:          {}".format(s.get("frame_timeouts", 0)))
        print("Auto-resync count:       {}".format(s.get("auto_resyncs", 0)))
        print("")
        print("PIO block:               {}".format(s.get("pio_block", "?")))
        print("SM ids:                  ctrl={} data={}".format(s.get("sm_ctrl_id", "?"), s.get("sm_data_id", "?")))
        print("TX pin / trigger pin:    {} / {}".format(s.get("tx_pin", self.tx_pin), s.get("trigger_pin", self.trigger_pin)))
        print("\nFirst 8 channels:")
        for i in range(min(8, self.channels)):
            print("  Channel {}: {}".format(i + 1, self.dmx_data[i]))
        print("=" * 40)

    def help(self):
        print("\nAvailable commands:")
        print("  start           - Start DMX transmission")
        print("  stop            - Stop DMX transmission")
        print("  status          - Show current status")
        print("  clear           - Clear all channels to 0")
        print("  c <ch> <val>    - Set channel <ch> to value <val> (1-indexed)")
        print("  all <val>       - Set all channels to value <val>")
        print("  bench           - Benchmark setter path")
        print("  benchlive       - Benchmark live command-to-next-frame latency")
        print("  lsbtest         - Load LSB test pattern into first channels")
        print("  timesync        - Sync RTC from NTP (requires Wi-Fi)")
        print("  verbose on/off  - Toggle update prints")
        print("  help            - Show this help")
        print("  exit            - Exit\n")


def main():
    print("=" * 50)
    print("DMX512 Controller - Native C backend")
    print("=" * 50)

    dmx = DMXControllerPIO_DMA(
        tx_pin=DMX_TX_PIN,
        trigger_pin=DMX_TRIGGER_PIN,
        channels=DMX_CHANNELS,
        refresh_rate=DMX_REFRESH_RATE,
    )
    dmx.start()
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
            elif cmd == "timesync":
                dmx.sync_time_ntp()
            elif cmd in ("verbose on", "verbose off"):
                dmx.print_updates = (cmd == "verbose on")
                print("Update prints: {}".format("ON" if dmx.print_updates else "OFF"))
            elif cmd.startswith("c "):
                parts = cmd.split()
                if len(parts) == 3:
                    try:
                        dmx.set_channel(int(parts[1]), int(parts[2]))
                    except ValueError:
                        print("Error: channel and value must be integers")
                else:
                    print("Usage: c <channel> <value>")
            elif cmd.startswith("all "):
                parts = cmd.split()
                if len(parts) == 2:
                    try:
                        dmx.set_all(int(parts[1]))
                    except ValueError:
                        print("Error: value must be an integer")
                else:
                    print("Usage: all <value>")
            elif cmd == "help":
                dmx.help()
            else:
                print("Unknown command: '{}'".format(cmd))

        except KeyboardInterrupt:
            dmx.stop()
            print("\nExiting...")
            break
        except Exception as exc:
            print("Error: {}".format(exc))


if __name__ == "__main__":
    main()
