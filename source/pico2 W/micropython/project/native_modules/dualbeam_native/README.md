# dualbeam_native MicroPython C Module

This module provides a native dual-beam PIO measurement interface for the RP2040 (Raspberry Pi Pico) in MicroPython.

## Features
- Loads a dual-beam PIO program
- Arms the state machine and TX FIFO
- Handles RX FIFO and IRQ for results
- Python API: `init(pin1, pin2)`, `arm(debounce)`, `read()`

## Usage
```python
import dualbeam_native

dualbeam_native.init(2, 3)  # Example: GPIO2, GPIO3

dualbeam_native.arm(8)      # Debounce cycles
result = dualbeam_native.read()
if result:
    print("Result:", result)
```

## Build
Add this directory as a user C module in your MicroPython build.

## TODO
- Replace the PIO program with your actual assembler output
- Expand result handling as needed
