"""
nxt_controller.py — Controlador del NXT. Corre en la PC, conectado por USB.

No necesitas instalar nada en el NXT ni microSD.
nxt-python habla directamente con el brick por USB.

Lógica de entrega (modo QR):
  1. Motores A+B arrancan despacio hacia adelante.
  2. La cámara lee QR del suelo en tiempo real.
  3. Fase 1 — Aproximación: espera hasta ver el QR destino al menos 1 vez.
  4. Fase 2 — Encima: el robot sigue avanzando hasta que el QR deja de leerse
             (el robot lo está tapando → llegó).
  5. Para los motores. Registra la distancia recorrida (tacómetro).
  6. Motor C inclina la plataforma → el objeto cae.
  7. Motor C regresa a posición normal.
  8. Motores A+B retroceden exactamente la misma distancia registrada.

Requisito: pip install nxt-python
"""

import logging
import sys
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import yaml

# ── Helpers visuales ──────────────────────────────────────────────────────────
_R = "\033[0m"
_BOLD = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_GRAY   = "\033[90m"

def _bar(value: int, total: int, width: int = 20) -> str:
    filled = int(width * min(value, total) / max(total, 1))
    return f"[{'█' * filled}{'░' * (width - filled)}]"

def _qr_icon(count: int) -> str:
    if count >= 2: return f"{_GREEN}[QR QR]{_R}"
    if count == 1: return f"{_YELLOW}[QR -- ]{_R}"
    return f"{_GRAY}[--  --]{_R}"

def _live(phase: str, frame: int, total: int, count: int, extra: str = ""):
    bar  = _bar(frame, total)
    icon = _qr_icon(count)
    pct  = int(100 * frame / max(total, 1))
    line = f"  {_CYAN}{phase:<6}{_R} {icon}  {bar} {pct:3d}%  {_GRAY}{extra}{_R}"
    sys.stdout.write(f"\r{line}   ")
    sys.stdout.flush()

def _live_done():
    sys.stdout.write("\n")
    sys.stdout.flush()

if TYPE_CHECKING:
    from station.vision.camera import Camera
    from station.vision.classifier import QRClassifier

log = logging.getLogger("nxt")

_CALIBRATION_PATH = Path(__file__).parent.parent / "config" / "calibration.yaml"

# Frames máximos buscando el QR antes de abortar (no expuesto en UI, valor fijo).
FRAMES_TIMEOUT = 300

# Dirección de las ruedas. Si el robot va al revés con potencia positiva, cambia a -1.
WHEEL_DIR = 1

# Dirección de la plataforma. Si inclina al lado equivocado, cambia a -1.
TILT_DIR = -1

# Distancia (cm) por debajo de la cual se considera que hay un obstáculo.
OBSTACLE_THRESHOLD_CM = 20


def load_calibration() -> dict:
    """Lee config/calibration.yaml y retorna los parámetros actuales."""
    with open(_CALIBRATION_PATH) as f:
        data = yaml.safe_load(f)
    return {
        "advance_power":  int(data.get("advance_power",  30)),
        "return_power":   int(data.get("return_power",   40)),
        "tilt_power":     int(data.get("tilt_power",     60)),
        "tilt_degrees":   int(data.get("tilt_degrees",  120)),
        "frames_on_top":  int(data.get("frames_on_top",   6)),
    }


# ── Mock para simulación sin NXT físico ───────────────────────────────────────

class _MockBrick:
    pass

class _MockSensor:
    def get_sample(self):
        return 255  # sin obstáculo en simulación

class _MockMotor:
    def __init__(self, name):
        self._name = name
        self._tacho = 0
        self._power = 0

    def run(self, power):
        self._power = power
        log.info(f"[SIM NXT] Motor {self._name} corriendo a {power}%")

    def brake(self):
        log.info(f"[SIM NXT] Motor {self._name} frenado")
        self._power = 0

    def idle(self):
        self._power = 0

    def get_tacho(self):
        # Simula avance gradual mientras el motor corre
        class Tacho:
            pass
        t = Tacho()
        if self._power > 0:
            self._tacho += 50
        elif self._power < 0:
            self._tacho -= 50
        t.tacho_count = self._tacho
        return t

    def reset_position(self, relative=False):
        self._tacho = 0

    def turn(self, power, tacho_units, brake=True):
        direction = "adelante" if power > 0 else "atrás"
        log.info(f"[SIM NXT] Motor {self._name} → {tacho_units}° {direction}")
        time.sleep(abs(tacho_units) / 500)


