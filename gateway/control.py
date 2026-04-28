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

from queue import Queue
from gateway_state import node_status, node_status_lock
from datetime import datetime
from mqtt_status import publish_node_status

control_queue = Queue()

def control_loop(gateway, control_queue):
    while True:
        cmd = control_queue.get()   # bloquea sin CPU

        if cmd["type"] == "RESET":
            mac = cmd["mac"]
            controlled_node_mac = cmd["controlled_node_mac"]
            gateway.send_control(destination_mac=mac, payload="reset", controlled_node_mac = controlled_node_mac)

        if cmd["type"] == "CONN-ACK":
            mac = cmd["mac"]
            controlled_node_mac = cmd["controlled_node_mac"]

            publish_node_status(
                        controlled_node_mac=controlled_node_mac,
                        state="PENDING",
                        message="Comprobando conexión",
                        timestamp=datetime.now()
            )

            response = gateway.send_control(destination_mac=mac, payload="connection_ack_request", controlled_node_mac = controlled_node_mac)   

            if(response):
                with node_status_lock:
                    node_status[controlled_node_mac] = {
                        "state": "CONNECTED",
                        "timestamp": datetime.now(),
                        "message": "Conexión establecida"
                    }

                    publish_node_status(
                        controlled_node_mac=controlled_node_mac,
                        state="CONNECTED",
                        message="Conexión establecida",
                        timestamp=datetime.now()
                    )
            
            else:
                with node_status_lock:
                    node_status[controlled_node_mac] ={
                        "state": "DISCONNECTED",
                        "timestamp": datetime.now(),
                        "message": "Sin conexión"
                    }

                    publish_node_status(
                        controlled_node_mac=controlled_node_mac,
                        state="DISCONNECTED",
                        message="Sin conexión",
                        timestamp=datetime.now()
                    )

        if cmd["type"] == "HARD-REBOOT":
            mac = cmd["mac"]
            controlled_node_mac = cmd["controlled_node_mac"]
            gateway.send_control(destination_mac=mac, payload="hard-reboot", controlled_node_mac = controlled_node_mac)
