# CANtastic-stuff
Materials for the introduction to CAN and OBD-II security module.


## Contents

-[OBD_Sim_Gateway](https://github.com/tjcruz-dei/CANtastic-stuff/tree/main/OBD_Sim_Gateway)</li>: this is the source code for the dual CAN bus bridge, used to bridge the OBD-II CAN lines with an internal CAN bus.

-[Original OBD Simulator code - no bridging](https://github.com/tjcruz-dei/CANtastic-stuff/tree/main/Original%20OBD%20Simulator%20code%20-%20no%20bridging/OBD_Sim_Encoder)

-[PSA DBC Confort](https://github.com/tjcruz-dei/CANtastic-stuff/tree/main/PSA%20DBC%20Confort)

-[PiCAN Bridge to MQTT](https://github.com/tjcruz-dei/CANtastic-stuff/tree/main/PiCAN%20Bridge%20to%20MQTT): this is the source code for the CAN-MQTT bridge. The implemented bridge uses python-can to receive frames from SocketCAN and cantools to decode them according to the PSA confort-bus DBC. Decoded messages are then serialized and published through an MQTT broker using the Eclipse Paho client.

-[Schematics](https://github.com/tjcruz-dei/CANtastic-stuff/tree/main/Schematics)
