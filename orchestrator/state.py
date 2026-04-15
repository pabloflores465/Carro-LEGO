"""
state.py — Registro de estado de todas las estaciones activas.

El orquestador consulta este módulo para decidir si autorizar una solicitud.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class StationState:
    station_id: str
    status: str = "idle"            # idle | detecting | classifying | waiting_auth | executing | returning | error
    last_seen: float = field(default_factory=time.time)
    packages_done: int = 0
    last_class: Optional[str] = None
    last_hole: Optional[int] = None


class StateRegistry:
    def __init__(self):
        self._stations: Dict[str, StationState] = {}

    def update_status(self, station_id: str, status: str):
        s = self._get_or_create(station_id)
        s.status = status
        s.last_seen = time.time()

    def record_completion(self, station_id: str, class_name: str, hole: int):
        s = self._get_or_create(station_id)
        s.packages_done += 1
        s.last_class = class_name
        s.last_hole = hole
        s.last_seen = time.time()

    def can_authorize(self, station_id: str) -> bool:
        """Solo autoriza si la estación está esperando autorización."""
        s = self._stations.get(station_id)
        return s is not None and s.status == "waiting_auth"

    def all_stations(self) -> Dict[str, dict]:
        return {
            sid: {
                "status": s.status,
                "packages_done": s.packages_done,
                "last_seen": s.last_seen,
                "last_class": s.last_class,
                "last_hole": s.last_hole,
            }
            for sid, s in self._stations.items()
        }

    def _get_or_create(self, station_id: str) -> StationState:
        if station_id not in self._stations:
            self._stations[station_id] = StationState(station_id=station_id)
        return self._stations[station_id]
