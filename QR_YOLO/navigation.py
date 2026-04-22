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

import cv2
import numpy as np

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
FrameCallback = Callable[[np.ndarray], None]  # frames anotados para la UI


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


# ── Dibujar debug en frame ────────────────────────────────────────────────────

def _annotate_frame(
    frame: np.ndarray,
    robot_det: Optional[QRDetection],
    target_det: Optional[QRDetection],
    robot_label: str,
    target_label: str,
) -> np.ndarray:
    """Dibuja coordenadas, vectores y heading en el frame para debug visual."""
    img = frame

    if robot_det:
        rx, ry = robot_det.center_x, robot_det.center_y
        # Centro del robot: cruz verde
        cv2.circle(img, (rx, ry), 10, (0, 255, 0), 2)
        cv2.drawMarker(img, (rx, ry), (0, 255, 0), cv2.MARKER_CROSS, 20, 2)
        cv2.putText(img, robot_label, (rx + 15, ry - 10),
                     cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        # Polígono del QR del robot
        if robot_det.polygon and len(robot_det.polygon) >= 4:
            pts = np.array(robot_det.polygon, np.int32)
            cv2.polylines(img, [pts], True, (0, 180, 255), 2)
            # Flecha de heading (arista 0→1)
            h = _qr_heading(robot_det)
            if h is not None:
                arrow_len = 60
                ex = int(rx + arrow_len * math.cos(h))
                ey = int(ry + arrow_len * math.sin(h))
                cv2.arrowedLine(img, (rx, ry), (ex, ey), (0, 180, 255), 3)
                cv2.putText(img, f"H={math.degrees(h):+.0f}°",
                            (ex + 5, ey), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 180, 255), 1)

    if target_det:
        tx, ty = target_det.center_x, target_det.center_y
        # Centro del target: cruz roja
        cv2.circle(img, (tx, ty), 10, (0, 0, 255), 2)
        cv2.drawMarker(img, (tx, ty), (0, 0, 255), cv2.MARKER_CROSS, 20, 2)
        cv2.putText(img, target_label, (tx + 15, ty - 10),
                     cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    if robot_det and target_det:
        # Línea robot → target
        cv2.line(img, (robot_det.center_x, robot_det.center_y),
                 (target_det.center_x, target_det.center_y), (255, 0, 255), 2)
        # Texto con distancia y ángulo
        dx = target_det.center_x - robot_det.center_x
        dy = target_det.center_y - robot_det.center_y
        dist = math.sqrt(dx*dx + dy*dy)
        ang = math.degrees(math.atan2(dy, dx))
        mx = (robot_det.center_x + target_det.center_x) // 2
        my = (robot_det.center_y + target_det.center_y) // 2
        cv2.putText(img, f"d={dist:.0f}px  ∠{ang:.0f}°  dx={dx:+d} dy={dy:+d}",
                    (mx - 80, my - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)

    return img


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
        frame_callback: Optional[FrameCallback] = None,
    ):
        self.camera      = camera
        self.detector    = detector
        self.robot       = robot
        self.config      = config
        self._status_cb  = status_callback
        self._frame_cb   = frame_callback
        self._state      = NavState.IDLE
        self._running    = False
        self._heading    = None       # heading suavizado (rad)
        self._positions: list = []    # historial de (x, y) para derivar heading

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
        self._positions = []
        self._heading   = None
        last_dist      = None   # para detectar si se acerca o aleja

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

            # ── Debug: enviar frame anotado a la UI ───────────────────────────
            debug_frame = _annotate_frame(
                frame.copy(), robot_det, target_det,
                robot_qr, target_qr,
            )
            if self._frame_cb:
                try:
                    self._frame_cb(debug_frame)
                except Exception:
                    pass

            # ── Alguno no visible → PARAR inmediatamente ──────────────────────
            if robot_det is None or target_det is None:
                self.robot.stop()
                lost_streak   += 1
                arrival_streak = 0

                if lost_streak < self.config.lost_debounce:
                    time.sleep(0.05)
                    continue

                search_frames += 1
                if search_frames > self.config.max_search_frames:
                    log.error("Timeout: no se ven %s y/o %s", robot_qr, target_qr)
                    self._set_state(NavState.ERROR)
                    return False

                missing = []
                if robot_det  is None: missing.append(robot_qr)
                if target_det is None: missing.append(target_qr)
                log.warning("No visible: %s — girando a buscar (frame %d)",
                            ", ".join(missing), search_frames)

                self._set_state(NavState.SEARCHING)
                # Limpiar historial: posiciones anteriores ya no son válidas
                self._positions = []
                self._heading = None
                # Giro en sitio: diferencial opuesto con más potencia
                p = max(self.config.search_power, 40)
                self.robot.steer(p, -p)
                time.sleep(0.12)
                self.robot.stop()
                time.sleep(0.08)
                continue

            # ── QR visible: resetear contadores ───────────────────────────────
            lost_streak   = 0
            search_frames = 0

            # ── Vector robot → destino ────────────────────────────────────────
            dx = target_det.center_x - robot_det.center_x
            dy = target_det.center_y - robot_det.center_y
            dist = math.sqrt(dx * dx + dy * dy)
            angle_to_target = math.atan2(dy, dx)

            # ── Detectar si se acerca o se aleja ─────────────────────────────
            proximity = ""
            if last_dist is not None:
                delta = dist - last_dist
                if delta > 5:
                    proximity = " [⚠ ALEJÁNDOSE]"
                elif delta < -5:
                    proximity = " [✓ ACERCÁNDOSE]"
            last_dist = dist

            # ── Derivar heading estable desde ventana de posiciones ───────────
            rx, ry = robot_det.center_x, robot_det.center_y
            self._positions.append((rx, ry))
            if len(self._positions) > 8:
                self._positions.pop(0)

            new_heading = None
            if len(self._positions) >= 4:
                ox, oy = self._positions[0]
                nx, ny = self._positions[-1]
                move_dx = nx - ox
                move_dy = ny - oy
                move_dist = math.sqrt(move_dx * move_dx + move_dy * move_dy)

                if move_dist > 15:
                    new_heading = math.atan2(move_dy, move_dx)

            if new_heading is not None:
                if self._heading is None:
                    self._heading = new_heading
                else:
                    diff = _angle_diff(new_heading, self._heading)
                    self._heading = self._heading + 0.3 * diff
                    while self._heading > math.pi:
                        self._heading -= 2 * math.pi
                    while self._heading < -math.pi:
                        self._heading += 2 * math.pi

            if self._heading is None:
                self._set_state(NavState.ADVANCING)
                log.info("[ADVANCING] esperando movimiento... robot=(%d,%d) target=(%d,%d) dist=%.0fpx%s",
                         rx, ry, target_det.center_x, target_det.center_y, dist, proximity)
                self.robot.steer(self.config.advance_power, self.config.advance_power)
                time.sleep(0.05)
                continue

            # ── Calcular error angular ────────────────────────────────────────
            angle_err = _angle_diff(angle_to_target, self._heading)
            angle_err_deg = math.degrees(angle_err)
            abs_err = abs(angle_err)

            # ── Estrategia de 3 niveles ───────────────────────────────────────
            # El robot rota ~40°/s con power=55. Necesitamos ser más agresivos
            # para alinear antes de perder QR.
            #
            # Nivel 1: error > 90° → giro agresivo en sitio (power 85)
            # Nivel 2: error 30-90° → avanzar + girar con corrección fuerte
            # Nivel 3: error < 30° → avanzar con corrección suave
            #
            if abs_err > math.radians(90):
                # Giro agresivo en sitio
                turn_power = 85
                if angle_err > 0:
                    self.robot.steer(turn_power, -turn_power)
                else:
                    self.robot.steer(-turn_power, turn_power)
                mode = "GIRO"

            elif abs_err > math.radians(30):
                # Avanzar mientras gira: corrección fuerte pero sin perder avance
                steer_amount = min(0.6, (abs_err / math.pi) * 1.2)
                if angle_err > 0:
                    # Derecha: rueda izq más rápida
                    left_p = self.config.advance_power
                    right_p = max(20, int(self.config.advance_power * (1.0 - steer_amount)))
                else:
                    # Izquierda: rueda der más rápida
                    right_p = self.config.advance_power
                    left_p = max(20, int(self.config.advance_power * (1.0 - steer_amount)))
                self.robot.steer(left_p, right_p)
                mode = "CURVA"

            else:
                # Error pequeño: avance suave con corrección mínima
                dist_ratio = min(1.0, dist / 400)
                adaptive_gain = self.config.steer_gain * (0.3 + 0.7 * dist_ratio)
                steering = (angle_err / math.pi) * adaptive_gain * self.config.steer_invert
                steering = max(-0.2, min(0.2, steering))

                base = self.config.advance_power
                min_p = self.config.min_power
                left_p  = max(min_p, min(100, int(base * (1.0 + steering))))
                right_p = max(min_p, min(100, int(base * (1.0 - steering))))
                self.robot.steer(left_p, right_p)
                mode = "RECTO"

            self._set_state(NavState.ADVANCING)
            log.info(
                "[%s] robot=(%d,%d) target=(%d,%d) dist=%.0fpx%s "
                "| heading=%+.0f° tgt=%+.0f° err=%+.1f° | %s",
                mode,
                rx, ry, target_det.center_x, target_det.center_y, dist, proximity,
                math.degrees(self._heading), math.degrees(angle_to_target),
                angle_err_deg, mode,
            )
            time.sleep(0.05)

        self.robot.stop()
        return False

    def _set_state(self, state: NavState) -> None:
        if self._state == state:
            return
        log.info("Nav: %s → %s", self._state.name, state.name)
        self._state = state
        if self._status_cb:
            try:
                self._status_cb(state)
            except Exception:
                pass
