# Copyright (C) 2026 Diego Rios Gomez
#
# This file is part of Non-intrusive monitoring for AlLoRa.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import time, gc, json, machine, esp32, sys
from AlLoRa.Nodes.Source import Source
from AlLoRa.File import CTP_File
from AlLoRa.Connectors.SX127x_connector import SX127x_connector
from AlLoRa.Digital_Endpoint import Digital_Endpoint

from machine import I2C, Pin

gc.enable()

i2c = I2C(0, scl=Pin(16), sda=Pin(15), freq=100000) 
SLAVE_ADDR = 0x28

def request_metrics(): 
    ''' Descomentar para versión nodo sensor 1 core (comentar para versión 2 cores)
    try: 
        # Enviar comando 0x01 para pedir muestreo inmediato 
        i2c.writeto(SLAVE_ADDR, b'\x01')
    except Exception as e: 
        print("Error writeto:", e) 
        return None 
    
    time.sleep_ms(50) # dar tiempo al esclavo a preparar la respuesta 
    '''
    try:
        raw = i2c.readfrom(SLAVE_ADDR, 128)

        if raw[0] != 0xAA:
            print("Frame desincronizado")
            return None

        length = raw[1] | (raw[2] << 8)

        if length <= 0 or length > 120:
            print("Longitud inválida:", length)
            return None

        payload = raw[3:3+length]

        return payload.decode()

    except Exception as e:
        print("Error readfrom:", e)
        return None

def clean_timing_file():
    test_log = open('log.txt', "wb")
    test_log.write("")
    test_log.close()

# if __name__ == "__main__":
# AlLoRa setup
connector=SX127x_connector()
lora_node = Source(connector, config_file="LoRa.json", i2c=i2c)
chunk_size = lora_node.get_chunk_size() #235

try:
    clean_timing_file()
    print("Waiting first OK")
    backup = lora_node.establish_connection()
    print("Connection OK")

    # This is how to handle a backup file if needed (not implemented in this example...)
    if backup:
        print("Asking backup")
        #file = Datasource.get_backup()
        #lora_node.restore_file(file)

    # with an established connection, we start sending data periodically
    while True:
        try:
            if not lora_node.got_file():
                gc.collect()
                
                data = request_metrics()

                if not data:
                    print("[SRC] No se recibieron métricas válidas")
                    time.sleep(1)
                    continue

                print("Métricas obtenidas por I2C:", data)

                file = CTP_File(name="Envio_metricas",
                                content=bytearray(data, 'utf-8'),
                                chunk_size=chunk_size)
                lora_node.set_file(file)

                print("[SRC] Enviando métricas...")
                lora_node.send_file()
                print("[SRC] Métricas enviadas correctamente")
        
        except Exception as e:
            print("[SRC] Error al enviar métricas:", repr(e))
            sys.print_exception(e)
            gc.collect()

except KeyboardInterrupt as e:
    print("EXIT")
