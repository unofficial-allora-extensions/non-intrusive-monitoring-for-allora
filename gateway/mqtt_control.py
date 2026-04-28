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

import json
import time
import paho.mqtt.client as mqtt

from control import control_queue

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "allora/gateway_01/+/control"

CONTROL_TYPES = {"RESET", "CONN-ACK", "HARD-REBOOT"}

def load_controller_mac(controlled_node_mac):
    with open("controladores.json", "r") as f:
        data = json.load(f)
    return data[controlled_node_mac]

def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected with rc={rc}")
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        cmd_type = payload.get("type")
        controlled_node_mac = payload.get("controlled_node_mac")

        if cmd_type not in CONTROL_TYPES:
            print(f"[MQTT] Unknown control type: {cmd_type}")
            return

        if not controlled_node_mac:
            print("[MQTT] Missing controlled_node_mac")
            return

        mac_controlador = load_controller_mac(controlled_node_mac)

        cmd = {
            "type": cmd_type,
            "mac": mac_controlador,
            "controlled_node_mac": controlled_node_mac
        }

        control_queue.put(cmd)
        print(f"[MQTT] Enqueued control command: {cmd}")

    except Exception as e:
        print(f"[MQTT] Error processing message: {e}")

def mqtt_control_loop():
    while True:
        try:
            client = mqtt.Client()
            client.on_connect = on_connect
            client.on_message = on_message

            client.connect(MQTT_BROKER, MQTT_PORT, 60)
            client.loop_forever()

        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            time.sleep(5)
