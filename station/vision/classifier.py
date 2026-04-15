"""
classifier.py — Lector de códigos QR para clasificación de paquetes.
"""

import ctypes
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

# Carga explícita de libzbar (Homebrew en Apple Silicon)
for _zbar_path in [
    "/opt/homebrew/opt/zbar/lib/libzbar.dylib",
    "/usr/local/lib/libzbar.dylib",
]:
    try:
        ctypes.CDLL(_zbar_path)
        break
    except OSError:
        pass

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
    PYZBAR_AVAILABLE = True
except Exception:
    PYZBAR_AVAILABLE = False


def _preprocess_variants(frame: np.ndarray) -> list[tuple[str, np.ndarray]]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    h, w = frame.shape[:2]
    x3 = cv2.resize(gray, (min(w * 3, 1920), min(h * 3, 1440)),
                    interpolation=cv2.INTER_LINEAR)
    return [
        ("original", frame),
        ("gray",     gray),
        ("clahe",    clahe.apply(gray)),
        ("sharp",    cv2.filter2D(gray, -1, kernel)),
        ("x3",       x3),
    ]


@dataclass
class Detection:
    class_name: str
    hole: int
    confidence: float = 1.0


def _parse_hole(qr_content: str) -> Optional[int]:
    match = re.search(r"\d+", qr_content)
    return int(match.group()) if match else None


class QRClassifier:

    def predict(self, frame: np.ndarray) -> Optional[Detection]:
        all_detections = self.predict_all(frame)
        return all_detections[0] if all_detections else None

    def predict_all(self, frame: np.ndarray) -> list[Detection]:
        contents = self._read_all_qr(frame)
        detections = []
        for content in contents:
            hole = _parse_hole(content)
            if hole is not None:
                detections.append(Detection(class_name=content, hole=hole))
        return detections

    def count_fast(self, frame: np.ndarray, target: str) -> int:
        """Conteo rápido para el loop de movimiento — solo grises + original."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for img in (gray, frame):
            try:
                results = self._decode(img)
            except Exception:
                results = []
            if results:
                return Counter(results)[target]
        return 0

    def already_at_destination(self, frame: np.ndarray) -> Optional[Detection]:
        detections = self.predict_all(frame)
        seen: dict = {}
        for d in detections:
            if d.class_name in seen:
                return d
            seen[d.class_name] = True
        return None

    # ── Lectura interna ───────────────────────────────────────────────────────

    def _decode(self, img: np.ndarray) -> list[str]:
        """Decoder único: pyzbar si está disponible, OpenCV si no."""
        if PYZBAR_AVAILABLE:
            return [c.data.decode("utf-8").strip()
                    for c in pyzbar_decode(img) if c.data]
        # Fallback OpenCV
        det = cv2.QRCodeDetectorAruco()
        try:
            ok, decoded, _, _ = det.detectAndDecodeMulti(img)
            if ok and decoded:
                return [d.strip() for d in decoded if d]
        except Exception:
            pass
        det2 = cv2.QRCodeDetector()
        data, _, _ = det2.detectAndDecode(img)
        return [data.strip()] if data else []

    def _read_all_qr(self, frame: np.ndarray) -> list[str]:
        """
        Prueba variantes preprocesadas conservando duplicados.
        Para en cuanto encuentra un QR duplicado.
        """
        max_count: Counter = Counter()
        for _name, variant in _preprocess_variants(frame):
            try:
                results = self._decode(variant)
            except Exception:
                results = []
            c = Counter(results)
            for qr, cnt in c.items():
                if cnt > max_count[qr]:
                    max_count[qr] = cnt
            if any(v >= 2 for v in max_count.values()):
                break
        out = []
        for qr, cnt in max_count.items():
            out.extend([qr] * cnt)
        return out


Classifier = QRClassifier
