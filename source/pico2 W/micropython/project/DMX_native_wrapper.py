try:
    import dmx_native as _native
except ImportError:
    _native = None


if _native is None:
    raise ImportError("dmx_native module unavailable")


class DMXControllerPIO_DMA:
    def __init__(self, tx_pin=0, trigger_pin=1, channels=512, refresh_rate=43,
                 sm_ctrl_id=8, sm_data_id=9):
        self.tx_pin = tx_pin
        self.trigger_pin = trigger_pin
        self.channels = min(max(1, channels), 512)
        self.refresh_rate = min(max(1, refresh_rate), 1000)
        self.sm_ctrl_id = sm_ctrl_id
        self.sm_data_id = sm_data_id
        self.auto_ntp_sync = False
        self.auto_status_log = False
        self._running = False

        _native.init(
            tx_pin=self.tx_pin,
            trigger_pin=self.trigger_pin,
            channels=self.channels,
            refresh_rate=self.refresh_rate,
            sm_ctrl_id=self.sm_ctrl_id,
            sm_data_id=self.sm_data_id,
        )

    def start(self):
        _native.start()
        self._running = True

    def stop(self):
        _native.stop()
        self._running = False

    def is_running(self):
        return _native.is_running()

    def clear(self):
        _native.clear()

    def set_channel(self, channel, value):
        _native.set_channel(channel, value)

    def set_channels(self, values):
        return _native.set_channels(values)

    def service(self):
        return None

    def status(self):
        status = _native.status()
        status["backend"] = "dmx_native"
        return status