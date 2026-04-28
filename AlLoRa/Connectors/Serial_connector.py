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

import json
import serial, struct
from time import sleep, time

from AlLoRa.Packet import Packet
from AlLoRa.Connectors.Connector import Connector
from AlLoRa.utils.debug_utils import print

import threading

class Serial_connector(Connector):
    MAX_ATTEMPTS = 30  # Maximum attempts before resetting
    RESET_TIMEOUT = 60  # Timeout in seconds before allowing another reset

    def __init__(self, reset_function=None):
        super().__init__()
        self.attempt_count = 0
        self.last_reset_time = 0
        self.reset_function = reset_function

        self._io_lock = threading.RLock()
        self._rx_buf = bytearray()

    def config(self, config_json):  #max_timeout = 10
        # JSON Example:
        # {
        #     "name": "N",
        #     "mesh_mode": false,
        #     "debug": false,
        #     "min_timeout": 0.5,
        #     "max_timeout": 6
        #     "serial_port": "/dev/ttyAMA3",
        #     "baud": 9600,
        #     "timeout": 1
        # }
        super().config(config_json)
        if self.config_parameters:
            self.serial_port = self.config_parameters.get('serial_port', "/dev/ttyAMA3")
            self.baud = self.config_parameters.get('baud', 9600)
            self.timeout = self.config_parameters.get('timeout', 1)
            self.serial = serial.Serial(self.serial_port, self.baud, timeout=0.05)
            if self.debug:
                print("Serial Connector configure: serial_port: {}, baud: {}, timeout: {}".format(self.serial_port, self.baud, self.timeout))
    
    #Additional Functions:
    def read_until_any_prefix(self, prefixes, timeout_s):
        start = time()
        while True:
            remaining = timeout_s - (time() - start)
            if remaining <= 0:
                return None
            frame = self.serial_receive(remaining)
            if frame is None:
                return None
            for p in prefixes:
                if frame.startswith(p):
                    return frame
            if self.debug:
                print("Discarded stray frame:", frame[:120])

    def read_until_prefix(self, prefix, timeout_s):
        return self.read_until_any_prefix([prefix], timeout_s)

    def _drain_until_silence(self, silence_s=0.15, max_total_s=0.5):
        t0 = time()
        last_rx = time()
        while time() - t0 < max_total_s:
            n = self.serial.in_waiting
            if n:
                _ = self.serial.read(n)
                last_rx = time()
            else:
                if time() - last_rx >= silence_s:
                    break
                sleep(0.005)

    def _prepare_rx(self):
        self._drain_until_silence()
        self._rx_buf = bytearray()
        try:
            self.serial.reset_input_buffer()
        except Exception:
            pass
    ####################################################################            

    def send_command(self, command, focus_time=None, expect_prefixes=(b"OK", b"ERROR", b"EXCEPTION")):
        with self._io_lock:

            self._prepare_rx()

            if focus_time is None:
                focus_time = self.timeout
            # Send command and wait for response
            try:
                self.serial.write(command)
                self.serial.flush()
                # Wait for ack response
                print("Command sent:", command, "focus_time:", focus_time)
                #response = self.serial_receive(focus_time)
                response = self.read_until_any_prefix(list(expect_prefixes), timeout_s=focus_time)
                if response is None:  # Check if no response was received
                    self._drain_until_silence()
                    if self.debug:
                        print("No response received (timeout).")
                    raise Exception("No ACK received")
                else:
                    self.attempt_count = 0
                return response
            except Exception as e:
                if self.debug:
                    print("Error sending command or no response: ", e)
                self.attempt_count += 1
                if self.debug:
                    print("Attempt count: ", self.attempt_count, "/", self.MAX_ATTEMPTS) 

                if self.attempt_count >= self.MAX_ATTEMPTS:
                    self.attempt_reset()
                else: # If the maximum attempts have not been reached
                    if self.debug:
                        print("Max attempts not reached: ", self.attempt_count)

                return None

    def serial_receive(self, focus_time, end_phrase=b"<<END>>\n", max_len=65536):
        start = time()
        needle = end_phrase
        nlen = len(needle)

        while True:
            if time() - start > focus_time:
                return None

            idx = self._rx_buf.find(needle)
            if idx != -1:
                frame = bytes(self._rx_buf[:idx])
                self._rx_buf = self._rx_buf[idx + nlen:]
                return frame

            chunk = self.serial.read(256)  # no readline
            if chunk:
                self._rx_buf += chunk
                if len(self._rx_buf) > max_len:
                    self._rx_buf = self._rx_buf[-max_len:]
            else:
                sleep(0.005)

    def attempt_reset(self):
        if time() - self.last_reset_time > self.RESET_TIMEOUT:
            if self.reset_function is not None:
                if self.debug:
                    print("Resetting...")
                self.reset_function()  # Call the passed-in reset function
                self.attempt_count = 0
                self.last_reset_time = time()
            else:
                if self.debug:
                    print("No reset function provided.")
        else:
            if self.debug:
                print("Reset recently triggered, waiting...")

    def send_and_wait_response(self, packet: Packet):
        with self._io_lock:
            binary_payload = packet.get_content()
            command = b"S&W:" + binary_payload.hex().encode() + b"<<END>>\n"
            packet_size_sent = len(binary_payload)

            self._prepare_rx()

            self.serial.write(command)
            self.serial.flush()
            if self.debug:
                print("Command sent:", command[:80])

            ack = self.read_until_prefix(b"ACK:", timeout_s=self.timeout)
            if not ack:
                return {"type":"SEND_ERROR","message":"No ACK received from serial interface"}, packet_size_sent, 0, 0

            if self.debug:
                print("ACK frame:", ack)

            try:
                ack_value = float(ack.split(b"ACK:")[1])
                self.adaptive_timeout = ack_value + 0.5
            except Exception as e:
                return {"type":"PARSE_ERROR","message":f"Failed to parse ACK: {e}"}, packet_size_sent, 0, 0

            t0 = time()
            frame = self.read_until_any_prefix([b"{", b"ERROR_TYPE:", b"EXCEPTION:"], timeout_s=self.adaptive_timeout)
            td = time() - t0

            if frame is None:
                self._drain_until_silence()
                return {"type":"TIMEOUT","message":"No data received within timeout"}, packet_size_sent, 0, td

            if frame.startswith(b"ERROR_TYPE:"):
                parsed = self.parse_error_message(frame)
                return parsed, packet_size_sent, len(frame), td

            if frame.startswith(b"EXCEPTION:"):
                return {"type":"EXCEPTION","message":frame.decode(errors="ignore")}, packet_size_sent, len(frame), td

            # JSON (packet.get_dict() + rssi/snr)
            if frame.startswith(b"{"):
                try:
                    d = json.loads(frame.decode("utf-8", errors="strict"))
                except Exception as e:
                    return {"type":"PARSE_ERROR","message":f"Failed to parse JSON response: {e}"}, packet_size_sent, len(frame), td

                # If the adapter sent an error already normalized
                if isinstance(d, dict) and ("type" in d and "message" in d and "command" not in d):
                    return d, packet_size_sent, len(frame), td

                try:
                    response_packet = Packet(self.mesh_mode, self.short_mac)
                    ok = response_packet.load_dict(d)
                    if not ok:
                        return {"type":"CORRUPTED_PACKET","message":"Packet.load_dict() returned False"}, packet_size_sent, len(frame), td

                    # We set as attributes of the packet the values inserted in the JSON by the AlLoRa Adapter upon its reception (RSSI & SNR),
                    # so the Requester can update its status
                    response_packet.rssi = d.get("rssi")
                    response_packet.snr  = d.get("snr")
                    return response_packet, packet_size_sent, len(response_packet.get_content()), td
                except Exception as e:
                    return {"type":"LOAD_ERROR","message":f"Failed to load Packet from JSON: {e}"}, packet_size_sent, len(frame), td

            # If an unexpected response arrives
            self._drain_until_silence()
            return {"type":"INVALID_RESPONSE","message":f"Unexpected frame: {frame[:120]!r}"}, packet_size_sent, len(frame), td

    def send(self, packet: Packet):
        with self._io_lock:

            packet_content = packet.get_content()
            command = b"Send:" + packet_content.hex().encode() + b"<<END>>\n"  # Append the custom end phrase to the command
            ack_response = self.send_command(command, expect_prefixes=(b"OK", b"ERROR", b"EXCEPTION"))  # Use send_command to transmit
            if ack_response and b"OK" in ack_response:  # Check if the response contains "OK"
                return True
            else:
                if self.debug:
                    print("Send command not acknowledged or error occurred.")
                return False

    def recv(self, focus_time=12): 
        with self._io_lock:

            command = b"Listen:" + str(focus_time).encode() + b"<<END>>\n"
            ack_response = self.send_command(command, focus_time=focus_time + 0.5, expect_prefixes=(b"OK", b"ERROR", b"EXCEPTION"))
            if ack_response and b"OK" in ack_response:
                # Wait for the actual response
                if self.debug:
                    print("Listen command acknowledged, waiting for data for {} seconds...".format(focus_time + 0.5))
                received_data = self.serial_receive(focus_time + 0.5)
                if received_data:
                    if self.debug:
                        print("Received data: ", received_data)
                    return received_data
                else:
                    if self.debug:
                        print("No data received")
            else:
                if self.debug:
                    print("Listen command not acknowledged or error occurred.")
            return None

    def change_rf_config(self, frequency=None, sf=None, bw=None, cr=None, tx_power=None, backup=True):
        with self._io_lock:

            command = b"C_RFC:"
            if frequency:
                command += b"FREQ:" + str(frequency).encode() + b"|"
            if sf:
                command += b"SF:" + str(sf).encode() + b"|"
            if bw:
                command += b"BW:" + str(bw).encode() + b"|"
            if cr:
                command += b"CR:" + str(cr).encode() + b"|"
            if tx_power:
                command += b"TX_POWER:" + str(tx_power).encode() + b"|"
            command += b"<<END>>\n"
            response = self.send_command(command, expect_prefixes=(b"OK", b"ERROR", b"EXCEPTION"))
            if response and response.startswith(b"OK"):
                return True
            else:
                if self.debug:
                    print("Error changing RF config: {}".format(response))

    def get_rf_config(self):
        with self._io_lock:
            command = b"GET_RFC:<<END>>\n"

            self._prepare_rx()

            self.serial.write(command)
            self.serial.flush()

            response = self.read_until_prefix(b"FREQ:", timeout_s=self.timeout)
            if not response:
                return []

            params = response.split(b"|")
            rf_params = []
            for param in params:
                if not param:
                    continue
                key, value = param.split(b":", 1)
                rf_params.append(int(value.decode("utf-8")))
            return rf_params if len(rf_params) == 5 else []
    
    def request_mac(self, retries=5, delay=1):
        command = b"GET_MAC:<<END>>\n"
        for _ in range(retries):
            with self._io_lock:
                self._prepare_rx()
                self.serial.write(command)
                self.serial.flush()
                frame = self.serial_receive(self.timeout)
            if frame:
                s = frame.decode(errors="ignore").strip()
                if len(s) == 8 and all(c in "0123456789abcdefABCDEF" for c in s):
                    self.MAC = s.lower()
                    return self.MAC
            sleep(delay)
        return "00000000"
            

    def parse_error_message(self, error_data):
        error_str = error_data.decode(errors="ignore")
        d = {}
        for part in error_str.split("|"):
            if ":" in part:
                k, v = part.split(":", 1)
                d[k.strip()] = v.strip()
        return {
            "type": d.get("ERROR_TYPE") or "ERROR",
            "message": d.get("MESSAGE") or error_str,
            "focus_time": d.get("FOCUS_TIME"),
        }
    
