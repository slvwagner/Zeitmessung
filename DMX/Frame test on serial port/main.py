from machine import Pin
import time

# Simple test pattern generator
def dmx_signal_test():
    tx_pin = Pin(0, Pin.OUT)
    de_pin = Pin(2, Pin.OUT)
    
    print("DMX Signal Test - Connect oscilloscope to GPIO0")
    print("1. Break pattern")
    print("2. Single byte pattern") 
    print("3. Continuous DMX frames")
    print("Press Ctrl+C to stop")
    
    try:
        while True:
            # Test 1: Break and Mark (should see 88μs low + 8μs high)
            print("\n=== BREAK TEST ===")
            de_pin.value(1)
            tx_pin.low()
            time.sleep_us(88)  # Should see 88μs low
            tx_pin.high()
            time.sleep_us(8)   # Should see 8μs high
            de_pin.value(0)
            time.sleep(2)
            
            # Test 2: Single byte transmission
            print("=== SINGLE BYTE TEST ===")
            send_dmx_byte(tx_pin, de_pin, 0x55)  # 01010101 pattern
            time.sleep(2)
            
            # Test 3: Full DMX frame
            print("=== FULL FRAME TEST ===")
            send_test_frame(tx_pin, de_pin)
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("\nTest stopped")

def send_dmx_byte(tx_pin, de_pin, byte_value):
    """Send a single DMX byte with proper timing"""
    de_pin.value(1)
    
    # Break
    tx_pin.low()
    time.sleep_us(88)
    
    # Mark after break
    tx_pin.high() 
    time.sleep_us(8)
    
    # Start bit (low)
    tx_pin.low()
    time.sleep_us(4)
    
    # Data bits (LSB first)
    for bit in range(8):
        tx_pin.value((byte_value >> bit) & 1)
        time.sleep_us(4)
    
    # Stop bits (high)
    tx_pin.high()
    time.sleep_us(8)
    
    de_pin.value(0)

def send_test_frame(tx_pin, de_pin):
    """Send a test DMX frame with known pattern"""
    de_pin.value(1)
    
    # Break and mark
    tx_pin.low()
    time.sleep_us(88)
    tx_pin.high()
    time.sleep_us(8)
    
    # Start code (0)
    send_byte(tx_pin, 0x00)
    
    # Test pattern: alternating 0xAA and 0x55
    for i in range(1, 33):  # First 32 channels
        if i % 2 == 0:
            send_byte(tx_pin, 0xAA)  # 10101010
        else:
            send_byte(tx_pin, 0x55)  # 01010101
    
    de_pin.value(0)

def send_byte(tx_pin, byte_value):
    """Send a single byte without break/mark"""
    # Start bit (low)
    tx_pin.low()
    time.sleep_us(4)
    
    # Data bits (LSB first)
    for bit in range(8):
        tx_pin.value((byte_value >> bit) & 1)
        time.sleep_us(4)
    
    # Stop bits (high)
    tx_pin.high()
    time.sleep_us(8)

# Run the test
dmx_signal_test()
