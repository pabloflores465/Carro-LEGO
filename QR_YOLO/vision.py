"""
vision.py — Captura de cámara y detección/decodificación de QR.

── Pipeline obligatorio: YOLO + pyzbar ──────────────────────────────────────
YOLO (YOLOv8) y pyzbar cumplen roles complementarios e insustituibles:

  YOLO    → LOCALIZACIÓN: detecta las regiones del frame donde hay un QR
            (bounding box). Funciona bien incluso con distancia, ángulo y
            escenas complejas. Por sí solo NO puede leer el contenido del QR.

  pyzbar  → DECODIFICACIÓN: lee los bits del QR y retorna la cadena de texto.
            Opera sobre el recorte (crop) que YOLO ya localizó, con mucha más
            precisión que si escaneara el frame completo.

Flujo por frame:
  1. YOLO infiere sobre el frame completo → lista de bounding boxes.
  2. Cada bbox se recorta con padding y se envía a pyzbar para decodificar.
  3. pyzbar también hace una pasada sobre el frame completo para capturar QRs
     que YOLO pudiera perder (ej. QR muy pequeño o baja confianza del modelo).
  4. Se consolidan los resultados eliminando duplicados.

Modelo YOLO:
  - Por defecto se usa "yolov8n.pt" (nano, ~6 MB), descargado automáticamente
    la primera vez desde los servidores de Ultralytics.
  - Para mayor precisión: entrena un modelo específico de QR con tus propias
    imágenes y colócalo en models/qr_detector.pt. Ver README para el script
    de entrenamiento.
──────────────────────────────────────────────────────────────────────────────
"""
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
from ultralytics import YOLO  # obligatorio

log = logging.getLogger("vision")

# ── Rutas de modelos ──────────────────────────────────────────────────────────

_MODELS_DIR   = Path(__file__).parent / "models"
_CUSTOM_MODEL = _MODELS_DIR / "qr_detector.pt"
_DEFAULT_MODEL = "yolov8n.pt"  # descargado automáticamente por ultralytics

# ── Carga de pyzbar (requiere libzbar en el SO) ────────────────────────────────
# En macOS con Homebrew Apple Silicon la dylib está en /opt/homebrew/lib;
# DYLD_LIBRARY_PATH debe incluir esa ruta para que ctypes la encuentre.

def _fix_dyld() -> None:
    for candidate in ("/opt/homebrew/lib", "/usr/local/lib"):
        if os.path.isdir(candidate):
            current = os.environ.get("DYLD_LIBRARY_PATH", "")
            if candidate not in current:
                os.environ["DYLD_LIBRARY_PATH"] = (
                    f"{candidate}:{current}" if current else candidate
                )

_fix_dyld()

try:
    from pyzbar import pyzbar as _pyzbar
    _PYZBAR_OK = True
except ImportError:
    _PYZBAR_OK = False
    log.warning(
        "pyzbar no disponible — usando cv2.QRCodeDetector como decodificador. "
        "Para mejor rendimiento: brew install zbar && pip install pyzbar"
    )


# ── Estructura de detección ───────────────────────────────────────────────────

@dataclass
class QRDetection:
    content: str
    bbox: tuple        # (x, y, w, h) en píxeles del frame completo
    center_x: int
    center_y: int
    area: int          # w * h
    confidence: float  = 0.0
    polygon: list      = None   # 4 esquinas [(x,y)…] — útil para calcular orientación


# ── Cámara ────────────────────────────────────────────────────────────────────

class Camera:
    """
    Captura de video con OpenCV.
    index=1 para iPhone via Continuity Camera en Mac.
    """

    def __init__(self, index: int = 1):
        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"No se pudo abrir la cámara con índice {index}. "
                "Verifica que el iPhone esté conectado y Continuity Camera esté activo."
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self._cap.set(cv2.CAP_PROP_FPS, 30)
        log.info("Cámara abierta (índice %d, 640×480 @ 30fps)", index)

    def capture(self) -> np.ndarray:
        ret, frame = self._cap.read()
        if not ret:
            raise RuntimeError("No se pudo leer frame de la cámara.")
        return frame

    def capture_sharp(self) -> np.ndarray:
        """Descarta 3 frames para estabilizar la auto-exposición."""
        for _ in range(3):
            self._cap.read()
        ret, frame = self._cap.read()
        if not ret:
            raise RuntimeError("Error capturando frame nítido.")
        return frame

    def release(self) -> None:
        self._cap.release()
        log.info("Cámara liberada.")


