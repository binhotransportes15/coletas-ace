"""
Editor de TV / Dashboard em janela separada (CRT).

- Aba Parede: grade 2×3, setor por TV, modo parede
- Aba Dashboard: logo, margens, letras e gráfico (layout das telas é fixo para TV)
"""
from __future__ import annotations

import json
import socket
import threading
from copy import deepcopy
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tv_layout import (
    ARM_VIEWS,
    SECTOR_LABELS,
    default_view_ui,
    load_layout,
    normalize_layout,
    push_layout_to_sheets,
    save_layout,
    wall_off,
    wall_on,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings

    _HAS_WEBENGINE = True
except Exception:  # noqa: BLE001
    QWebEngineView = None  # type: ignore[misc, assignment]
    QWebEngineSettings = None  # type: ignore[misc, assignment]
    _HAS_WEBENGINE = False

GRID_COLS = 12
GRID_ROWS = 12
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"

_preview_httpd: ThreadingHTTPServer | None = None
_preview_port: int = 0
_preview_lock = threading.Lock()


def _ensure_preview_server() -> int:
    """HTTP local do /dashboard — evita CORS/file:// no WebEngine."""
    global _preview_httpd, _preview_port
    with _preview_lock:
        if _preview_httpd is not None and _preview_port:
            return _preview_port

        class _Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
                super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

            def log_message(self, *_args) -> None:  # noqa: ANN002
                return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
        sock.close()

        httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="ace-dash-preview")
        thread.start()
        _preview_httpd = httpd
        _preview_port = port
        return port


BLOCK_DEFS: dict[str, list[dict[str, Any]]] = {
    "distribuicao:agendamento": [
        {"id": "kpis", "label": "KPIs / totais", "x": 0, "y": 0, "w": 12, "h": 3},
        {"id": "chart", "label": "Gráfico / torres", "x": 0, "y": 3, "w": 8, "h": 6},
        {"id": "status", "label": "Status / alertas", "x": 8, "y": 3, "w": 4, "h": 6},
        {"id": "amanha", "label": "Amanhã", "x": 0, "y": 9, "w": 12, "h": 3},
    ],
    "distribuicao:coleta": [
        {"id": "resumo", "label": "Resumo total", "x": 0, "y": 0, "w": 12, "h": 3},
        {"id": "placas", "label": "Placas", "x": 0, "y": 3, "w": 3, "h": 9},
        {"id": "torres", "label": "Torres", "x": 3, "y": 3, "w": 6, "h": 9},
        {"id": "prazo", "label": "Prazo", "x": 9, "y": 3, "w": 3, "h": 9},
    ],
    "distribuicao:entrega": [
        {"id": "resumo", "label": "Resumo entrega", "x": 0, "y": 0, "w": 12, "h": 3},
        {"id": "banners", "label": "Faixas %", "x": 0, "y": 3, "w": 8, "h": 9},
        {"id": "pendencias", "label": "Pendências", "x": 8, "y": 3, "w": 4, "h": 9},
    ],
    "armazem": [
        {"id": "kpis", "label": "KPIs armazém", "x": 0, "y": 0, "w": 12, "h": 3},
        {"id": "chart", "label": "Gráfico", "x": 0, "y": 3, "w": 7, "h": 9},
        {"id": "tabela", "label": "Tabela veículos", "x": 7, "y": 3, "w": 5, "h": 9},
    ],
    "armazem:patio": [
        {"id": "kpis", "label": "KPIs pátio", "x": 0, "y": 0, "w": 12, "h": 3},
        {"id": "chart", "label": "Descarga", "x": 0, "y": 3, "w": 12, "h": 4},
        {"id": "tabela", "label": "Veículos", "x": 0, "y": 7, "w": 12, "h": 5},
    ],
    "armazem:conferentes": [
        {"id": "kpis", "label": "KPIs 177", "x": 0, "y": 0, "w": 12, "h": 2},
        {"id": "chart", "label": "Ranking", "x": 0, "y": 2, "w": 12, "h": 10},
    ],
}

# Altura mínima sugerida por tipo de bloco (evita texto cortado na TV)
BLOCK_MIN_H: dict[str, int] = {
    "kpis": 3,
    "resumo": 3,
    "chart": 5,
    "torres": 5,
    "banners": 5,
    "status": 4,
    "amanha": 2,
    "placas": 4,
    "prazo": 4,
    "pendencias": 4,
    "tabela": 4,
}


def blocks_for_key(key: str) -> list[dict[str, Any]]:
    defs = BLOCK_DEFS.get(key) or BLOCK_DEFS.get("distribuicao:agendamento") or []
    return [deepcopy(b) | {"visible": True} for b in defs]


