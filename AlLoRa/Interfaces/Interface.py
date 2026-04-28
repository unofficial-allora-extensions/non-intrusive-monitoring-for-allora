# Code from the AlLoRa project:
# https://github.com/SMARTLAGOON/AlLoRa
#
# Original work Copyright (C) Benjamin Arratia and contributors

from AlLoRa.Packet import Packet
from AlLoRa.Connectors.Connector import Connector

class Interface:

    def __init__(self):
        pass

    def setup(self, connector: Connector, debug, config):
        self.connector = connector
        self.debug = debug
        self.config_parameters = config

    def backup_config(self):
        return self.config_parameters

    def client_API(self):
        pass
