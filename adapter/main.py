# Code from the AlLoRa project:
# https://github.com/SMARTLAGOON/AlLoRa
#
# Original work Copyright (C) Benjamin Arratia and contributors

# Main for Adaper in Gateway Side AlLoRa
# HW: TTGO LoRa 32

from AlLoRa.Nodes.Adapter import Adapter
from AlLoRa.Connectors.SX127x_connector import SX127x_connector
from AlLoRa.Interfaces.Serial_interface import Serial_Interface

if __name__ == "__main__":

	serial_iface = Serial_Interface()
	connector = SX127x_connector()
	connector.MAC = "00000000"
	lora_adapter = Adapter(connector, serial_iface, config_file="LoRa.json")
	lora_adapter.run()



