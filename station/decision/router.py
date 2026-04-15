"""
router.py — Determina el agujero de destino a partir de la lectura del QR.

Modo QR directo (por defecto):
  El contenido del QR es el destino. No se necesita tabla de mapeo.
  QR con "1" o "QR1" → agujero 1. QR con "2" o "QR2" → agujero 2. Etc.

Modo tabla (opcional, para lógica personalizada):
  Si destinations.yaml define overrides, estos tienen prioridad.
  Ejemplo: el QR dice "fragil" → forzarlo al agujero 3.

Uso:
    router = Router("config/destinations.yaml")
    hole = router.get_destination(detection)   # detection: objeto Detection de classifier.py
    hole = router.get_destination(detection)   # → int o None si el destino no existe

Requisito: pip install pyyaml
"""

from pathlib import Path
from typing import Optional, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from station.vision.classifier import Detection


class Router:
    def __init__(self, config_path: str = "config/destinations.yaml"):
        path = Path(config_path)
        self.overrides: dict[str, int] = {}
        self.valid_holes: list[int] = []

        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            self.overrides = data.get("overrides", {})
            self.valid_holes = data.get("valid_holes", [])

    def get_destination(self, detection: "Detection") -> Optional[int]:
        """
        Retorna el número de agujero para la detección dada.

        Prioridad:
          1. Si el QR está en 'overrides', usa ese mapeo.
          2. Si no, usa el número extraído directamente del QR (detection.hole).
          3. Si valid_holes está definido y el número no está en la lista → None.
        """
        # 1. Overrides manuales
        if detection.class_name in self.overrides:
            return self.overrides[detection.class_name]

        hole = detection.hole

        # 2. Validar contra lista de agujeros físicos disponibles
        if self.valid_holes and hole not in self.valid_holes:
            return None

        return hole
