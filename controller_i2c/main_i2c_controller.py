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

# The main program executed by the LilyGO controllers, in case their controlled nodes are also LilyGOs.
# The I2C serial protocol is used between LilyGO-LilyGO. This controller reads the I2C channel to
# gather the metrics to be sent via AlLoRa when the corresponding Gateway asks for them.

import time, gc, json, machine, esp32, sys
from AlLoRa.Nodes.Source import Source
from AlLoRa.File import CTP_File
from AlLoRa.Connectors.SX127x_connector import SX127x_connector
from AlLoRa.Digital_Endpoint import Digital_Endpoint

from machine import I2C, Pin, WDT

gc.enable()

# ============================================================
# I2C CONFIGURATION
# ============================================================

i2c = I2C(0, scl=Pin(16), sda=Pin(15), freq=100000)
SLAVE_ADDR = 0x28

# ============================================================
# BOOT MARKER LED
# ============================================================

# MARCAR FÍSICAMENTE LOS REINICIOS DEL CONTROLADOR
boot_led = Pin(37, Pin.OUT)

def blink_boot_marker():
    for _ in range(3):
        boot_led.value(1)
        time.sleep_ms(150)
        boot_led.value(0)
        time.sleep_ms(150)

blink_boot_marker()

# ============================================================
# ROBUSTNESS SETTINGS
# Internal watchdog + periodic preventive restart
# ============================================================

WDT_TIMEOUT_MS = 180000                       # 180 s
PERIODIC_RESTART_MS = 24 * 60 * 60 * 1000    # 24 h

wdt = WDT(timeout=WDT_TIMEOUT_MS)
boot_time = time.ticks_ms()

def feed_wdt():
    try:
        wdt.feed()
    except Exception as e:
        print("Error feeding WDT:", e)

def should_periodic_restart():
    return time.ticks_diff(time.ticks_ms(), boot_time) >= PERIODIC_RESTART_MS

def do_periodic_restart():
    print("[SYS] Scheduled preventive restart")
    time.sleep_ms(200)
    machine.reset()

def service_housekeeping():
    feed_wdt()

    if should_periodic_restart():
        do_periodic_restart()

# ============================================================
# CONTROL LISTENING AFTER LOCAL FAILURES
# ============================================================

CONTROL_LISTEN_AFTER_FAILURES = 5
CONTROL_LISTEN_WINDOW_MS = 3000

def listen_for_control_commands(lora_node, window_ms=5000):
    """
    Allows the controller to keep attending to AlLoRa commands
    even if the terminal node is not delivering metrics via I2C.
    """
    t0 = time.ticks_ms()

    while time.ticks_diff(time.ticks_ms(), t0) < window_ms:
        service_housekeeping()

        try:
            lora_node.listen_requester()
        except Exception as e:
            print("[SRC] Error while listening for control:", repr(e))
            try:
                sys.print_exception(e)
            except:
                pass

        gc.collect()
        service_housekeeping()

# ============================================================
# I2C METRICS ACQUISITION
# ============================================================

def request_metrics():
    """
    Format of the response that is expected:
    [0xAA][len_L][len_H][payload...]
    """

    '''
    Descomentar para versión nodo sensor 1 core
    y comentar para versión 2 cores.

    try:
        # Enviar comando 0x01 para pedir muestreo inmediato
        i2c.writeto(SLAVE_ADDR, b'\x01')
    except Exception as e:
        print("Error writeto:", e)
        return None

    time.sleep_ms(50)  # dar tiempo al esclavo a preparar la respuesta
    '''

    try:
        service_housekeeping()

        raw = i2c.readfrom(SLAVE_ADDR, 128)

        service_housekeeping()

        if raw[0] != 0xAA:
            print("Desynchronized frame")
            return None

        length = raw[1] | (raw[2] << 8)

        if length <= 0 or length > 120:
            print("Invalid length:", length)
            return None

        payload = raw[3:3 + length]

        return payload.decode()

    except Exception as e:
        print("Error readfrom:", e)
        return None

# ============================================================
# AUXILIARY
# ============================================================

def clean_timing_file():
    test_log = open('log.txt', "wb")
    test_log.write("")
    test_log.close()

# ============================================================
# ALLORA SETUP
# ============================================================

connector = SX127x_connector()
lora_node = Source(connector, config_file="LoRa.json", i2c=i2c)
chunk_size = lora_node.get_chunk_size()  # 235

try:
    clean_timing_file()
    service_housekeeping()

    print("Waiting first OK")
    backup = lora_node.establish_connection()
    service_housekeeping()

    print("Connection OK")

    # This is how to handle a backup file if needed.
    if backup:
        print("Asking backup")
        # file = Datasource.get_backup()
        # lora_node.restore_file(file)

    consecutive_metric_failures = 0

    # With an established connection, we start sending data periodically.
    while True:
        try:
            service_housekeeping()

            if not lora_node.got_file():
                gc.collect()
                service_housekeeping()

                data = request_metrics()
                service_housekeeping()

                if not data:
                    consecutive_metric_failures += 1

                    print("[SRC] Valid metrics were not received")
                    print("[SRC] Consecutive metric failures:", consecutive_metric_failures)

                    if consecutive_metric_failures >= CONTROL_LISTEN_AFTER_FAILURES:
                        print("[SRC] Failure threshold reached")
                        print("[SRC] Opening LoRa control listening window...")

                        listen_for_control_commands(
                            lora_node,
                            window_ms=CONTROL_LISTEN_WINDOW_MS
                        )

                        consecutive_metric_failures = 0

                    time.sleep(1)
                    service_housekeeping()
                    continue

                # If valid metrics are obtained, reset the failure counter.
                consecutive_metric_failures = 0

                print("Metrics obtained via I2C:", data)

                file = CTP_File(
                    name="Envio_metricas",
                    content=bytearray(data, 'utf-8'),
                    chunk_size=chunk_size
                )

                lora_node.set_file(file)

                service_housekeeping()

                print("[SRC] Sending metrics...")
                lora_node.send_file()
                service_housekeeping()

                print("[SRC] Metrics sent correctly")

            service_housekeeping()
            time.sleep_ms(100)

        except Exception as e:
            print("[SRC] Error while sending metrics:", repr(e))
            try:
                sys.print_exception(e)
            except:
                pass

            gc.collect()
            service_housekeeping()
            time.sleep(1)

except KeyboardInterrupt as e:
    print("EXIT")