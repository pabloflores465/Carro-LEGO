"""
qr_manager.py — Generación y gestión de imágenes QR.

Los 6 QR base (QR1…QR6) y sus destinos (QR1.1…QR6.6) se generan una sola vez
y se guardan en qrs/. Si el archivo ya existe con el mismo contenido, no se
regenera, garantizando que el contenido sea siempre idéntico.
"""
import logging
from pathlib import Path
from typing import List

import qrcode
from PIL import Image

log = logging.getLogger("qr_manager")

QRS_DIR = Path(__file__).parent / "qrs"

# QR1-QR3: paquetes  |  QR4-QR6: destinos en el suelo
ALL_QRS: List[str] = ["QR1", "QR2", "QR3", "QR4", "QR5", "QR6"]


def _safe_filename(name: str) -> str:
    """QR1.1 → QR1_1  (el punto no es válido en algunos FS)."""
    return name.replace(".", "_")


def qr_path(name: str) -> Path:
    return QRS_DIR / f"{_safe_filename(name)}.png"


def generate_qr(content: str, overwrite: bool = False) -> Path:
    """
    Genera un PNG de código QR para el contenido dado.
    No sobreescribe si ya existe (a menos que overwrite=True).
    """
    path = qr_path(content)
    if path.exists() and not overwrite:
        return path

    QRS_DIR.mkdir(parents=True, exist_ok=True)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,  # M tolera más daño que L
        box_size=20,   # módulos más grandes → más fácil de leer desde lejos
        border=4,
    )
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(path)
    log.info("QR generado: %s → %s", content, path)
    return path


def ensure_base_qrs() -> List[Path]:
    """Genera los 6 QR (QR1-QR6) si no existen. Retorna lista de rutas."""
    return [generate_qr(name) for name in ALL_QRS]


# Alias por compatibilidad
ensure_all_qrs = ensure_base_qrs


def get_qr_image(name: str) -> Image.Image:
    """Retorna la imagen PIL del QR. La genera si no existe."""
    path = qr_path(name)
    if not path.exists():
        generate_qr(name)
    return Image.open(path).convert("RGB")


def list_generated() -> List[str]:
    """Retorna los nombres de los QR ya generados en disco."""
    if not QRS_DIR.exists():
        return []
    return [
        p.stem.replace("_", ".", 1)
        for p in sorted(QRS_DIR.glob("*.png"))
    ]