def merge_blocks(saved: Any, key: str) -> list[dict[str, Any]]:
    base = blocks_for_key(key)
    if not isinstance(saved, list):
        return base
    by_id = {str(b.get("id")): b for b in saved if isinstance(b, dict) and b.get("id")}
    out = []
    for b in base:
        s = by_id.get(b["id"])
        if not s:
            out.append(b)
            continue
        min_h = int(BLOCK_MIN_H.get(b["id"], 1))
        min_w = 2 if b["id"] in ("kpis", "resumo", "chart", "torres", "banners") else 1
        h = max(min_h, min(GRID_ROWS, int(s.get("h", b["h"]))))
        w = max(min_w, min(GRID_COLS, int(s.get("w", b["w"]))))
        x = max(0, min(GRID_COLS - w, int(s.get("x", b["x"]))))
        y = max(0, min(GRID_ROWS - h, int(s.get("y", b["y"]))))
        out.append(
            {
                "id": b["id"],
                "label": b["label"],
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "visible": bool(s.get("visible", True)),
            }
        )
    return out


class BlockCanvas(QWidget):
    """Grade 12×12 com blocos arrastáveis e redimensionáveis (snap)."""

    changed = Signal()
    HANDLE = 14  # px no canto inferior direito

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(520, 360)
        self._blocks: list[dict[str, Any]] = []
        self._drag_id: str | None = None
        self._mode: str = "move"  # move | resize
        self._drag_origin = QPoint()
        self._drag_start_xy = (0, 0)
        self._drag_start_wh = (1, 1)
        self._hover_id: str | None = None
        self._hover_resize = False
        self._locked = False
        self.setMouseTracking(True)

    def set_blocks(self, blocks: list[dict[str, Any]]) -> None:
        self._blocks = deepcopy(blocks)
        self.update()

    def blocks(self) -> list[dict[str, Any]]:
        return deepcopy(self._blocks)

    def set_locked(self, locked: bool) -> None:
        self._locked = bool(locked)
        if locked:
            self.unsetCursor()

    def _cell(self) -> tuple[float, float]:
        w = max(1, self.width() - 8)
        h = max(1, self.height() - 8)
        return w / GRID_COLS, h / GRID_ROWS

    def _block_rect(self, b: dict[str, Any]) -> QRect:
        cw, ch = self._cell()
        return QRect(
            int(4 + b["x"] * cw),
            int(4 + b["y"] * ch),
            max(8, int(b["w"] * cw) - 2),
            max(8, int(b["h"] * ch) - 2),
        )

    def _handle_rect(self, b: dict[str, Any]) -> QRect:
        r = self._block_rect(b)
        return QRect(r.right() - self.HANDLE + 1, r.bottom() - self.HANDLE + 1, self.HANDLE, self.HANDLE)

    def _hit(self, pos: QPoint) -> dict[str, Any] | None:
        for b in reversed(self._blocks):
            if not b.get("visible", True):
                continue
            if self._block_rect(b).contains(pos):
                return b
        return None

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0b1220"))
        cw, ch = self._cell()
        pen = QPen(QColor("#1e293b"))
        pen.setWidth(1)
        p.setPen(pen)
        for c in range(GRID_COLS + 1):
            x = int(4 + c * cw)
            p.drawLine(x, 4, x, self.height() - 4)
        for r in range(GRID_ROWS + 1):
            y = int(4 + r * ch)
            p.drawLine(4, y, self.width() - 4, y)

        colors = {
            "kpis": "#166534",
            "resumo": "#166534",
            "chart": "#1e3a5f",
            "torres": "#1e3a5f",
            "banners": "#1e3a5f",
            "status": "#5b21b6",
            "prazo": "#5b21b6",
            "pendencias": "#5b21b6",
            "amanha": "#92400e",
            "placas": "#0f766e",
            "tabela": "#0f766e",
        }
        font = QFont("Segoe UI", 10)
        font.setBold(True)
        p.setFont(font)
        for b in self._blocks:
            if not b.get("visible", True):
                continue
            rect = self._block_rect(b)
            col = QColor(colors.get(str(b["id"]), "#334155"))
            if self._drag_id == b["id"]:
                col = col.lighter(120)
            p.fillRect(rect, col)
            p.setPen(QPen(QColor("#e2e8f0")))
            p.drawRect(rect)
            p.drawText(
                rect.adjusted(6, 4, -6, -4),
                Qt.AlignLeft | Qt.AlignTop,
                str(b.get("label") or b["id"]),
            )
            p.setPen(QPen(QColor("#94a3b8")))
            tiny = QFont("Segoe UI", 8)
            p.setFont(tiny)
            p.drawText(
                rect.adjusted(6, 0, -6, -4),
                Qt.AlignLeft | Qt.AlignBottom,
                f"{b['x']},{b['y']} · {b['w']}×{b['h']}",
            )
            # alça de redimensionar (canto inferior direito)
            hr = self._handle_rect(b)
            p.fillRect(hr, QColor("#fbbf24" if self._hover_id == b["id"] and self._hover_resize else "#cbd5e1"))
            p.setPen(QPen(QColor("#0f172a")))
            p.drawRect(hr)
            p.setFont(font)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._locked or event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        hit = self._hit(pos)
        if not hit:
            return
        self._drag_id = str(hit["id"])
        self._drag_origin = pos
        self._drag_start_xy = (int(hit["x"]), int(hit["y"]))
        self._drag_start_wh = (int(hit["w"]), int(hit["h"]))
        self._mode = "resize" if self._handle_rect(hit).contains(pos) else "move"

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._locked:
            self.unsetCursor()
            return

        # cursor / hover quando não arrasta
        if not self._drag_id:
            hit = self._hit(pos)
            self._hover_id = str(hit["id"]) if hit else None
            self._hover_resize = bool(hit and self._handle_rect(hit).contains(pos))
            if self._hover_resize:
                self.setCursor(Qt.SizeFDiagCursor)
            elif hit:
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.unsetCursor()
            self.update()
            return

        cw, ch = self._cell()
        delta = pos - self._drag_origin
        dx = int(round(delta.x() / cw))
        dy = int(round(delta.y() / ch))
        for b in self._blocks:
            if b["id"] != self._drag_id:
                continue
            if self._mode == "resize":
                min_h = int(BLOCK_MIN_H.get(str(b["id"]), 1))
                min_w = 2 if b["id"] in ("kpis", "resumo", "chart", "torres", "banners") else 1
                nw = max(min_w, min(GRID_COLS - int(b["x"]), self._drag_start_wh[0] + dx))
                nh = max(min_h, min(GRID_ROWS - int(b["y"]), self._drag_start_wh[1] + dy))
                if nw != b["w"] or nh != b["h"]:
                    b["w"], b["h"] = nw, nh
                    self.update()
                    self.changed.emit()
            else:
                nx = max(0, min(GRID_COLS - int(b["w"]), self._drag_start_xy[0] + dx))
                ny = max(0, min(GRID_ROWS - int(b["h"]), self._drag_start_xy[1] + dy))
                if nx != b["x"] or ny != b["y"]:
                    b["x"], b["y"] = nx, ny
                    self.update()
                    self.changed.emit()
            break

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        was_drag = self._drag_id is not None
        if event.button() == Qt.LeftButton:
            self._drag_id = None
            self._mode = "move"
            self.update()
            if was_drag:
                self.changed.emit()