# ── Controlador real NXT ──────────────────────────────────────────────────────

class NXTController:
    """
    Controla el NXT por USB desde la PC.
    Recibe la cámara y el clasificador para leer QR durante el movimiento.
    """

    def __init__(
        self,
        camera: "Camera",
        classifier: "QRClassifier",
        simulate: bool = False,
    ):
        self.camera = camera
        self.classifier = classifier
        self.simulate = simulate

        if simulate:
            self._brick = _MockBrick()
            self.motor_tilt  = _MockMotor("A")   # plataforma (objetos)
            self.motor_left  = _MockMotor("B")   # rueda izquierda
            self.motor_right = _MockMotor("C")   # rueda derecha
            self.sensor_prox = _MockSensor()     # sensor de proximidad S1
        else:
            import nxt.locator
            import nxt.motor
            import nxt.sensor
            import nxt.sensor.generic
            self._brick = nxt.locator.find()
            self.motor_tilt  = nxt.motor.Motor(self._brick, nxt.motor.Port.A)  # plataforma
            self.motor_left  = nxt.motor.Motor(self._brick, nxt.motor.Port.B)  # rueda izquierda
            self.motor_right = nxt.motor.Motor(self._brick, nxt.motor.Port.C)  # rueda derecha
            self.sensor_prox = nxt.sensor.generic.Ultrasonic(self._brick, nxt.sensor.Port.S1, check_compatible=False)
            log.info("NXT conectado por USB.")

    # ── API pública ───────────────────────────────────────────────────────────

    def deliver(self, target_qr: str) -> bool:
        """
        Lleva el paquete al QR destino y regresa a home.

        Condición de parada: la cámara pasa de ver 2 QR iguales (paquete + suelo)
        a ver solo 1 (el suelo quedó tapado bajo el robot).

        target_qr: contenido del QR (ej. "QR1").
        Retorna True si completó el ciclo, False si hubo timeout.
        """
        cfg = load_calibration()
        log.info(
            f"{_BOLD}Destino:{_R} {_CYAN}{target_qr}{_R}  "
            f"avance={cfg['advance_power']}%  retorno={cfg['return_power']}%  "
            f"frames_on_top={cfg['frames_on_top']}"
        )

        # ── Fase 1: espera ver 2 QR (robot quieto) ───────────────────────────
        log.info(f"{_BOLD}▶ FASE 1{_R}  Buscando 2 QR iguales...")
        timeout_count = 0
        while True:
            count = self._count_qr(target_qr)
            _live("F1", timeout_count, FRAMES_TIMEOUT, count, "esperando 2 QR")
            if count >= 2:
                _live_done()
                log.info(f"  {_GREEN}✓ 2 QR encontrados{_R} — arrancando motores")
                break
            timeout_count += 1
            if timeout_count >= FRAMES_TIMEOUT:
                _live_done()
                log.error(f"  {_RED}✗ Timeout Fase 1{_R} — no se detectaron 2 QR")
                return False

        # ── Fase 2: avanza hasta cubrir el QR del suelo ──────────────────────
        # No depende del QR del paquete (difícil de leer en movimiento).
        # Lógica: espera ver el QR del suelo (count >= 1), luego para
        # cuando lo cubre (count == 0 durante frames_on_top frames).
        self.motor_left.reset_position(relative=False)
        self._motors_run(cfg["advance_power"])
        log.info(f"{_BOLD}▶ FASE 2{_R}  Avanzando  [{_CYAN}QR suelo visible = seguir  /  QR tapado = parar{_R}]")

        seen_floor_qr = False   # ya vio el QR del suelo al menos una vez
        on_top        = 0       # frames consecutivos sin ver ningún QR
        timeout_count = 0

        while True:
            if self._wait_while_blocked(cfg["advance_power"]):
                on_top = 0   # pausa por obstáculo no cuenta como "encima del QR"

            count = self._count_qr(target_qr)

            if count >= 1:
                seen_floor_qr = True
                on_top = 0
            elif seen_floor_qr:
                on_top += 1   # solo cuenta si ya había visto el QR del suelo

            _live("F2", timeout_count, FRAMES_TIMEOUT, count,
                  f"tapando {on_top}/{cfg['frames_on_top']}" if seen_floor_qr and count == 0
                  else ("QR suelo visible ✓" if seen_floor_qr else "buscando QR suelo..."))

            if seen_floor_qr and on_top >= cfg["frames_on_top"]:
                break

            timeout_count += 1
            if timeout_count >= FRAMES_TIMEOUT:
                _live_done()
                self._motors_brake()
                log.error(f"  {_RED}✗ Timeout Fase 2{_R} — no se encontró el QR del suelo")
                return False

        _live_done()
        self._motors_brake()
        tacho = self.motor_left.get_tacho().tacho_count
        log.info(f"  {_GREEN}✓ Parado{_R} — {_BOLD}{tacho}°{_R} recorridos")

        # ── Depositar ─────────────────────────────────────────────────────────
        log.info(f"{_BOLD}▶ DEPOSITAR{_R}  Inclinando plataforma...")
        self._tilt(cfg["tilt_degrees"], cfg["tilt_power"])
        time.sleep(0.4)
        self._tilt(-cfg["tilt_degrees"], cfg["tilt_power"])
        time.sleep(0.3)
        log.info(f"  {_GREEN}✓ Paquete depositado{_R}")

        # ── Regresar a home ───────────────────────────────────────────────────
        log.info(f"{_BOLD}▶ RETORNO{_R}  Regresando {_BOLD}{tacho}°{_R} a home...")
        self._motors_return(tacho, cfg["return_power"])
        log.info(f"  {_GREEN}✓ Ciclo completo{_R}")
        return True

    def close(self):
        if not self.simulate:
            try:
                self._brick.close()
            except Exception:
                pass

    # ── Helpers de motor ──────────────────────────────────────────────────────

    def _motors_run(self, power: int):
        self.motor_left.run(WHEEL_DIR * power)
        self.motor_right.run(WHEEL_DIR * power)

    def _motors_brake(self):
        self.motor_left.brake()
        self.motor_right.brake()

    def _motors_return(self, tacho_forward: int, power: int):
        """Retrocede la misma distancia que avanzó usando el valor del tacómetro."""
        distance = abs(tacho_forward)
        self.motor_left.turn(-WHEEL_DIR * power, distance, brake=True)
        self.motor_right.turn(-WHEEL_DIR * power, distance, brake=True)

    def _tilt(self, degrees: int, power: int):
        """Inclina o restablece el Motor A (plataforma de objetos)."""
        direction = TILT_DIR * (power if degrees > 0 else -power)
        self.motor_tilt.turn(direction, abs(degrees), brake=True)

    def _is_blocked(self) -> bool:
        """Retorna True si el sensor de proximidad (S1) detecta un obstáculo cercano."""
        try:
            dist = self.sensor_prox.get_sample()
            return dist < OBSTACLE_THRESHOLD_CM
        except Exception as e:
            log.warning(f"Error leyendo sensor de proximidad: {e}")
            return False

    def _wait_while_blocked(self, power: int) -> bool:
        """Para los motores mientras haya obstáculo y los reanuda al despejarse.
        Retorna True si hubo una pausa real (para que el llamador resetee contadores)."""
        if not self._is_blocked():
            return False
        self._motors_brake()
        sys.stdout.write("\n")
        log.warning(f"  {_YELLOW}⚠ OBSTÁCULO DETECTADO{_R} — esperando...")
        while self._is_blocked():
            time.sleep(0.1)
        log.info(f"  {_GREEN}✓ Camino despejado{_R} — reanudando")
        self._motors_run(power)
        return True

    def _count_qr(self, target_qr: str) -> int:
        """
        Retorna cuántas veces aparece target_qr en el frame actual.
        2 = paquete + suelo visibles (robot aún no llegó)
        1 = solo el paquete visible (robot tapó el QR del suelo → parar)
        0 = ninguno visible
        Usa detección rápida (solo grises) para no frenar el movimiento.
        """
        if self.simulate:
            time.sleep(0.05)
            return 2  # en simulación siempre "ve 2" hasta que station lo maneja
        try:
            frame = self.camera.capture()
            return self.classifier.count_fast(frame, target_qr)
        except Exception as e:
            log.warning(f"Error leyendo QR durante movimiento: {e}")
            return 2  # ante duda, seguir avanzando
