"""
navigation.py — Navegación visual con cámara cenital (overhead).

¿Cómo funciona con la cámara encima mirando hacia abajo?
─────────────────────────────────────────────────────────
Con una cámara fija sobre la escena, el frame muestra TODO el entorno a la vez:
el robot (identificado por su QR de paquete, ej. QR1) y el destino (ej. QR4).

El error del enfoque anterior era navegar basándose en dónde está QR4 respecto
al CENTRO DEL FRAME. Eso no funciona porque depende de dónde esté la cámara,
no de dónde esté el robot.

El enfoque correcto:
  1. Detectar QR_robot  (ej. QR1) → posición del robot en el frame.
  2. Detectar QR_target (ej. QR4) → posición del destino en el frame.
  3. Calcular el vector robot → destino:
       dx = target.center_x − robot.center_x
       dy = target.center_y − robot.center_y  (positivo = abajo en pantalla)
  4. Calcular la orientación del robot usando las esquinas del QR (polígono).
     El QR1 está encima del robot alineado hacia adelante, así que la dirección
     "adelante" del robot = dirección de la arista superior del QR1.
  5. Calcular el ángulo entre "adelante del robot" y "vector hacia destino".
  6. Convertir ese ángulo en diferencial de motores:
       ángulo ≈ 0  → avanzar recto
       ángulo > 0  → curvar a la derecha
       ángulo < 0  → curvar a la izquierda
  7. Cuando la distancia robot-destino (en píxeles) cae bajo arrival_px → llegó.

Esta lógica funciona sin importar cómo esté orientado el robot ni dónde esté
la cámara, siempre que la cámara vea el QR del robot y el QR del destino.
"""
import logging
import math
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional

from vision import Camera, QRDetector, QRDetection
from robot_controller import RobotController

log = logging.getLogger("nav")


# ── Configuración ─────────────────────────────────────────────────────────────

@dataclass
class NavigationConfig:
    arrival_px: int           = 120    # distancia en píxeles robot→destino para considerar llegada
    advance_power: int        = 55     # potencia base de avance
    min_power: int            = 30     # potencia mínima de la rueda interior (evita parada total)
    search_power: int         = 25     # potencia durante búsqueda giratoria
    steer_gain: float         = 0.5    # 0=recto siempre, 1=máxima corrección; ajustar si gira mucho
    steer_invert: int         = 1      # 1=normal, -1=invertir si los motores están al revés
    lost_debounce: int        = 6      # frames sin ver algún QR antes de activar búsqueda
    arrival_debounce: int     = 4      # frames consecutivos cerca para confirmar llegada
    return_after_delivery: bool = True
    tilt_degrees: int         = 45
    tilt_power: int           = 60
    max_search_frames: int    = 300


# ── Estados ───────────────────────────────────────────────────────────────────

class NavState(Enum):
    IDLE       = auto()
    ADVANCING  = auto()
    SEARCHING  = auto()
    ARRIVING   = auto()
    DELIVERING = auto()
    RETURNING  = auto()
    DONE       = auto()
    ERROR      = auto()


StatusCallback = Callable[[NavState], None]


# ── Helpers geométricos ───────────────────────────────────────────────────────

def _qr_heading(det: QRDetection) -> Optional[float]:
    """
    Calcula el ángulo de orientación del QR en radianes usando su polígono.
    El polígono de pyzbar tiene 4 esquinas en orden; la arista [0]→[1] apunta
    hacia la derecha del QR tal como está impreso.
    Retorna el ángulo en radianes (0 = derecha, π/2 = abajo en coords de pantalla).
    Retorna None si no hay polígono disponible.
    """
    if not det.polygon or len(det.polygon) < 2:
        return None
    x0, y0 = det.polygon[0]
    x1, y1 = det.polygon[1]
    return math.atan2(y1 - y0, x1 - x0)


def _angle_diff(a: float, b: float) -> float:
    """Diferencia angular normalizada a [-π, π]."""
    d = a - b
    while d >  math.pi: d -= 2 * math.pi
    while d < -math.pi: d += 2 * math.pi
    return d


# ── Navegador ─────────────────────────────────────────────────────────────────

