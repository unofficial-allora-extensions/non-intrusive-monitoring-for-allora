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

import gc
from AlLoRa.Nodes.Node import Node, Packet, urandom, loads, dumps
from AlLoRa.File import CTP_File
from AlLoRa.Connectors.Connector import Connector
from AlLoRa.Interfaces.Interface import Interface
from AlLoRa.utils.time_utils import current_time_ms as time, sleep
from AlLoRa.utils.debug_utils import print
from AlLoRa.utils.os_utils import os

class Adapter(Node):

    def __init__(self, connector: Connector, interface: Interface, config_file = "LoRa.json"):
        super().__init__(connector, config_file)
        gc.enable()
        self.sf_trial = None
        self.interface = interface
        self.config_interface()

        # The RSSI and SNR parameters are included in the data packets by the AlLoRa Adapter upon reception, as it was the only
        # option for its inclusion, because of timing restrictions of the protocol which impeded its measurement in the sender
        self.status["RSSI"] = "-"
        self.status["SNR"] = "-"

    def config_interface(self):
        with open(self.config_file, "r") as f:
            lora_config = loads(f.read())
        config_interface = lora_config['interface']
        self.interface.setup(self.connector, self.debug, config_interface)

    def backup_config(self):
        conf = {"name": self.name,
                "chunk_size": self.chunk_size,
                "mesh_mode": self.mesh_mode,
                "debug": self.debug,
                "connector" : self.connector.backup_config(),
                "interface": self.interface.backup_config()}
        with open(self.config_file, "w") as f:
            f.write(dumps(conf))

    def run(self):
        THREAD_EXIT = False
        while True:
            try:
                if THREAD_EXIT:
                    break
                success = self.interface.client_API()  # change name
                if success:
                    # Initialize link-quality fields so the adapter always exposes a complete
                    # status dictionary, even before the first RF response is received.
                    self.status["RSSI"] = self.connector.get_rssi()
                    self.status["SNR"] = self.connector.get_snr()
                    self.notify_subscribers()
                    gc.collect()
                sleep(0.1)

            except KeyboardInterrupt as e:
                THREAD_EXIT = True
                if self.debug:
                    print("THREAD_EXIT")
            except Exception as e:
                if self.debug:
                    print("Error in Adapter: {}".format(e))
