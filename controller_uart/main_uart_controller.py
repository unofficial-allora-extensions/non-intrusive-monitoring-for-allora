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

from machine import UART, Pin

gc.enable()

# UART en lugar de I2C
uart = UART(1, baudrate=115200, tx=Pin(41), rx=Pin(42), timeout=50)

def request_metrics():
    """
    Envía 0x01 por UART para pedir métricas y espera una trama:
    [0xAA][len_L][len_H][payload...]
    """
    try:
        # Limpiar posibles restos en RX para empezar sincronizados
        while uart.any():
            uart.read()

        # Pedir métricas al nodo controlado
        uart.write(b'\x01')

        # Dar un pequeño margen para que prepare la respuesta
        time.sleep_ms(30)

        deadline = time.ticks_add(time.ticks_ms(), 500)

        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            # Buscar byte de sincronización 0xAA
            first = uart.read(1)
            if not first:
                time.sleep_ms(5)
                continue

            if first[0] != 0xAA:
                continue

            # Leer los 2 bytes restantes de cabecera (longitud)
            header_rest = b""
            while len(header_rest) < 2 and time.ticks_diff(deadline, time.ticks_ms()) > 0:
                chunk = uart.read(2 - len(header_rest))
                if chunk:
                    header_rest += chunk
                else:
                    time.sleep_ms(5)

            if len(header_rest) < 2:
                print("Cabecera incompleta")
                return None

            length = header_rest[0] | (header_rest[1] << 8)

            if length <= 0 or length > 512:
                print("Longitud inválida:", length)
                continue

            # Leer payload completo
            payload = b""
            payload_deadline = time.ticks_add(time.ticks_ms(), 500)

            while len(payload) < length and time.ticks_diff(payload_deadline, time.ticks_ms()) > 0:
                chunk = uart.read(length - len(payload))
                if chunk:
                    payload += chunk
                else:
                    time.sleep_ms(5)

            if len(payload) != length:
                print("Payload incompleto")
                continue

            return payload.decode()

        print("Timeout esperando trama UART")
        return None

    except Exception as e:
        print("Error UART:", e)
        return None

def clean_timing_file():
    test_log = open('log.txt', "wb")
    test_log.write("")
    test_log.close()

# AlLoRa setup
connector = SX127x_connector()
lora_node = Source(connector, config_file="LoRa.json", uart=uart)
chunk_size = lora_node.get_chunk_size()  # 235

try:
    clean_timing_file()
    print("Waiting first OK")
    backup = lora_node.establish_connection()
    print("Connection OK")

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

                print("Métricas obtenidas por UART:", data)

                file = CTP_File(
                    name="Envio_metricas",
                    content=bytearray(data, 'utf-8'),
                    chunk_size=chunk_size
                )
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