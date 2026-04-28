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

from flask import Flask, render_template, redirect
from gateway_state import metrics, metrics_lock, node_status, node_status_lock
from control import control_queue
from datetime import datetime
from pathlib import Path
import json

app = Flask(__name__)

@app.route("/")
def index():
    with metrics_lock:
        data = dict(metrics)
    print("DEBUG metrics:", metrics) 

    with node_status_lock:
        status_copy = dict(node_status)

    base_dir = Path(__file__).resolve().parent
    json_path = base_dir / "controladores.json"

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            client_macs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        client_macs = {}

    return render_template("index.html", metrics=data, node_status=status_copy, client_macs=client_macs)

@app.route("/reset/<mac>", methods=["POST"])
def reset(mac):
    with open("controladores.json", "r") as f:
        data = json.load(f)

    mac_controlador = data[mac]

    control_queue.put({
        "type": "RESET",
        "mac": mac_controlador,
        "controlled_node_mac": mac
    })
    return redirect("/")

@app.route("/conn-ack/<mac>", methods=["POST"])
def conn_ack(mac):
    with node_status_lock:
        node_status[mac] = {
            "state": "PENDING",
            "timestamp": datetime.now(),
            "message": "Comprobando conexión"
        }

    with open("controladores.json", "r") as f:
        data = json.load(f)

    mac_controlador = data[mac]

    control_queue.put({
        "type": "CONN-ACK",
        "mac": mac_controlador,
        "controlled_node_mac": mac
    })
    return redirect("/")  

@app.route("/hard-reboot/<mac>", methods=["POST"])
def reboot(mac):
    with open("controladores.json", "r") as f:
        data = json.load(f)

    mac_controlador = data[mac]

    control_queue.put({
        "type": "HARD-REBOOT",
        "mac": mac_controlador,
        "controlled_node_mac": mac
    })
    return redirect("/")  
