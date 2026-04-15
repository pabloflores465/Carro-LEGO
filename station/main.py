"""
main.py — Ciclo principal de la estación de clasificación.

Máquina de estados:
  IDLE → DETECTING → CLASSIFYING → WAITING_AUTH → EXECUTING → RETURNING → IDLE
                                         ↓ (error)
                                       ERROR → IDLE

Uso:
  # Con NXT y cámara reales (abre ventana de preview):
  python station/main.py --station-id 1 --broker localhost

  # Sin ventana de preview:
  python station/main.py --station-id 1 --no-preview

  # Modo simulación (sin NXT ni cámara):
  python station/main.py --simulate

Dependencias: pip install opencv-python pyzbar paho-mqtt pyyaml nxt-python
"""

import argparse
import logging
import sys
import threading
import time
from enum import Enum, auto
from pathlib import Path

import cv2

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from station.vision.camera import Camera
from station.vision.classifier import QRClassifier
from station.decision.router import Router
from station.comms.mqtt_client import StationMQTT
from robot.nxt_controller import NXTController

class _ColorFormatter(logging.Formatter):
    _RESET  = "\033[0m"
    _BOLD   = "\033[1m"
    _COLORS = {
        logging.DEBUG:    "\033[37m",    # blanco
        logging.INFO:     "\033[36m",    # cian
        logging.WARNING:  "\033[33m",    # amarillo
        logging.ERROR:    "\033[91m",    # rojo brillante
        logging.CRITICAL: "\033[95m",    # magenta
    }
    _STATE_COLOR = {
        "IDLE":         "\033[37m",      # gris
        "DETECTING":    "\033[96m",      # cian brillante
        "CLASSIFYING":  "\033[96m",
        "WAITING_AUTH": "\033[93m",      # amarillo brillante
        "EXECUTING":    "\033[92m",      # verde brillante
        "RETURNING":    "\033[92m",
        "ERROR":        "\033[91m",      # rojo brillante
    }

    def format(self, record):
        color = self._COLORS.get(record.levelno, self._RESET)
        ts    = self.formatTime(record, "%H:%M:%S")
        msg   = record.getMessage()

        # Resalta transiciones de estado
        if "Estado:" in msg and "→" in msg:
            parts = msg.split("→")
            new_state = parts[-1].strip()
            sc = self._STATE_COLOR.get(new_state, self._RESET)
            msg = f"{parts[0]}→ {sc}{self._BOLD}{new_state}{self._RESET}"

        lvl = f"{color}{record.levelname:8}{self._RESET}"
        return f"\033[90m{ts}\033[0m {lvl} {msg}"

