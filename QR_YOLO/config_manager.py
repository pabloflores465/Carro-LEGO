"""
config_manager.py — Carga y guarda la configuración persistente del sistema.

Gestiona:
  - config/associations.json : mapa origen → destino (QR1 → QR4, etc.)
  - config/nav_config.json   : umbrales de navegación visual

QR de paquete  : QR1, QR2, QR3  (pegados encima de cada paquete)
QR de destino  : QR4, QR5, QR6  (en el suelo, marca dónde soltar)
"""
import json
import logging
from pathlib import Path
from typing import Dict

log = logging.getLogger("config")

# ── Nombres de QR ─────────────────────────────────────────────────────────────

QR_PACKAGE_NAMES  = ["QR1", "QR2", "QR3"]   # QR que llevan los paquetes
QR_DEST_NAMES     = ["QR4", "QR5", "QR6"]   # QR en el suelo (destinos)
QR_NAMES          = QR_PACKAGE_NAMES + QR_DEST_NAMES   # los 6 en total

# ── Valores por defecto ────────────────────────────────────────────────────────

DEFAULT_ASSOCIATIONS: Dict[str, str] = {
    "QR1": "QR4",
    "QR2": "QR5",
    "QR3": "QR6",
}

DEFAULT_NAV: Dict = {
    # ── Cámara cenital: navegación por posición relativa robot↔destino ──────
    "camera_index": 1,       # índice de la cámara (0=built-in, 1=USB, etc.)
    "arrival_px": 120,       # distancia en píxeles robot→destino para considerar llegada
    "advance_power": 55,     # potencia base (0-100)
    "min_power": 30,         # potencia mínima de la rueda interior (evita parada total)
    "search_power": 25,      # potencia durante búsqueda giratoria
    "steer_gain": 0.5,       # 0=recto siempre, 1=máxima corrección
    "steer_invert": 1,       # 1=normal, -1=invertir si los motores están al revés
    "heading_offset_deg": 0, # compensación angular heading del QR (calibrar si se desvía)
    "lost_debounce": 6,      # frames sin ver algún QR antes de activar búsqueda
    "arrival_debounce": 4,   # frames cerca para confirmar llegada
    "return_after_delivery": True,
    "tilt_degrees": 45,
    "tilt_power": 60,
}

# ── Rutas ──────────────────────────────────────────────────────────────────────

_CONFIG_DIR = Path(__file__).parent / "config"
_ASSOCIATIONS_PATH = _CONFIG_DIR / "associations.json"
_NAV_CONFIG_PATH = _CONFIG_DIR / "nav_config.json"


class ConfigManager:
    """Carga y persiste la configuración en archivos JSON locales."""

    def __init__(self):
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.associations: Dict[str, str] = {}
        self.nav_config: Dict = {}
        self.load()

    # ── Carga ──────────────────────────────────────────────────────────────────

    def load(self) -> None:
        self.associations = self._load_json(
            _ASSOCIATIONS_PATH, DEFAULT_ASSOCIATIONS.copy()
        )
        self.nav_config = self._load_json(
            _NAV_CONFIG_PATH, DEFAULT_NAV.copy()
        )

    @staticmethod
    def _load_json(path: Path, default: dict) -> dict:
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"Error leyendo {path}: {e} — usando valores por defecto")
        with open(path, "w") as f:
            json.dump(default, f, indent=2)
        return default

    # ── Asociaciones ──────────────────────────────────────────────────────────

    def get_destination(self, source_qr: str) -> str:
        return self.associations.get(source_qr, "")

    def set_association(self, source: str, destination: str) -> None:
        self.associations[source] = destination
        self.save_associations()

    def set_associations_bulk(self, mapping: Dict[str, str]) -> None:
        self.associations.update(mapping)
        self.save_associations()

    def save_associations(self) -> None:
        with open(_ASSOCIATIONS_PATH, "w") as f:
            json.dump(self.associations, f, indent=2)
        log.info("Asociaciones guardadas en %s", _ASSOCIATIONS_PATH)

    # ── Navegación ────────────────────────────────────────────────────────────

    def save_nav_config(self) -> None:
        with open(_NAV_CONFIG_PATH, "w") as f:
            json.dump(self.nav_config, f, indent=2)
        log.info("Configuración de navegación guardada en %s", _NAV_CONFIG_PATH)

    def get_nav(self, key: str):
        return self.nav_config.get(key, DEFAULT_NAV.get(key))
