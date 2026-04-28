# i2c_scanner.py (MicroPython) 
from machine import I2C, Pin 

i2c = I2C(0, scl=Pin(16), sda=Pin(15), freq=100000) 
devices = i2c.scan() 

print("Dispositivos I2C detectados (decimal):", devices) 
print("En hexadecimal:", [hex(d) for d in devices])