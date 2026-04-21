"""
prueba_giro.py — Detecta el QR del paquete y gira buscando ese QR en el suelo.

Flujo:
  1. Espera a ver 2 QR iguales (paquete colocado en home).
  2. Gira en su lugar hasta encontrar el QR destino en el suelo.
  3. Para y reporta cuántos grados giró.

Uso:
  python prueba_giro.py
  python prueba_giro.py --power 25 --dir -1 --camera 0
"""

import argparse
import sys
import threading
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from station.vision.camera import Camera
from station.vision.classifier import QRClassifier

_R      = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_GRAY   = "\033[90m"

FRAMES_TIMEOUT = 400

_preview_label  = "Esperando paquete..."
_preview_qr     = ""
_preview_running = True


def run_preview(camera: Camera, classifier: QRClassifier):
    """Ventana de cámara en vivo — debe correr en el hilo principal (macOS)."""
    cv2.namedWindow("Giro — Cámara", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Giro — Cámara", 640, 480)
    global _preview_running
    while _preview_running:
        try:
            frame = camera.capture()
        except Exception:
            time.sleep(0.1)
            continue

        detection = classifier.predict(frame)
        h, w = frame.shape[:2]

        cv2.rectangle(frame, (0, 0), (w, 42), (20, 20, 20), -1)
        cv2.putText(frame, _preview_label,
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)

        if _preview_qr:
            cv2.rectangle(frame, (0, h - 40), (w, h), (20, 20, 20), -1)
            cv2.putText(frame, f"QR: {_preview_qr}",
                        (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 220, 80), 2)

        if detection:
            cv2.rectangle(frame, (4, 46), (w - 4, h - 44), (0, 200, 80), 2)

        cv2.imshow("Giro — Cámara", frame)
        if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
            _preview_running = False
            break

    cv2.destroyAllWindows()


def scan_package(camera: Camera, classifier: QRClassifier) -> str:
    """Espera hasta ver 2 QR iguales y retorna el contenido del QR."""
    global _preview_label, _preview_qr
    _preview_label = "Esperando paquete..."
    print(f"{_CYAN}Esperando paquete — coloca el paquete sobre el QR del suelo...{_R}")
    while _preview_running:
        try:
            frame = camera.capture_sharp()
            detection = classifier.already_at_destination(frame)
            if detection:
                _preview_label = f"Paquete: {detection.class_name}"
                _preview_qr    = detection.class_name
                return detection.class_name
        except Exception as e:
            print(f"\r{_YELLOW}Error escaneando: {e}{_R}   ", end="")
        time.sleep(0.3)


def turn_to_qr(target_qr: str, motor_left, motor_right,
               camera: Camera, classifier: QRClassifier,
               power: int, direction: int):
    """Gira el robot hasta encontrar target_qr en el suelo."""
    global _preview_label
    motor_left.reset_position(relative=False)
    _preview_label = f"Girando — buscando {target_qr} en suelo"

    print(f"Girando {'derecha' if direction > 0 else 'izquierda'} "
          f"a {power}% buscando {_CYAN}{_BOLD}{target_qr}{_R} en el suelo...")
    print(f"{_GRAY}Presiona Ctrl+C para abortar.{_R}\n")

    motor_left.run( direction * power)
    motor_right.run(-direction * power)

    # Espera a que el QR del paquete (en home) desaparezca del campo visual
    sys.stdout.write("  Esperando salir de home...")
    sys.stdout.flush()
    for _ in range(FRAMES_TIMEOUT):
        try:
            frame = camera.capture()
            detections = classifier.predict_all(frame)
            count = sum(1 for d in detections if d.class_name == target_qr)
        except Exception:
            count = 1
        if count == 0:
            break
        time.sleep(0.05)
    sys.stdout.write(f"\r  QR de home fuera de vista — buscando en destino...\n")
    sys.stdout.flush()

    found   = False
    frames  = 0
    confirm = 0
    CONFIRM_NEEDED = 3

    try:
        while frames < FRAMES_TIMEOUT:
            try:
                frame = camera.capture()
                detections = classifier.predict_all(frame)
                count = sum(1 for d in detections if d.class_name == target_qr)
            except Exception as e:
                sys.stdout.write(f"\r{_RED}Error cámara: {e}{_R}   ")
                sys.stdout.flush()
                time.sleep(0.1)
                continue

            confirm = confirm + 1 if count >= 1 else 0

            tacho  = motor_left.get_tacho().tacho_count
            status = (f"{_GREEN}QR visible ({confirm}/{CONFIRM_NEEDED}){_R}"
                      if confirm > 0 else f"{_GRAY}buscando...{_R}")
            sys.stdout.write(f"\r  frame {frames:4d}  {abs(tacho):5d}°  {status}   ")
            sys.stdout.flush()

            if confirm >= CONFIRM_NEEDED:
                found = True
                break

            frames += 1
            time.sleep(0.05)

    except KeyboardInterrupt:
        print(f"\n{_YELLOW}Abortado.{_R}")
    finally:
        motor_left.brake()
        motor_right.brake()

    tacho = motor_left.get_tacho().tacho_count
    print()
    if found:
        _preview_label = f"✓ {target_qr} encontrado ({abs(tacho)}°)"
        print(f"{_GREEN}{_BOLD}✓ {target_qr} encontrado — giré {abs(tacho)}°{_R}")
    else:
        _preview_label = f"✗ {target_qr} no encontrado"
        print(f"{_RED}✗ {target_qr} no encontrado tras {abs(tacho)}° y {frames} frames{_R}")

    return found


def _logic(power: int, direction: int, camera: Camera, classifier: QRClassifier):
    """Lógica principal — corre en hilo secundario."""
    global _preview_running
    import nxt.locator
    import nxt.motor

    print("Conectando al NXT...")
    try:
        brick = nxt.locator.find()
    except Exception as e:
        print(f"{_RED}No se encontró el NXT: {e}{_R}")
        _preview_running = False
        return
    print(f"{_GREEN}NXT conectado.{_R}\n")

    motor_left  = nxt.motor.Motor(brick, nxt.motor.Port.B)
    motor_right = nxt.motor.Motor(brick, nxt.motor.Port.C)

    try:
        target_qr = scan_package(camera, classifier)
        print(f"\n{_GREEN}✓ Paquete detectado:{_R} {_CYAN}{_BOLD}{target_qr}{_R}\n")
        time.sleep(0.5)

        turn_to_qr(target_qr, motor_left, motor_right,
                   camera, classifier, power, direction)

    except KeyboardInterrupt:
        motor_left.brake()
        motor_right.brake()
    finally:
        try:
            brick.close()
        except Exception:
            pass
        _preview_running = False


def main(power: int, direction: int, camera_device: int):
    global _preview_running
    print("Iniciando cámara...")
    camera = Camera(camera_device)
    classifier = QRClassifier()

    t = threading.Thread(target=_logic, args=(power, direction, camera, classifier), daemon=True)
    t.start()

    # Preview corre en el hilo principal (requerido en macOS)
    run_preview(camera, classifier)

    t.join(timeout=2)
    camera.release()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Detecta paquete y gira buscando su QR destino")
    p.add_argument("--power",  type=int, default=20, help="Potencia de giro 5-100 (default: 20)")
    p.add_argument("--dir",    type=int, default=1,  choices=[1, -1],
                   help="Dirección: 1=derecha, -1=izquierda (default: 1)")
    p.add_argument("--camera", type=int, default=1,  help="Índice de cámara (default: 1)")
    args = p.parse_args()
    main(args.power, args.dir, args.camera)
