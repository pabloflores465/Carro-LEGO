"""
ui.py — Interfaz gráfica Tkinter para el sistema de robot QR.

Layout:
  Columna izquierda : 6 QR permanentes con thumbnail, etiqueta y selector de destino.
  Columna derecha   : Preview de cámara + panel de estado + botones de control + log.

Threading:
  - La UI corre en el hilo principal (obligatorio en macOS con Tkinter).
  - El loop de cámara y la navegación corren en hilos daemon separados.
  - update_frame() y log_event() son thread-safe (usan after() y Queue).
"""
import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np
from PIL import Image, ImageTk

from config_manager import ConfigManager, QR_DEST_NAMES, QR_NAMES, QR_PACKAGE_NAMES, QR_DEST_NAMES as _DEST
from qr_manager import ensure_base_qrs, get_qr_image
from vision import Camera, QRDetector
from navigation import NavState

log = logging.getLogger("ui")

_THUMB = (80, 80)
_PREVIEW = (480, 360)

# Colores por estado de navegación
_STATE_COLORS: Dict[str, str] = {
    "IDLE":       "#888888",
    "SEARCHING":  "#e6ac00",
    "CENTERING":  "#e67e00",
    "ADVANCING":  "#4caf50",
    "ARRIVING":   "#2196f3",
    "DELIVERING": "#9c27b0",
    "RETURNING":  "#00bcd4",
    "DONE":       "#4caf50",
    "ERROR":      "#f44336",
}


