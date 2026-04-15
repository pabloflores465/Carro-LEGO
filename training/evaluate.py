"""
evaluate.py — Evaluación del modelo entrenado sobre el conjunto de validación.

Uso:
  python training/evaluate.py --model training/runs/paquetes_v1/weights/best.pt

Métricas reportadas: mAP50, mAP50-95, precision, recall, confusion matrix.
Requisito: pip install ultralytics
"""

import argparse
from pathlib import Path
from ultralytics import YOLO

BASE_DIR = Path(__file__).parent
DATA_YAML = BASE_DIR / "dataset" / "data.yaml"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default=str(BASE_DIR / "runs" / "paquetes_v1" / "weights" / "best.pt"),
        help="Ruta al modelo .pt a evaluar",
    )
    args = parser.parse_args()

    model = YOLO(args.model)
    metrics = model.val(data=str(DATA_YAML), verbose=True)

    print("\n── Métricas de validación ──────────────────")
    print(f"  mAP50:       {metrics.box.map50:.4f}")
    print(f"  mAP50-95:    {metrics.box.map:.4f}")
    print(f"  Precision:   {metrics.box.mp:.4f}")
    print(f"  Recall:      {metrics.box.mr:.4f}")


if __name__ == "__main__":
    main()
