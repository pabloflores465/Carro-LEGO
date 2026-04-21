"""
robot_controller.py — Capa de abstracción para el control de motores.

Dos implementaciones:
  SimulatedController  → imprime acciones en consola, sin hardware.
  NXTController        → controla el NXT real por USB usando nxt-python.

Mapeo de motores NXT:
  Motor A (OUT A) → plataforma inclinable (suelta la bola)
  Motor B (OUT B) → rueda izquierda
  Motor C (OUT C) → rueda derecha

Si el robot se mueve en sentido contrario al esperado, cambia WHEEL_DIR = -1.
Si la plataforma inclina al lado equivocado, cambia TILT_DIR = -1.

TODO: Si tu modelo exacto de NXT usa firmware distinto o versión diferente de
      nxt-python, ajusta NXTController._connect() y los nombres de Port.
"""
import logging
import time
from abc import ABC, abstractmethod

log = logging.getLogger("robot")

WHEEL_DIR = 1   # 1 = adelante con potencia positiva; -1 = invertir
TILT_DIR  = -1  # dirección de inclinación de la plataforma


# ── Interfaz abstracta ────────────────────────────────────────────────────────

class RobotController(ABC):

    @abstractmethod
    def move_forward(self, power: int = 60) -> None: ...

    @abstractmethod
    def move_backward(self, power: int = 60) -> None: ...

    @abstractmethod
    def turn_left(self, power: int = 40) -> None: ...

    @abstractmethod
    def turn_right(self, power: int = 40) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def release_payload(self, degrees: int = 45, power: int = 60) -> None:
        """Inclina el motor A para soltar la bola y regresa a posición neutral."""
        ...

    @abstractmethod
    def get_tacho(self) -> int:
        """Retorna la posición actual del tacómetro de la rueda izquierda (°)."""
        ...

    @abstractmethod
    def reset_tacho(self) -> None: ...

    @abstractmethod
    def steer(self, left_power: int, right_power: int) -> None:
        """
        Conducción diferencial: corre los dos motores a velocidades distintas.
        Ej: steer(30, 60) curva a la izquierda; steer(60, 30) curva a la derecha.
        Úsalo en lugar de stop+turn para movimiento suave y continuo.
        """
        ...

    @abstractmethod
    def reverse_distance(self, tacho_units: int, power: int = 60) -> None:
        """Retrocede exactamente tacho_units grados (distancia registrada al ir)."""
        ...

    @abstractmethod
    def close(self) -> None: ...


# ── Controlador simulado ──────────────────────────────────────────────────────

class SimulatedController(RobotController):
    """Imprime acciones en consola. No requiere hardware."""

    def __init__(self):
        self._tacho = 0
        self._moving = False
        log.info("[SIM] Controlador simulado listo.")

    def move_forward(self, power: int = 60) -> None:
        self._moving = True
        log.info("[SIM] ▶  avanzar  power=%d%%", power)

    def move_backward(self, power: int = 60) -> None:
        self._moving = True
        log.info("[SIM] ◀  retroceder  power=%d%%", power)

    def turn_left(self, power: int = 40) -> None:
        log.info("[SIM] ↰  girar izquierda  power=%d%%", power)

    def turn_right(self, power: int = 40) -> None:
        log.info("[SIM] ↱  girar derecha  power=%d%%", power)

    def steer(self, left_power: int, right_power: int) -> None:
        self._moving = True
        log.info("[SIM] ↗  diferencial  L=%d%%  R=%d%%", left_power, right_power)

    def stop(self) -> None:
        self._moving = False
        log.info("[SIM] ■  detener")

    def release_payload(self, degrees: int = 45, power: int = 60) -> None:
        log.info("[SIM] ↓  liberar carga  degrees=%d  power=%d%%", degrees, power)
        time.sleep(0.4)
        log.info("[SIM] ↑  plataforma regresa a posición neutral")

    def get_tacho(self) -> int:
        if self._moving:
            self._tacho += 50
        return self._tacho

    def reset_tacho(self) -> None:
        self._tacho = 0
        log.info("[SIM] tacómetro reseteado")

    def reverse_distance(self, tacho_units: int, power: int = 60) -> None:
        log.info("[SIM] ↩  retroceder %d° a home  power=%d%%", tacho_units, power)
        time.sleep(abs(tacho_units) / 1000)

    def close(self) -> None:
        log.info("[SIM] controlador cerrado")


