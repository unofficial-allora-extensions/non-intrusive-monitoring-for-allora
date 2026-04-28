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

# The main AlLoRa class executed by the AlLoRa Gateways. It handles the periodic polling of metrics, and the administration
# of the control commands (it sends them and receives their associated response)

import gc, time, json
from AlLoRa.Nodes.Node import Node, Packet
from AlLoRa.Digital_Endpoint import Digital_Endpoint
from AlLoRa.utils.time_utils import get_time, current_time_ms as time, sleep, sleep_ms
from AlLoRa.utils.debug_utils import print
from AlLoRa.utils.os_utils import os

import paho.mqtt.client as mqtt

import binascii
import hashlib

class Requester(Node):

    def __init__(self, connector = None, config_file = "LoRa.json", 
                    debug_hops = False, 
                    NEXT_ACTION_TIME_SLEEP = 0.5, 
                    max_sleep_time = 3, 
                    successful_interactions_required = 5,
                    mqtt_host="localhost", # MUST BE CHANGED if the MQTT Broker doesn't reside on the same machine as the AlLoRa Gateway
                    mqtt_port=1883, #DEFAULT PORT
                    gateway_id="gateway_01"):
        super().__init__(connector, config_file)
        gc.enable()
        
        self.debug_hops = debug_hops

        self.min_sleep_time, self.max_sleep_time = self.calculate_sleep_time_bounds()
        self.NEXT_ACTION_TIME_SLEEP = NEXT_ACTION_TIME_SLEEP

        #self.NEXT_ACTION_TIME_SLEEP = NEXT_ACTION_TIME_SLEEP
        self.observed_min_sleep = float('inf')
        self.observed_max_sleep = 0
        self.sleep_delta = 0.1  # Adjustable delta for dynamic sleep adjustments
        self.max_sleep_time = max_sleep_time  # Maximum sleep time
        self.successful_interactions_required = successful_interactions_required
        self.successful_interactions_count = 0  # Counter for successful interactions
        self.minimum_sleep_found = False  # Flag to indicate minimum sleep time found
        self.sleep_just_decreased = False  # Flag to indicate sleep time just changed
        self.last_sleep_time = self.NEXT_ACTION_TIME_SLEEP
        self.failure_count = 0  # Track consecutive failures
        self.max_failures = 3  # Maximum allowed consecutive failures
        self.exponential_backoff_threshold = 0.5  # Threshold for aggressive increase in sleep time
        
        #The "metrics object" contains the last metrics sent by every sensing node that the gateway has assigned, and is used for the 
        # legacy "Gateway Dashboard" mentioned on "/gateway/README.md". The "metrics_lock" serves the purpose of controlling the access to the
        # "metrics object", to avoid race conditions in its use.
        self.metrics = None
        self.metrics_lock = None

        if self.config:
            self.result_path = self.config.get('result_path', "Results")
            if self.debug:
                print("Result path: ", self.result_path)
            try:
                os.mkdir(self.result_path)
            except Exception as e:
                if self.debug:
                    print("Error creating result path: {}".format(e))

        self.status["SMAC"] = "-"   # Source MAC
        self.source_mac = None
        self.time_request = time()

        #For the MQTT transmission of metrics
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.gateway_id = gateway_id
        self.mqtt_client = None
        self._init_mqtt()

        #The secret key for the HMAC
        self.K = b"allora_for_the_win"

        #A flag used to avoid race conditions when accesing the AlLoRa channel in the periodic polling and real-time control
        #simultaneously - it controls its use guaranteeing an access in sequence-
        self.control_in_progress = False
        
    #Auxiliary function
    def _try_parse_metrics(self, data):
        try:
            payload = json.loads(data.decode())
            if payload.get("type") == "metrics":
                return payload
        except Exception:
            pass
        return None
    #################

    #The function for connecting with the MQTT Broker
    def _init_mqtt(self):
        try:
            raw_id = self.gateway_id

            if isinstance(raw_id, bytes):
                raw_id = raw_id.hex()
            
            client_id = "allora_gateway_" + str(raw_id)

            self.mqtt_client = mqtt.Client(
                client_id=client_id,
                protocol=mqtt.MQTTv311
            )
            self.mqtt_client.connect(self.mqtt_host, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
            print("MQTT connected:", self.mqtt_host)
        except Exception as e:
            print("MQTT init failed:", e)
            self.mqtt_client = None
    #################

    #The function for publishing the messages on the corresponding MQTT topic
    def _publish_metrics(self, node_id, metrics_dict):
        if not self.mqtt_client:
            return

        topic = "allora/{}/{}/metrics".format(
            self.gateway_id,
            node_id
        )

        publish_dict = metrics_dict.copy()

        uptime_str = publish_dict.get("Uptime")
        if uptime_str:
            try: 
                publish_dict["Uptime"] = self._uptime_to_seconds(uptime_str)
            except Exception as e:
                print("Exception transforming Uptime format:", e)

        try:
            payload = json.dumps(publish_dict)

            result = self.mqtt_client.publish(topic, payload)

            print("Publishing to topic:", topic)

            print("MQTT publish result:", result.rc)
        except Exception as e:
            print("MQTT publish error:", e)
            try:
                self.mqtt_client.connect()
            except:
                self.mqtt_client = None
    #################

    #Function to convert received Uptime from hh:mm:ss format to seconds (for compatibility with Telegraf)
    def _uptime_to_seconds(self, uptime_str):
        h, m, s = map(int, uptime_str.split(":"))
        return h*3600 + m*60 + s
    #################

    #Functions to generate the HMAC of the packet. It includes the secret key hardcoded into this class and the Source one, in addition 
    #to the main message that comprises: the command sent, the sequence number to prevent replay attacks, and the sender's MAC.
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
    def load_counter(self):
        try:
            with open("counter.txt", "r") as f:
                return int(f.read().strip())
        except FileNotFoundError:
            return 0

    def save_counter(self, value):
        with open("counter.txt", "w") as f:
            f.write(str(value))
    #################

    def create_request(self, destination, mesh_active, sleep_mesh):
        packet = Packet(self.mesh_mode, False)
        packet.set_source(self.connector.get_mac())
        packet.set_destination(destination)
        if mesh_active:
            packet.enable_mesh()
            if not sleep_mesh:
                packet.disable_sleep()
        
        return packet

    # def send_request(self, packet: Packet) -> Packet:
    #     if self.mesh_mode:
    #         packet.set_id(self.generate_id())
    #         if self.debug_hops:
    #             packet.enable_debug_hops()

    #     self.time_since_last_request = time() - self.time_request
    #     self.time_request = time()

    #     response_packet, packet_size_sent, packet_size_received, time_pr = self.connector.send_and_wait_response(packet)
        
    #     if self.subscribers:
    #         self.status['PSizeS'] = packet_size_sent
    #         self.status['PSizeR'] = packet_size_received
    #         self.status['TimePR'] = time_pr * 1000  # Time in ms
    #         self.status['TimeBtw'] = self.time_since_last_request * 1000  # Time in ms
    #         self.status['RSSI'] = self.connector.get_rssi()
    #         self.status['SNR'] = self.connector.get_snr()
    #         if response_packet is None:
    #             self.status['Retransmission'] += 1
    #             if packet_size_received > 0:
    #                 self.status['CorruptedPackets'] += 1

    #     return response_packet
    def send_request(self, packet: Packet) -> Packet:
        if self.mesh_mode:
            packet.set_id(self.generate_id())
            if self.debug_hops:
                packet.enable_debug_hops()

        self.time_since_last_request = time() - self.time_request
        self.time_request = time()

        # Get the response from the connector
        response_packet, packet_size_sent, packet_size_received, time_pr = self.connector.send_and_wait_response(packet)

        #This was deleted from "if self.subscribers:..." (just below) to obtain the values that come from the adapter in its response -in any case-
        if isinstance(response_packet, Packet):
            self.status['RSSI'] = response_packet.rssi
            self.status['SNR']  = response_packet.snr
        
        if self.subscribers:
            self.status['PSizeS'] = packet_size_sent
            self.status['PSizeR'] = packet_size_received
            self.status['TimePR'] = time_pr * 1000  # Time in ms
            self.status['TimeBtw'] = self.time_since_last_request * 1000  # Time in ms
            #self.status['RSSI'] = self.connector.get_rssi()
            #self.status['SNR'] = self.connector.get_snr()

            if isinstance(response_packet, dict):  # Handle errors
                self.status['Retransmission'] += 1
                if response_packet.get("type") == "CORRUPTED_PACKET":
                    self.status['CorruptedPackets'] += 1
                if self.debug:
                    print("Error received during request: ", response_packet)
                return None  # Signal failure

        elif isinstance(response_packet, dict):
            if self.debug:
                print("Error received during request: ", response_packet)
            return None

        return response_packet  # Return valid packet if successful
    
    #The main function added to the class
    def send_control(self, destination_mac, controlled_node_mac, payload, mesh_active=False, sleep_mesh=True, tries=3):
        packet = Packet(self.mesh_mode, False)
        packet.set_source(self.connector.get_mac())
        packet.set_destination(destination_mac)
        
        if mesh_active:
            packet.enable_mesh()
            
            if not sleep_mesh:
                packet.disable_sleep()
        
        if self.mesh_mode:
            packet.set_id(self.generate_id())
        
        #Control packets are defined by the OK command in addition to the Control Bit Flag
        packet.set_ok()
        packet.enable_control()

        #The logic to select one control command or other, depending on the "payload" value with which the function was invoked
        cmd = None

        if(payload == "reset"):
            cmd = b"RESET"

        if(payload == "connection_ack_request"):
            cmd = b"CONN_ACK_REQ"

        if(payload == "hard-reboot"):
            cmd = b"HARD-REBOOT"

        if cmd is None:
            print("Unknown control command:", payload)
            return None

        #HMAC establishment
        mac = self.connector.get_mac()

        if isinstance(mac, bytes):
            source_id = mac
        else:
            source_id = str(mac).encode()

        counter = self.load_counter()
        counter += 1
        self.save_counter(counter)

        hmac_value = self.generar_hmac(self.K, source_id, counter, cmd)

        #The payload of a control packet includes the command to be executed by the receiver, the counter used to prevent replay attacks, and the
        #MAC that identifies the packet's source
        packet.payload = json.dumps({
            "cmd": cmd.decode(),
            "counter": counter,
            "hmac": binascii.hexlify(hmac_value).decode()
        }).encode()

        print("HMAC calculated", binascii.hexlify(hmac_value))
        #################

        if self.debug:
            if(payload == "reset"):
                print("Sending RESET to", destination_mac, "in order to reset node", controlled_node_mac)
            
            if(payload == "connection_ack_request"):
                print("Sending CONNECTION ACKNOWLEDGEMENT REQUEST to", destination_mac, "in order to confirm connection with", controlled_node_mac)

            if(payload == "hard-reboot"):
                print("Sending HARD REBOOT order to", destination_mac, "in order to hard-reboot node", controlled_node_mac)

        #We try to send the control packet in several occasions, to prevent timing problems of the protocol
        for attempt in range(tries):
            self.control_in_progress = True
            try:    
                response_packet, packet_size_sent, packet_size_received, time_pr = self.connector.send_and_wait_response(packet)
            finally:
                self.control_in_progress = False
            if isinstance(response_packet, Packet):
                #We have to check that the response packet is addressed to this requester, and that it comes from the node to which we sent
                #the control packet
                my_mac = self.connector.get_mac()
                if isinstance(my_mac, bytes):
                    my_mac = my_mac.decode()

                if (
                    response_packet.get_destination() != my_mac or
                    response_packet.get_source() != destination_mac
                ):
                    if self.debug:
                        print("Ignoring foreign packet during control:",
                            "src=", response_packet.get_source(),
                            "dst=", response_packet.get_destination())
                    sleep(0.1)
                    continue

                print("Response Packet Payload to command control:", response_packet.payload)
                if (
                    response_packet.get_command() == Packet.OK
                    and response_packet.payload == b"RESET"
                    and response_packet.get_source() == packet.get_destination()
                ):
                    if self.debug:
                        print("RESET of", controlled_node_mac, "confirmed by", destination_mac)
                    return response_packet

                '''
                if (
                    response_packet.get_command() == Packet.OK
                    and response_packet.payload == b"CONN_ACK"
                    and response_packet.get_source() == packet.get_destination()
                ):
                    if self.debug:
                        print("CONNECTION to GATEWAY acknowledged by", controlled_node_mac, "via", destination_mac)
                    return True
                '''

                payload = response_packet.payload


                try:
                    parsed = json.loads(payload.decode())
                    print("Instant metrics obtained:", parsed)


                    if parsed.get("type") == "conn_ack":
                        metrics = parsed.get("metrics", {})

                        if self.debug:
                            print("CONNECTION to GATEWAY acknowledged by", controlled_node_mac, "via", destination_mac)

                        last_rssi = self.status["RSSI"]
                        last_snr  = self.status["SNR"]

                        with self.metrics_lock:
                                    self.metrics[controlled_node_mac] = {
                                        "RAM_Libre": metrics.get("RAM_Libre") if metrics else None,
                                        "RAM_Usada": metrics.get("RAM_Usada") if metrics else None,
                                        "RAM_Total": metrics.get("RAM_Total") if metrics else None,
                                        "Temperature": metrics.get("Temperature") if metrics else None,
                                        "Uptime": metrics.get("Uptime") if metrics else None,
                                        "rssi": last_rssi,
                                        "snr": last_snr
                                    }

                                    #We send the instant metrics obtained with the CONN-ACK message, to the MQTT Broker
                                    self._publish_metrics(controlled_node_mac, self.metrics[controlled_node_mac])

                        return True

                except ValueError:
                    pass

                if (
                    response_packet.get_command() == Packet.OK
                    and response_packet.payload == b"REBOOTED"
                    and response_packet.get_source() == packet.get_destination()
                ):
                    if self.debug:
                        print("HARD REBOOT of", controlled_node_mac, "confirmed by", destination_mac)
                    return response_packet

            if self.debug:
                print("Control response error:", response_packet)  
            sleep(0.3)      
           
        print("No confirmation of control command received") 
        return None

    def ask_ok(self, packet: Packet):
        packet.set_ok()
        response_packet = self.send_request(packet)
        if response_packet is None:
            return None, None
        if self.save_hops(response_packet):
            return  (1, "hop_catch.json"), response_packet.get_hop()
        if response_packet.get_command() == Packet.OK:
            hop = response_packet.get_hop()
            return True, hop
        return None, None

    def ask_metadata(self, packet: Packet):
        packet.ask_metadata()
        response_packet = self.send_request(packet)
        if response_packet is None:
            return None, None
        if self.save_hops(response_packet):
            return  (1, "hop_catch.json"), response_packet.get_hop()
        if response_packet.get_command() == Packet.METADATA:
            try:
                metadata = response_packet.get_metadata()
                hop = response_packet.get_hop()
                length = metadata["LENGTH"]
                filename = metadata["FILENAME"]
                if self.subscribers:
                    self.status['File'] = filename
                return (length, filename), hop
            except:
                return None, None
        return None, None

    def ask_data(self, packet: Packet, next_chunk):
        packet.ask_data(next_chunk)
        response_packet = self.send_request(packet)
        if response_packet is None:
            return None, None
        if self.save_hops(response_packet):
            return b"0", response_packet.get_hop()
        if response_packet.get_command() == Packet.DATA:
            try:
                chunk = response_packet.get_payload()
                
                if self.mesh_mode:
                    id = response_packet.get_id()
                    if not self.check_id_list(id):
                        return None, None
                    hop = response_packet.get_hop()
                    if self.debug and hop:
                        print("CHUNK + HOP: {} -> {} - Node: {}".format(chunk, hop, self.source_mac))
                    return chunk, hop
                else: 
                    if self.debug:
                        print("CHUNK: {} - Node: {}".format(chunk, self.source_mac))
                    return chunk, None

            except Exception as e:
                if self.debug:
                    print("ASKING DATA ERROR: {} Node {}".format(e, self.source_mac))
                return None, None
        return None, None

    def listen_to_endpoint(self, digital_endpoint: Digital_Endpoint, listening_time=None,
                       print_file=False, save_file=False, one_file=False):
        stop = False
        #print("print de prueba")
        mac = digital_endpoint.get_mac_address()
        self.source_mac = mac

        if self.subscribers:
            self.status['SMAC'] = mac
        save_to = self.result_path + "/" + mac
        sleep_mesh = digital_endpoint.get_sleep()

        connector_ok = self.prepare_connector(digital_endpoint)

        if not connector_ok:
            if self.debug:
                print("Connector not ready for endpoint: ", mac)
            return False

        t0 = time()
        if listening_time is None:
            listening_time = float('inf')
        end_time = t0 + (listening_time * 1000)
        
        while time() < end_time:
            t0 = time()

            if self.control_in_progress:
                sleep(0.2)
                continue
            
            try:
                packet_request = self.create_request(mac, digital_endpoint.get_mesh(), sleep_mesh)

                if digital_endpoint.state == "REQUEST_DATA_STATE":
                    if self.debug:
                        print("ASKING METADATA to {}".format(mac))
                    metadata, hop = self.ask_metadata(packet_request)
                    t0 = time()
                    digital_endpoint.set_metadata(metadata, hop, self.mesh_mode, save_to)
                    if self.debug:
                        print("METADATA from {}: {}".format(mac, metadata))
                
                elif digital_endpoint.state == "PROCESS_CHUNK_STATE":
                    next_chunk = digital_endpoint.get_next_chunk()
                    
                    if next_chunk is not None:
                        if self.debug:
                            print("ASKING CHUNK: {} to {}".format(next_chunk, mac))
                        data, hop = self.ask_data(packet_request, next_chunk)
                        
                        last_rssi = self.status["RSSI"]
                        last_snr  = self.status["SNR"]
                        
                        #Update metrics on the "metrics object" / Publish metrics in the MQTT topic
                        metrics_payload = self._try_parse_metrics(data)
                        
                        if metrics_payload is not None:

                            mac_nodo = self.controllers_reverse_map.get(self.source_mac, self.source_mac)

                            with self.metrics_lock:
                                self.metrics[mac_nodo] = {
                                    "RAM_Libre": metrics_payload.get("RAM_Libre") if metrics_payload else None,
                                    "RAM_Usada": metrics_payload.get("RAM_Usada") if metrics_payload else None,
                                    "RAM_Total": metrics_payload.get("RAM_Total") if metrics_payload else None,
                                    "Temperature": metrics_payload.get("Temperature") if metrics_payload else None,
                                    "Uptime": metrics_payload.get("Uptime") if metrics_payload else None,
                                    "rssi": last_rssi,
                                    "snr": last_snr
                                }
                                self._publish_metrics(mac_nodo, self.metrics[mac_nodo])
                        ####################
                        t0 = time()
                        self.status['Chunk'] = digital_endpoint.file_reception_info["total_chunks"] - next_chunk
                        file = digital_endpoint.set_data(data, hop, self.mesh_mode)
                        print("DEBUG: file desde set_data =", file)
                        
                        if file:
                            final_ok = self.create_request(mac, digital_endpoint.get_mesh(), sleep_mesh)
                            final_ok.set_ok()
                            final_ok.set_source(self.connector.get_mac())
                            self.send_lora(final_ok)
                            sleep(1)
                            self.status['Chunk'] = "DONE"
                            if print_file:
                                print(file.get_content())
                            if save_file:
                                file.save(save_to)
                            if one_file:
                                #stop = True
                                print("DEBUG: one_file=True, file =", file)
                                return file

                elif digital_endpoint.state == "OK":
                    if self.debug:
                        print("ASKING OK to {}".format(mac))
                    ok, hop = self.ask_ok(packet_request)
                    t0 = time()
                    digital_endpoint.connected(ok, hop, self.mesh_mode)

                if self.sf_trial:
                    if self.debug:
                        print("SF Trial ended successfully")
                    self.sf_trial = False
                    self.backup_config()

                self.successful_interactions_count += 1
                if self.successful_interactions_count >= self.successful_interactions_required:
                    self.last_sleep_time = self.NEXT_ACTION_TIME_SLEEP
                    #self.decrease_sleep_time()
                    #self.sleep_just_decreased = True
                    self.failure_count = 0

            except Exception as e:
                if self.debug:
                    print("LISTEN_TO_ENDPOINT ERROR: {} Node {}".format(e, mac))
                if self.sf_trial:
                    self.sf_trial -= 1
                    if self.sf_trial <= 0:
                        if self.debug:
                            print("Restoring RF config")
                        self.restore_rf_config()
                        self.sf_trial = False

                dt = (time() - t0) / 1000
                
                #self.increase_sleep_time()
                self.successful_interactions_count = 0
                self.failure_count += 1
                if self.sleep_just_decreased:
                    self.sleep_just_decreased = False
                    self.minimum_sleep_found = True
                    self.NEXT_ACTION_TIME_SLEEP = self.last_sleep_time
                    self.observed_min_sleep = self.last_sleep_time
                    if self.debug:
                        print("Minimum sleep time found: ", self.NEXT_ACTION_TIME_SLEEP)
                
                if self.failure_count >= self.max_failures:
                    self.observed_min_sleep = self.NEXT_ACTION_TIME_SLEEP
                    if self.debug:
                        print("Updated minimum sleep time to higher value: ", self.observed_min_sleep)
                    self.NEXT_ACTION_TIME_SLEEP = self.observed_min_sleep
                    self.failure_count = 0

            finally:
                if self.subscribers:
                    self.status['Status'] = digital_endpoint.state
                    self.notify_subscribers()

                gc.collect()
                dt = (time() - t0) / 1000
                if self.debug:
                    print("DT: ", dt, "Sleep time: ", self.NEXT_ACTION_TIME_SLEEP)
                sleep_time = max(0, self.NEXT_ACTION_TIME_SLEEP)
                if self.debug:
                    print("Sleep time: ", sleep_time)
                if sleep_time > 0:
                    sleep(sleep_time)
                
                if stop:
                    break
            
    def save_hops(self, packet):
        if packet is None:
            return False
        if packet.get_debug_hops():
            hops = packet.get_message_path()
            id = packet.get_id()
            t = get_time()  #strftime("%Y-%m-%d_%H:%M:%S")
            line = "{}: ID={} -> {}\n".format(t, id, hops)
            with open('log_rssi.txt', 'a') as log:
                log.write(line)
            return True
        return False

    def ask_change_rf(self, digital_endpoint, new_sf):
        try_for = 3
        if 7 <= new_sf <= 12:
            while True:
                packet = Packet(self.mesh_mode, False)
                packet.set_destination(digital_endpoint.get_mac_address())
                packet.set_change_rf(new_sf)
                if digital_endpoint.get_mesh():
                    packet.enable_mesh()
                    if not digital_endpoint.get_sleep():
                        packet.disable_sleep()
                response_packet = self.send_request(packet)
                if response_packet.get_command() == Packet.OK:
                    sf_response = int(response_packet.get_payload().decode().split('"')[1])
                    print(sf_response)
                    if sf_response == new_sf:
                        return True
                else:
                    try_for -= 1
                    if try_for <= 0:
                        return False

    def ask_change_rf(self, digital_endpoint, new_config):
        try_for = 20
        new_config = [new_config.get("freq", None), new_config.get("sf", None), 
                        new_config.get("bw", None), new_config.get("cr", None), 
                        new_config.get("tx_power", None), 
                        new_config.get("cks", None)] 
        config = self.connector.get_rf_config()
        if self.debug:
            print("Current config: ", config)
            print("New config: ", new_config)
        # Only change the values that are different from the current configuration
        new_freq = new_config[0] if new_config[0] != config[0] else None
        new_sf = new_config[1] if new_config[1] != config[1] else None
        new_bw = new_config[2] if new_config[2] != config[2] else None
        new_cr = new_config[3] if new_config[3] != config[3] else None
        new_tx_power = new_config[4] if new_config[4] != config[4] else None
        new_chunk_size = new_config[5] if new_config[5] != self.chunk_size else None
        while True:
            packet = Packet(self.mesh_mode, False)
            packet.set_destination(digital_endpoint.get_mac_address())
            changes = packet.set_change_rf({"freq": new_freq, "sf": new_sf, 
                                            "bw": new_bw, "cr": new_cr, 
                                            "tx_power": new_tx_power,
                                            "cks": new_chunk_size})
            if not changes:
                return False
            if digital_endpoint.get_mesh():
                packet.enable_mesh()
                if not digital_endpoint.get_sleep():
                    packet.disable_sleep()
            response_packet = self.send_request(packet)
            try:
                if response_packet.get_command() == Packet.OK:
                    new_config = response_packet.get_config()
                    if self.debug:
                        print("OK and changing config to: ", new_config)
                    changed = self.change_rf_config(new_config)
                    if not changed:
                        return False
                        
                    self.notify_subscribers()
                    self.reset_sleep_time()
                    return True
                else:
                    try_for -= 1
                    if try_for <= 0:
                        return False
            except Exception as e:
                if self.debug:
                    print("Error changing RF config: ", e)
                try_for -= 1
                if try_for <= 0:
                    return False

    def increase_sleep_time(self):
        if self.NEXT_ACTION_TIME_SLEEP < self.exponential_backoff_threshold:
            self.NEXT_ACTION_TIME_SLEEP *= 2  # Exponential increase
        else:
            random_factor = int.from_bytes(os.urandom(2), "little") / 2**16
            self.NEXT_ACTION_TIME_SLEEP = min(self.NEXT_ACTION_TIME_SLEEP * (1 + random_factor), self.max_sleep_time)
        
        if self.debug:
            print("Increased sleep time to:", self.NEXT_ACTION_TIME_SLEEP)

    def decrease_sleep_time(self):
        smoothing_factor = 0.2
        absolute_min_sleep = 0.01
        new_sleep_time = self.NEXT_ACTION_TIME_SLEEP * (1 - smoothing_factor)
        new_sleep_time = max(absolute_min_sleep, new_sleep_time)
        
        # Update observed minimum if the new sleep time is lower
        if self.minimum_sleep_found and new_sleep_time < self.observed_min_sleep:
            self.observed_min_sleep = new_sleep_time
        elif not self.minimum_sleep_found:
            self.observed_min_sleep = new_sleep_time
        
        self.NEXT_ACTION_TIME_SLEEP = max(new_sleep_time, self.observed_min_sleep)
        if self.debug:
            print("Decreased sleep time to:", self.NEXT_ACTION_TIME_SLEEP)

    def reset_sleep_time(self):
        self.min_sleep_time, self.max_sleep_time = self.calculate_sleep_time_bounds()
        self.NEXT_ACTION_TIME_SLEEP = 0.5
        self.observed_min_sleep = float('inf')
        self.observed_max_sleep = 0
        self.sleep_delta = 0.1
        self.successful_interactions_count = 0
        self.minimum_sleep_found = False
        self.sleep_just_decreased = False
        self.last_sleep_time = self.NEXT_ACTION_TIME_SLEEP
        self.failure_count = 0
        if self.debug:
            print("Reset sleep time to:", self.NEXT_ACTION_TIME_SLEEP)


    def calculate_sleep_time_bounds(self):
        sf = self.connector.sf
        bw = self.connector.bw
        # Basic heuristic to calculate min and max sleep times based on SF and BW
        sf_factor = 2 ** (sf - 7)  # SF7 as baseline
        bw_factor = 250 / bw  # 500kHz as baseline
        base_min_sleep_time = 0.001  # Adjust as needed
        base_max_sleep_time = 0.5  # Adjust as needed
        min_sleep_time = base_min_sleep_time * bw_factor / sf_factor
        max_sleep_time = base_max_sleep_time * sf_factor / bw_factor
        if self.debug:
            print("Min sleep time: ", min_sleep_time, "Max sleep time: ", max_sleep_time)
        return min_sleep_time, max_sleep_time

    def prepare_connector(self, digital_endpoint):
        if self.debug:
            print("Preparing connector for endpoint: ", digital_endpoint)
        de_freq = digital_endpoint.freq
        de_sf = digital_endpoint.sf
        de_bw = digital_endpoint.bw
        de_cr = digital_endpoint.cr
        de_tx_power = digital_endpoint.tx_power

        freq, sf, bw, cr, tx_power = self.connector.get_rf_config()
        print("Current RF config: ", freq, sf, bw, cr, tx_power)
        print("Endpoint RF config: ", de_freq, de_sf, de_bw, de_cr, de_tx_power)
        if de_freq != freq or de_sf != sf or de_bw != bw or de_cr != cr or de_tx_power != tx_power:
            if self.debug:
                print("Changing RF config to: ", de_freq, de_sf, de_bw, de_cr, de_tx_power)
            # try 3 times to change the RF config to fit the endpoint configuration
            for i in range(3):
                success = self.connector.change_rf_config(frequency=de_freq, sf=de_sf, bw=de_bw, cr=de_cr, tx_power=de_tx_power)
                if success:
                    sleep(1)
                    for i in range(3):
                        rf_params = self.connector.get_rf_config()
                        if rf_params:
                            # Check that the RF configuration has been changed successfully
                            if rf_params[0] == de_freq and rf_params[1] == de_sf and rf_params[2] == de_bw and rf_params[3] == de_cr and rf_params[4] == de_tx_power:
                                if self.debug:
                                    print("RF configuration changed successfully")
                                return True
                            break
                        sleep(1)
                sleep(1)
            if self.debug:
                print("Failed to change RF configuration")
            return False    # Failed to change RF configuration
        if self.debug:
            print("RF config already set to endpoint config")
        return True
            



