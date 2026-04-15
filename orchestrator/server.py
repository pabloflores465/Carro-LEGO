"""
server.py — Orquestador central MQTT.

Suscribe a todos los topics de estaciones, mantiene el estado global
y autoriza/rechaza solicitudes de ejecución.

Uso:
  python orchestrator/server.py --broker localhost

Requisito: pip install paho-mqtt
Broker: instalar Mosquitto → brew install mosquitto (macOS) o apt install mosquitto (Linux)
        Iniciar: mosquitto -v  (o brew services start mosquitto)
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator.state import StateRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ORCH] %(message)s",
)
log = logging.getLogger("orchestrator")

LOG_FILE = ROOT / "orchestrator" / "events.csv"


class Orchestrator:
    def __init__(self, broker_host: str = "localhost", broker_port: int = 1883):
        self.registry = StateRegistry()
        self._init_log()

        self.client = mqtt.Client(client_id="orchestrator")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self.client.connect(broker_host, broker_port, keepalive=60)

    def _init_log(self):
        write_header = not LOG_FILE.exists()
        self._log_file = open(LOG_FILE, "a", newline="")
        self._csv = csv.writer(self._log_file)
        if write_header:
            self._csv.writerow(["timestamp", "station", "event", "details"])

    def _on_connect(self, client, userdata, flags, rc):
        log.info(f"Conectado al broker (rc={rc})")
        client.subscribe("station/+/status")
        client.subscribe("station/+/event")
        client.subscribe("station/+/auth/request")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            log.warning(f"Mensaje no-JSON en {topic}")
            return

        # Extrae station_id del topic: station/{id}/...
        parts = topic.split("/")
        station_id = parts[1] if len(parts) >= 2 else "unknown"

        if topic.endswith("/status"):
            self._handle_status(station_id, payload)
        elif topic.endswith("/event"):
            self._handle_event(station_id, payload)
        elif topic.endswith("/auth/request"):
            self._handle_auth_request(station_id, payload)

    def _handle_status(self, station_id: str, payload: dict):
        status = payload.get("status", "unknown")
        self.registry.update_status(station_id, status)
        log.info(f"[{station_id}] status → {status}")

    def _handle_event(self, station_id: str, payload: dict):
        event = payload.get("event", "unknown")
        log.info(f"[{station_id}] evento: {event} | {payload}")
        self._csv.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            station_id,
            event,
            json.dumps({k: v for k, v in payload.items() if k not in ("station", "event", "timestamp")}),
        ])
        self._log_file.flush()

        if event == "cycle_complete":
            self.registry.record_completion(
                station_id,
                class_name=payload.get("class_name", ""),
                hole=payload.get("hole", 0),
            )

    def _handle_auth_request(self, station_id: str, payload: dict):
        authorized = self.registry.can_authorize(station_id)
        response = {"station": station_id, "granted": authorized}
        self.client.publish(
            f"station/{station_id}/auth/response",
            json.dumps(response),
        )
        log.info(f"[{station_id}] auth → {'GRANTED' if authorized else 'DENIED'}")

    def run(self):
        log.info("Orquestador iniciado. Esperando estaciones...")
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            log.info("Deteniendo orquestador...")
        finally:
            self._log_file.close()
            self.client.disconnect()


def parse_args():
    p = argparse.ArgumentParser(description="Orquestador central MQTT")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    Orchestrator(broker_host=args.broker, broker_port=args.port).run()
