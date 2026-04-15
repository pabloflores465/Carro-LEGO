"""
app.py — Dashboard web en tiempo real para el docente.

Muestra el estado de todas las estaciones, el conteo global de paquetes
y un panel para ajustar la calibración del robot sin editar código.

Uso:
  python orchestrator/dashboard/app.py --broker localhost

Abre http://localhost:8000 en el navegador.

Requisitos: pip install fastapi uvicorn paho-mqtt pyyaml
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Set

import paho.mqtt.client as mqtt
import uvicorn
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator.state import StateRegistry

log = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [DASH] %(message)s")

CALIBRATION_FILE = ROOT / "config" / "calibration.yaml"

app = FastAPI()
registry = StateRegistry()
_ws_clients: Set[WebSocket] = set()
_broadcast_queue: asyncio.Queue = None


# ── Modelo de calibración ─────────────────────────────────────────────────────

class Calibration(BaseModel):
    advance_power: int = Field(ge=5,  le=100, description="Potencia de avance")
    return_power:  int = Field(ge=5,  le=100, description="Potencia de retorno")
    tilt_power:    int = Field(ge=5,  le=100, description="Potencia Motor C")
    tilt_degrees:  int = Field(ge=30, le=360, description="Grados de inclinación")
    frames_on_top: int = Field(ge=1,  le=30,  description="Frames para confirmar parada")


def _read_calibration() -> dict:
    with open(CALIBRATION_FILE) as f:
        return yaml.safe_load(f)


def _write_calibration(data: dict):
    with open(CALIBRATION_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


# ── HTML del dashboard ────────────────────────────────────────────────────────

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Dashboard — Clasificador LEGO</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body   { font-family: monospace; background: #111; color: #eee;
             padding: 2rem; max-width: 960px; margin: 0 auto; }
    h1     { color: #4cf; margin-bottom: 0.25rem; }
    h2     { color: #8af; font-size: 1rem; margin: 1.8rem 0 0.8rem; border-bottom: 1px solid #333; padding-bottom: 0.3rem; }
    #summary { margin-bottom: 1.2rem; font-size: 1.05rem; }

    /* Tabla de estaciones */
    table  { border-collapse: collapse; width: 100%; margin-bottom: 0.5rem; }
    th, td { border: 1px solid #2a2a2a; padding: 0.45rem 0.8rem; text-align: left; }
    th     { background: #1a1a1a; color: #888; }
    .idle        { color: #666; }
    .detecting, .classifying,
    .waiting_auth, .executing,
    .returning   { color: #fc0; }
    .error       { color: #f55; }
    #updated     { font-size: 0.78rem; color: #444; margin-top: 0.4rem; }

    /* Panel de calibración */
    #calib-panel { background: #181818; border: 1px solid #2a2a2a;
                   border-radius: 6px; padding: 1.2rem 1.5rem; }
    .param-row   { display: flex; align-items: center; gap: 1rem;
                   margin-bottom: 0.9rem; flex-wrap: wrap; }
    .param-row label { width: 11rem; color: #aaa; flex-shrink: 0; }
    .param-row input[type=range] { flex: 1; min-width: 140px; accent-color: #4cf; }
    .param-row input[type=number]{ width: 4.5rem; background: #222; border: 1px solid #444;
                                   color: #eee; padding: 0.2rem 0.4rem; border-radius: 3px;
                                   text-align: center; font-family: monospace; }
    .param-hint  { font-size: 0.75rem; color: #555; width: 100%;
                   padding-left: 12rem; margin-top: -0.5rem; }

    #save-btn    { margin-top: 0.6rem; padding: 0.5rem 1.6rem;
                   background: #4cf; color: #111; border: none;
                   border-radius: 4px; cursor: pointer; font-weight: bold;
                   font-family: monospace; font-size: 0.95rem; }
    #save-btn:hover  { background: #6df; }
    #save-btn:active { background: #29c; }
    #save-msg    { margin-left: 1rem; font-size: 0.88rem; }
    .ok  { color: #4c4; }
    .err { color: #f55; }
  </style>
</head>
<body>
  <h1>Clasificador LEGO NXT — Monitor</h1>

  <!-- ── Estaciones ── -->
  <h2>Estaciones</h2>
  <div id="summary">Paquetes totales: <strong id="total">0</strong></div>
  <table>
    <thead>
      <tr>
        <th>Estación</th><th>Estado</th><th>Paquetes</th>
        <th>Último QR</th><th>Último agujero</th><th>Última actividad</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <div id="updated"></div>

  <!-- ── Calibración ── -->
  <h2>Calibración del robot</h2>
  <div id="calib-panel">

    <div class="param-row">
      <label>Velocidad avance</label>
      <input type="range"  id="advance_power" min="5" max="100" step="1">
      <input type="number" id="advance_power_n" min="5" max="100">
    </div>
    <div class="param-hint">Más bajo = para con más precisión encima del QR.</div>

    <div class="param-row">
      <label>Velocidad retorno</label>
      <input type="range"  id="return_power" min="5" max="100" step="1">
      <input type="number" id="return_power_n" min="5" max="100">
    </div>
    <div class="param-hint">Velocidad al regresar a home.</div>

    <div class="param-row">
      <label>Potencia Motor C</label>
      <input type="range"  id="tilt_power" min="5" max="100" step="1">
      <input type="number" id="tilt_power_n" min="5" max="100">
    </div>
    <div class="param-hint">Fuerza del motor que inclina la plataforma.</div>

    <div class="param-row">
      <label>Grados inclinación</label>
      <input type="range"  id="tilt_degrees" min="30" max="360" step="5">
      <input type="number" id="tilt_degrees_n" min="30" max="360">
    </div>
    <div class="param-hint">Cuánto gira Motor C para soltar el objeto. Prueba 90 → 120 → 150.</div>

    <div class="param-row">
      <label>Frames encima</label>
      <input type="range"  id="frames_on_top" min="1" max="30" step="1">
      <input type="number" id="frames_on_top_n" min="1" max="30">
    </div>
    <div class="param-hint">Frames sin ver el QR para confirmar que el robot está encima. Sube si para antes de tiempo.</div>

    <div style="margin-top:1rem">
      <button id="save-btn" onclick="saveCalib()">Guardar calibración</button>
      <span id="save-msg"></span>
    </div>
  </div>

  <script>
    // ── WebSocket: tabla de estaciones ────────────────────────────────────────
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      if (data.stations === undefined) return;
      const tbody = document.getElementById("rows");
      tbody.innerHTML = "";
      let total = 0;
      for (const [id, s] of Object.entries(data.stations)) {
        total += s.packages_done;
        const ts = s.last_seen ? new Date(s.last_seen * 1000).toLocaleTimeString() : "—";
        tbody.innerHTML += `<tr>
          <td>${id}</td>
          <td class="${s.status}">${s.status.toUpperCase()}</td>
          <td>${s.packages_done}</td>
          <td>${s.last_class || "—"}</td>
          <td>${s.last_hole  || "—"}</td>
          <td>${ts}</td>
        </tr>`;
      }
      document.getElementById("total").textContent = total;
      document.getElementById("updated").textContent = "Actualizado: " + new Date().toLocaleTimeString();
    };
    ws.onclose = () => {
      document.getElementById("updated").textContent = "⚠ Conexión perdida";
    };

    // ── Calibración: enlace slider ↔ número ──────────────────────────────────
    const PARAMS = ["advance_power","return_power","tilt_power","tilt_degrees","frames_on_top"];

    function link(id) {
      const slider = document.getElementById(id);
      const num    = document.getElementById(id + "_n");
      slider.addEventListener("input", () => { num.value = slider.value; });
      num.addEventListener("input",   () => { slider.value = num.value;  });
    }
    PARAMS.forEach(link);

    function setCalib(data) {
      PARAMS.forEach(p => {
        document.getElementById(p).value   = data[p];
        document.getElementById(p + "_n").value = data[p];
      });
    }

    // Carga valores actuales al abrir la página
    fetch("/calibration")
      .then(r => r.json())
      .then(setCalib)
      .catch(() => console.warn("No se pudo cargar calibración"));

    function saveCalib() {
      const body = {};
      PARAMS.forEach(p => { body[p] = parseInt(document.getElementById(p + "_n").value); });
      const msg = document.getElementById("save-msg");
      fetch("/calibration", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      })
      .then(r => r.json())
      .then(data => {
        setCalib(data);
        msg.textContent = "✓ Guardado";
        msg.className = "ok";
        setTimeout(() => { msg.textContent = ""; }, 3000);
      })
      .catch(() => {
        msg.textContent = "✗ Error al guardar";
        msg.className = "err";
      });
    }
  </script>
</body>
</html>
"""


