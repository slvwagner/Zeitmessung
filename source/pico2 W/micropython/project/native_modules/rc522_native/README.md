# rc522_native (prototype)

Prototype native C module for MFRC522 low-level access.

## Status

This is a first prototype intended for validation and profiling. It currently provides:

- SPI/pin init and chip setup
- register read/write helpers
- REQA helper
- anti-collision level-1 helper
- UID4 quick read (`get_uid4`)
- module status counters

It does not yet implement full 7-byte/10-byte cascade UID selection in C.

## Build

From `micropython/ports/rp2`:

```powershell
make BOARD=RPI_PICO2_W USER_C_MODULES=../../../native_modules/micropython.cmake
```

## Python usage

```python
import rc522_native as rc

rc.init(spi_id=1, sck=10, mosi=11, miso=12, cs=13, rst=22, baud=50_000)
print(hex(rc.version()))
print(rc.status())

uid4 = rc.get_uid4()
if uid4:
    print(uid4)

rc.deinit()
```

## Next steps

- Add full cascade UID select (CL1/CL2/CL3) in C
- Add optional Python wrapper class to keep compatibility with `RC522LL`
- Profile scan latency and CPU load against `rc522_lowlevel.py`
