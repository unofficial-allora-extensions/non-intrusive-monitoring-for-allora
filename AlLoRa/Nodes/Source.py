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

# The main AlLoRa class executed by the controllers. It handles the serial gathering of metrics from the sensing node asigned to a particular
# controller, and the transmision of the CONN-ACK and S-RESET control messages to it. It also activates the H-RESET circuit, interacting with
# a particular GPIO of the LilyGO that runs this code

import gc, machine, json
from AlLoRa.Nodes.Node import Node, Packet, urandom
from AlLoRa.File import CTP_File
from AlLoRa.utils.time_utils import get_time, current_time_ms as time, sleep, sleep_ms
from AlLoRa.utils.debug_utils import print
from AlLoRa.utils.os_utils import os

#I2C and UART are the serial protocols used for the communication between the controller nodes and the sensing nodes
from machine import I2C, Pin, UART

import binascii
import hashlib

class Source(Node):

    def __init__(self, connector, config_file = "LoRa.json", uart=None, i2c=None):
        super().__init__(connector, config_file)
        gc.enable()

        max_chunk_size = self.calculate_max_chunk_size()
        if self.chunk_size > max_chunk_size:
            self.chunk_size = max_chunk_size
            if self.debug:
                print("Chunk size too big, setting to max: ", self.chunk_size)

        self.file = None

        #As there is only one "Source class", its needed to manually comment/uncomment some code blocks in this class to alternate
        #between the two serial protocols. In this first case, its intended that the user/administrator chooses between the
        #initialization of the UART or the I2C object. Tip: search for "comment" to locate all the points in which this selection
        #has to be made

        #self.uart = uart

        self.i2c = i2c
        self.SLAVE_ADDR = 0x28

        #The secret key for the HMAC
        self.K = b"allora_for_the_win"

    '''
    def request_metrics(self):
        #Descomentar para versión nodo sensor 1 core (comentar para versión 2 cores) - Y cambiar códigos de control enviados.
        try: 
            # Enviar comando 0x01 para pedir muestreo inmediato 
            self.i2c.writeto(self.SLAVE_ADDR, b'\x01')
        except Exception as e: 
            print("Error writeto:", e) 
            return None 
        
        sleep_ms(200) # dar tiempo al esclavo a preparar la respuesta 
    
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
    '''
    #Functions to generate the HMAC of the packet. It includes the secret key hardcoded into this class and the Requester one, in addition 
    #to the main message that comprises: the command received, the sequence number to prevent replay attacks, and the sender's MAC.
    def hmac_sha256(self, key: bytes, msg: bytes) -> bytes:
        block_size = 64  # SHA-256 block size

        # Keys longer than block size must be hashed
        if len(key) > block_size:
            key = hashlib.sha256(key).digest()

        # Keys shorter must be padded with zeros
        if len(key) < block_size:
            key = key + b'\x00' * (block_size - len(key))

        o_key_pad = bytes((b ^ 0x5C) for b in key)
        i_key_pad = bytes((b ^ 0x36) for b in key)

        inner = hashlib.sha256(i_key_pad + msg).digest()

        return hashlib.sha256(o_key_pad + inner).digest()
        
    def generar_hmac(self, K_secreta: bytes, source_id, counter: int, command: bytes) -> bytes:
        if isinstance(source_id, str):
            source_id = source_id.encode("utf-8")

        msg = source_id + counter.to_bytes(4, "big") + command
        full_hmac = self.hmac_sha256(K_secreta, msg)
        return full_hmac[:8]
    #################    

    #To manage the HMAC's counter
    def load_last_counter(self):
        try:
            with open("last_counter.txt", "r") as f:
                return int(f.read().strip())
        except FileNotFoundError:
            return -1

    def save_last_counter(self, value):
        with open("last_counter.txt", "w") as f:
            f.write(str(value))
    #################        

    def _i2c_command(self, cmd_byte: int, read_size=128, max_payload=120, tries=6):
        """
        Format of the response that is expected:
        [0xAA][len_L][len_H][payload...]
        The function reads a block of fixed size and resynchronizes searching for "0xAA" inside the block
        """
        try:
            # Send command
            self.i2c.writeto(self.SLAVE_ADDR, bytes([cmd_byte]))
            sleep_ms(50)

            #We try to read the serial channel looking for a response for the control command sent, multiple times, to avoid timing problems
            for _ in range(tries):
                raw = self.i2c.readfrom(self.SLAVE_ADDR, read_size)
                if not raw or len(raw) < 3:
                    sleep_ms(10)
                    continue

                #Here we search for the synchronization "0xAA" header inside the block read
                pos = raw.find(b"\xAA")
                if pos == -1:
                    if self.debug:
                        print("[I2C] Desynchronized Frame:", raw[:8])
                    sleep_ms(10)
                    continue

                if pos + 3 > len(raw):
                    if self.debug:
                        print("[I2C] Header at the end of the block, retrying...")
                    sleep_ms(10)
                    continue

                length = raw[pos + 1] | (raw[pos + 2] << 8)

                if length <= 0 or length > max_payload:
                    if self.debug:
                        print("[I2C] Invalid Length:", length, "pos:", pos, "raw[:12]:", raw[:12])
                    sleep_ms(10)
                    continue

                # Complete payload?
                start = pos + 3
                end = start + length
                if end > len(raw):
                    # insufficient block (rare with read_size=128 and max_payload=120, but possible)
                    if self.debug:
                        print("[I2C] Incomplete payload in block, retrying... Len:", length, "have:", len(raw) - start)
                    sleep_ms(10)
                    continue

                return raw[start:end]

            return None

        except Exception as e:
            if self.debug:
                print("I2C error:", e)
            return None

    # The analogous UART function to the previous I2C one, expecting the same kind of response message 
    # -with the format [0xAA][len_L][len_H][payload...]-
    def _uart_command(self, cmd_byte: int, max_payload=512, tries=4, timeout_ms=250, inter_try_ms=20):
        if self.uart is None:
            print("[UART] Not initialized")
            return None
        try:
            #We try to read the serial channel looking for a response for the control command sent, multiple times, to avoid timing problems
            for attempt in range(tries):
                if self.debug:
                    print("[UART] Try", attempt + 1, "/", tries, "cmd=", cmd_byte)

                max_flush_reads = 32
                count = 0
                # We clean previous remaining data from the UART channel
                while self.uart.any() and count < max_flush_reads:
                    self.uart.read()
                    count += 1

                # Send command
                self.uart.write(bytes([cmd_byte]))
                sleep_ms(20)

                t0 = time()

                # We look for the "0xAA" header
                found_header = False
                while (time() - t0) < timeout_ms:
                    b = self.uart.read(1)
                    if not b:
                        sleep_ms(5)
                        continue

                    if b[0] == 0xAA:
                        found_header = True
                        break

                if not found_header:
                    if self.debug:
                        print("[UART] Timeout esperando 0xAA")
                    sleep_ms(inter_try_ms)
                    continue

                # Reading len_L + len_H
                header_rest = b""
                while len(header_rest) < 2 and (time() - t0) < timeout_ms:
                    chunk = self.uart.read(2 - len(header_rest))
                    if chunk:
                        header_rest += chunk
                    else:
                        sleep_ms(5)

                if len(header_rest) < 2:
                    if self.debug:
                        print("[UART] Incomplete header")
                    sleep_ms(inter_try_ms)
                    continue

                length = header_rest[0] | (header_rest[1] << 8)

                if length <= 0 or length > max_payload:
                    if self.debug:
                        print("[UART] Invalid length:", length)
                    sleep_ms(inter_try_ms)
                    continue

                # Reading the complete payload
                payload = b""
                while len(payload) < length and (time() - t0) < timeout_ms:
                    chunk = self.uart.read(length - len(payload))
                    if chunk:
                        payload += chunk
                    else:
                        sleep_ms(5)

                if len(payload) != length:
                    if self.debug:
                        print("[UART] Incomplete payload:", len(payload), "/", length)
                    sleep_ms(inter_try_ms)
                    continue

                return payload

            return None

        except Exception as e:
            if self.debug:
                print("[UART] error:", e)
            return None

    def handle_control_packet(self, packet: Packet) -> bool:
        """
        It handles the packets received to check if they possess the control bit flag.
        It executes the corresponding hard-reboot when needed.
        """

        #If its a control packet addressed at this controller node
        if packet.get_control() and packet.get_destination() == self.MAC:
            
            mensaje = packet.get_payload()

            try:
                ctrl = json.loads(mensaje.decode())
            except Exception as e:
                print("Invalid control payload:", e)
                return True

            cmd = ctrl.get("cmd")
            counter = ctrl.get("counter")
            hmac_hex = ctrl.get("hmac")

            if not cmd or counter is None or not hmac_hex:
                print("Some control fields are missing")
                return True

            try:
                hmac_recibida = binascii.unhexlify(hmac_hex)
            except Exception as e:
                print("HMAC inválida:", e)
                return True

            #print("Control Packet", mensaje)

            # Minimum security layer: we check via HMAC if the control messages come from the corresponding gateway, and we avoid replay attacks
            # with the counter included in those messages
            last = self.load_last_counter()

            if counter <= last:
                print("Replay attack/duplicate control ignored")
                return True

            source_id = packet.get_source()
            if isinstance(source_id, str):
                source_id = source_id.encode()

            hmac_calculada = self.generar_hmac(self.K, source_id, counter, cmd.encode())

            if(hmac_calculada != hmac_recibida):
                if self.debug:
                    print("COMMAND IGNORED: unauthorized source")
                    print("HMAC calculated: ", binascii.hexlify(hmac_calculada))
                    print("HMAC received: ", binascii.hexlify(hmac_recibida))
                return True

            # If this point of the execution is reached, the control packet received is considered to be legitimate

            self.save_last_counter(counter)

            if(cmd == "RESET"):
                print("[SRC] Processing RESET...")

                #We try to send the control packet over the serial connection in several occasions, to prevent timing problems of the protocol
                for attempt in range(3):
                    #Uncomment (and comment the other line) to use I2C communication instead of UART
                    #payload = self._i2c_command(0x02)
                    payload = self._uart_command(0x02, tries=3, timeout_ms=300)
                    print("[SRC] try number", attempt + 1, "RESET UART:", payload)

                    #print("Mensaje I2C recibido:", payload)

                    # We send a confirmation when needed. The response packet is also an OK command, but DOES NOT have the control bit flag set
                    if(payload == b"RESET"):
                        # Sending a minimal confirmation of the S-RESET execution
                        ok = Packet(mesh_mode=self.mesh_mode, short_mac=False)
                        ok.set_source(self.MAC)
                        ok.set_destination(packet.get_source())
                        ok.set_data(b"RESET")
                        ok.set_ok()

                        self.send_lora(ok)

                        sleep_ms(100)

                        print("RESET confirmation received, notifying the GW...")

                        return True

                return True     

            if(cmd == "CONN_ACK_REQ"):
                print("[SRC] Processing CONN_ACK_REQ...")

                #We try to send the control packet over the serial connection in several occasions, to prevent timing problems of the protocol
                for attempt in range(3):
                    #Uncomment (and comment the other line) to use I2C communication instead of UART
                    #payload = self._i2c_command(0x01)
                    payload = self._uart_command(0x01, tries=3, timeout_ms=300)
                    print("[SRC] try number", attempt + 1, "payload UART:", payload)

                    if not payload:
                        sleep_ms(30)
                        continue

                    try:
                        parsed = json.loads(payload.decode())
                    except Exception as e:
                        print("[CTRL] Invalid JSON:", e)
                        sleep_ms(30)
                        continue

                    if parsed.get("type") != "metrics":
                        print("[CTRL] Unexpected type:", parsed.get("type"))
                        sleep_ms(30)
                        continue

                    # We send a confirmation when needed. The response packet is also an OK command, but DOES NOT have the control bit flag set
                    if parsed.get("type") == "metrics":
                        print("[UART]: Connection acknowledgement received")
                        
                        parsed.pop("type", None)
                        metrics_dict = parsed

                        print("[UART]: Instant metrics obtained:", metrics_dict)

                        if metrics_dict:
                            try:
                                response_payload = json.dumps({
                                    "type": "conn_ack",
                                    "metrics": metrics_dict
                                })

                                print("[SRC]: CONN-ACK metrics - ", response_payload)

                                ok = Packet(mesh_mode=self.mesh_mode, short_mac=False)
                                ok.set_source(self.MAC)
                                ok.set_destination(packet.get_source())
                                ok.set_data(response_payload.encode())
                                ok.set_ok()

                                self.send_lora(ok)

                                sleep_ms(100)

                                print("The correct connection to the controlled node has been confirmed to the GW...")

                                return True

                            except Exception as e:
                                print("Error building conn_ack:", e)

                return True
                
            if(cmd == "HARD-REBOOT"):
                #We execute the hard-reboot of the controlled node, and send a minimal reboot confirmation

                #The code to execute the hard-reboot of the node cutting its power supply with the low-side MOSFET or relay 
                #(via the controller node's PINOUT). Note that the "0" and "1" values must be inverted in the relay case
                #with respect to the low-side MOSFET case. For the reasoning behind this, please refer to "/docs/circuits_README.md"
                #in the "non-intrusive-monitoring-for-allora" repository.

                relay = Pin(38, Pin.OUT)
                relay.value(0)

                sleep_ms(5000)

                relay.value(1)

                
                #Code for sending that minimal confirmation of the reboot
                ok = Packet(mesh_mode=self.mesh_mode, short_mac=False)
                ok.set_source(self.MAC)
                ok.set_destination(packet.get_source())
                ok.set_data(b"REBOOTED")
                ok.set_ok()
                
                self.send_lora(ok)

                sleep_ms(100)
                
                print("The hard-reboot of the controlled sensing node has been confirmed...")

                return True

        return False

    def get_chunk_size(self):
        return self.chunk_size

    def got_file(self):     # Check if I have a file to send
        return self.file is not None

    def set_file(self, file : CTP_File):
        self.file = file

    def restore_file(self, file: CTP_File):
        self.set_file(file)
        self.file.first_sent = time()
        self.file.metadata_sent = True

    def send_response(self, response_packet: Packet):
        if response_packet:
            if self.mesh_mode:
                response_packet.set_id(self.generate_id())
            t0 = time()
            if self.connector.sf == 12:
                sleep(1)
            self.send_lora(response_packet)
            tf = time()
            time_send = tf - t0
            time_reply = tf - self.tr
            if self.debug:
                print("Time Send: ", time_send, " Time Reply: ", time_reply)
            if self.subscribers:
                self.status['PSizeS'] = len(response_packet.get_content())
                self.status['TimePS'] = time_send
                self.status['TimeBtw'] = time_reply
                self.notify_subscribers()

    #This function ensures that a received message matches the criteria of any expected message.
    def listen_requester(self):
        packet = Packet(mesh_mode=self.mesh_mode, short_mac=False)
        focus_time = self.connector.adaptive_timeout
        t0 = time()
        data = self.connector.recv(focus_time)
        self.tr = time() # Get the time when the packet was received
        td = (self.tr - t0) / 1000  # Calculate the time difference in seconds

        if not data:
            if self.debug:
                print("No data received within focus time")
            
            self.connector.increase_adaptive_timeout()
            return None

        try:
            if not packet.load(data):
                return None
        except Exception as e:
            if data:
                if self.debug:
                    print("Error loading: ", data, " -> ", e)
                self.status["CorruptedPackets"] += 1
            else:
                if self.debug:
                    print("No data received")
            return None

        if self.mesh_mode:
            try:
                packet_id = packet.get_id()  # Check if already forwarded or sent by myself
                if packet_id in self.LAST_SEEN_IDS or packet_id in self.LAST_IDS:
                    if self.debug:
                        print("ALREADY_SEEN", self.LAST_SEEN_IDS)
                    return None
            except Exception as e:
                if self.debug:
                    print(e)

        try:            
            if self.handle_control_packet(packet):
                return None
        except Exception as e:
            if self.debug:
                print("Control packet corrupted:", repr(e))
                return None

        if self.debug:
            rssi = self.connector.get_rssi()
            snr = self.connector.get_snr()
            print('LISTEN_REQUESTER({}) at: {} || request_content : {}'.format(td, self.connector.adaptive_timeout, packet.get_content()))
            print("RSSI: ", rssi, " SNR: ", snr)
            self.status['RSSI'] = rssi
            self.status['SNR'] = snr
            self.status['PSizeR'] = len(data)
            self.status['TimePR'] = td * 1000  # Time in ms

        self.connector.decrease_adaptive_timeout(td)

        return packet

