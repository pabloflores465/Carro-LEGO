"""
mqtt_client.py — Cliente MQTT de la estación.

Publica estado y eventos al orquestador.
Suscribe al topic de autorización y llama un callback cuando llega la respuesta.

Topics:
  Publica:
    station/{id}/status         → estado actual de la estación
    station/{id}/event          → evento completado (detección, ciclo, error)
    station/{id}/auth/request   → solicita autorización para ejecutar
  Suscribe:
    station/{id}/auth/response  → respuesta del orquestador (granted / denied)

Requisito: pip install paho-mqtt
"""

import json
import threading
from typing import Callable, Optional

import paho.mqtt.client as mqtt


class StationMQTT:
    def __init__(
        self,
        station_id: str,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        on_auth_response: Optional[Callable[[bool], None]] = None,
    ):
        self.station_id = station_id
        self.on_auth_response = on_auth_response
        self._auth_event = threading.Event()
        self._auth_granted = False

        self.client = mqtt.Client(client_id=f"station_{station_id}")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self.client.connect(broker_host, broker_port, keepalive=60)
        self.client.loop_start()

    # ── Topics ──────────────────────────────────────────────────────────────

    def _topic(self, suffix: str) -> str:
        return f"station/{self.station_id}/{suffix}"

    # ── Callbacks MQTT ───────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        client.subscribe(self._topic("auth/response"))

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            return

        if msg.topic == self._topic("auth/response"):
            granted = payload.get("granted", False)
            self._auth_granted = granted
            self._auth_event.set()
            if self.on_auth_response:
                self.on_auth_response(granted)

    # ── Publicación ──────────────────────────────────────────────────────────

    def publish_status(self, status: str):
        """status: idle | detecting | classifying | waiting_auth | executing | returning | error"""
        self.client.publish(
            self._topic("status"),
            json.dumps({"station": self.station_id, "status": status}),
        )

    def publish_event(self, event_type: str, **kwargs):
        """Publica un evento de ciclo: detection, classification, delivery, error."""
        import time
        payload = {
            "station": self.station_id,
            "event": event_type,
            "timestamp": time.time(),
            **kwargs,
        }
        self.client.publish(self._topic("event"), json.dumps(payload))

    def request_auth(self, class_name: str, hole: int, timeout: float = 10.0) -> bool:
        """
        Solicita autorización al orquestador para ejecutar la entrega.
        Bloquea hasta recibir respuesta o agotar el timeout.
        Retorna True si fue autorizado, False si rechazado o timeout.
        """
        self._auth_event.clear()
        self._auth_granted = False
        self.client.publish(
            self._topic("auth/request"),
            json.dumps({
                "station": self.station_id,
                "class": class_name,
                "hole": hole,
            }),
        )
        received = self._auth_event.wait(timeout=timeout)
        return received and self._auth_granted

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()
