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

# Based on code from the AlLoRa project:
# https://github.com/SMARTLAGOON/AlLoRa
#
# Original work Copyright (C) Benjamin Arratia and contributors
# Modifications Copyright (C) 2026 Diego Rios Gomez

import RPi.GPIO as GPIO
import time
import  sys, gc

from threading import Thread
from time import sleep

from AlLoRa.Nodes.Gateway import Gateway
from AlLoRa.Connectors.Serial_connector import Serial_connector

from control import control_loop, control_queue
from mqtt_control import mqtt_control_loop
from mqtt_status import init_mqtt_status
from web import app

from gateway_state import metrics, metrics_lock

config_file = "LoRa.json"
node_file = "Nodes.json"


def reset_esp32():
    RST_PIN = 23  # Or load from configuration
    GPIO.setmode(GPIO.BCM)
    
    try:
        GPIO.setup(RST_PIN, GPIO.OUT)
    except RuntimeError:
        GPIO.cleanup(RST_PIN)  # Cleanup the specific pin if setup fails
        GPIO.setup(RST_PIN, GPIO.OUT)  # Retry setup after cleanup

    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(0.1)
    GPIO.output(RST_PIN, GPIO.HIGH)
    print("ESP32 has been reset.")
    GPIO.cleanup()


if __name__ == "__main__":
    reset_esp32()     
    connector = Serial_connector(reset_function=reset_esp32)
    lora_gateway = Gateway(connector, config_file= config_file, debug_hops= False)
    
    #PRUEBA: 3 hilos de ejecución en la RP4: la toma de métricas (check digital endpoints), la cola de control (para los reset),
    #y el servidor web con la visualización de dichas métricas y el botón de reset
    
    lora_gateway.metrics = metrics
    lora_gateway.metrics_lock = metrics_lock
    
    
    #1 - Métricas clientes
    Thread(
        target=lora_gateway.check_digital_endpoints,
        kwargs={"print_file_content": False, "save_files": False},
        daemon=True
    ).start()
    
    #2 - Cola de control
    Thread(
        target=control_loop,
        args=(lora_gateway, control_queue),
        daemon=True
    ).start()
    
    #3 - Servidor Web
    Thread(
        target=app.run,
        kwargs={"host": "0.0.0.0", "port": 8080},
        daemon=True
    ).start()

    #4 - Gestión MQTT RX
    Thread(
        target=mqtt_control_loop,
        daemon=True
    ).start()

    init_mqtt_status()
    
    # Mantener vivo el proceso
    while True:
        sleep(1)
