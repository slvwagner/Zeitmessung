# [Zeitmessung] Project Version: 0.1.0
# Native MicroPython Modules

This folder contains project-local user C modules for the cloned MicroPython
`v1.27.0` tree.

## Current modules

- `dmx_native`: initial skeleton for a native DMX engine API.
- `rc522_native`: prototype native MFRC522 low-level driver API.

Current state:

- Build-integrated through `USER_C_MODULES`
- Importable as `import dmx_native` and `import rc522_native`
- API shape is in place for both
- `dmx_native`: production backend with PIO/DMA path
- `rc522_native`: prototype focused on init/register I/O/REQA/anticoll/UID4

## Build example

From `micropython/ports/rp2`:

```powershell
make BOARD=RPI_PICO2_W USER_C_MODULES=../../../native_modules/micropython.cmake
```

If you want the Wi-Fi capable board firmware for Pico 2 W, use `RPI_PICO2_W`.

## Intended Python API

```python
import dmx_native

dmx_native.init(tx_pin=0, trigger_pin=1, channels=512, refresh_rate=43)
dmx_native.start()
dmx_native.set_channel(1, 255)
dmx_native.set_channels(bytes([0] * 512))
print(dmx_native.status())
dmx_native.stop()
```

## Next step

Replace the stub state implementation in `dmx_native/moddmx_native.c` with:

- PIO program setup
- DMA channel setup
- IRQ/timer scheduler
- double-buffered universe transfer
- start/stop/status hooks