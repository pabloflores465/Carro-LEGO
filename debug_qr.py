"""
debug_qr.py — Diagnóstico visual de detección de QR.

Ventana izquierda: cámara en vivo con overlay de detecciones.
Ventana derecha:   cuadrícula con las 3 variantes base para ver contraste.

Consola: muestra qué variante detectó cada QR y cuántos frames duró la
         duplicidad (2 QR iguales en el mismo frame).

Uso: python debug_qr.py
Cierra con Q o ESC.
"""

import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from station.vision.classifier import _preprocess_variants

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
    def _decode(img):
        # Retorna lista CON duplicados: ["QR1", "QR1"] si hay 2 físicos iguales
        return [c.data.decode().strip() for c in pyzbar_decode(img) if c.data]
except ImportError:
    def _decode(img):
        det = cv2.QRCodeDetector()
        data, _, _ = det.detectAndDecode(img)
        return [data.strip()] if data else []


cam = cv2.VideoCapture(1)
print("Debug QR activo. Pon los 2 QR frente a la cámara. Cierra con Q.")

# ── Estado del timer ──────────────────────────────────────────────────────────
dual_frames      = 0          # frames consecutivos con 2 QR iguales
last_dual_frames = 0          # cuánto duró la última duplicidad
show_last_until  = 0          # nro de frame hasta el que mostrar el mensaje
frame_idx        = 0

while True:
    ok, frame = cam.read()
    if not ok:
        break
    frame_idx += 1

    variants = _preprocess_variants(frame)

    # ── Detectar con cada variante ────────────────────────────────────────────
    # max_count[qr] = máximo de veces que ese QR aparece en cualquier variante
    max_count: Counter = Counter()
    found: dict[str, list[str]] = {}   # variante → lista única (para mostrar)

    for name, img in variants:
        try:
            hits = _decode(img)   # lista con posibles duplicados
        except Exception:
            hits = []
        if hits:
            c = Counter(hits)
            for qr, cnt in c.items():
                if cnt > max_count[qr]:
                    max_count[qr] = cnt
            found[name] = list(dict.fromkeys(hits))  # únicos, conserva orden

    # QRs visibles (sin importar cuántas veces)
    all_unique = list(max_count.keys())

    # ¿Hay algún QR con 2 o más instancias?
    dual_qr = next((qr for qr, cnt in max_count.items() if cnt >= 2), None)

    # ── Timer de duplicidad ───────────────────────────────────────────────────
    if dual_qr:
        dual_frames += 1
    else:
        if dual_frames > 0:
            last_dual_frames = dual_frames
            show_last_until  = frame_idx + 90   # muestra ~3 s a 30 fps
            print(f"\n[Timer] Duplicidad '{dual_qr or '?'}' duró {last_dual_frames} frames")
        dual_frames = 0

    # ── Consola ───────────────────────────────────────────────────────────────
    if all_unique:
        working = [f"{k}→{v}" for k, v in found.items() if k != "x2"]
        dup_info = f"  DUPLICADO={dual_qr}({dual_frames}f)" if dual_qr else ""
        print(f"QRs: {sorted(all_unique)}{dup_info}  |  {working}        ", end="\r")
    else:
        print("Sin QR detectado                                                  ", end="\r")

    # ── Ventana principal: cámara en vivo ─────────────────────────────────────
    live = frame.copy()
    h, w = live.shape[:2]

    # Barra superior: QRs detectados
    cv2.rectangle(live, (0, 0), (w, 44), (20, 20, 20), -1)
    label = f"Detectados: {sorted(all_unique)}" if all_unique else "Sin QR"
    color = (0, 220, 80) if dual_qr else (0, 180, 255) if all_unique else (80, 80, 80)
    cv2.putText(live, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    # Barra inferior: estado + timer
    cv2.rectangle(live, (0, h - 50), (w, h), (20, 20, 20), -1)
    if dual_qr:
        msg = f"DUPLICIDAD '{dual_qr}': {dual_frames} frames"
        mc  = (0, 255, 80)
    elif frame_idx <= show_last_until and last_dual_frames > 0:
        msg = f"Ultima duplicidad: {last_dual_frames} frames"
        mc  = (0, 200, 255)
    elif len(all_unique) == 1:
        msg = "Solo 1 QR — falta el segundo"
        mc  = (0, 200, 255)
    else:
        msg = "Sin QR"
        mc  = (80, 80, 80)
    cv2.putText(live, msg, (10, h - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.8, mc, 2)

    # Marco verde cuando hay duplicidad
    if dual_qr:
        cv2.rectangle(live, (4, 48), (w - 4, h - 54), (0, 200, 80), 3)

    cv2.imshow("Live — Deteccion QR", live)

    # ── Ventana secundaria: cuadrícula (solo variantes base, no x2) ───────────
    base_only = [(n, img) for n, img in variants if n != "x3"]
    thumb_w, thumb_h = 280, 210
    grid_cols = 4
    grid_rows = (len(base_only) + grid_cols - 1) // grid_cols
    grid = np.zeros((grid_rows * thumb_h, grid_cols * thumb_w), dtype=np.uint8)

    for i, (name, img) in enumerate(base_only):
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        thumb = cv2.resize(img, (thumb_w, thumb_h))
        r, c = divmod(i, grid_cols)
        grid[r*thumb_h:(r+1)*thumb_h, c*thumb_w:(c+1)*thumb_w] = thumb
        hits_here = found.get(name, [])
        label_color = 255 if hits_here else 120
        cv2.putText(grid, name, (c*thumb_w + 4, r*thumb_h + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, label_color, 1)
        if hits_here:
            cv2.putText(grid, str(hits_here), (c*thumb_w + 4, r*thumb_h + 36),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, 255, 1)

    cv2.imshow("Variantes preprocesadas", grid)

    if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
        break

cam.release()
cv2.destroyAllWindows()
print()