class TvEditorDialog(QDialog):
    """Janela única: Parede TV + personalização visual do dashboard."""

    def __init__(self, parent: QWidget | None = None, layout: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ACE · Editor TV e Dashboard")
        self.setMinimumSize(720, 520)
        self.resize(900, 640)
        self._loading = False
        self._layout = normalize_layout(layout or load_layout())
        self._selected_slot = 1
        self._preview_ready = False
        self._preview_hash = ""
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(90)
        self._preview_timer.timeout.connect(self._push_preview_layout)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_wall_tab(), "Parede / TVs")
        self.tabs.addTab(self._build_dash_tab(), "Dashboard")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox()
        self.btn_save = buttons.addButton("Salvar e enviar às TVs", QDialogButtonBox.AcceptRole)
        self.btn_save.setObjectName("primary")
        buttons.addButton("Fechar", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._reload_forms()
        # Preview / grade de blocos desativados — layout TV fixo

    def resulting_layout(self) -> dict[str, Any]:
        return deepcopy(self._layout)

    # ── Parede ─────────────────────────────────────────────────────
    def _build_wall_tab(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        tip = QLabel(
            "Grade = modo normal (cada TV com seu setor).\n"
            "Modo parede = as 6 TVs viram pedaços de um setor."
        )
        tip.setWordWrap(True)
        tip.setObjectName("hint")
        lay.addWidget(tip)

        lay.addWidget(QLabel("Parede 2×3"))
        grid = QGridLayout()
        self._slot_btns: dict[int, QPushButton] = {}
        self._slot_group = QButtonGroup(self)
        self._slot_group.setExclusive(True)
        for sid in range(1, 7):
            btn = QPushButton(f"TV {sid}")
            btn.setCheckable(True)
            btn.setMinimumHeight(64)
            btn.clicked.connect(lambda _=False, s=sid: self._select_slot(s))
            self._slot_btns[sid] = btn
            self._slot_group.addButton(btn, sid)
            r, c = divmod(sid - 1, 3)
            grid.addWidget(btn, r, c)
        lay.addLayout(grid)

        form = QFormLayout()
        self.tv_sector = QComboBox()
        for sid, lab in SECTOR_LABELS.items():
            self.tv_sector.addItem(lab, sid)
        self.tv_sector.currentIndexChanged.connect(self._wall_slot_changed)

        self.tv_view = QComboBox()
        self._fill_tv_view_combo("distribuicao")
        self.tv_view.currentIndexChanged.connect(self._wall_slot_changed)

        self.tv_logo = QComboBox()
        self.tv_logo.addItem("Padrão do setor", "inherit")
        self.tv_logo.addItem("Mostrar logo", "on")
        self.tv_logo.addItem("Esconder logo", "off")
        self.tv_logo.currentIndexChanged.connect(self._wall_slot_changed)

        self.tv_margins = QComboBox()
        self.tv_margins.addItem("Padrão do setor", "inherit")
        self.tv_margins.addItem("Sem margem", "none")
        self.tv_margins.addItem("Com margem quadrada", "normal")
        self.tv_margins.currentIndexChanged.connect(self._wall_slot_changed)

        form.addRow("Setor desta TV", self.tv_sector)
        form.addRow("Tela / rotação", self.tv_view)
        form.addRow("Logo", self.tv_logo)
        form.addRow("Margens", self.tv_margins)
        lay.addLayout(form)

        self.tv_sync = QCheckBox("Sincronizar rotação nas TVs (parede Dist/Armazém)")
        self.tv_sync.stateChanged.connect(self._wall_global_changed)
        lay.addWidget(self.tv_sync)

        self.wall_status = QLabel("")
        lay.addWidget(self.wall_status)

        wall_row = QHBoxLayout()
        self.wall_sector = QComboBox()
        for sid, lab in SECTOR_LABELS.items():
            self.wall_sector.addItem(lab, sid)
        wall_row.addWidget(self.wall_sector, 1)
        b_on = QPushButton("Ativar parede")
        b_on.clicked.connect(self._wall_on)
        b_off = QPushButton("Voltar ao normal")
        b_off.clicked.connect(self._wall_off)
        wall_row.addWidget(b_on)
        wall_row.addWidget(b_off)
        lay.addLayout(wall_row)
        lay.addStretch(1)
        return wrap

    # ── Dashboard ──────────────────────────────────────────────────
    def _build_dash_tab(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)

        tip = QLabel(
            "Layout das TVs é fixo (otimizado para painel).\n"
            "Aqui você só ajusta: gráfico do agendamento, tamanho das letras, "
            "logo e margens por setor.\n"
            "Na aba Parede dá para sobrescrever logo/margem por TV. Depois Salvar."
        )
        tip.setWordWrap(True)
        tip.setObjectName("hint")
        lay.addWidget(tip)

        form = QFormLayout()
        self.dash_sector = QComboBox()
        for sid, lab in SECTOR_LABELS.items():
            self.dash_sector.addItem(lab, sid)
        self.dash_sector.currentIndexChanged.connect(self._dash_context_changed)

        self.dash_view = QComboBox()
        self._fill_dash_view_combo("distribuicao")
        self.dash_view.currentIndexChanged.connect(self._dash_context_changed)

        self.dash_chart = QComboBox()
        self.dash_chart.addItem("Torres", "towers")
        self.dash_chart.addItem("Pizza", "pizza")
        self.dash_chart.addItem("Barras laterais", "bars")
        self.dash_chart.currentIndexChanged.connect(self._dash_options_changed)

        self.dash_scale = QComboBox()
        self.dash_scale.addItem("Grande", "large")
        self.dash_scale.addItem("Normal", "normal")
        self.dash_scale.addItem("Compacto", "small")
        self.dash_scale.currentIndexChanged.connect(self._dash_options_changed)
        self.dash_scale.hide()  # escala de blocos desativada — layout TV fixo

        form.addRow("Setor", self.dash_sector)
        form.addRow("Tela", self.dash_view)
        form.addRow("Gráfico (agendamento)", self.dash_chart)

        font_row = QHBoxLayout()
        self.dash_font = QSlider(Qt.Horizontal)
        self.dash_font.setRange(70, 160)
        self.dash_font.setSingleStep(5)
        self.dash_font.setPageStep(10)
        self.dash_font.setTickInterval(10)
        self.dash_font.setTickPosition(QSlider.TicksBelow)
        self.dash_font.setValue(100)
        self.dash_font.valueChanged.connect(self._dash_font_changed)
        self.dash_font_lbl = QLabel("100%")
        self.dash_font_lbl.setMinimumWidth(44)
        font_row.addWidget(self.dash_font, 1)
        font_row.addWidget(self.dash_font_lbl)
        form.addRow("Letras na TV", font_row)

        self.dash_logo = QComboBox()
        self.dash_logo.addItem("Mostrar logo", "on")
        self.dash_logo.addItem("Esconder logo", "off")
        self.dash_logo.currentIndexChanged.connect(self._dash_chrome_changed)

        self.dash_margins = QComboBox()
        self.dash_margins.addItem("Sem margem (borda a borda)", "none")
        self.dash_margins.addItem("Com margem quadrada", "normal")
        self.dash_margins.currentIndexChanged.connect(self._dash_chrome_changed)

        form.addRow("Logo (setor)", self.dash_logo)
        form.addRow("Margens (setor)", self.dash_margins)
        lay.addLayout(form)

        # Editor de blocos / preview desativados (causavam instabilidade nas TVs)
        self.block_checks: dict[str, QCheckBox] = {}
        self.canvas = BlockCanvas()
        self.canvas.hide()
        self.preview: Any = None
        self.preview_status = QLabel("")
        self.preview_status.hide()
        self.dash_locked = QCheckBox("Layout fixado")
        self.dash_locked.setChecked(True)
        self.dash_locked.hide()

        note = QLabel(
            "As telas Coleta, Entrega, Agendamento e Armazém usam posição fixa "
            "calibrada para TV. Não é mais necessário arrastar blocos."
        )
        note.setWordWrap(True)
        note.setObjectName("hint")
        lay.addWidget(note)
        lay.addStretch(1)
        return wrap

    def _preview_route_hash(self) -> str:
        sector = str(self.dash_sector.currentData() or "distribuicao")
        view = str(self.dash_view.currentData() or "coleta")
        if sector == "armazem":
            v = view if view in ARM_VIEWS else "patio"
            return f"tv/armazem/{v}"
        if sector == "distribuicao":
            if view not in ("coleta", "entrega", "agendamento"):
                view = "agendamento"
            return f"tv/distribuicao/{view}"
        return f"tv/{sector}"

    def _boot_preview(self) -> None:
        if not self.preview:
            return
        try:
            port = _ensure_preview_server()
        except Exception as err:  # noqa: BLE001
            self.preview_status.setText(f"Preview offline: {err}")
            return
        self._preview_hash = self._preview_route_hash()
        url = QUrl(f"http://127.0.0.1:{port}/index.html#{self._preview_hash}")
        self.preview_status.setText(f"Carregando preview… #{self._preview_hash}")
        self.preview.setUrl(url)

    def _reload_preview(self) -> None:
        self._preview_ready = False
        self._boot_preview()

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._schedule_preview()

    def _on_preview_loaded(self, ok: bool) -> None:
        self._preview_ready = bool(ok)
        if not ok:
            self.preview_status.setText("Falha ao carregar o dashboard no preview.")
            return
        self.preview_status.setText(f"Preview · #{self._preview_hash}")
        self._push_preview_layout()

    def _schedule_preview(self) -> None:
        if not self.preview:
            return
        self._preview_timer.start()

    def _push_preview_layout(self) -> None:
        if not self.preview or not self._preview_ready:
            return
        if not self._loading:
            try:
                self._dash_options_changed()
                self._canvas_changed()
            except Exception:  # noqa: BLE001
                pass

        want = self._preview_route_hash()
        if want != self._preview_hash:
            self._preview_hash = want
            self._preview_ready = False
            try:
                port = _ensure_preview_server()
            except Exception as err:  # noqa: BLE001
                self.preview_status.setText(f"Preview offline: {err}")
                return
            self.preview.setUrl(QUrl(f"http://127.0.0.1:{port}/index.html#{want}"))
            return

        layout_json = json.dumps(self._layout, ensure_ascii=False)
        sector = json.dumps(str(self.dash_sector.currentData() or "distribuicao"))
        view = json.dumps(str(self.dash_view.currentData() or "coleta"))
        js = f"""
        (function() {{
          try {{
            window.__ACE_CRT_PREVIEW__ = true;
            window.__ACE_CRT_LAYOUT__ = {layout_json};
            TV_LAYOUT = window.__ACE_CRT_LAYOUT__;
            if (!TV_SLOT) TV_SLOT = 1;
            try {{ localStorage.setItem('ace_tv_slot', '1'); }} catch (e) {{}}
            if (typeof resolveTvEffective === 'function') {{
              TV_EFFECTIVE = resolveTvEffective(TV_LAYOUT, TV_SLOT || 1);
            }}
            var wantSector = {sector};
            var wantView = {view};
            var sameRoute = (typeof CURRENT_SECTOR !== 'undefined' && CURRENT_SECTOR === wantSector
              && typeof CURRENT_VIEW !== 'undefined' && CURRENT_VIEW === wantView
              && typeof TV_MODE !== 'undefined' && !!TV_MODE);
            document.body.classList.add('preview-layout');
            if (!sameRoute && typeof setSector === 'function') {{
              setSector(wantSector, {{
                syncHash: false,
                forceTv: true,
                view: wantView,
              }});
            }} else {{
              if (typeof applyTvChrome === 'function') applyTvChrome();
              if (typeof applyDashboardChrome === 'function') applyDashboardChrome();
              if (typeof applyArmViews === 'function' && wantSector === 'armazem') applyArmViews();
              if (typeof applyOpsLiveViews === 'function' && wantSector === 'distribuicao') applyOpsLiveViews();
            }}
            if (typeof applyTvChrome === 'function') applyTvChrome();
            if (typeof applyDashboardChrome === 'function') applyDashboardChrome();
            return 'ok';
          }} catch (err) {{
            return String(err);
          }}
        }})();
        """
        self.preview.page().runJavaScript(js, self._on_preview_js_done)

    def _on_preview_js_done(self, result: Any) -> None:
        if result and result != "ok":
            self.preview_status.setText(f"Preview: {result}")
        else:
            self.preview_status.setText(f"Preview ao vivo · #{self._preview_hash}")

    # ── data helpers ───────────────────────────────────────────────
    def _fill_tv_view_combo(self, sector: str) -> None:
        self.tv_view.blockSignals(True)
        self.tv_view.clear()
        if sector == "distribuicao":
            self.tv_view.addItem("Girar Coleta → Entrega → Agenda", "rotate")
            self.tv_view.addItem("Só Coleta", "coleta")
            self.tv_view.addItem("Só Entrega", "entrega")
            self.tv_view.addItem("Só Agendamento", "agendamento")
        elif sector == "armazem":
            self.tv_view.addItem("Girar Armazém ↔ Conferentes", "rotate")
            self.tv_view.addItem("Só Armazém (pátio)", "patio")
            self.tv_view.addItem("Só Conferentes", "conferentes")
        else:
            self.tv_view.addItem("Tela única", "fixed")
        self.tv_view.blockSignals(False)

    def _fill_dash_view_combo(self, sector: str) -> None:
        self.dash_view.blockSignals(True)
        cur = self.dash_view.currentData()
        self.dash_view.clear()
        if sector == "distribuicao":
            self.dash_view.addItem("Coleta", "coleta")
            self.dash_view.addItem("Entrega", "entrega")
            self.dash_view.addItem("Agendamento", "agendamento")
        elif sector == "armazem":
            self.dash_view.addItem("Armazém (pátio)", "patio")
            self.dash_view.addItem("Conferentes", "conferentes")
        else:
            self.dash_view.addItem("Padrão", "default")
        if cur is not None:
            i = self.dash_view.findData(cur)
            if i >= 0:
                self.dash_view.setCurrentIndex(i)
        self.dash_view.blockSignals(False)

    def _dash_key(self) -> str:
        sector = str(self.dash_sector.currentData() or "distribuicao")
        if sector == "distribuicao":
            return f"distribuicao:{self.dash_view.currentData() or 'agendamento'}"
        if sector == "armazem":
            return f"armazem:{self.dash_view.currentData() or 'patio'}"
        return sector

    def _ui_bucket(self) -> dict[str, Any]:
        sector = str(self.dash_sector.currentData() or "distribuicao")
        bucket = self._sector_defaults_bucket()
        if sector in ("distribuicao", "armazem"):
            default_view = "agendamento" if sector == "distribuicao" else "patio"
            view = str(self.dash_view.currentData() or default_view)
            views = bucket.setdefault("views", {})
            if view not in views or not isinstance(views.get(view), dict):
                views[view] = default_view_ui("towers")
            return views[view]
        if not isinstance(bucket.get("ui"), dict):
            bucket["ui"] = default_view_ui("towers")
        return bucket["ui"]

    def _sector_defaults_bucket(self) -> dict[str, Any]:
        sector = str(self.dash_sector.currentData() or "distribuicao")
        sd = self._layout.setdefault("sectorDefaults", {})
        bucket = sd.setdefault(
            sector,
            {"showLogo": True, "margins": "none", "ui": default_view_ui(), "views": {}},
        )
        if not isinstance(bucket, dict):
            bucket = {"showLogo": True, "margins": "none", "ui": default_view_ui(), "views": {}}
            sd[sector] = bucket
        bucket.setdefault("showLogo", True)
        bucket.setdefault("margins", "none")
        bucket.setdefault("views", {})
        return bucket

    def _reload_forms(self) -> None:
        self._loading = True
        self.tv_sync.setChecked(bool(self._layout.get("syncSwap", True)))
        ws = str(self._layout.get("wallSector") or "distribuicao")
        i = self.wall_sector.findData(ws)
        if i >= 0:
            self.wall_sector.setCurrentIndex(i)
        self._refresh_slot_labels()
        self._select_slot(self._selected_slot)
        idx = self.dash_view.findData("agendamento")
        if idx >= 0:
            self.dash_view.setCurrentIndex(idx)
        self._loading = False
        self._load_dash_into_form()

    def _refresh_slot_labels(self) -> None:
        wall = bool(self._layout.get("wallMode"))
        wall_sec = str(self._layout.get("wallSector") or "")
        for s in self._layout.get("slots") or []:
            sid = int(s["id"])
            btn = self._slot_btns.get(sid)
            if not btn:
                continue
            sec = str(s.get("sector") or "distribuicao")
            name = SECTOR_LABELS.get(sec, sec)
            if wall:
                btn.setText(f"TV {sid}\n▦ {SECTOR_LABELS.get(wall_sec, wall_sec)[:10]}")
            else:
                btn.setText(f"TV {sid}\n{name}")
            btn.setChecked(sid == self._selected_slot)
        if wall:
            self.wall_status.setText(
                f"Modo: PAREDE · {SECTOR_LABELS.get(wall_sec, wall_sec)}"
            )
        else:
            self.wall_status.setText("Modo: NORMAL · cada TV = setor da grade")

    def _select_slot(self, slot_id: int) -> None:
        self._selected_slot = int(slot_id)
        slot = next(
            (s for s in self._layout.get("slots") or [] if int(s["id"]) == self._selected_slot),
            None,
        )
        if not slot:
            return
        self._loading = True
        si = self.tv_sector.findData(str(slot.get("sector") or "distribuicao"))
        if si >= 0:
            self.tv_sector.setCurrentIndex(si)
        sector = str(slot.get("sector") or "distribuicao")
        self._fill_tv_view_combo(sector)
        self.tv_view.setEnabled(sector in ("distribuicao", "armazem"))
        if sector in ("distribuicao", "armazem"):
            if slot.get("mode") == "rotate":
                v = "rotate"
            else:
                v = str(slot.get("view") or ("patio" if sector == "armazem" else "coleta"))
            vi = self.tv_view.findData(v)
            if vi >= 0:
                self.tv_view.setCurrentIndex(vi)
        logo = slot.get("showLogo")
        lm = "inherit" if logo is None else ("on" if logo else "off")
        li = self.tv_logo.findData(lm)
        if li >= 0:
            self.tv_logo.setCurrentIndex(li)
        marg = slot.get("margins")
        mm = "inherit" if marg is None else ("none" if str(marg) == "none" else "normal")
        mi = self.tv_margins.findData(mm)
        if mi >= 0:
            self.tv_margins.setCurrentIndex(mi)
        self._loading = False
        self._refresh_slot_labels()

    def _wall_slot_changed(self) -> None:
        if self._loading:
            return
        slot = next(
            (s for s in self._layout.get("slots") or [] if int(s["id"]) == self._selected_slot),
            None,
        )
        if not slot:
            return
        sector = str(self.tv_sector.currentData() or "distribuicao")
        slot["sector"] = sector
        self._fill_tv_view_combo(sector)
        self.tv_view.setEnabled(sector in ("distribuicao", "armazem"))
        if sector == "distribuicao":
            vkey = str(self.tv_view.currentData() or "rotate")
            if vkey == "rotate":
                slot["mode"] = "rotate"
                slot["view"] = "coleta"
            else:
                slot["mode"] = "fixed"
                slot["view"] = vkey
        elif sector == "armazem":
            vkey = str(self.tv_view.currentData() or "rotate")
            if vkey == "rotate":
                slot["mode"] = "rotate"
                slot["view"] = "patio"
            else:
                slot["mode"] = "fixed"
                slot["view"] = vkey if vkey in ARM_VIEWS else "patio"
        else:
            slot["mode"] = "fixed"
            slot["view"] = "coleta"
        logo_mode = str(self.tv_logo.currentData() or "inherit")
        slot["showLogo"] = None if logo_mode == "inherit" else (logo_mode == "on")
        marg_mode = str(self.tv_margins.currentData() or "inherit")
        slot["margins"] = None if marg_mode == "inherit" else marg_mode
        self._refresh_slot_labels()

    def _wall_global_changed(self) -> None:
        if self._loading:
            return
        self._layout["syncSwap"] = self.tv_sync.isChecked()

    def _wall_on(self) -> None:
        sector = str(self.wall_sector.currentData() or "distribuicao")
        self._layout = wall_on(self._layout, sector)
        if sector in ("distribuicao", "armazem"):
            self.tv_sync.setChecked(True)
            self._layout["syncSwap"] = True
        self._refresh_slot_labels()

    def _wall_off(self) -> None:
        self._layout = wall_off(self._layout)
        self._refresh_slot_labels()

    def _load_dash_into_form(self) -> None:
        self._loading = True
        sector = str(self.dash_sector.currentData() or "distribuicao")
        self._fill_dash_view_combo(sector)
        self.dash_view.setEnabled(sector in ("distribuicao", "armazem"))
        ui = self._ui_bucket()
        # Layout fixo nas TVs — não carrega/persiste grade de blocos
        ui["blocks"] = []
        ui["locked"] = True
        ui["showKpis"] = True
        ui["showChart"] = True
        ui["showAmanha"] = True
        ui["showStatus"] = True

        ci = self.dash_chart.findData(str(ui.get("chart") or "towers"))
        if ci >= 0:
            self.dash_chart.setCurrentIndex(ci)
        si = self.dash_scale.findData(str(ui.get("scale") or "large"))
        if si >= 0:
            self.dash_scale.setCurrentIndex(si)
        try:
            fz = float(ui.get("fontZoom", 1.0) or 1.0)
        except (TypeError, ValueError):
            fz = 1.0
        pct = int(round(max(0.7, min(1.6, fz)) * 100))
        self.dash_font.blockSignals(True)
        self.dash_font.setValue(pct)
        self.dash_font.blockSignals(False)
        self.dash_font_lbl.setText(f"{pct}%")

        self.dash_locked.blockSignals(True)
        self.dash_locked.setChecked(True)
        self.dash_locked.blockSignals(False)
        self.canvas.set_locked(True)
        self.canvas.set_blocks([])

        chrome = self._sector_defaults_bucket()
        logo_key = "on" if chrome.get("showLogo", True) else "off"
        li = self.dash_logo.findData(logo_key)
        if li >= 0:
            self.dash_logo.setCurrentIndex(li)
        marg_key = "none" if str(chrome.get("margins") or "none") == "none" else "normal"
        mi = self.dash_margins.findData(marg_key)
        if mi >= 0:
            self.dash_margins.setCurrentIndex(mi)

        chart_ok = sector == "distribuicao" and str(self.dash_view.currentData()) == "agendamento"
        self.dash_chart.setEnabled(chart_ok)
        self.dash_font.setEnabled(True)
        self._loading = False

    def _set_dash_controls_enabled(self, enabled: bool) -> None:
        self.dash_chart.setEnabled(enabled)
        self.dash_font.setEnabled(True)

    def _dash_chrome_changed(self) -> None:
        if self._loading:
            return
        chrome = self._sector_defaults_bucket()
        chrome["showLogo"] = str(self.dash_logo.currentData() or "on") == "on"
        chrome["margins"] = str(self.dash_margins.currentData() or "none")

    def _dash_context_changed(self) -> None:
        if self._loading:
            return
        self._load_dash_into_form()

    def _dash_font_changed(self, value: int) -> None:
        self.dash_font_lbl.setText(f"{int(value)}%")
        if self._loading:
            return
        ui = self._ui_bucket()
        ui["fontZoom"] = round(max(70, min(160, int(value))) / 100.0, 2)
        ui["blocks"] = []

    def _dash_options_changed(self) -> None:
        if self._loading:
            return
        ui = self._ui_bucket()
        ui["chart"] = str(self.dash_chart.currentData() or "towers")
        ui["scale"] = "large"
        ui["fontZoom"] = round(max(70, min(160, int(self.dash_font.value()))) / 100.0, 2)
        ui["blocks"] = []
        ui["showKpis"] = True
        ui["showChart"] = True
        ui["showAmanha"] = True
        ui["showStatus"] = True
        ui["locked"] = True

    def _block_vis_changed(self) -> None:
        return

    def _canvas_changed(self) -> None:
        return

    def _dash_reset_blocks(self) -> None:
        ui = self._ui_bucket()
        ui["blocks"] = []
        self._load_dash_into_form()

    def _dash_lock_toggled(self) -> None:
        return

    def _dash_fix(self) -> None:
        self._dash_options_changed()
        ui = self._ui_bucket()
        ui["locked"] = True
        ui["blocks"] = []

    def _dash_unlock(self) -> None:
        return

    def _save(self) -> None:
        self._wall_slot_changed()
        self._wall_global_changed()
        self._dash_chrome_changed()
        self._dash_options_changed()
        for bucket in (self._layout.get("sectorDefaults") or {}).values():
            if not isinstance(bucket, dict):
                continue
            ui = bucket.get("ui")
            if isinstance(ui, dict):
                ui["blocks"] = []
                ui["locked"] = True
            views = bucket.get("views")
            if isinstance(views, dict):
                for vui in views.values():
                    if isinstance(vui, dict):
                        vui["blocks"] = []
                        vui["locked"] = True
        try:
            self._layout = save_layout(self._layout)
            ok, msg = push_layout_to_sheets(self._layout)
            if ok:
                QMessageBox.information(
                    self,
                    "ACE",
                    f"Salvo e enviado (v{self._layout.get('version')}).",
                )
                self.accept()
            else:
                QMessageBox.warning(
                    self,
                    "ACE",
                    f"Salvo neste PC (v{self._layout.get('version')}), "
                    f"mas a planilha falhou:\n{msg}",
                )
                self.accept()
        except Exception as err:  # noqa: BLE001
            QMessageBox.warning(self, "ACE", f"Falha ao salvar:\n{err}")