handler = logging.StreamHandler()
handler.setFormatter(_ColorFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
log = logging.getLogger("station")

# Colores por estado para el overlay (BGR)
STATE_COLORS = {
    "IDLE":         (100, 100, 100),
    "DETECTING":    (0,   200, 255),
    "CLASSIFYING":  (0,   200, 255),
    "WAITING_AUTH": (0,   200, 255),
    "EXECUTING":    (0,   220,  50),
    "RETURNING":    (0,   220,  50),
    "ERROR":        (0,    50, 220),
}


# ── Estados ───────────────────────────────────────────────────────────────────

class State(Enum):
    IDLE         = auto()
    DETECTING    = auto()
    CLASSIFYING  = auto()
    WAITING_AUTH = auto()
    EXECUTING    = auto()
    RETURNING    = auto()
    ERROR        = auto()


# ── Preview de cámara (hilo separado) ─────────────────────────────────────────

class CameraPreview:
    """
    Muestra la cámara en una ventana OpenCV con overlay de QR y estado.
    run_main_thread() debe llamarse desde el hilo principal (requerido en macOS).
    """

    def __init__(self, camera: Camera, classifier: QRClassifier):
        self.camera = camera
        self.classifier = classifier
        self._state_label = "IDLE"
        self._last_qr = ""
        self._running = True

    def stop(self):
        self._running = False

    def update_state(self, state_name: str, qr: str = ""):
        self._state_label = state_name
        if qr:
            self._last_qr = qr

    def run_main_thread(self):
        """Bloquea el hilo principal mostrando la ventana. Cierra con Q o ESC."""
        cv2.namedWindow("Estación — Cámara", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Estación — Cámara", 640, 480)

        while self._running:
            try:
                frame = self.camera.capture()
            except Exception:
                time.sleep(0.1)
                continue

            detection = self.classifier.predict(frame)
            if detection:
                self._last_qr = detection.class_name

            # ── Overlay ───────────────────────────────────────────────────────
            color = STATE_COLORS.get(self._state_label, (200, 200, 200))
            h, w = frame.shape[:2]

            cv2.rectangle(frame, (0, 0), (w, 42), (20, 20, 20), -1)
            cv2.putText(frame, f"Estado: {self._state_label}",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

            if self._last_qr:
                cv2.rectangle(frame, (0, h - 40), (w, h), (20, 20, 20), -1)
                cv2.putText(frame, f"QR: {self._last_qr}",
                            (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                            (80, 220, 80), 2)

            if detection:
                cv2.rectangle(frame, (4, 46), (w - 4, h - 44), (0, 200, 80), 2)

            cv2.imshow("Estación — Cámara", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):
                break

        cv2.destroyAllWindows()


# ── Estación principal ────────────────────────────────────────────────────────

class Station:
    def __init__(
        self,
        station_id: str,
        broker_host: str,
        camera_device: int = 1,
        simulate: bool = False,
        preview: bool = True,
    ):
        self.station_id = station_id
        self.simulate = simulate
        self.state = State.IDLE

        self.router = Router(str(ROOT / "config" / "destinations.yaml"))
        self.mqtt = StationMQTT(station_id, broker_host=broker_host)

        if simulate:
            self.camera = None
            self.classifier = None
            self._preview = None
        else:
            self.camera = Camera(camera_device)
            self.classifier = QRClassifier()
            self._preview = CameraPreview(self.camera, self.classifier) if preview else None

        self.nxt = NXTController(
            camera=self.camera,
            classifier=self.classifier,
            simulate=simulate,
        )

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    def run(self):
        log.info(f"Estación {self.station_id} iniciada.")
        if self._preview:
            # La lógica de la estación corre en un hilo secundario
            # El preview corre en el hilo principal (requerido por macOS)
            t = threading.Thread(target=self._run_cycles, daemon=True)
            t.start()
            log.info("Preview de cámara abierto. Presiona Q para cerrarlo.")
            self._preview.run_main_thread()  # bloquea hasta que se cierre la ventana
            self._preview.stop()
        else:
            self._run_cycles()

    def _run_cycles(self):
        try:
            while True:
                self._cycle()
        except KeyboardInterrupt:
            log.info("Deteniendo estación...")
        finally:
            self.mqtt.disconnect()
            self.nxt.close()
            if self.camera:
                self.camera.release()

    def _transition(self, new_state: State, qr: str = ""):
        log.info(f"Estado: {self.state.name} → {new_state.name}")
        self.state = new_state
        self.mqtt.publish_status(new_state.name.lower())
        if self._preview:
            self._preview.update_state(new_state.name, qr)

    # ── Fases del ciclo ───────────────────────────────────────────────────────

    def _cycle(self):

        # ── IDLE: espera que aparezca un paquete con QR ───────────────────────
        self._transition(State.IDLE)
        log.info("Esperando paquete...")
        detection = None

        # Espera hasta ver 2 QR iguales (paquete colocado sobre el QR del suelo en home)
        while detection is None:
            self._transition(State.DETECTING)
            try:
                detection = self._scan_package_qr()
            except Exception as e:
                log.warning(f"Error al escanear: {e}")
                time.sleep(1)
                continue
            if detection is None:
                time.sleep(0.3)

        # ── CLASSIFYING ───────────────────────────────────────────────────────
        self._transition(State.CLASSIFYING, qr=detection.class_name)
        log.info(f"2 QR '{detection.class_name}' detectados → paquete listo, destino agujero {detection.hole}")
        self.mqtt.publish_event("detection", qr_content=detection.class_name, hole=detection.hole)

        hole = self.router.get_destination(detection)
        if hole is None:
            log.error(f"QR '{detection.class_name}' no tiene destino válido")
            self.mqtt.publish_event("error", reason="invalid_destination", qr=detection.class_name)
            self._transition(State.ERROR)
            time.sleep(3)
            return

        floor_qr = detection.class_name

        # ── WAITING_AUTH ──────────────────────────────────────────────────────
        self._transition(State.WAITING_AUTH, qr=floor_qr)
        granted = self.mqtt.request_auth(detection.class_name, hole)
        if not granted:
            log.warning("Autorización denegada o timeout")
            self.mqtt.publish_event("error", reason="auth_denied")
            self._transition(State.ERROR)
            time.sleep(3)
            return

        # ── EXECUTING ─────────────────────────────────────────────────────────
        self._transition(State.EXECUTING, qr=floor_qr)
        cycle_start = time.time()

        if self.simulate:
            log.info(f"[SIM] Robot buscando QR '{floor_qr}' en el suelo...")
            time.sleep(2)
            success = True
        else:
            success = self.nxt.deliver(floor_qr)

        if not success:
            log.error("El robot no encontró el QR destino en el recorrido")
            self.mqtt.publish_event("error", reason="qr_not_found_on_floor", floor_qr=floor_qr)
            self._transition(State.ERROR)
            time.sleep(3)
            return

        # ── RETURNING ─────────────────────────────────────────────────────────
        self._transition(State.RETURNING, qr=floor_qr)
        cycle_time = time.time() - cycle_start

        self.mqtt.publish_event(
            "cycle_complete",
            qr=detection.class_name,
            hole=hole,
            cycle_time_s=round(cycle_time, 2),
        )
        log.info(f"Ciclo completo en {cycle_time:.2f}s — paquete en agujero {hole}")

    def _scan_package_qr(self):
        """
        Retorna un Detection solo cuando la cámara ve 2 QR iguales,
        lo que significa que el paquete está colocado y listo.
        Retorna None mientras no se cumple esa condición.
        """
        if self.simulate:
            log.info("[SIM] Esperando paquete (2 QR iguales)...")
            time.sleep(0.5)
            from station.vision.classifier import Detection
            return Detection(class_name="QR1", hole=1)
        frame = self.camera.capture_sharp()
        return self.classifier.already_at_destination(frame)


# ── Punto de entrada ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Estación de clasificación de paquetes (NXT)")
    p.add_argument("--station-id", default="1")
    p.add_argument("--broker",     default="localhost")
    p.add_argument("--camera",     type=int, default=1, help="Índice de cámara OpenCV")
    p.add_argument("--simulate",   action="store_true", help="Modo sin NXT ni cámara real")
    p.add_argument("--no-preview", action="store_true", help="No abrir ventana de cámara")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    Station(
        station_id=args.station_id,
        broker_host=args.broker,
        camera_device=args.camera,
        simulate=args.simulate,
        preview=not args.no_preview,
    ).run()