# ── FastAPI endpoints ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return DASHBOARD_HTML


@app.get("/calibration")
async def get_calibration():
    return JSONResponse(_read_calibration())


@app.post("/calibration")
async def post_calibration(data: Calibration):
    updated = data.model_dump()
    _write_calibration(updated)
    log.info(f"Calibración actualizada: {updated}")
    return JSONResponse(updated)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        await ws.send_text(json.dumps({"stations": registry.all_stations()}))
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        _ws_clients.discard(ws)


async def _broadcast_loop():
    global _broadcast_queue
    while True:
        msg = await _broadcast_queue.get()
        dead = set()
        for ws in list(_ws_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        _ws_clients.difference_update(dead)


# ── MQTT listener (hilo separado) ─────────────────────────────────────────────

def start_mqtt(broker_host: str, broker_port: int, loop: asyncio.AbstractEventLoop):
    client = mqtt.Client(client_id="dashboard")

    def on_connect(c, ud, flags, rc):
        c.subscribe("station/+/status")
        c.subscribe("station/+/event")
        log.info(f"Dashboard MQTT conectado (rc={rc})")

    def on_message(c, ud, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            return
        parts = msg.topic.split("/")
        station_id = parts[1] if len(parts) >= 2 else "unknown"

        if msg.topic.endswith("/status"):
            registry.update_status(station_id, payload.get("status", "unknown"))
        elif msg.topic.endswith("/event") and payload.get("event") == "cycle_complete":
            registry.record_completion(
                station_id,
                class_name=payload.get("qr", ""),
                hole=payload.get("hole", 0),
            )

        snapshot = json.dumps({"stations": registry.all_stations()})
        asyncio.run_coroutine_threadsafe(_broadcast_queue.put(snapshot), loop)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker_host, broker_port, keepalive=60)
    client.loop_forever()


# ── Arranque ──────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Dashboard de monitoreo de estaciones")
    p.add_argument("--broker",   default="localhost")
    p.add_argument("--port",     type=int, default=1883)
    p.add_argument("--web-port", type=int, default=8000)
    return p.parse_args()


if __name__ == "__main__":
    import threading
    args = parse_args()

    @app.on_event("startup")
    async def startup():
        global _broadcast_queue
        _broadcast_queue = asyncio.Queue()
        loop = asyncio.get_event_loop()
        threading.Thread(
            target=start_mqtt,
            args=(args.broker, args.port, loop),
            daemon=True,
        ).start()
        asyncio.create_task(_broadcast_loop())

    uvicorn.run(app, host="0.0.0.0", port=args.web_port)