# ── Controlador NXT real ──────────────────────────────────────────────────────

class NXTController(RobotController):
    """
    Controla el LEGO Mindstorms NXT por USB desde la Mac.

    Requiere:
      pip install nxt-python>=3.3
      El NXT debe estar conectado por USB — nada se instala en el brick.

    TODO: Si el NXT no se detecta automáticamente, prueba:
          import nxt.locator; b = nxt.locator.find(name="NXT")
          Cambia "NXT" por el nombre programado en tu brick.
    """

    def __init__(self):
        self._brick = None
        self._motor_tilt  = None  # Motor A — plataforma
        self._motor_left  = None  # Motor B — rueda izquierda
        self._motor_right = None  # Motor C — rueda derecha
        self._connect()

    def _connect(self) -> None:
        try:
            import nxt.locator
            import nxt.motor
            self._nxt_motor = nxt.motor  # guardamos referencia para turn()
            self._brick = nxt.locator.find()
            self._motor_tilt  = nxt.motor.Motor(self._brick, nxt.motor.Port.A)
            self._motor_left  = nxt.motor.Motor(self._brick, nxt.motor.Port.B)
            self._motor_right = nxt.motor.Motor(self._brick, nxt.motor.Port.C)
            self._motor_left.reset_position(relative=False)
            log.info("NXT conectado por USB.")
        except Exception as exc:
            raise ConnectionError(
                f"No se pudo conectar al NXT: {exc}\n"
                "Verifica que:\n"
                "  1. El cable USB esté conectado.\n"
                "  2. El NXT esté encendido.\n"
                "  3. nxt-python esté instalado: pip install nxt-python>=3.3"
            ) from exc

    def move_forward(self, power: int = 60) -> None:
        self._motor_left.run(WHEEL_DIR * power)
        self._motor_right.run(WHEEL_DIR * power)

    def move_backward(self, power: int = 60) -> None:
        self._motor_left.run(-WHEEL_DIR * power)
        self._motor_right.run(-WHEEL_DIR * power)

    def turn_left(self, power: int = 40) -> None:
        self._motor_left.brake()
        self._motor_right.run(WHEEL_DIR * power)

    def turn_right(self, power: int = 40) -> None:
        self._motor_left.run(WHEEL_DIR * power)
        self._motor_right.brake()

    def steer(self, left_power: int, right_power: int) -> None:
        self._motor_left.run(WHEEL_DIR * left_power)
        self._motor_right.run(WHEEL_DIR * right_power)

    def stop(self) -> None:
        self._motor_left.brake()
        self._motor_right.brake()

    def release_payload(self, degrees: int = 45, power: int = 60) -> None:
        self._motor_tilt.turn(TILT_DIR * power,  abs(degrees), brake=True)
        time.sleep(0.3)
        self._motor_tilt.turn(-TILT_DIR * power, abs(degrees), brake=True)

    def get_tacho(self) -> int:
        return abs(self._motor_left.get_tacho().tacho_count)

    def reset_tacho(self) -> None:
        self._motor_left.reset_position(relative=False)

    def reverse_distance(self, tacho_units: int, power: int = 60) -> None:
        dist = abs(tacho_units)
        self._motor_left.turn(-WHEEL_DIR * power,  dist, brake=True)
        self._motor_right.turn(-WHEEL_DIR * power, dist, brake=True)

    def close(self) -> None:
        try:
            self.stop()
            self._brick.close()
            log.info("NXT desconectado.")
        except Exception:
            pass


# ── Factoría ──────────────────────────────────────────────────────────────────

def make_controller(simulate: bool) -> RobotController:
    """Retorna el controlador adecuado según el modo."""
    if simulate:
        return SimulatedController()
    return NXTController()
