"""
main.py — Punto de entrada del sistema de robot LEGO QR.

Uso:
  python main.py              # modo real (NXT por USB + cámara iPhone)
  python main.py --simulate   # modo simulación (sin hardware)

Flujo de una misión:
  1. Usuario inicia cámara → la UI detecta el QR que lleva el robot encima.
  2. La configuración asocia ese QR a un destino (p. ej. QR1 → QR3).
  3. Usuario hace clic en "Conectar robot" → se abre conexión USB con el NXT.
  4. Usuario hace clic en "Iniciar misión" → el Navigator conduce el robot
     visualmente hacia el QR destino en el suelo, suelta la bola y regresa.
"""
import argparse
import logging
import threading
import tkinter as tk
from tkinter import messagebox

from config_manager import ConfigManager, QR_PACKAGE_NAMES, QR_DEST_NAMES
from navigation import NavigationConfig, Navigator, NavState
from qr_manager import ensure_base_qrs
from robot_controller import RobotController, make_controller
from ui import QRRobotUI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-10s] %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sistema QR Robot LEGO NXT")
    p.add_argument(
        "--simulate", action="store_true",
        help="Modo simulación: sin NXT ni cámara real."
    )
    return p.parse_args()


class App:
    def __init__(self, simulate: bool):
        self.simulate   = simulate
        self.config     = ConfigManager()
        self.robot: RobotController | None = None
        self.navigator: Navigator | None   = None
        self._nav_thread: threading.Thread | None = None

        # Genera los QR base al arrancar si no existen
        ensure_base_qrs()

        # Ventana principal
        self.root = tk.Tk()
        self.ui   = QRRobotUI(self.root, self.config)

        # Conectar callbacks de la UI con la lógica de la app
        self.ui.on_connect_robot = self._connect_robot
        self.ui.on_start_mission = self._start_mission
        self.ui.on_stop_mission  = self._stop_mission

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if simulate:
            self.ui.log_event("Modo simulación activo — sin hardware requerido.")

    # ─────────────────────────────────────────────────────────────────────────
    # Ciclo de vida
    # ─────────────────────────────────────────────────────────────────────────

    def run(self) -> None:
        self.root.mainloop()

    def _on_close(self) -> None:
        self._stop_mission()
        if self.robot:
            self.robot.close()
        if self.ui.camera:
            self.ui.camera.release()
        self.root.destroy()

    # ─────────────────────────────────────────────────────────────────────────
    # Callbacks de la UI
    # ─────────────────────────────────────────────────────────────────────────

    def _connect_robot(self) -> None:
        try:
            self.robot = make_controller(self.simulate)
            self.ui.set_robot_connected(True, sim=self.simulate)
            mode = "simulado" if self.simulate else "NXT USB"
            self.ui.log_event(f"Robot conectado ({mode}).")
            log.info("Robot conectado.")
        except Exception as exc:
            self.ui.log_event(f"Error conectando robot: {exc}")
            messagebox.showerror("Error NXT", str(exc))

    def _start_mission(self) -> None:
        if self._nav_thread and self._nav_thread.is_alive():
            self.ui.log_event("Ya hay una misión en curso.")
            return

        if self.robot is None:
            self.ui.log_event("Primero conecta el robot.")
            return

        if not self.ui.camera_running:
            self.ui.log_event("Primero inicia la cámara.")
            return

        # Quitar el sufijo " (paquete)" / " (destino)" que agrega la UI
        # y tomar solo el primer token (ej. "QR1 (paquete)" → "QR1")
        detected = self.ui.get_detected_qr().split()[0]
        if not detected or detected == "—":
            self.ui.log_event("No se detectó QR. Apunta la cámara al QR del paquete (QR1, QR2 o QR3).")
            return

        if detected in QR_DEST_NAMES:
            self.ui.log_event(
                f"{detected} es un QR de destino (suelo), no de paquete. "
                f"Apunta la cámara al paquete: QR1, QR2 o QR3."
            )
            return

        if detected not in QR_PACKAGE_NAMES:
            self.ui.log_event(f"QR desconocido: {detected}. Se esperaba QR1, QR2 o QR3.")
            return

        target = self.config.get_destination(detected)
        if not target:
            self.ui.log_event(f"Sin destino configurado para {detected}. Guarda las asociaciones primero.")
            return

        self.ui.log_event(f"Misión: {detected} → {target}")
        self.ui.set_dest_qr(target)

        nav_cfg = NavigationConfig(
            arrival_px            = self.config.get_nav("arrival_px"),
            advance_power         = self.config.get_nav("advance_power"),
            min_power             = self.config.get_nav("min_power"),
            search_power          = self.config.get_nav("search_power"),
            steer_gain            = self.config.get_nav("steer_gain"),
            steer_invert          = self.config.get_nav("steer_invert"),
            lost_debounce         = self.config.get_nav("lost_debounce"),
            arrival_debounce      = self.config.get_nav("arrival_debounce"),
            tilt_degrees          = self.config.get_nav("tilt_degrees"),
            tilt_power            = self.config.get_nav("tilt_power"),
            return_after_delivery = self.config.get_nav("return_after_delivery"),
            max_search_frames     = 300,
        )
        # Nota: heading_offset_deg ya no se usa — el heading se deriva del
        # movimiento real del robot entre frames consecutivos.

        self.navigator = Navigator(
            camera           = self.ui.camera,
            detector         = self.ui.detector,
            robot            = self.robot,
            config           = nav_cfg,
            status_callback  = self._on_nav_state,
            frame_callback   = self.ui.update_frame,
        )

        # Guardar robot_qr para pasarlo al navigate_to
        self._robot_qr = detected

        self._nav_thread = threading.Thread(
            target=self._run_mission, args=(target, detected), daemon=True
        )
        self._nav_thread.start()

    def _stop_mission(self) -> None:
        if self.navigator:
            self.navigator.stop()
            self.ui.log_event("Misión detenida manualmente.")

    # ─────────────────────────────────────────────────────────────────────────
    # Lógica de misión (hilo daemon)
    # ─────────────────────────────────────────────────────────────────────────

    def _run_mission(self, target: str, robot_qr: str) -> None:
        success = self.navigator.navigate_to(target, robot_qr)
        if success:
            self.ui.log_event(f"✓ Misión completada — bola entregada en {target}.")
        else:
            self.ui.log_event("✗ Misión fallida o abortada.")

    def _on_nav_state(self, state: NavState) -> None:
        """Recibe actualizaciones de estado desde el Navigator."""
        self.ui.set_nav_state(state)
        self.ui.log_event(f"Nav → {state.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Punto de entrada
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_args()
    App(simulate=args.simulate).run()
