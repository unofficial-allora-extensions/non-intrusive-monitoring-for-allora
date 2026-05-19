# Copyright (C) 2026 Diego Rios Gomez
#
# This file is part of Non-intrusive monitoring for AlLoRa.

import time, gc, json, machine, esp32, sys
from AlLoRa.Nodes.Source import Source
from AlLoRa.File import CTP_File
from AlLoRa.Connectors.SX127x_connector import SX127x_connector
from AlLoRa.Digital_Endpoint import Digital_Endpoint

from machine import UART, Pin, WDT

gc.enable()

# ============================================================
# UART CONFIGURATION
# ============================================================

uart = UART(1, baudrate=115200, tx=Pin(41), rx=Pin(42), timeout=50)

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
    even if the terminal node is not delivering metrics via UART.
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
# UART METRICS ACQUISITION
# ============================================================

def request_metrics():
    """
    Sends 0x01 via UART to request metrics and expects a response
    with the following format:
    [0xAA][len_L][len_H][payload...]
    """
    try:
        service_housekeeping()

        # We consume all possible remnants of data in the UART channel
        # to guarantee a clean start of the operation.
        while uart.any():
            uart.read()

        # We ask the controlled node for metrics.
        uart.write(b'\x01')

        # Some small margin is given for the controlled node to prepare its answer.
        time.sleep_ms(30)

        deadline = time.ticks_add(time.ticks_ms(), 500)

        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            service_housekeeping()

            # We look for the 0xAA synchronization byte.
            first = uart.read(1)

            if not first:
                time.sleep_ms(5)
                continue

            if first[0] != 0xAA:
                continue

            # We read the 2 remaining bytes of the header.
            header_rest = b""

            while len(header_rest) < 2 and time.ticks_diff(deadline, time.ticks_ms()) > 0:
                service_housekeeping()

                chunk = uart.read(2 - len(header_rest))

                if chunk:
                    header_rest += chunk
                else:
                    time.sleep_ms(5)

            if len(header_rest) < 2:
                print("Incomplete header")
                return None

            length = header_rest[0] | (header_rest[1] << 8)

            if length <= 0 or length > 512:
                print("Invalid length:", length)
                continue

            # We read the complete payload.
            payload = b""
            payload_deadline = time.ticks_add(time.ticks_ms(), 500)

            while len(payload) < length and time.ticks_diff(payload_deadline, time.ticks_ms()) > 0:
                service_housekeeping()

                chunk = uart.read(length - len(payload))

                if chunk:
                    payload += chunk
                else:
                    time.sleep_ms(5)

            if len(payload) != length:
                print("Incomplete payload")
                continue

            return payload.decode()

        print("Timeout while awaiting for UART frame")
        return None

    except Exception as e:
        print("UART Error:", e)
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
lora_node = Source(connector, config_file="LoRa.json", uart=uart)
chunk_size = lora_node.get_chunk_size()  # 235

try:
    clean_timing_file()
    service_housekeeping()

    print("Waiting first OK")
    backup = lora_node.establish_connection()
    service_housekeeping()

    print("Connection OK")

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

                print("Metrics obtained via UART:", data)

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