"""
robot_main.py — Corre DENTRO del EV3 (ev3dev Python)

Escucha comandos JSON por Bluetooth serial y ejecuta rutinas de motor.

Protocolo de entrada (línea JSON por comando):
  {"action": "deliver", "hole": 1}   → lleva paquete al agujero N y regresa a home
  {"action": "home"}                  → solo regresa a posición inicial
  {"action": "ping"}                  → responde {"status": "ok"}

Protocolo de salida (línea JSON por respuesta):
  {"status": "ok"}
  {"status": "error", "msg": "..."}
  {"status": "done", "action": "deliver", "hole": 1}
  {"status": "done", "action": "home"}

Motores:
  Motor A + Motor B → llantas (horario = adelante, antihorario = atrás)
  Motor C           → plataforma giratoria (deposita paquete al inclinar)

Calibración (ajustar según construcción física):
  HOLE_POSITIONS  → dict hole_number: distancia en grados del motor de avance
  TILT_ANGLE      → ángulos de Motor C para soltar el objeto
  MOTOR_SPEED     → velocidad de avance (0-100)
"""

import json
import sys

try:
    from ev3dev2.motor import LargeMotor, MediumMotor, OUTPUT_A, OUTPUT_B, OUTPUT_C
    from ev3dev2.motor import MoveSteering, SpeedPercent
    EV3_AVAILABLE = True
except ImportError:
    # Permite importar el módulo en PC para pruebas (mock)
    EV3_AVAILABLE = False
    print("[WARN] ev3dev2 no disponible — modo simulación", file=sys.stderr)


# ── Calibración ────────────────────────────────────────────────────────────────

MOTOR_SPEED = 30          # % velocidad de avance (0-100)
RETURN_SPEED = 40         # % velocidad de retorno

# Grados que giran los motores A+B para llegar a cada agujero desde home.
# Ajustar experimentalmente con el robot real.
HOLE_POSITIONS = {
    1: 200,
    2: 400,
    3: 600,
    4: 800,
    5: 1000,
}

TILT_ANGLE = 90           # grados que gira Motor C para inclinar la plataforma
TILT_SPEED = 20           # % velocidad de Motor C


# ── Inicialización de motores ──────────────────────────────────────────────────

def init_motors():
    if not EV3_AVAILABLE:
        return None, None, None
    motor_a = LargeMotor(OUTPUT_A)
    motor_b = LargeMotor(OUTPUT_B)
    motor_c = MediumMotor(OUTPUT_C)
    drive = MoveSteering(OUTPUT_A, OUTPUT_B)
    return drive, motor_c, (motor_a, motor_b)


# ── Rutinas de movimiento ──────────────────────────────────────────────────────

def move_forward(drive, degrees: int, speed: int = MOTOR_SPEED):
    """Avanza girando los motores A+B el número de grados indicado."""
    if not EV3_AVAILABLE:
        print(f"[SIM] Avanzar {degrees}° a {speed}%")
        return
    drive.on_for_degrees(steering=0, speed=SpeedPercent(speed), degrees=degrees)


def move_backward(drive, degrees: int, speed: int = RETURN_SPEED):
    """Retrocede girando los motores A+B en sentido inverso."""
    if not EV3_AVAILABLE:
        print(f"[SIM] Retroceder {degrees}° a {speed}%")
        return
    drive.on_for_degrees(steering=0, speed=SpeedPercent(-speed), degrees=degrees)


def tilt_platform(motor_c, direction: str = "drop"):
    """
    Inclina Motor C para soltar el paquete (direction='drop')
    o restablece la plataforma (direction='reset').
    """
    angle = TILT_ANGLE if direction == "drop" else -TILT_ANGLE
    if not EV3_AVAILABLE:
        print(f"[SIM] Motor C {direction}: {angle}°")
        return
    motor_c.on_for_degrees(speed=SpeedPercent(TILT_SPEED), degrees=angle)


def go_to_hole(drive, motor_c, hole: int):
    """Mueve el robot al agujero indicado y deposita el paquete."""
    if hole not in HOLE_POSITIONS:
        raise ValueError(f"Agujero {hole} no definido en HOLE_POSITIONS")
    degrees = HOLE_POSITIONS[hole]
    move_forward(drive, degrees)
    tilt_platform(motor_c, "drop")
    tilt_platform(motor_c, "reset")


def return_home(drive, current_hole: int):
    """Regresa el robot a la posición inicial (home)."""
    if current_hole not in HOLE_POSITIONS:
        raise ValueError(f"Agujero {current_hole} no definido en HOLE_POSITIONS")
    degrees = HOLE_POSITIONS[current_hole]
    move_backward(drive, degrees)


# ── Bucle principal de comandos ────────────────────────────────────────────────

def respond(obj: dict):
    print(json.dumps(obj), flush=True)


def run():
    drive, motor_c, _ = init_motors()
    current_hole = 0  # 0 = home

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            respond({"status": "error", "msg": f"JSON inválido: {e}"})
            continue

        action = cmd.get("action")

        if action == "ping":
            respond({"status": "ok"})

        elif action == "deliver":
            hole = cmd.get("hole")
            if not isinstance(hole, int) or hole not in HOLE_POSITIONS:
                respond({"status": "error", "msg": f"Agujero inválido: {hole}"})
                continue
            try:
                go_to_hole(drive, motor_c, hole)
                current_hole = hole
                return_home(drive, current_hole)
                current_hole = 0
                respond({"status": "done", "action": "deliver", "hole": hole})
            except Exception as e:
                respond({"status": "error", "msg": str(e)})

        elif action == "home":
            try:
                if current_hole != 0:
                    return_home(drive, current_hole)
                    current_hole = 0
                respond({"status": "done", "action": "home"})
            except Exception as e:
                respond({"status": "error", "msg": str(e)})

        else:
            respond({"status": "error", "msg": f"Acción desconocida: {action}"})


if __name__ == "__main__":
    run()
