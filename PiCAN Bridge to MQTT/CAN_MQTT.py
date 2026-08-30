#!/usr/bin/env python3
"""
Bridge selected SocketCAN frames to an MQTT broker, decoding payloads with a DBC file.

Requirements:
    python3 -m venv CAN
    source CAN/bin/activate    
    pip install python-can paho-mqtt cantools

Example:
    source CAN/bin/activate    

    python can_to_mqtt_dbc.py \
        --can-interface can0 \
        --broker 192.168.1.10 \
        --port 1883 \
        --topic vehicles/can \
        --dbc vehicle.dbc \
        --ids 0x123 0x456

If you omit --ids, all frames that can be decoded by the DBC will be considered.
Published topic format:
    <base_topic>/<message_name>
Example:
    vehicles/can/EngineData
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, List

import can
import cantools
import paho.mqtt.client as mqtt


@dataclass
class DecodedCanPayload:
    timestamp: float
    channel: str | None
    arbitration_id: str
    message_name: str
    is_extended_id: bool
    dlc: int
    raw_data_hex: str
    signals: dict[str, Any]


class CanToMqttDbcBridge:
    def __init__(
        self,
        can_interface: str,
        broker: str,
        port: int,
        base_topic: str,
        dbc_path: str,
        accepted_ids: Iterable[int] | None = None,
        username: str | None = None,
        password: str | None = None,
        qos: int = 0,
        keepalive: int = 60,
        retain: bool = False,
        publish_undecodable: bool = False,
    ) -> None:
        self.can_interface = can_interface
        self.broker = broker
        self.port = port
        self.base_topic = base_topic.rstrip("/")
        self.qos = qos
        self.keepalive = keepalive
        self.retain = retain
        self.publish_undecodable = publish_undecodable
        self._running = True

        self.db = cantools.database.load_file(dbc_path)
        self.accepted_ids = list(accepted_ids) if accepted_ids is not None else None

        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username is not None:
            self.mqtt_client.username_pw_set(username, password)

        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect

        self.bus: can.BusABC | None = None

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            print(f"[INFO] Connected to MQTT broker {self.broker}:{self.port}")
        else:
            print(f"[ERROR] MQTT connection failed with code: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self._running:
            print(f"[WARN] MQTT disconnected unexpectedly: {reason_code}")

    def _build_can_filters(self) -> list[dict] | None:
        if self.accepted_ids:
            ids = self.accepted_ids
        else:
            ids = [msg.frame_id for msg in self.db.messages]

        if not ids:
            return None

        filters: list[dict] = []
        for can_id in ids:
            is_extended = can_id > 0x7FF
            mask = 0x1FFFFFFF if is_extended else 0x7FF
            filters.append(
                {
                    "can_id": can_id,
                    "can_mask": mask,
                    "extended": is_extended,
                }
            )
        return filters

    def start(self) -> None:
        try:
            self.mqtt_client.connect(self.broker, self.port, self.keepalive)
            self.mqtt_client.loop_start()
        except Exception as exc:
            raise RuntimeError(f"Failed to connect to MQTT broker: {exc}") from exc

        try:
            self.bus = can.interface.Bus(
                channel=self.can_interface,
                interface="socketcan",
                can_filters=self._build_can_filters(),
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to open CAN interface '{self.can_interface}': {exc}") from exc

        if self.accepted_ids:
            print(
                f"[INFO] Listening on {self.can_interface} for IDs: "
                + ", ".join(hex(x) for x in self.accepted_ids)
            )
        else:
            print(f"[INFO] Listening on {self.can_interface} for DBC-decodable frames")

        while self._running:
            try:
                msg = self.bus.recv(timeout=1.0)
            except can.CanError as exc:
                print(f"[ERROR] CAN receive error: {exc}")
                time.sleep(1.0)
                continue

            if msg is None:
                continue

            self.process_message(msg)

    def stop(self) -> None:
        self._running = False
        print("[INFO] Shutting down...")

        try:
            if self.bus is not None:
                self.bus.shutdown()
        except Exception:
            pass

        try:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        except Exception:
            pass

    def process_message(self, msg: can.Message) -> None:
        try:
            message_def = self.db.get_message_by_frame_id(msg.arbitration_id)
            decoded_signals = message_def.decode(msg.data)
        except Exception as exc:
            if self.publish_undecodable:
                topic = f"{self.base_topic}/undecodable/{msg.arbitration_id:X}"
                payload = {
                    "timestamp": msg.timestamp,
                    "channel": msg.channel,
                    "arbitration_id": f"{msg.arbitration_id:X}",
                    "is_extended_id": msg.is_extended_id,
                    "dlc": msg.dlc,
                    "raw_data_hex": msg.data.hex().upper(),
                    "error": str(exc),
                }
                self._publish(topic, payload)
            else:
                print(
                    f"[DEBUG] Skipping undecodable frame 0x{msg.arbitration_id:X}: {exc}"
                )
            return

        payload = DecodedCanPayload(
            timestamp=msg.timestamp,
            channel=msg.channel,
            arbitration_id=f"{msg.arbitration_id:X}",
            message_name=message_def.name,
            is_extended_id=msg.is_extended_id,
            dlc=msg.dlc,
            raw_data_hex=msg.data.hex().upper(),
            signals=decoded_signals,
        )

        topic = f"{self.base_topic}/{message_def.name}"
        self._publish(topic, asdict(payload))

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=str)

        result = self.mqtt_client.publish(
            topic=topic,
            payload=body,
            qos=self.qos,
            retain=self.retain,
        )

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"[ERROR] Failed to publish to '{topic}': rc={result.rc}")
        else:
            print(f"[DEBUG] Published {topic}: {body}")


def parse_can_id(raw: str) -> int:
    try:
        return int(raw, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid CAN ID: {raw}") from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Forward DBC-decoded SocketCAN frames to MQTT."
    )
    parser.add_argument(
        "--can-interface",
        required=True,
        help="SocketCAN interface name, e.g. can0",
    )
    parser.add_argument(
        "--broker",
        required=True,
        help="MQTT broker hostname or IP",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1883,
        help="MQTT broker port (default: 1883)",
    )
    parser.add_argument(
        "--topic",
        required=True,
        help="Base MQTT topic, e.g. vehicles/can",
    )
    parser.add_argument(
        "--dbc",
        required=True,
        help="Path to DBC file",
    )
    parser.add_argument(
        "--ids",
        nargs="*",
        type=parse_can_id,
        default=None,
        help="Optional list of CAN IDs to forward, e.g. 0x123 0x456",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="MQTT username",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="MQTT password",
    )
    parser.add_argument(
        "--qos",
        type=int,
        choices=[0, 1, 2],
        default=0,
        help="MQTT QoS (default: 0)",
    )
    parser.add_argument(
        "--retain",
        action="store_true",
        help="Publish with MQTT retain flag",
    )
    parser.add_argument(
        "--publish-undecodable",
        action="store_true",
        help="Publish undecodable/raw frames under <topic>/undecodable/<id>",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    bridge = CanToMqttDbcBridge(
        can_interface=args.can_interface,
        broker=args.broker,
        port=args.port,
        base_topic=args.topic,
        dbc_path=args.dbc,
        accepted_ids=args.ids,
        username=args.username,
        password=args.password,
        qos=args.qos,
        retain=args.retain,
        publish_undecodable=args.publish_undecodable,
    )

    def _handle_signal(signum, frame) -> None:
        bridge.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        bridge.start()
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        bridge.stop()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
