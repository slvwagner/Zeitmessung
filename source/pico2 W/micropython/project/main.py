# MicroPython main.py
# Write your code here

from machine import Pin
from utime import sleep
       
pin = Pin("LED", Pin.OUT, value=1)  # Pico2 W onboard LED (GPIO15)

print("LED starts flashing...")
while True:
    try:
        pin.toggle()
        sleep(0.3) # sleep 1sec
    except KeyboardInterrupt:
        break
pin.off()
print("Finished.")