class Navigator:
    """
    Navega el robot desde su posición actual (marcada por robot_qr en el frame)
    hasta target_qr usando la posición relativa de ambos en la imagen.
    Diseñado para cámara cenital fija.
    """

    def __init__(
        self,
        camera: Camera,
        detector: QRDetector,
        robot: RobotController,
        config: NavigationConfig,
        status_callback: Optional[StatusCallback] = None,
    ):
        self.camera   = camera
        self.detector = detector
        self.robot    = robot
        self.config   = config
        self._cb      = status_callback
        self._state   = NavState.IDLE
        self._running = False

    @property
    def state(self) -> NavState:
        return self._state

    def stop(self) -> None:
        self._running = False
        self.robot.stop()
        log.info("Navegación detenida externamente.")

    # ── Loop principal ────────────────────────────────────────────────────────

    def navigate_to(self, target_qr: str, robot_qr: str) -> bool:
        """
        Navega el robot (identificado por robot_qr) hacia target_qr.
        Retorna True si completó la entrega.
        Llamar desde un hilo secundario.
        """
        log.info("Navegación cenital: %s → %s", robot_qr, target_qr)
        self._running = True
        self.robot.reset_tacho()

        lost_streak    = 0
        arrival_streak = 0
        search_frames  = 0

        while self._running:
            # ── Captura ───────────────────────────────────────────────────────
            try:
                frame = self.camera.capture()
            except Exception as exc:
                log.error("Error de cámara: %s", exc)
                self.robot.stop()
                self._set_state(NavState.ERROR)
                return False

            robot_det  = self.detector.detect_content(frame, robot_qr)
            target_det = self.detector.detect_content(frame, target_qr)

            # ── Alguno no visible ─────────────────────────────────────────────
            if robot_det is None or target_det is None:
                lost_streak   += 1
                arrival_streak = 0

                if lost_streak < self.config.lost_debounce:
                    time.sleep(0.05)
                    continue

                search_frames += 1
                if search_frames > self.config.max_search_frames:
                    log.error("Timeout: no se ven %s y/o %s", robot_qr, target_qr)
                    self.robot.stop()
                    self._set_state(NavState.ERROR)
                    return False

                missing = []
                if robot_det  is None: missing.append(robot_qr)
                if target_det is None: missing.append(target_qr)
                log.debug("No visible: %s — girando a buscar", missing)

                self._set_state(NavState.SEARCHING)
                self.robot.steer(0, self.config.search_power)
                time.sleep(0.05)
                continue

            lost_streak   = 0
            search_frames = 0

            # ── Vector robot → destino ────────────────────────────────────────
            dx = target_det.center_x - robot_det.center_x
            dy = target_det.center_y - robot_det.center_y
            dist = math.sqrt(dx * dx + dy * dy)

            # ── Llegada ───────────────────────────────────────────────────────
            if dist <= self.config.arrival_px:
                arrival_streak += 1
                if arrival_streak < self.config.arrival_debounce:
                    self.robot.steer(
                        self.config.advance_power // 2,
                        self.config.advance_power // 2,
                    )
                    time.sleep(0.05)
                    continue

                self.robot.stop()
                self._set_state(NavState.ARRIVING)
                tacho = self.robot.get_tacho()
                log.info("Llegó a %s  dist=%.0fpx  tacho=%d°", target_qr, dist, tacho)

                self._set_state(NavState.DELIVERING)
                self.robot.release_payload(
                    degrees=self.config.tilt_degrees,
                    power=self.config.tilt_power,
                )

                if self.config.return_after_delivery:
                    self._set_state(NavState.RETURNING)
                    self.robot.reverse_distance(tacho, self.config.advance_power)

                self._set_state(NavState.DONE)
                self._running = False
                log.info("Misión completada.")
                return True

            arrival_streak = 0

            # ── Calcular ángulo de corrección ─────────────────────────────────
            #
            # angle_to_target: ángulo del vector robot→destino en el frame.
            # robot_heading:   dirección "adelante" del robot, calculada desde
            #                  las esquinas del QR del robot (polígono pyzbar).
            #
            # Si no hay polígono disponible (cv2 fallback), usamos solo dx
            # como proxy del error lateral — menos preciso pero funcional.
            #
            angle_to_target = math.atan2(dy, dx)
            robot_heading   = _qr_heading(robot_det)

            if robot_heading is not None:
                # Ángulo que el robot tiene que girar: positivo=derecha, negativo=izquierda
                angle_err = _angle_diff(angle_to_target, robot_heading)
            else:
                # Sin orientación: usar solo la componente lateral (dx normalizado)
                angle_err = math.atan2(dx, max(abs(dy), 1)) * self.config.steer_invert

            # Convertir a steering [-1, +1] y aplicar gain.
            # Negamos angle_err para coincidir con la convención del fallback:
            #   dx > 0 (target a la derecha) → steering positivo → gira derecha
            #   dx < 0 (target a la izquierda) → steering negativo → gira izquierda
            steering = (-angle_err / math.pi) * self.config.steer_gain * self.config.steer_invert

            base  = self.config.advance_power
            min_p = self.config.min_power
            left_p  = max(min_p, min(100, int(base * (1.0 + steering))))
            right_p = max(min_p, min(100, int(base * (1.0 - steering))))

            self._set_state(NavState.ADVANCING)
            log.debug(
                "dist=%.0fpx  angle_err=%+.0f°  steering=%+.2f  L=%d R=%d",
                dist, math.degrees(angle_err), steering, left_p, right_p,
            )

            # B=rueda derecha recibe right_p, C=rueda izquierda recibe left_p
            self.robot.steer(right_p, left_p)
            time.sleep(0.05)

        self.robot.stop()
        return False

    def _set_state(self, state: NavState) -> None:
        if self._state == state:
            return
        log.info("Nav: %s → %s", self._state.name, state.name)
        self._state = state
        if self._cb:
            try:
                self._cb(state)
            except Exception:
                pass
