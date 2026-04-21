"""
prueba_sensor.py — Prueba el sensor de proximidad ultrasónico conectado al NXT.

Uso:
  python prueba_sensor.py              # escanea todos los puertos automáticamente
  python prueba_sensor.py --port 1     # fuerza puerto S1
"""

import argparse
import sys
import time

OBSTACLE_THRESHOLD_CM = 20

_R      = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_GRAY   = "\033[90m"


def _bar(value: float, max_val: float = 100, width: int = 30) -> str:
    filled = int(width * min(value, max_val) / max_val)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def try_read(sensor, attempts: int = 5, delay: float = 0.3) -> float | None:
    for _ in range(attempts):
        try:
            return sensor.get_sample()
        except Exception:
            time.sleep(delay)
    return None


def scan_ports(brick):
    """Intenta inicializar el sensor ultrasónico en cada puerto y leer una muestra."""
    import nxt.sensor
    import nxt.sensor.generic

    ports = [
        (1, nxt.sensor.Port.S1),
        (2, nxt.sensor.Port.S2),
        (3, nxt.sensor.Port.S3),
        (4, nxt.sensor.Port.S4),
    ]

    print(f"\n{_CYAN}Escaneando puertos S1–S4...{_R}\n")
    found = None
    for num, port in ports:
        sys.stdout.write(f"  S{num} ... ")
        sys.stdout.flush()
        try:
            sensor = nxt.sensor.generic.Ultrasonic(brick, port, check_compatible=False)
            time.sleep(0.5)   # deja tiempo al sensor para inicializarse
            dist = try_read(sensor)
            if dist is not None:
                print(f"{_GREEN}✓ detectado — {dist:.1f} cm{_R}")
                if found is None:
                    found = (num, sensor)
            else:
                print(f"{_YELLOW}sin respuesta (timeout){_R}")
        except Exception as e:
            print(f"{_RED}error: {e}{_R}")

    return found


def run_loop(sensor, port_num: int):
    print(f"\nSensor en S{port_num} activo. Umbral: {_BOLD}{OBSTACLE_THRESHOLD_CM} cm{_R}")
    print(f"{_GRAY}Presiona Ctrl+C para salir.{_R}\n")

    consecutive_errors = 0
    try:
        while True:
            try:
                dist = sensor.get_sample()
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                sys.stdout.write(f"\r{_RED}Error lectura #{consecutive_errors}: {e}{_R}   ")
                sys.stdout.flush()
                time.sleep(0.2)
                if consecutive_errors >= 10:
                    print(f"\n{_RED}Demasiados errores consecutivos — verifica la conexión.{_R}")
                    break
                continue

            blocked = dist < OBSTACLE_THRESHOLD_CM
            color   = _RED if blocked else _GREEN
            status  = f"{_RED}{_BOLD}⚠ OBSTÁCULO{_R}" if blocked else f"{_GREEN}libre{_R}"
            bar     = _bar(dist, max_val=100)

            sys.stdout.write(
                f"\r  {_CYAN}S{port_num}{_R}  {color}{_BOLD}{dist:5.1f} cm{_R}  {bar}  {status}   "
            )
            sys.stdout.flush()
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\nPrueba terminada.")


def main(port_num: int | None):
    import nxt.locator
    import nxt.sensor
    import nxt.sensor.generic

    print("Conectando al NXT...")
    try:
        brick = nxt.locator.find()
    except Exception as e:
        print(f"{_RED}No se encontró el NXT: {e}{_R}")
        sys.exit(1)
    print(f"{_GREEN}NXT conectado.{_R}")

    if port_num is None:
        result = scan_ports(brick)
        if result is None:
            print(f"\n{_RED}No se encontró el sensor en ningún puerto.{_R}")
            print("Verifica que el sensor esté bien enchufado y que sea el modelo NXT Ultrasónico.")
            brick.close()
            sys.exit(1)
        port_num, sensor = result
        print(f"\n{_GREEN}Usando S{port_num} para el loop de lectura.{_R}")
    else:
        port_map = {1: nxt.sensor.Port.S1, 2: nxt.sensor.Port.S2,
                    3: nxt.sensor.Port.S3, 4: nxt.sensor.Port.S4}
        print(f"Iniciando sensor en S{port_num}...")
        try:
            sensor = nxt.sensor.generic.Ultrasonic(brick, port_map[port_num], check_compatible=False)
            time.sleep(0.5)
        except Exception as e:
            print(f"{_RED}Error al inicializar sensor: {e}{_R}")
            brick.close()
            sys.exit(1)

    try:
        run_loop(sensor, port_num)
    finally:
        try:
            brick.close()
        except Exception:
            pass


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Prueba sensor ultrasónico NXT")
    p.add_argument("--port", type=int, default=None,
                   help="Puerto del sensor (1-4). Sin argumento escanea todos los puertos.")
    args = p.parse_args()
    main(args.port)
