"""
Editor de TV / Dashboard em janela separada (CRT).

- Aba Parede: grade 2×3, setor por TV, modo parede
- Aba Dashboard: setor + tela, gráfico/tamanho, canvas arrastável dos blocos
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt, Signal
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tv_layout import (
    SECTOR_LABELS,
    default_view_ui,
    load_layout,
    normalize_layout,
    push_layout_to_sheets,
    save_layout,
    wall_off,
    wall_on,
)

GRID_COLS = 12
GRID_ROWS = 12

BLOCK_DEFS: dict[str, list[dict[str, Any]]] = {
    "distribuicao:agendamento": [
        {"id": "kpis", "label": "KPIs / totais", "x": 0, "y": 0, "w": 12, "h": 2},
        {"id": "chart", "label": "Gráfico / torres", "x": 0, "y": 2, "w": 8, "h": 7},
        {"id": "status", "label": "Status / alertas", "x": 8, "y": 2, "w": 4, "h": 7},
        {"id": "amanha", "label": "Amanhã", "x": 0, "y": 9, "w": 12, "h": 3},
    ],
    "distribuicao:coleta": [
        {"id": "resumo", "label": "Resumo total", "x": 0, "y": 0, "w": 12, "h": 2},
        {"id": "placas", "label": "Placas", "x": 0, "y": 2, "w": 3, "h": 10},
        {"id": "torres", "label": "Torres", "x": 3, "y": 2, "w": 6, "h": 10},
        {"id": "prazo", "label": "Prazo", "x": 9, "y": 2, "w": 3, "h": 10},
    ],
    "distribuicao:entrega": [
        {"id": "resumo", "label": "Resumo entrega", "x": 0, "y": 0, "w": 12, "h": 2},
        {"id": "banners", "label": "Faixas %", "x": 0, "y": 2, "w": 8, "h": 10},
        {"id": "pendencias", "label": "Pendências", "x": 8, "y": 2, "w": 4, "h": 10},
    ],
    "armazem": [
        {"id": "kpis", "label": "KPIs armazém", "x": 0, "y": 0, "w": 12, "h": 2},
        {"id": "chart", "label": "Gráfico", "x": 0, "y": 2, "w": 7, "h": 10},
        {"id": "tabela", "label": "Tabela veículos", "x": 7, "y": 2, "w": 5, "h": 10},
    ],
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
        out.append(
            {
                "id": b["id"],
                "label": b["label"],
                "x": max(0, min(GRID_COLS - 1, int(s.get("x", b["x"])))),
                "y": max(0, min(GRID_ROWS - 1, int(s.get("y", b["y"])))),
                "w": max(1, min(GRID_COLS, int(s.get("w", b["w"])))),
                "h": max(1, min(GRID_ROWS, int(s.get("h", b["h"])))),
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
                nw = max(1, min(GRID_COLS - int(b["x"]), self._drag_start_wh[0] + dx))
                nh = max(1, min(GRID_ROWS - int(b["y"]), self._drag_start_wh[1] + dy))
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
        if event.button() == Qt.LeftButton:
            self._drag_id = None
            self._mode = "move"
            self.update()


class TvEditorDialog(QDialog):
    """Janela única: Parede TV + personalização visual do dashboard."""

    def __init__(self, parent: QWidget | None = None, layout: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ACE · Editor TV e Dashboard")
        self.setMinimumSize(980, 640)
        self.resize(1100, 720)
        self._loading = False
        self._layout = normalize_layout(layout or load_layout())
        self._selected_slot = 1

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_wall_tab(), "Parede / TVs")
        self.tabs.addTab(self._build_dash_tab(), "Dashboard")
        root.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox()
        self.btn_save = buttons.addButton("Salvar e enviar às TVs", QDialogButtonBox.AcceptRole)
        self.btn_save.setObjectName("primary")
        buttons.addButton("Fechar", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._reload_forms()

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
        self.tv_view.addItem("Girar Coleta → Entrega → Agenda", "rotate")
        self.tv_view.addItem("Só Coleta", "coleta")
        self.tv_view.addItem("Só Entrega", "entrega")
        self.tv_view.addItem("Só Agendamento", "agendamento")
        self.tv_view.currentIndexChanged.connect(self._wall_slot_changed)

        self.tv_logo = QComboBox()
        self.tv_logo.addItem("Padrão do setor", "inherit")
        self.tv_logo.addItem("Mostrar logo", "on")
        self.tv_logo.addItem("Esconder logo", "off")
        self.tv_logo.currentIndexChanged.connect(self._wall_slot_changed)

        form.addRow("Setor desta TV", self.tv_sector)
        form.addRow("Tela (Distribuição)", self.tv_view)
        form.addRow("Logo", self.tv_logo)
        lay.addLayout(form)

        self.tv_sync = QCheckBox("Sincronizar Coleta/Entrega/Agenda na parede Distribuição")
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
            "Escolha o setor (e a tela). Arraste o bloco para mover. "
            "Arraste o quadrado amarelo no canto para aumentar/diminuir. "
            "Depois Salvar. Fixar trava o layout."
        )
        tip.setWordWrap(True)
        tip.setObjectName("hint")
        lay.addWidget(tip)

        top = QHBoxLayout()
        form = QFormLayout()
        self.dash_sector = QComboBox()
        for sid, lab in SECTOR_LABELS.items():
            self.dash_sector.addItem(lab, sid)
        self.dash_sector.currentIndexChanged.connect(self._dash_context_changed)

        self.dash_view = QComboBox()
        self.dash_view.addItem("Coleta", "coleta")
        self.dash_view.addItem("Entrega", "entrega")
        self.dash_view.addItem("Agendamento", "agendamento")
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

        form.addRow("Setor", self.dash_sector)
        form.addRow("Tela", self.dash_view)
        form.addRow("Gráfico", self.dash_chart)
        form.addRow("Tamanho", self.dash_scale)
        top.addLayout(form, 1)

        vis = QVBoxLayout()
        self.block_checks: dict[str, QCheckBox] = {}
        vis.addWidget(QLabel("Blocos visíveis"))
        for bid, lab in (
            ("kpis", "KPIs"),
            ("resumo", "Resumo"),
            ("chart", "Gráfico"),
            ("torres", "Torres"),
            ("banners", "Faixas"),
            ("status", "Status"),
            ("amanha", "Amanhã"),
            ("placas", "Placas"),
            ("prazo", "Prazo"),
            ("pendencias", "Pendências"),
            ("tabela", "Tabela"),
        ):
            cb = QCheckBox(lab)
            cb.setChecked(True)
            cb.stateChanged.connect(self._block_vis_changed)
            self.block_checks[bid] = cb
            vis.addWidget(cb)
        vis.addStretch(1)
        top.addLayout(vis)
        lay.addLayout(top)

        self.canvas = BlockCanvas()
        self.canvas.changed.connect(self._canvas_changed)
        lay.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        self.dash_locked = QCheckBox("Layout fixado")
        self.dash_locked.stateChanged.connect(self._dash_lock_toggled)
        b_reset = QPushButton("Resetar posições")
        b_reset.clicked.connect(self._dash_reset_blocks)
        b_fix = QPushButton("Fixar")
        b_fix.clicked.connect(self._dash_fix)
        b_unlock = QPushButton("Desbloquear")
        b_unlock.clicked.connect(self._dash_unlock)
        row.addWidget(self.dash_locked)
        row.addStretch(1)
        row.addWidget(b_reset)
        row.addWidget(b_unlock)
        row.addWidget(b_fix)
        lay.addLayout(row)
        return wrap

    # ── data helpers ───────────────────────────────────────────────
    def _dash_key(self) -> str:
        sector = str(self.dash_sector.currentData() or "distribuicao")
        if sector == "distribuicao":
            return f"distribuicao:{self.dash_view.currentData() or 'agendamento'}"
        return sector

    def _ui_bucket(self) -> dict[str, Any]:
        sector = str(self.dash_sector.currentData() or "distribuicao")
        sd = self._layout.setdefault("sectorDefaults", {})
        bucket = sd.setdefault(
            sector,
            {"showLogo": True, "margins": "none", "ui": default_view_ui(), "views": {}},
        )
        if sector == "distribuicao":
            view = str(self.dash_view.currentData() or "agendamento")
            views = bucket.setdefault("views", {})
            if view not in views or not isinstance(views.get(view), dict):
                views[view] = default_view_ui("towers")
            return views[view]
        if not isinstance(bucket.get("ui"), dict):
            bucket["ui"] = default_view_ui("towers")
        return bucket["ui"]

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
        self.tv_view.setEnabled(sector == "distribuicao")
        if sector == "distribuicao":
            if slot.get("mode") == "rotate":
                v = "rotate"
            else:
                v = str(slot.get("view") or "coleta")
            vi = self.tv_view.findData(v)
            if vi >= 0:
                self.tv_view.setCurrentIndex(vi)
        logo = slot.get("showLogo")
        lm = "inherit" if logo is None else ("on" if logo else "off")
        li = self.tv_logo.findData(lm)
        if li >= 0:
            self.tv_logo.setCurrentIndex(li)
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
        self.tv_view.setEnabled(sector == "distribuicao")
        if sector == "distribuicao":
            vkey = str(self.tv_view.currentData() or "rotate")
            if vkey == "rotate":
                slot["mode"] = "rotate"
                slot["view"] = "coleta"
            else:
                slot["mode"] = "fixed"
                slot["view"] = vkey
        else:
            slot["mode"] = "fixed"
            slot["view"] = "coleta"
        logo_mode = str(self.tv_logo.currentData() or "inherit")
        slot["showLogo"] = None if logo_mode == "inherit" else (logo_mode == "on")
        self._refresh_slot_labels()

    def _wall_global_changed(self) -> None:
        if self._loading:
            return
        self._layout["syncSwap"] = self.tv_sync.isChecked()

    def _wall_on(self) -> None:
        sector = str(self.wall_sector.currentData() or "distribuicao")
        self._layout = wall_on(self._layout, sector)
        if sector == "distribuicao":
            self.tv_sync.setChecked(True)
            self._layout["syncSwap"] = True
        self._refresh_slot_labels()

    def _wall_off(self) -> None:
        self._layout = wall_off(self._layout)
        self._refresh_slot_labels()

    def _load_dash_into_form(self) -> None:
        self._loading = True
        sector = str(self.dash_sector.currentData() or "distribuicao")
        self.dash_view.setEnabled(sector == "distribuicao")
        ui = self._ui_bucket()
        key = self._dash_key()
        blocks = merge_blocks(ui.get("blocks"), key)
        ui["blocks"] = blocks

        ci = self.dash_chart.findData(str(ui.get("chart") or "towers"))
        if ci >= 0:
            self.dash_chart.setCurrentIndex(ci)
        si = self.dash_scale.findData(str(ui.get("scale") or "large"))
        if si >= 0:
            self.dash_scale.setCurrentIndex(si)

        known = {b["id"] for b in blocks}
        for bid, cb in self.block_checks.items():
            cb.blockSignals(True)
            if bid in known:
                cb.show()
                vis = next((b for b in blocks if b["id"] == bid), None)
                cb.setChecked(bool(vis and vis.get("visible", True)))
            else:
                cb.hide()
            cb.blockSignals(False)

        locked = bool(ui.get("locked"))
        self.dash_locked.setChecked(locked)
        self.canvas.set_locked(locked)
        self.canvas.set_blocks(blocks)
        self._set_dash_controls_enabled(not locked)
        self._loading = False

    def _set_dash_controls_enabled(self, enabled: bool) -> None:
        for w in (self.dash_chart, self.dash_scale, *self.block_checks.values()):
            w.setEnabled(enabled)
        self.canvas.set_locked(not enabled)

    def _dash_context_changed(self) -> None:
        if self._loading:
            return
        self._load_dash_into_form()

    def _dash_options_changed(self) -> None:
        if self._loading:
            return
        ui = self._ui_bucket()
        if ui.get("locked"):
            return
        ui["chart"] = str(self.dash_chart.currentData() or "towers")
        ui["scale"] = str(self.dash_scale.currentData() or "large")
        # sync legacy flags from blocks
        blocks = self.canvas.blocks()
        by = {b["id"]: b for b in blocks}
        ui["showKpis"] = bool(by.get("kpis", {}).get("visible", True)) if "kpis" in by else ui.get("showKpis", True)
        ui["showChart"] = bool(
            (by.get("chart") or by.get("torres") or by.get("banners") or {}).get("visible", True)
        )
        ui["showAmanha"] = bool(by.get("amanha", {}).get("visible", True)) if "amanha" in by else True
        ui["showStatus"] = bool(by.get("status", {}).get("visible", True)) if "status" in by else True

    def _block_vis_changed(self) -> None:
        if self._loading:
            return
        ui = self._ui_bucket()
        if ui.get("locked"):
            return
        blocks = self.canvas.blocks()
        for b in blocks:
            cb = self.block_checks.get(str(b["id"]))
            if cb is not None:
                b["visible"] = cb.isChecked()
        ui["blocks"] = blocks
        self.canvas.set_blocks(blocks)
        self._dash_options_changed()

    def _canvas_changed(self) -> None:
        if self._loading:
            return
        ui = self._ui_bucket()
        if ui.get("locked"):
            return
        ui["blocks"] = self.canvas.blocks()

    def _dash_reset_blocks(self) -> None:
        ui = self._ui_bucket()
        if ui.get("locked"):
            QMessageBox.information(self, "ACE", "Desbloqueie o layout para resetar.")
            return
        ui["blocks"] = blocks_for_key(self._dash_key())
        self._load_dash_into_form()

    def _dash_lock_toggled(self) -> None:
        if self._loading:
            return
        ui = self._ui_bucket()
        ui["locked"] = self.dash_locked.isChecked()
        self._set_dash_controls_enabled(not ui["locked"])

    def _dash_fix(self) -> None:
        self._dash_options_changed()
        self._canvas_changed()
        ui = self._ui_bucket()
        ui["locked"] = True
        self._loading = True
        self.dash_locked.setChecked(True)
        self._set_dash_controls_enabled(False)
        self._loading = False
        QMessageBox.information(self, "ACE", "Layout fixado. Use Salvar para enviar às TVs.")

    def _dash_unlock(self) -> None:
        ui = self._ui_bucket()
        ui["locked"] = False
        self._loading = True
        self.dash_locked.setChecked(False)
        self._set_dash_controls_enabled(True)
        self._loading = False

    def _save(self) -> None:
        self._wall_slot_changed()
        self._wall_global_changed()
        self._dash_options_changed()
        self._canvas_changed()
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
