"""
camera.py — Captura de frames desde la cámara de la estación.

Uso:
    cam = Camera(device=0)
    frame = cam.capture()        # numpy array BGR (frame inmediato)
    frame = cam.capture_sharp()  # espera hasta obtener un frame nítido
    cam.release()

Nota iPhone/Continuity Camera:
  Si la imagen sale desenfocada, desactiva Portrait Mode en macOS:
  Barra de menú → Control Center → Video Effects → Portrait (apagar)
"""

import logging
import cv2
import numpy as np

log = logging.getLogger("camera")


def _sharpness(frame: np.ndarray) -> float:
    """Varianza del Laplaciano: mayor = más nítido."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
    return cv2.Laplacian(gray, cv2.CV_64F).var()


class Camera:
    def __init__(self, device: int = 0, width: int = 640, height: int = 480):
        self.cap = cv2.VideoCapture(device)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        # Intenta desactivar el autofocus (funciona en algunas cámaras/drivers)
        self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        if not self.cap.isOpened():
            raise RuntimeError(f"No se pudo abrir la cámara (device={device})")

    def capture(self):
        """Retorna un frame BGR como numpy array, o lanza RuntimeError."""
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("Error al leer frame de la cámara")
        return frame

    def capture_sharp(self, min_sharpness: float = 80.0, attempts: int = 8):
        """
        Captura hasta `attempts` frames y devuelve el más nítido.
        Si ninguno supera `min_sharpness`, devuelve el mejor que encontró.
        Útil al escanear el QR del paquete cuando el robot está quieto.
        """
        best_frame = None
        best_score = -1.0
        for _ in range(attempts):
            ok, frame = self.cap.read()
            if not ok:
                continue
            score = _sharpness(frame)
            if score > best_score:
                best_score = score
                best_frame = frame
            if score >= min_sharpness:
                break
        if best_frame is None:
            raise RuntimeError("Error al leer frame de la cámara")
        if best_score < min_sharpness:
            log.debug(f"Frame más nítido disponible: {best_score:.1f} (umbral={min_sharpness})")
        return best_frame

    def release(self):
        self.cap.release()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()
