#!/usr/bin/env python3
"""
prepare_and_train.py — Convierte HEIC→JPG, auto-etiqueta con pyzbar y entrena YOLOv8.

Uso:
  python prepare_and_train.py                    # entrena con defaults
  python prepare_and_train.py --epochs 120        # más épocas
  python prepare_and_train.py --dry-run           # solo inspecciona dataset
  python prepare_and_train.py --inspect-images    # abre preview del dataset

Datos esperados:
  Archivo/QR1/*.HEIC   → clase 0 (QR1)
  Archivo/QR2/*.HEIC   → clase 1 (QR2)
  ...
  Archivo/QR6/*.HEIC   → clase 5 (QR6)

El modelo entrenado se instala en models/qr_detector.pt y el sistema
lo carga automáticamente al reiniciar (vision.py lo busca ahí).
"""
import argparse
import logging
import os
import random
import shutil
import subprocess
from pathlib import Path

# ── Fix pyzbar en macOS Apple Silicon ─────────────────────────────────────────
for _lib in ("/opt/homebrew/lib", "/usr/local/lib"):
    if os.path.isdir(_lib):
        _cur = os.environ.get("DYLD_LIBRARY_PATH", "")
        if _lib not in _cur:
            os.environ["DYLD_LIBRARY_PATH"] = f"{_lib}:{_cur}" if _cur else _lib

import cv2
import numpy as np

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
    from pyzbar.pyzbar import ZBarSymbol
    _PYZBAR_OK = True
except ImportError:
    _PYZBAR_OK = False
    ZBarSymbol = None

from ultralytics import YOLO

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("train")

# ── Configuración ─────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "Archivo"          # donde están las carpetas QR1..QR6
QR_NAMES   = ["QR1", "QR2", "QR3", "QR4", "QR5", "QR6"]
DATASET    = BASE_DIR / "dataset"
MODELS_DIR = BASE_DIR / "models"
DEFAULT_EPOCHS = 80
IMGSZ      = 640
BATCH      = -1     # auto-detect
VAL_SPLIT  = 0.15   # 15 % validación


# ── Paso 1: HEIC → JPG ───────────────────────────────────────────────────────

def convert_heic(src: Path, dst: Path) -> list[Path]:
    """Convierte HEIC/JPG/PNG a JPG unificado en dst."""
    dst.mkdir(parents=True, exist_ok=True)

    # Soporta HEIC, heic, jpg, jpeg, png como entrada
    patterns = ["*.HEIC", "*.heic", "*.jpg", "*.jpeg", "*.JPG", "*.png", "*.PNG"]
    files = []
    for p in patterns:
        files.extend(src.glob(p))
    files = sorted(set(files))

    jpgs: list[Path] = []
    for src_file in files:
        out = dst / (src_file.stem + ".jpg")

        if src_file.suffix.lower() in (".jpg", ".jpeg"):
            # Ya es JPG — copiar directamente
            if not out.exists():
                shutil.copy(src_file, out)
        elif src_file.suffix.lower() == ".png":
            # PNG → JPG via cv2
            img = cv2.imread(str(src_file))
            if img is not None:
                cv2.imwrite(str(out), img)
        else:
            # HEIC → JPG via sips
            if not out.exists():
                subprocess.run(
                    ["sips", "-s", "format", "jpeg", str(src_file), "--out", str(out)],
                    check=True, capture_output=True,
                )

        if out.exists():
            jpgs.append(out)

    log.info("  %s: %d imágenes → %d JPG válidos", src.name, len(files), len(jpgs))
    return jpgs


# ── Paso 2: Auto-etiquetado ───────────────────────────────────────────────────