#Function modified to have a second-based timeout, and not an iteration-based one
    def establish_connection(self, timeout=None):
        start_time = time()
        while True:
            print("Establish")
            new_sf = None
            packet = self.listen_requester()
            if packet:
                if self.is_for_me(packet):
                    command = packet.get_command()
                    if Packet.check_command(command):
                        if command != Packet.OK:
                            return True
                        response_packet = Packet(self.mesh_mode, False)
                        response_packet.set_source(self.MAC)
                        response_packet.set_destination(packet.get_source())
                        response_packet.set_ok()

                        if packet.get_change_rf():
                            new_sf = packet.get_config()
                            response_packet.set_change_rf(new_sf)
                        if self.mesh_mode and packet.get_mesh() and packet.get_hop():
                            response_packet.enable_mesh()
                            if not packet.get_sleep():
                                response_packet.disable_sleep()
                        if packet.get_debug_hops():
                            response_packet.add_previous_hops(packet.get_message_path())
                            response_packet.add_hop(self.name, self.connector.get_rssi(), 0)

                        self.send_response(response_packet)
                        
                        if self.subscribers:
                            self.status['Status'] = 'OK'
                            self.notify_subscribers() 

                        if new_sf:
                            response_packet.set_change_rf(new_sf)
                            self.change_rf_config(new_sf)
                                
                        return False
                else:
                    if self.debug:
                        print("Not for me, my mac is: ", self.MAC, " and packet mac is: ", packet.get_destination())
                    if self.mesh_mode:
                        self.forward(packet)
            gc.collect()
            
            if timeout and (time() - start_time) > (timeout * 1000):
                print("Timeout establishing connection on source node")
                return False

    def send_file(self, timeout=float('inf')):  
        t0 = time() # Start time in ms
        while not self.file.sent:
            packet = self.listen_requester()
            if packet:
                if self.is_for_me(packet=packet):
                    response_packet, new_sf = self.response(packet)
                    if response_packet is not None:
                        self.send_response(response_packet)
                    if new_sf:
                        backup_cks = self.chunk_size
                        self.change_rf_config(new_sf)
                        if self.chunk_size != backup_cks:
                            self.file.change_chunk_size(self.chunk_size)
                else:
                    self.forward(packet=packet)
            elif self.sf_trial:
                self.sf_trial -= 1
                if self.sf_trial <= 0:
                    self.restore_rf_config()
                    self.sf_trial = False

            if time() - t0 > timeout:
                last_sent = self.file.last_chunk_sent 
                del(self.file)
                gc.collect()
                self.file = None
                if self.debug:
                    print("Timeout reached")
                # If something was sent, but not all, we return a True
                if last_sent:
                    return True
                return False 
                    
        del(self.file)
        gc.collect()
        self.file = None
        return True

    def response(self, packet):
        command = packet.get_command()
        if not Packet.check_command(command):
            return None, None

        src = packet.get_source()
        if not src:
            if self.debug:
                print("[SRC] Packet without a valid source, discarding it:", packet.get_content())
            return None, None

        response_packet = Packet(mesh_mode=self.mesh_mode, short_mac=False)
        response_packet.set_source(self.MAC)
        response_packet.set_destination(packet.get_source())

        if self.mesh_mode:
            if packet.get_mesh() and packet.get_hop():
                response_packet.enable_mesh()
                if not packet.get_sleep():
                    response_packet.disable_sleep()

        new_sf = None
        if self.sf_trial:
            if self.debug:
                print("SF Trial ended successfully")
            self.sf_trial = False
            self.backup_config()

        if packet.get_debug_hops():
            response_packet.set_data("")
            response_packet.enable_debug_hops()
            response_packet.add_previous_hops(packet.get_message_path())
            response_packet.add_hop(self.name, self.connector.get_rssi(), 0)
            return response_packet, new_sf

        if command == Packet.CHUNK:
            requested_chunk = int(packet.get_payload().decode())
            response_packet.set_data(self.file.get_chunk(requested_chunk))
            if self.subscribers:
                self.status['Chunk'] = self.file.get_length() - requested_chunk
                self.status['Status'] = 'CHUNK'
                self.status['Retransmission'] = self.file.retransmission

            if self.debug:
                print("RC: {} / {}".format(requested_chunk, self.file.get_length()))

            if not self.file.first_sent:
                self.file.report_SST(True)
            return response_packet, new_sf

        if command == Packet.METADATA:    # handle for new file
            filename = self.file.get_name()
            response_packet.set_metadata(self.file.get_length(), filename)

            if self.file.metadata_sent:
                self.file.retransmission += 1
                if self.debug:
                    print("Asked again for Metadata...")
            else:
                self.file.metadata_sent = True

            if self.subscribers:
                self.status['File'] = filename
                self.status['Status'] = 'Metadata'
                self.status['Chunk'] = self.file.get_length()
                self.status['Retransmission'] = self.file.retransmission
            return response_packet, new_sf

        if command == Packet.OK:
            response_packet.set_ok()

            if packet.get_change_rf():
                new_sf = packet.get_config()
                response_packet.set_change_rf(new_sf)
            elif self.file.first_sent and not self.file.last_sent:	# If some chunks are already sent...
                self.file.sent_ok()
            return response_packet, new_sf

        return response_packet, new_sf

    def forward(self, packet: Packet):
        try:
            if packet.get_mesh():
                if self.debug:
                    print("FORWARDED", packet.get_content())
                
                random_sleep = 0
                if packet.get_sleep():
                    random_sleep = (urandom(1)[0] % 5 + 1) * 0.1
                    
                if packet.get_debug_hops():
                    packet.add_hop(self.name, self.connector.get_rssi(), random_sleep)
                packet.enable_hop()
                if random_sleep:
                    sleep(random_sleep)

                success = self.send_lora(packet)
                if success:
                    self.LAST_SEEN_IDS.append(packet.get_id())
                    self.LAST_SEEN_IDS = self.LAST_SEEN_IDS[-self.MAX_IDS_CACHED:]
                else:
                    if self.debug:
                        print("ALREADY_FORWARDED", self.LAST_SEEN_IDS)
        except Exception as e:
            # If packet was corrupted along the way, won't read the COMMAND part
            if self.debug:
                print("ERROR FORWARDING", e)
