"""
train.py — Fine-tuning de YOLOv8 con dataset propio de paquetes.

Pasos previos:
  1. Coloca imágenes y labels en training/dataset/ con estructura YOLO:
       dataset/
         images/train/   *.jpg
         images/val/     *.jpg
         labels/train/   *.txt
         labels/val/     *.txt
  2. Edita training/dataset/data.yaml (generado por LabelImg o Roboflow).

Uso:
  python training/train.py

Requisito: pip install ultralytics
"""

from pathlib import Path
from ultralytics import YOLO

BASE_DIR = Path(__file__).parent

# Ruta al data.yaml generado al etiquetar con LabelImg / Roboflow
DATA_YAML = BASE_DIR / "dataset" / "data.yaml"

# Modelo base (yolov8n = nano, más rápido; yolov8s = small, más preciso)
BASE_MODEL = "yolov8n.pt"

EPOCHS = 50
IMAGE_SIZE = 640
BATCH_SIZE = 16
PROJECT_DIR = BASE_DIR / "runs"
RUN_NAME = "paquetes_v1"


def main():
    model = YOLO(BASE_MODEL)
    results = model.train(
        data=str(DATA_YAML),
        epochs=EPOCHS,
        imgsz=IMAGE_SIZE,
        batch=BATCH_SIZE,
        project=str(PROJECT_DIR),
        name=RUN_NAME,
        exist_ok=True,
    )
    print(f"\nModelo guardado en: {PROJECT_DIR / RUN_NAME / 'weights' / 'best.pt'}")
    return results


if __name__ == "__main__":
    main()