def _detect_bbox(img_path: Path):
    """
    Retorna (x, y, w, h) en píxeles del QR detectado, o None.
    Prueba variantes de preprocesamiento para maximizar detecciones.
    """
    img = cv2.imread(str(img_path))
    if img is None:
        return None
    ih, iw = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    # Variantes de preprocesamiento
    variants = []
    variants.append(("original", gray))
    variants.append(("clahe", clahe.apply(gray)))

    # Reducir a 640px para fotos grandes de iPhone (mejora pyzbar)
    if iw > 800:
        scale = 640.0 / iw
        small = cv2.resize(gray, (640, int(ih * scale)))
        variants.append(("resize", small))
        variants.append(("resize+clahe", clahe.apply(small)))

    # Threshold inverso (QR blanco sobre fondo oscuro)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    variants.append(("thresh_inv", thresh))

    for label, v in variants:
        sx = iw / v.shape[1]
        sy = ih / v.shape[0]

        if _PYZBAR_OK:
            codes = pyzbar_decode(v, symbols=[ZBarSymbol.QRCODE])
            if codes:
                r = codes[0].rect
                log.debug("    pyzbar OK en %s  rect=(%d,%d,%d,%d)",
                          label, r.left, r.top, r.width, r.height)
                return (int(r.left * sx), int(r.top * sy),
                        int(r.width * sx), int(r.height * sy))
        else:
            data, pts, _ = cv2.QRCodeDetector().detectAndDecode(v)
            if data and pts is not None:
                pts = pts[0].astype(int)
                x = int(pts[:, 0].min())
                y = int(pts[:, 1].min())
                w = int(pts[:, 0].max()) - x
                h = int(pts[:, 1].max()) - y
                return (int(x * sx), int(y * sy), int(w * sx), int(h * sy))

    return None


def _to_yolo(bbox, iw, ih) -> str:
    """Convierte bbox píxel a formato YOLO: class_id cx cy w h (normalizado)."""
    x, y, w, h = bbox
    cx = (x + w / 2) / iw
    cy = (y + h / 2) / ih
    return f"{cx:.6f} {cy:.6f} {w/iw:.6f} {h/ih:.6f}"


# ── Paso 3: Construir dataset ─────────────────────────────────────────────────

def build_dataset() -> Path:
    log.info("Construyendo dataset en %s", DATASET)

    for split in ("train", "val"):
        (DATASET / "images" / split).mkdir(parents=True, exist_ok=True)
        (DATASET / "labels" / split).mkdir(parents=True, exist_ok=True)

    total_ok = 0
    total_skip = 0
    class_counts = {}

    for class_id, qr_name in enumerate(QR_NAMES):
        src = DATA_DIR / qr_name
        if not src.exists():
            log.warning("Carpeta no encontrada: %s — omitida", src)
            continue

        log.info("Procesando %s ...", qr_name)
        jpgs = convert_heic(src, DATASET / "converted" / qr_name)
        labeled: list[tuple] = []

        for jpg in jpgs:
            img = cv2.imread(str(jpg))
            if img is None:
                continue
            ih, iw = img.shape[:2]
            bbox = _detect_bbox(jpg)
            if bbox is None:
                log.warning("    Sin QR en %s", jpg.name)
                total_skip += 1
                continue
            labeled.append((jpg, class_id, _to_yolo(bbox, iw, ih), bbox))
            total_ok += 1

        random.shuffle(labeled)
        n_val = max(1, int(len(labeled) * VAL_SPLIT))
        splits = {"val": labeled[:n_val], "train": labeled[n_val:]}
        class_counts[qr_name] = {"train": len(splits["train"]), "val": len(splits["val"])}

        for split, items in splits.items():
            img_dir = DATASET / "images" / split
            lbl_dir = DATASET / "labels" / split
            for jpg, cid, yolo_line, bbox in items:
                dst_img = img_dir / f"{qr_name}_{jpg.name}"
                shutil.copy(jpg, dst_img)
                (lbl_dir / (dst_img.stem + ".txt")).write_text(f"{cid} {yolo_line}\n")

    log.info("=" * 60)
    log.info("Dataset completo:")
    log.info("  Etiquetadas OK:  %d", total_ok)
    log.info("  Omitidas (sin QR detectado): %d", total_skip)
    log.info("")
    log.info("  Por clase:")
    for qr_name, counts in class_counts.items():
        log.info("    %s: train=%d  val=%d", qr_name, counts["train"], counts["val"])
    log.info("=" * 60)

    yaml_path = DATASET / "data.yaml"
    yaml_path.write_text(
        f"path: {DATASET}\n"
        f"train: images/train\n"
        f"val:   images/val\n"
        f"nc: {len(QR_NAMES)}\n"
        f"names: {QR_NAMES}\n"
    )
    log.info("data.yaml creado en %s", yaml_path)
    return yaml_path


