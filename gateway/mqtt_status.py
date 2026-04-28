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
import threading
import paho.mqtt.client as mqtt

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

_client = None
_lock = threading.Lock()

def init_mqtt_status():
    global _client
    with _lock:
        if _client is None:
            client = mqtt.Client()
            client.connect(MQTT_BROKER, MQTT_PORT, 60)
            client.loop_start()   # hilo interno del cliente MQTT
            _client = client

def publish_json(topic, payload):
    if _client is None:
        raise RuntimeError("MQTT status client not initialized")
    _client.publish(topic, json.dumps(payload), qos=1, retain=True)

def publish_node_status(controlled_node_mac, state, message=None, command_type=None, timestamp=None):
    topic = f"allora/gateway_01/{controlled_node_mac}/status"
    payload = {
        "controlled_node_mac": controlled_node_mac,
        "state": state,
        "message": message,
        "command_type": command_type,
        "timestamp": timestamp.isoformat() if timestamp else None
    }
    publish_json(topic, payload)