# ── Detector QR ──────────────────────────────────────────────────────────────

class QRDetector:
    """
    Pipeline YOLO → pyzbar/cv2 para detectar y decodificar QR en tiempo real.

    YOLO localiza las regiones; pyzbar (o cv2 si libzbar no está) decodifica.
    """

    # Padding alrededor del bbox de YOLO antes de enviar a pyzbar (píxeles)
    _CROP_PAD = 24

    def __init__(self, conf_threshold: float = 0.25):
        """
        conf_threshold: confianza mínima YOLO para considerar una detección.
        Baja a 0.1 si el modelo pierde QRs en condiciones difíciles.
        """
        self._conf = conf_threshold
        self._model = self._load_model()

    # ── Carga de modelo ────────────────────────────────────────────────────────

    @staticmethod
    def _load_model() -> YOLO:
        _MODELS_DIR.mkdir(parents=True, exist_ok=True)
        if _CUSTOM_MODEL.exists():
            log.info("Cargando modelo YOLO personalizado: %s", _CUSTOM_MODEL)
            return YOLO(str(_CUSTOM_MODEL))
        log.info(
            "Usando modelo base %s (descarga automática si no existe localmente). "
            "Para mejor precisión entrena un modelo QR y colócalo en models/qr_detector.pt",
            _DEFAULT_MODEL,
        )
        return YOLO(_DEFAULT_MODEL)

    # ── API pública ────────────────────────────────────────────────────────────

    def detect(self, frame: np.ndarray) -> List[QRDetection]:
        """
        Retorna todas las detecciones QR en el frame.
        Pipeline: YOLO localiza → pyzbar/cv2 decodifica crops + frame completo.
        """
        seen: Dict[str, QRDetection] = {}

        # ── Paso 1: YOLO → bounding boxes ─────────────────────────────────────
        yolo_boxes = self._run_yolo(frame)

        # ── Paso 2: decodificar cada crop localizado por YOLO ──────────────────
        for (x1, y1, x2, y2, conf) in yolo_boxes:
            crop = self._crop(frame, x1, y1, x2, y2)
            for d in self._decode(crop):
                if d.content not in seen:
                    # Proyecta coordenadas del crop al frame completo
                    ox = max(0, x1 - self._CROP_PAD)
                    oy = max(0, y1 - self._CROP_PAD)
                    seen[d.content] = QRDetection(
                        content=d.content,
                        bbox=(ox + d.bbox[0], oy + d.bbox[1], d.bbox[2], d.bbox[3]),
                        center_x=ox + d.center_x,
                        center_y=oy + d.center_y,
                        area=d.area,
                        confidence=conf,
                    )

        # ── Paso 3: pasada de refuerzo sobre el frame completo ─────────────────
        # Captura QRs que YOLO no localizó (baja confianza, tamaño pequeño, etc.)
        for d in self._decode(frame):
            if d.content not in seen:
                seen[d.content] = d

        return list(seen.values())

    def detect_first(self, frame: np.ndarray) -> Optional[QRDetection]:
        results = self.detect(frame)
        return results[0] if results else None

    def detect_content(self, frame: np.ndarray, target: str) -> Optional[QRDetection]:
        for d in self.detect(frame):
            if d.content == target:
                return d
        return None

    def count_detections(self, frame: np.ndarray, target: str) -> int:
        return sum(1 for d in self.detect(frame) if d.content == target)

    def draw_detections(
        self, frame: np.ndarray, detections: List[QRDetection]
    ) -> np.ndarray:
        out = frame.copy()
        h_frame = frame.shape[0]
        for d in detections:
            x, y, w, h = d.bbox
            cv2.rectangle(out, (x, y), (x + w, y + h), (0, 220, 60), 2)
            label = f"{d.content}  {d.confidence:.0%}" if d.confidence else d.content
            cv2.putText(out, label, (x, max(y - 8, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 60), 2)
            cv2.circle(out, (d.center_x, d.center_y), 5, (255, 80, 0), -1)
        # Banner YOLO en la esquina
        cv2.putText(out, "YOLO+pyzbar", (8, h_frame - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)
        return out

    # ── Métodos internos ───────────────────────────────────────────────────────

    def _run_yolo(self, frame: np.ndarray) -> List[tuple]:
        """Ejecuta YOLO y retorna lista de (x1, y1, x2, y2, conf)."""
        try:
            results = self._model(frame, verbose=False, conf=self._conf)[0]
            boxes = []
            for box in results.boxes:
                conf = float(box.conf[0])
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                boxes.append((x1, y1, x2, y2, conf))
            return boxes
        except Exception as exc:
            log.warning("YOLO inference falló: %s", exc)
            return []

    def _crop(self, frame: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
        """Recorta el frame con padding para mejorar la decodificación QR."""
        pad = self._CROP_PAD
        H, W = frame.shape[:2]
        return frame[
            max(0, y1 - pad): min(H, y2 + pad),
            max(0, x1 - pad): min(W, x2 + pad),
        ]

    def _decode(self, img: np.ndarray) -> List[QRDetection]:
        """
        Intenta decodificar QRs con múltiples estrategias de preprocesamiento.
        Para cada variante de la imagen prueba primero pyzbar (más robusto)
        y luego cv2.QRCodeDetector como respaldo.
        Se detiene en cuanto alguna variante produce resultado.
        """
        for variant in self._preprocess_variants(img):
            results = (
                self._scan_pyzbar(variant)
                if _PYZBAR_OK
                else self._scan_opencv(variant)
            )
            if results:
                return results
        return []

    # ── Preprocesamiento ──────────────────────────────────────────────────────

    @staticmethod
    def _preprocess_variants(img: np.ndarray) -> List[np.ndarray]:
        """
        Genera variantes del frame para maximizar la detección.
        Se prueban en orden: la primera que funcione gana.

        Por qué cada variante:
          1. Gris directo      — caso ideal, buena iluminación
          2. CLAHE             — mejora contraste local (sombras, reflejos)
          3. Umbral adaptativo — convierte a blanco/negro puro, ayuda con fondos
          4. Escala 2×         — QR pequeño o lejano; ampliar mejora pyzbar
          5. Suavizado + gris  — reduce ruido de cámara en movimiento
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        variants: List[np.ndarray] = []

        # 1. Gris directo
        variants.append(gray)

        # 2. CLAHE (contraste adaptativo local)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        variants.append(clahe.apply(gray))

        # 3. Umbral adaptativo gaussiano
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2,
        )
        variants.append(thresh)

        # 4. Escala 2× (útil para QR pequeños o lejanos)
        h, w = gray.shape
        if w < 800:   # no ampliar si ya es grande, solo desperdicia CPU
            upscaled = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
            variants.append(upscaled)
            variants.append(clahe.apply(upscaled))

        # 5. Suavizado ligero (reduce ruido de movimiento)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        variants.append(blurred)

        return variants

    # ── Escáneres de bajo nivel ───────────────────────────────────────────────

    @staticmethod
    def _scan_pyzbar(gray: np.ndarray) -> List[QRDetection]:
        results: List[QRDetection] = []
        for obj in _pyzbar.decode(gray):
            if obj.type != "QRCODE":
                continue
            try:
                content = obj.data.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            r = obj.rect
            polygon = [(p.x, p.y) for p in obj.polygon] if obj.polygon else None
            results.append(QRDetection(
                content=content,
                bbox=(r.left, r.top, r.width, r.height),
                center_x=r.left + r.width  // 2,
                center_y=r.top  + r.height // 2,
                area=r.width * r.height,
                polygon=polygon,
            ))
        return results

    @staticmethod
    def _scan_opencv(gray: np.ndarray) -> List[QRDetection]:
        """cv2.QRCodeDetector — un QR por imagen, sin libzbar."""
        data, points, _ = cv2.QRCodeDetector().detectAndDecode(gray)
        if not data or points is None:
            return []
        pts = points[0].astype(int)
        x = int(pts[:, 0].min()); y = int(pts[:, 1].min())
        w = int(pts[:, 0].max()) - x
        h = int(pts[:, 1].max()) - y
        return [QRDetection(
            content=data.strip(),
            bbox=(x, y, w, h),
            center_x=x + w // 2,
            center_y=y + h // 2,
            area=w * h,
        )]