# ── Paso 3b: Dry run — inspección ─────────────────────────────────────────────

def inspect_dataset() -> None:
    """Muestra estadísticas del dataset sin entrenar."""
    log.info("Inspección del dataset en %s", DATASET)
    for split in ("train", "val"):
        img_dir = DATASET / "images" / split
        lbl_dir = DATASET / "labels" / split
        n_imgs = len(list(img_dir.glob("*.jpg"))) if img_dir.exists() else 0
        n_lbls = len(list(lbl_dir.glob("*.txt"))) if lbl_dir.exists() else 0
        log.info("  %s: %d imágenes, %d labels", split, n_imgs, n_lbls)

    if not (DATASET / "images" / "train").exists():
        log.warning("Dataset no construido aún. Ejecuta sin --dry-run primero.")


# ── Paso 4: Entrenar ──────────────────────────────────────────────────────────

def train(yaml_path: Path, epochs: int) -> None:
    log.info("Entrenando YOLOv8n — %d épocas, imgsz=%d, batch=%s", epochs, IMGSZ, BATCH)
    model = YOLO("yolov8n.pt")
    results = model.train(
        data=str(yaml_path),
        epochs=epochs,
        imgsz=IMGSZ,
        batch=BATCH,
        name="qr_model",
        project=str(BASE_DIR / "runs"),
        patience=25,
        # Augmentation suave — los QR no deben deformarse demasiado
        degrees=15.0,       # rotación máxima ±15°
        translate=0.1,      # traslación 10%
        scale=0.3,          # zoom ±30%
        hsv_h=0.015,        # hue muy bajo (QR es blanco/negro)
        hsv_s=0.3,          # saturación
        hsv_v=0.3,          # brillo
        fliplr=0.5,         # flip horizontal
        flipud=0.1,         # flip vertical
        mosaic=0.4,         # mosaic mixing
        blur=0.5,           # simular motion blur leve
        # Early stopping y logging
        verbose=True,
    )
    return results


# ── Paso 5: Instalar modelo ───────────────────────────────────────────────────

def install_model() -> bool:
    candidates = sorted((BASE_DIR / "runs").glob("qr_model*/weights/best.pt"))
    if not candidates:
        log.error("No se encontró best.pt — revisa la carpeta runs/")
        return False

    best = candidates[-1]
    MODELS_DIR.mkdir(exist_ok=True)
    dst = MODELS_DIR / "qr_detector.pt"

    # Backup del modelo anterior
    if dst.exists():
        backup = MODELS_DIR / f"qr_detector_backup_{dst.stat().st_mtime:.0f}.pt"
        shutil.copy(dst, backup)
        log.info("Backup del modelo anterior: %s", backup.name)

    shutil.copy(best, dst)
    size_mb = dst.stat().st_size / (1024 * 1024)
    log.info("Modelo instalado: %s (%.1f MB)", dst, size_mb)
    log.info("Reinicia el sistema para activarlo (se carga automáticamente).")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Entrenar detector QR con YOLOv8")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS,
                        help=f"Número de épocas (default: {DEFAULT_EPOCHS})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo inspecciona el dataset sin entrenar")
    parser.add_argument("--skip-train", action="store_true",
                        help="Construye dataset pero no entrena")
    args = parser.parse_args()

    if args.dry_run:
        if DATASET.exists():
            inspect_dataset()
        else:
            log.info("Dataset no existe. Construyéndolo primero ...")
            build_dataset()
            inspect_dataset()
        return

    log.info("=== 1/3  Convirtiendo y auto-etiquetando ===")
    yaml_path = build_dataset()

    if args.skip_train:
        log.info("Dataset listo. Ejecuta sin --skip-train para entrenar.")
        return

    log.info("=== 2/3  Entrenando YOLOv8n (%d épocas) ===", args.epochs)
    train(yaml_path, args.epochs)

    log.info("=== 3/3  Instalando modelo ===")
    if install_model():
        log.info("✅ Entrenamiento completado.")
    else:
        log.error("❌ Error instalando el modelo.")


if __name__ == "__main__":
    main()