class QRRobotUI:
    """
    Ventana principal de la aplicación.
    Los callbacks on_* deben asignarse desde main.py antes de llamar a run().
    """

    def __init__(self, root: tk.Tk, config: ConfigManager):
        self.root   = root
        self.config = config
        self.root.title("Robot LEGO — Sistema QR")
        self.root.minsize(900, 650)

        # Cámara (manejada internamente)
        self._camera: Optional[Camera] = None
        self._detector: Optional[QRDetector] = None
        self._camera_running = False

        # Variables de estado
        self._cam_status_var   = tk.StringVar(value="Inactiva")
        self._cam_index_var    = tk.IntVar(value=int(self.config.get_nav("camera_index") or 1))
        self._heading_offset_var = tk.DoubleVar(
            value=float(self.config.get_nav("heading_offset_deg") or 0)
        )
        self._detected_qr_var  = tk.StringVar(value="—")
        self._dest_qr_var      = tk.StringVar(value="—")
        self._nav_state_var    = tk.StringVar(value="—")
        self._robot_status_var = tk.StringVar(value="Sin robot")

        # Selectores de asociación por QR
        self._assoc_vars: Dict[str, tk.StringVar] = {}

        # Cola thread-safe para el log
        self._log_q: queue.Queue = queue.Queue()

        # Callbacks para que main.py conecte la lógica
        self.on_connect_robot: Optional[Callable] = None
        self.on_start_mission: Optional[Callable] = None
        self.on_stop_mission:  Optional[Callable] = None

        self._build()
        self._poll_log()

    # ─────────────────────────────────────────────────────────────────────────
    # Construcción de UI
    # ─────────────────────────────────────────────────────────────────────────

    def _build(self):
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=2)
        self.root.rowconfigure(0, weight=1)

        left  = self._scrollable_frame(self.root, column=0)
        right = self._scrollable_frame(self.root, column=1)

        self._build_qr_panel(left)
        self._build_right_panel(right)

    def _scrollable_frame(self, parent: tk.Misc, column: int) -> ttk.Frame:
        """Crea una columna con scroll vertical y retorna el Frame interior."""
        container = ttk.Frame(parent)
        container.grid(row=0, column=column, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        canvas = tk.Canvas(container, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=sb.set)

        inner = ttk.Frame(canvas, padding=10)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win_id, width=canvas.winfo_width())

        inner.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))

        # Scroll con rueda del ratón / trackpad
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        # macOS trackpad usa Button-4/5 en versiones antiguas de Tk
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll( 1, "units"))

        return inner

    # ── Panel izquierdo: QR permanentes ───────────────────────────────────────

    def _build_qr_panel(self, parent: ttk.Frame):
        ttk.Label(
            parent, text="QR de paquetes", font=("Helvetica", 13, "bold")
        ).pack(anchor="w", pady=(0, 2))
        ttk.Label(
            parent, text="QR1-QR3 van sobre los paquetes\nQR4-QR6 van en el suelo (destinos)",
            font=("Helvetica", 9), foreground="#888888"
        ).pack(anchor="w", pady=(0, 8))

        # ── Sección de paquetes: QR1, QR2, QR3 con selector de destino ────────
        pkg_lf = ttk.LabelFrame(parent, text="Asociaciones  (paquete → destino)", padding=6)
        pkg_lf.pack(fill="x", pady=(0, 8))
        pkg_lf.columnconfigure(0, weight=1)

        for i, name in enumerate(QR_PACKAGE_NAMES):
            frame = ttk.Frame(pkg_lf)
            frame.grid(row=i, column=0, sticky="ew", pady=4)
            frame.columnconfigure(3, weight=1)

            # Thumbnail
            lbl_thumb = ttk.Label(frame)
            lbl_thumb.grid(row=0, column=0, padx=(0, 8))
            self._set_thumb(lbl_thumb, name)

            ttk.Label(frame, text=name, font=("Helvetica", 11, "bold"), width=5).grid(
                row=0, column=1, sticky="w"
            )
            ttk.Label(frame, text="→", font=("Helvetica", 12)).grid(row=0, column=2, padx=6)

            dest_default = self.config.get_destination(name) or QR_DEST_NAMES[i]
            var = tk.StringVar(value=dest_default)
            self._assoc_vars[name] = var
            combo = ttk.Combobox(
                frame, textvariable=var, values=QR_DEST_NAMES, width=8, state="readonly"
            )
            combo.grid(row=0, column=3, sticky="w")

        # ── Sección de destinos: QR4, QR5, QR6 (solo thumbnail, sin selector) ─
        dst_lf = ttk.LabelFrame(parent, text="QR de destino (en el suelo)", padding=6)
        dst_lf.pack(fill="x", pady=(0, 8))

        dest_row = ttk.Frame(dst_lf)
        dest_row.pack(fill="x")
        for j, name in enumerate(QR_DEST_NAMES):
            col_frame = ttk.Frame(dest_row)
            col_frame.pack(side="left", padx=8)
            lbl_thumb = ttk.Label(col_frame)
            lbl_thumb.pack()
            self._set_thumb(lbl_thumb, name)
            ttk.Label(col_frame, text=name, font=("Helvetica", 9, "bold")).pack()

        # Botones
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=8)
        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Generar QR",           command=self._on_gen_qr).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Guardar asociaciones",  command=self._on_save).pack(side="left", padx=4)

    # ── Panel derecho ─────────────────────────────────────────────────────────

    def _build_right_panel(self, parent: ttk.Frame):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        # Cámara preview
        cam_lf = ttk.LabelFrame(parent, text="Vista de cámara", padding=4)
        cam_lf.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        # Selector de índice de cámara
        cam_idx_frame = ttk.Frame(cam_lf)
        cam_idx_frame.pack(fill="x", padx=4, pady=2)
        ttk.Label(cam_idx_frame, text="Índice:").pack(side="left")
        self._cam_index_spin = ttk.Spinbox(
            cam_idx_frame, from_=0, to=9, width=4,
            textvariable=self._cam_index_var,
        )
        self._cam_index_spin.pack(side="left", padx=4)

        # Heading offset — calibración de desviación angular
        cam_idx_frame2 = ttk.Frame(cam_lf)
        cam_idx_frame2.pack(fill="x", padx=4, pady=2)
        ttk.Label(cam_idx_frame2, text="Heading offset°:").pack(side="left")
        self._heading_offset_spin = ttk.Spinbox(
            cam_idx_frame2, from_=-180, to=180, increment=5, width=6,
            textvariable=self._heading_offset_var,
        )
        self._heading_offset_spin.pack(side="left", padx=4)
        ttk.Label(
            cam_idx_frame2,
            text="← desvía izq  |  desvía der →",
            font=("Helvetica", 8),
            foreground="#999",
        ).pack(side="left")

        self._preview_lbl = ttk.Label(cam_lf)
        self._preview_lbl.pack()
        self._show_placeholder()

        # Panel de estado
        status_lf = ttk.LabelFrame(parent, text="Estado del sistema", padding=8)
        status_lf.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        status_lf.columnconfigure(1, weight=1)
        rows: List[tuple] = [
            ("Cámara:",          self._cam_status_var,   None),
            ("QR detectado:",    self._detected_qr_var,  "#2196f3"),
            ("QR destino:",      self._dest_qr_var,      "#9c27b0"),
            ("Estado nav:",      self._nav_state_var,    None),
            ("Robot:",           self._robot_status_var, None),
        ]
        for i, (label, var, _) in enumerate(rows):
            ttk.Label(status_lf, text=label, font=("Helvetica", 10, "bold")).grid(
                row=i, column=0, sticky="w", pady=2
            )
            ttk.Label(status_lf, textvariable=var).grid(
                row=i, column=1, sticky="w", padx=8
            )

        # Botones de control
        ctrl_lf = ttk.LabelFrame(parent, text="Controles", padding=8)
        ctrl_lf.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        buttons: List[tuple] = [
            ("Iniciar cámara",   self._on_start_cam,  0, 0),
            ("Detener cámara",   self._on_stop_cam,   0, 1),
            ("Conectar robot",   self._on_connect,    0, 2),
            ("Iniciar misión",   self._on_start_mission, 1, 0),
            ("Detener misión",   self._on_stop_mission,  1, 1),
        ]
        for text, cmd, row, col in buttons:
            ttk.Button(ctrl_lf, text=text, command=cmd).grid(
                row=row, column=col, padx=4, pady=4, sticky="ew"
            )
            ctrl_lf.columnconfigure(col, weight=1)

        # Log de eventos
        log_lf = ttk.LabelFrame(parent, text="Log de eventos", padding=4)
        log_lf.grid(row=3, column=0, sticky="nsew")
        log_lf.columnconfigure(0, weight=1)
        log_lf.rowconfigure(0, weight=1)

        self._log_txt = tk.Text(
            log_lf, height=10, state="disabled",
            font=("Courier", 9), bg="#1a1a2e", fg="#e0e0e0", relief="flat"
        )
        self._log_txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(log_lf, command=self._log_txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._log_txt["yscrollcommand"] = sb.set

    # ─────────────────────────────────────────────────────────────────────────
    # API pública (thread-safe)
    # ─────────────────────────────────────────────────────────────────────────

    def update_frame(self, frame: np.ndarray) -> None:
        """Actualiza el preview de cámara. Seguro llamar desde otro hilo."""
        self.root.after(0, self._apply_frame, frame)

    def _apply_frame(self, frame: np.ndarray) -> None:
        try:
            rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img  = Image.fromarray(rgb).resize(_PREVIEW, Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._preview_lbl.configure(image=photo)
            self._preview_lbl.image = photo
        except Exception:
            pass

    def log_event(self, msg: str) -> None:
        """Agrega una línea al log. Seguro llamar desde cualquier hilo."""
        self._log_q.put(msg)

    def set_camera_status(self, active: bool) -> None:
        self._cam_status_var.set("Activa ✓" if active else "Inactiva")

    def set_detected_qr(self, qr: str) -> None:
        self._detected_qr_var.set(qr or "—")

    def set_dest_qr(self, qr: str) -> None:
        self._dest_qr_var.set(qr or "—")

    def set_nav_state(self, state: NavState) -> None:
        name  = state.name
        color = _STATE_COLORS.get(name, "#888888")
        self._nav_state_var.set(name)
        # Actualiza color del label de estado nav
        self.root.after(0, self._color_nav_label, color)

    def _color_nav_label(self, color: str) -> None:
        pass  # placeholder — se puede extender para colorear el widget

    def set_robot_connected(self, connected: bool, sim: bool = False) -> None:
        if sim:
            self._robot_status_var.set("Simulado")
        elif connected:
            self._robot_status_var.set("NXT conectado ✓")
        else:
            self._robot_status_var.set("Sin robot")

    # ─────────────────────────────────────────────────────────────────────────
    # Handlers de botones
    # ─────────────────────────────────────────────────────────────────────────

    def _on_gen_qr(self) -> None:
        try:
            paths = ensure_base_qrs()
            self.log_event(f"QR generados/verificados: {len(paths)} archivos en qrs/")
            messagebox.showinfo("QR", f"{len(paths)} códigos QR listos en qrs/")
            # Refresca thumbnails
            for name in QR_NAMES:
                pass  # los thumbs ya están cargados al inicio
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def _on_save(self) -> None:
        for name, var in self._assoc_vars.items():
            dest = var.get()
            if dest:
                self.config.set_association(name, dest)
        self.log_event("Asociaciones guardadas.")
        messagebox.showinfo("Guardado", "Asociaciones guardadas correctamente.")

    def _on_start_cam(self) -> None:
        if self._camera_running:
            return
        try:
            cam_idx = int(self._cam_index_var.get())
            self.config.nav_config["camera_index"] = cam_idx
            self.config.nav_config["heading_offset_deg"] = float(self._heading_offset_var.get())
            self.config.save_nav_config()
            self._camera   = Camera(index=cam_idx)
            self._detector = QRDetector()
            self._camera_running = True
            self.set_camera_status(True)
            self.log_event("Cámara iniciada (índice 1 — iPhone).")
            threading.Thread(target=self._camera_loop, daemon=True).start()
        except Exception as exc:
            messagebox.showerror("Error de cámara", str(exc))

    def _on_stop_cam(self) -> None:
        self._camera_running = False
        if self._camera:
            self._camera.release()
            self._camera = None
        self._detector = None
        self.set_camera_status(False)
        self.set_detected_qr("")
        self._show_placeholder()
        self.log_event("Cámara detenida.")

    def _on_connect(self) -> None:
        if self.on_connect_robot:
            self.on_connect_robot()

    def _on_start_mission(self) -> None:
        if self.on_start_mission:
            self.on_start_mission()

    def _on_stop_mission(self) -> None:
        if self.on_stop_mission:
            self.on_stop_mission()

    # ─────────────────────────────────────────────────────────────────────────
    # Loop de cámara (hilo daemon)
    # ─────────────────────────────────────────────────────────────────────────

    def _camera_loop(self) -> None:
        while self._camera_running and self._camera:
            try:
                frame = self._camera.capture()
                if self._detector:
                    detections = self._detector.detect(frame)
                    if detections:
                        # Priorizar QR de paquete (QR1-QR3) sobre QR de destino
                        pkg = next((d for d in detections if d.content in QR_PACKAGE_NAMES), None)
                        first = pkg if pkg else detections[0]
                        kind = " (paquete)" if first.content in QR_PACKAGE_NAMES else " (destino)"
                        self.root.after(0, self.set_detected_qr, first.content + kind)
                        dest = self.config.get_destination(first.content)
                        self.root.after(0, self.set_dest_qr, dest or "—")
                    frame = self._detector.draw_detections(frame, detections)
                self.update_frame(frame)
            except Exception as exc:
                log.debug("Camera loop: %s", exc)
                time.sleep(0.1)
            time.sleep(0.033)  # ~30 fps

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers internos
    # ─────────────────────────────────────────────────────────────────────────

    def _set_thumb(self, label: ttk.Label, qr_name: str) -> None:
        try:
            img   = get_qr_image(qr_name).resize(_THUMB, Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            label.configure(image=photo)
            label.image = photo  # evitar GC
        except Exception:
            label.configure(text="[?]")

    def _show_placeholder(self) -> None:
        img   = Image.new("RGB", _PREVIEW, (30, 30, 45))
        photo = ImageTk.PhotoImage(img)
        self._preview_lbl.configure(image=photo)
        self._preview_lbl.image = photo

    def _poll_log(self) -> None:
        """Lee la cola de log y escribe en el widget Text."""
        try:
            while True:
                msg = self._log_q.get_nowait()
                ts  = time.strftime("%H:%M:%S")
                self._log_txt.configure(state="normal")
                self._log_txt.insert("end", f"[{ts}] {msg}\n")
                self._log_txt.see("end")
                self._log_txt.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)

    # ─────────────────────────────────────────────────────────────────────────
    # Accesors para main.py
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def camera(self) -> Optional[Camera]:
        return self._camera

    @property
    def detector(self) -> Optional[QRDetector]:
        return self._detector

    @property
    def camera_running(self) -> bool:
        return self._camera_running

    def get_detected_qr(self) -> str:
        return self._detected_qr_var.get()
