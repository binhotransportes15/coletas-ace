"""
BINHO · ACE CRT — painel de gestão widescreen (cara de CMD).

Layout:
  esq  → cérebro de circuitos (acende ao rodar) + CPU/MEM/GPU + status
  centro → atalhos + log + prompt de comandos
  Menu (janela) → Configuração | Automação | Local | TV | Marca | Gestão

  python ace_crt.py
  ace.bat crt
"""
from __future__ import annotations

import math
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QPointF
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QLinearGradient,
    QRadialGradient,
    QBrush,
    QTextCursor,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from crt_bridge import append_log, publish, read_log_since, read_status, STATUS_PATH

_ROOT = Path(__file__).resolve().parent
_CUBES = _ROOT / "assets" / "cubes-binho.png"
_BRAIN = _ROOT / "assets" / "brain-circuit.png"
_LOGO = _ROOT / "assets" / "logo-binho.png"
_FONT_SHARE_TECH = _ROOT / "assets" / "fonts" / "ShareTechMono-Regular.ttf"

# Fonte robótica/hacker do CRT (Share Tech Mono + fallbacks de terminal)
CRT_FONT_FAMILY = "Share Tech Mono"


def load_crt_font() -> str:
    """Carrega Share Tech Mono do assets; retorna a família efetiva."""
    global CRT_FONT_FAMILY
    try:
        if _FONT_SHARE_TECH.is_file():
            fid = QFontDatabase.addApplicationFont(str(_FONT_SHARE_TECH))
            if fid >= 0:
                families = QFontDatabase.applicationFontFamilies(fid)
                if families:
                    CRT_FONT_FAMILY = str(families[0])
                    return CRT_FONT_FAMILY
    except Exception:
        pass
    # fallback se o TTF não estiver presente
    for name in ("Cascadia Mono", "Consolas", "Courier New", "monospace"):
        if QFontDatabase.hasFamily(name):
            CRT_FONT_FAMILY = name
            return CRT_FONT_FAMILY
    CRT_FONT_FAMILY = "monospace"
    return CRT_FONT_FAMILY


def crt_font(point_size: int = 11, *, bold: bool = False) -> QFont:
    f = QFont(CRT_FONT_FAMILY, point_size)
    f.setStyleHint(QFont.Monospace)
    f.setFixedPitch(True)
    if bold:
        f.setBold(True)
    return f

# Cubos oficiais (fallback animado se PNG sumir)
_CUBE_COLORS = (
    QColor("#8cc63f"),
    QColor("#fff200"),
    QColor("#ed1c24"),
    QColor("#29abe2"),
)

# Temas do CRT (fundo + texto + accentos)
CRT_THEMES: dict[str, dict[str, object]] = {
    "binho": {
        "label": "Escuro BINHO",
        "bg": "#050805",
        "panel": "#0a100c",
        "line": "#1a3d28",
        "text": "#39ff14",
        "dim": "#6b8f71",
        "muted": "#3d5c45",
        "input_bg": "#07110a",
        "input_text": "#8b1a1a",
        "btn_bg": "#07140c",
        "btn_hover": "#0d2416",
        "btn_press": "#11301c",
        "btn_dis_bd": "#102016",
        "sel": "#1a5c36",
        "prog_bg": "#07110a",
        "chunk0": "#009245",
        "chunk1": "#00ff66",
        "chunk2": "#8cc63f",
        "scan": True,
    },
    "painel": {
        "label": "Azul painel",
        "bg": "#050a14",
        "panel": "#0a121e",
        "line": "#1a2f4a",
        "text": "#7dd3fc",
        "dim": "#94a3b8",
        "muted": "#64748b",
        "input_bg": "#071018",
        "input_text": "#e2e8f0",
        "btn_bg": "#0c1624",
        "btn_hover": "#132338",
        "btn_press": "#1a3250",
        "btn_dis_bd": "#0f1a28",
        "sel": "#0c4a6e",
        "prog_bg": "#071018",
        "chunk0": "#0369a1",
        "chunk1": "#38bdf8",
        "chunk2": "#7dd3fc",
        "scan": True,
    },
    "ops": {
        "label": "Verde ops",
        "bg": "#080b09",
        "panel": "#0e1511",
        "line": "#2a4032",
        "text": "#c4ff4d",
        "dim": "#9caf88",
        "muted": "#5c6f58",
        "input_bg": "#0a100c",
        "input_text": "#e8f5c8",
        "btn_bg": "#101a14",
        "btn_hover": "#1a2a1e",
        "btn_press": "#243828",
        "btn_dis_bd": "#121a14",
        "sel": "#3f6212",
        "prog_bg": "#0a100c",
        "chunk0": "#65a30d",
        "chunk1": "#a3e635",
        "chunk2": "#d9f99d",
        "scan": True,
    },
    "claro": {
        "label": "Claro",
        "bg": "#e8edf2",
        "panel": "#f5f7fa",
        "line": "#c5d0dc",
        "text": "#0f172a",
        "dim": "#475569",
        "muted": "#64748b",
        "input_bg": "#ffffff",
        "input_text": "#0f172a",
        "btn_bg": "#ffffff",
        "btn_hover": "#e2e8f0",
        "btn_press": "#cbd5e1",
        "btn_dis_bd": "#dbe3ec",
        "sel": "#bae6fd",
        "prog_bg": "#dde5ee",
        "chunk0": "#0284c7",
        "chunk1": "#0ea5e9",
        "chunk2": "#38bdf8",
        "scan": False,
    },
    "fosco": {
        "label": "Escuro fosco",
        # Neutro (sem verde): blur Windows + painéis cinza
        "bg": "transparent",
        "panel": "rgba(14, 18, 24, 160)",
        "line": "rgba(180, 190, 205, 55)",
        "text": "#f4f7fb",
        "dim": "#a8b4c4",
        "muted": "#7a8798",
        "input_bg": "rgba(8, 12, 18, 230)",
        "input_text": "#eef3f8",
        "btn_bg": "rgba(22, 28, 38, 180)",
        "btn_hover": "rgba(42, 56, 76, 210)",
        "btn_press": "rgba(58, 78, 104, 230)",
        "btn_dis_bd": "rgba(50, 58, 70, 90)",
        "sel": "rgba(56, 120, 160, 140)",
        "prog_bg": "rgba(6, 10, 16, 200)",
        "chunk0": "#38bdf8",
        "chunk1": "#7dd3fc",
        "chunk2": "#bae6fd",
        # Fundo opaco sob texto que atualiza (evita “fantasma”)
        "label_bg": "rgba(12, 16, 22, 235)",
        "log_bg": "#0a0e14",
        "scan": False,
        "frost": True,
        # Fallback neutro AABBGGRR (cinza, sem matiz) — AA baixo = vê o desktop
        "acrylic_tint": 0x381A1A1A,
        "meter_h": 18,
    },
    "circuitos": {
        "label": "Circuitos (cérebro)",
        "bg": "#030712",
        "panel": "#070f1c",
        "line": "#164e63",
        "text": "#67e8f9",
        "dim": "#94a3b8",
        "muted": "#475569",
        "input_bg": "#06101c",
        "input_text": "#e0f2fe",
        "btn_bg": "#0c1a2e",
        "btn_hover": "#12304a",
        "btn_press": "#1a4568",
        "btn_dis_bd": "#0f2030",
        "sel": "#0e7490",
        "prog_bg": "#06101c",
        "chunk0": "#0891b2",
        "chunk1": "#22d3ee",
        "chunk2": "#fde047",
        "scan": True,
        "brain_glow": True,
    },
}


def frost_params(
    alpha_pct: int | None = None, blur_pct: int | None = None
) -> dict[str, float | int]:
    """Transparência/fosco → opacidade da janela + tint/painéis.

    No Win11 o acrylic do sistema quase não muda com tint — a transparência
    real vem de setWindowOpacity (opacity) + painéis mais abertos.
    """
    a = 55 if alpha_pct is None else max(0, min(100, int(alpha_pct)))
    b = 70 if blur_pct is None else max(0, min(100, int(blur_pct)))
    # Opacidade da janela: 0% → 0.97 · 100% → 0.62 (sem sumir o conteúdo)
    opacity = 0.97 - (0.97 - 0.62) * (a / 100.0)
    # AA acrylic (Win10); no Win11 é só reforço
    aa = int(round(0x50 - (0x50 - 0x10) * (a / 100.0)))
    tint = (aa << 24) | 0x1A1A1A
    if b <= 0:
        state = 2  # sem blur
    elif b < 40:
        state = 3  # mica / blur leve
    else:
        state = 4  # acrylic
    # Painéis: transparência sobe, mas nunca fica ilegível (mín. ~80)
    panel_a = int(round(210 - 120 * (a / 100.0)))
    label_a = int(round(200 - 100 * (a / 100.0)))
    root_a = int(round(120 - 90 * (a / 100.0)))
    return {
        "alpha": a,
        "blur": b,
        "tint": tint,
        "state": state,
        "panel_a": max(80, panel_a),
        "label_a": max(90, label_a),
        "root_a": max(25, root_a),
        "opacity": round(max(0.55, min(1.0, opacity)), 3),
    }

DEFAULT_CRT_THEME = "circuitos"



# Zonas do cerebro -> id das barrinhas do CRT
_BRAIN_REGIONS: dict[str, tuple[tuple[float, float], ...]] = {
    "dist": ((0.32, 0.34), (0.40, 0.28), (0.36, 0.42), (0.44, 0.36)),
    "78": ((0.52, 0.30), (0.58, 0.36), (0.54, 0.44)),
    "31": ((0.46, 0.18), (0.52, 0.22), (0.48, 0.26)),
    "73": ((0.64, 0.42), (0.70, 0.48), (0.66, 0.56)),
    "455": ((0.38, 0.56), (0.46, 0.60), (0.42, 0.66)),
    "mapa": ((0.50, 0.74), (0.56, 0.78), (0.48, 0.82)),
}
_SECTOR_TRACE_COLORS: dict[str, str] = {
    "dist": "#8cc63f",
    "78": "#29abe2",
    "31": "#fff200",
    "73": "#ed1c24",
    "455": "#fbb03b",
    "mapa": "#009245",
}

# Setores do automático: relatórios SSW + o que cada um faz (painel AGORA)
_SECTOR_GUIDE: tuple[dict[str, str], ...] = (
    {
        "id": "dist",
        "title": "Distribuição",
        "flag": "dist_in_loop",
        "interval": "dist_intervalo",
        "reports": "50 · 103 · 36 · 225",
        "blurb": "Coletas do dia, torres/limites, entregas/romaneios e agendamentos.",
    },
    {
        "id": "78",
        "title": "Armazém",
        "flag": "armazem_in_loop",
        "interval": "armazem_intervalo",
        "reports": "78 · 177",
        "blurb": "Pátio/veículos e ranking de conferentes (nomes via 607).",
    },
    {
        "id": "31",
        "title": "Pendência",
        "flag": "pendencia_in_loop",
        "interval": "pendencia_intervalo",
        "reports": "31",
        "blurb": "Códigos de pendência e ofensores/SLA da operação.",
    },
    {
        "id": "73",
        "title": "Contratação",
        "flag": "contratacao_in_loop",
        "interval": "contratacao_intervalo",
        "reports": "73 → 200",
        "blurb": "Fretes do dia (073) cruzados com manifesto/filiais 200.",
    },
    {
        "id": "455",
        "title": "Emissão",
        "flag": "emissao_in_loop",
        "interval": "emissao_intervalo",
        "reports": "455",
        "blurb": "CTEs, frete, picos e expedidores emitidos no dia.",
    },
    {
        "id": "mapa",
        "title": "Mapa",
        "flag": "mapa_in_loop",
        "interval": "mapa_intervalo",
        "reports": "36 + CyberMap",
        "blurb": "Rotas e placas na TV (tempo de troca: /tempo mapa).",
    },
)


class BinhoCubesWidget(QWidget):
    """Cerebro BINHO parado — circuitos acendem por setor / automacao."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Altura flexível: evita esmagar o painel esquerdo em janela “solta”
        self.setMinimumHeight(140)
        self.setMaximumHeight(260)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self._t = 0.0
        self._busy = False
        self._full = False
        self._active: set[str] = set()
        self._fill = QColor("#030712")
        self._green_glow = False
        self._cyan_glow = True
        self._hidden_brand = False
        self._pm = QPixmap()
        self._brain_rect = (0.0, 0.0, 0.0, 0.0)
        self._accent = QColor("#67e8f9")
        self._glow = QColor("#22d3ee")
        self._tint_alpha = 175
        self._theme_sector_colors: dict[str, QColor] = {}
        self.reload_brand_asset()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def reload_brand_asset(self) -> None:
        path = Path()
        try:
            from brand import resolve_crt_pixmap_path, load_brand

            b = load_brand()
            # Cerebro do CRT sempre visivel; logo das dashboards e independente
            self._hidden_brand = False
            path = resolve_crt_pixmap_path(b)
        except Exception:
            self._hidden_brand = False
            path = _BRAIN if _BRAIN.is_file() else _CUBES
        self._pm = QPixmap(str(path)) if path.is_file() else QPixmap()
        if self._pm.isNull() and _BRAIN.is_file():
            self._pm = QPixmap(str(_BRAIN))
        if self._pm.isNull() and _CUBES.is_file():
            self._pm = QPixmap(str(_CUBES))
        self.update()

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.update()

    def set_activity(self, sectors: set[str] | list[str] | None, *, full: bool = False) -> None:
        self._active = {str(s) for s in (sectors or []) if str(s)}
        self._full = bool(full)
        self._busy = self._full or bool(self._active)
        self.update()

    def brain_anchor(self, sector: str | None = None) -> QPointF:
        """Ponto do setor no cerebro (no interno) — saida real do circuito."""
        bx, by, bw, bh = self._brain_rect
        if bw <= 1 or bh <= 1:
            return QPointF(self.width() * 0.85, self.height() * 0.55)
        sid = str(sector or "")
        nodes = _BRAIN_REGIONS.get(sid)
        if nodes:
            # no mais a direita do setor = ponto de conexao
            nx, ny = max(nodes, key=lambda p: p[0])
            return QPointF(bx + nx * bw, by + ny * bh)
        # CPU/MEM/GPU / barra principal: ancora na borda direita media
        y_map = {
            "cpu": 0.40, "mem": 0.52, "gpu": 0.64, "_main": 0.72,
        }
        ny = y_map.get(sid, 0.55)
        return QPointF(bx + bw * 0.88, by + bh * ny)

    def set_fill_color(self, color: QColor) -> None:
        self._fill = QColor(color)
        self.update()

    def set_green_glow(self, on: bool) -> None:
        self._green_glow = bool(on)
        self.update()

    def set_cyan_glow(self, on: bool) -> None:
        self._cyan_glow = bool(on)
        self.update()

    def set_theme_palette(
        self,
        *,
        accent: QColor | str,
        glow: QColor | str | None = None,
        accents: list[QColor | str] | None = None,
        tint_alpha: int = 175,
        fill: QColor | None = None,
    ) -> None:
        """Recolore o cerebro e os circuitos conforme o tema do CRT."""
        self._accent = QColor(accent)
        self._glow = QColor(glow) if glow is not None else QColor(self._accent)
        self._tint_alpha = max(0, min(160, int(tint_alpha)))
        if fill is not None:
            self._fill = QColor(fill)
        cols = [QColor(c) for c in (accents or []) if c]
        if not cols:
            cols = [self._accent, self._glow, QColor("#fde047")]
        while len(cols) < 6:
            cols.append(cols[len(cols) % max(1, len(cols))])
        order = ("dist", "78", "31", "73", "455", "mapa")
        self._theme_sector_colors = {sid: cols[i] for i, sid in enumerate(order)}
        self.update()

    def _color_for_sector(self, sid: str) -> QColor:
        if sid in self._theme_sector_colors:
            return QColor(self._theme_sector_colors[sid])
        hex_c = _SECTOR_TRACE_COLORS.get(sid, None)
        if hex_c:
            return QColor(hex_c)
        return QColor(self._accent)

    def _themed_pixmap(self, src: QPixmap) -> QPixmap:
        """Mantem luminosidade da arte e aplica a matiz do tema (respeita transparencia)."""
        if src.isNull() or self._tint_alpha <= 0:
            return src
        out = QPixmap(src.size())
        out.fill(Qt.transparent)
        qp = QPainter(out)
        qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        qp.drawPixmap(0, 0, src)
        # SourceAtop: so pinta onde ja tem pixel (nao cria quadro)
        qp.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        tint = QColor(self._accent)
        tint.setAlpha(self._tint_alpha)
        qp.fillRect(out.rect(), tint)
        qp.setCompositionMode(QPainter.CompositionMode_Plus)
        qp.setOpacity(0.12)
        glow = QColor(self._glow)
        glow.setAlpha(70)
        qp.fillRect(out.rect(), glow)
        qp.end()
        return out

    def _tick(self) -> None:
        self._t += 0.033
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()
        # sem retangulo solido — cerebro flutua no painel
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 0))

        if self._hidden_brand:
            self._brain_rect = (0.0, 0.0, 0.0, 0.0)
            p.setPen(QColor(100, 116, 139, 160))
            p.setFont(crt_font(10))
            p.drawText(self.rect(), Qt.AlignCenter, "marca oculta")
            p.end()
            return

        t = self._t
        busy = self._busy
        full = self._full
        active = set(self._active)
        if full:
            active = set(_BRAIN_REGIONS.keys())

        if not self._pm.isNull():
            # ocupa quase todo o widget (sem caixa)
            target = int(min(w - 4, h - 4))
            scaled = self._pm.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            themed = self._themed_pixmap(scaled)
            x = (w - themed.width()) / 2.0
            y = (h - themed.height()) / 2.0
            self._brain_rect = (x, y, float(themed.width()), float(themed.height()))
            p.setOpacity(0.98 if busy else 0.94)
            p.drawPixmap(int(x), int(y), themed)
            p.setOpacity(1.0)
            self._paint_region_glow(p, x, y, themed.width(), themed.height(), active, full, t)
            self._paint_circuit_traces(p, x, y, themed.width(), themed.height(), active, full, t, busy)
            self._paint_circuit_nodes(p, x, y, themed.width(), themed.height(), active, full, t, busy)
        else:
            self._paint_fallback_brain(p, w, h, t, busy, active, full)

        # glow suave sob o cerebro (nao preenche um quadro)
        if self._cyan_glow or self._green_glow or busy:
            bx, by, bw, bh = self._brain_rect
            if bw > 1:
                glow = QRadialGradient(bx + bw * 0.5, by + bh * 0.55, max(bw, bh) * 0.65)
                gc = QColor(self._glow)
                gc.setAlpha(55 if busy else 28)
                glow.setColorAt(0.0, gc)
                glow.setColorAt(1.0, QColor(0, 0, 0, 0))
                p.fillRect(int(bx), int(by), int(bw), int(bh), glow)
        p.end()

    def _paint_region_glow(
        self, p: QPainter, bx: float, by: float, bw: float, bh: float,
        active: set[str], full: bool, t: float,
    ) -> None:
        if not active and not full:
            return
        p.setPen(Qt.NoPen)
        for sid, nodes in _BRAIN_REGIONS.items():
            if sid not in active:
                continue
            col = self._color_for_sector(sid)
            pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 3.2 + (hash(sid) % 7)))
            for nx, ny in nodes:
                cx = bx + nx * bw
                cy = by + ny * bh
                r = 10 + 6 * pulse
                p.setBrush(QColor(col.red(), col.green(), col.blue(), int(55 + 50 * pulse)))
                p.drawEllipse(QPointF(cx, cy), r, r)

    def _paint_circuit_traces(
        self, p: QPainter, bx: float, by: float, bw: float, bh: float,
        active: set[str], full: bool, t: float, busy: bool,
    ) -> None:
        paths: list[tuple[str, list[tuple[float, float]]]] = [
            ("dist", [(0.28, 0.40), (0.36, 0.40), (0.36, 0.30), (0.44, 0.30)]),
            ("78", [(0.48, 0.34), (0.56, 0.34), (0.56, 0.42), (0.62, 0.42)]),
            ("31", [(0.44, 0.24), (0.50, 0.24), (0.50, 0.18), (0.56, 0.18)]),
            ("73", [(0.60, 0.48), (0.68, 0.48), (0.68, 0.56), (0.74, 0.56)]),
            ("455", [(0.34, 0.58), (0.42, 0.58), (0.42, 0.66), (0.50, 0.66)]),
            ("mapa", [(0.46, 0.72), (0.54, 0.72), (0.54, 0.80), (0.60, 0.80)]),
            ("_", [(0.30, 0.50), (0.40, 0.50), (0.40, 0.60), (0.52, 0.60), (0.52, 0.70)]),
            ("_", [(0.58, 0.28), (0.66, 0.28), (0.66, 0.38)]),
        ]
        for sid, pts in paths:
            lit = full or (sid in active) or (sid == "_" and busy)
            base_a = 200 if lit else (70 if busy else 40)
            col = self._color_for_sector(sid) if sid != "_" else QColor(self._accent)
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), base_a), 1.05 if lit else 0.8))
            poly = [QPointF(bx + x * bw, by + y * bh) for x, y in pts]
            for i in range(len(poly) - 1):
                p.drawLine(poly[i], poly[i + 1])
            if len(poly) < 2:
                continue
            segs = []
            total = 0.0
            for i in range(len(poly) - 1):
                L = math.hypot(poly[i + 1].x() - poly[i].x(), poly[i + 1].y() - poly[i].y()) or 1.0
                segs.append((poly[i], poly[i + 1], L))
                total += L
            speed = 0.35 if lit else 0.12
            pos = (t * speed * total + (0 if sid == "_" else hash(sid) % 50)) % total
            acc = 0.0
            for a, bpt, L in segs:
                if acc + L >= pos:
                    u = (pos - acc) / L
                    px = a.x() + (bpt.x() - a.x()) * u
                    py = a.y() + (bpt.y() - a.y()) * u
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(col.red(), col.green(), col.blue(), 230 if lit else 120))
                    p.drawEllipse(QPointF(px, py), 3.2 if lit else 2.2, 3.2 if lit else 2.2)
                    p.setBrush(QColor(255, 255, 255, 160 if lit else 80))
                    p.drawEllipse(QPointF(px, py), 1.4, 1.4)
                    break
                acc += L

    def _paint_circuit_nodes(
        self, p: QPainter, bx: float, by: float, bw: float, bh: float,
        active: set[str], full: bool, t: float, busy: bool,
    ) -> None:
        p.setPen(Qt.NoPen)
        for sid, nodes in _BRAIN_REGIONS.items():
            lit_region = full or sid in active
            col = self._color_for_sector(sid)
            for i, (nx, ny) in enumerate(nodes):
                phase = t * (4.5 if lit_region else 1.4) + i * 0.9
                blink = math.sin(phase) > (0.05 if lit_region else 0.65)
                if not blink and not lit_region:
                    continue
                cx = bx + nx * bw
                cy = by + ny * bh
                r = 3.6 if lit_region else 2.2
                p.setBrush(QColor(col.red(), col.green(), col.blue(), 230 if lit_region else 130))
                p.drawEllipse(QPointF(cx, cy), r, r)
                if lit_region:
                    p.setBrush(QColor(col.red(), col.green(), col.blue(), 45))
                    p.drawEllipse(QPointF(cx, cy), r * 2.6, r * 2.6)

    def _paint_fallback_brain(
        self, p: QPainter, w: int, h: int, t: float, busy: bool,
        active: set[str], full: bool,
    ) -> None:
        cx, cy = w / 2.0, h / 2.0
        rw, rh = 52.0, 40.0
        self._brain_rect = (cx - rw, cy - rh, rw * 2, rh * 2)
        ac = QColor(self._accent)
        ac.setAlpha(200)
        p.setPen(QPen(ac, 2))
        p.setBrush(QColor(self._fill))
        p.drawEllipse(QPointF(cx, cy), rw, rh)
        self._paint_circuit_traces(p, cx - rw, cy - rh, rw * 2, rh * 2, active, full, t, busy)
        self._paint_circuit_nodes(p, cx - rw, cy - rh, rw * 2, rh * 2, active, full, t, busy)

    @staticmethod
    def _draw_iso_cube(p: QPainter, x: float, y: float, s: float, color: QColor) -> None:
        top = QColor(color).lighter(130)
        left = QColor(color)
        right = QColor(color).darker(125)
        hx, hy = s * 0.55, s * 0.32
        pts_top = [QPointF(x, y - hy), QPointF(x + hx, y), QPointF(x, y + hy), QPointF(x - hx, y)]
        pts_left = [QPointF(x - hx, y), QPointF(x, y + hy), QPointF(x, y + hy + s * 0.55), QPointF(x - hx, y + s * 0.55)]
        pts_right = [QPointF(x + hx, y), QPointF(x, y + hy), QPointF(x, y + hy + s * 0.55), QPointF(x + hx, y + s * 0.55)]
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        for pts, col in ((pts_top, top), (pts_left, left), (pts_right, right)):
            p.setBrush(QBrush(col))
            p.drawPolygon(QPolygonF(pts))


class CircuitBusOverlay(QWidget):
    """Linhas de circuito do cerebro ate as barras de progresso."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._t = 0.0
        self._brain: BinhoCubesWidget | None = None
        self._meters: dict[str, QWidget] = {}
        self._main_bar: QWidget | None = None
        self._active: set[str] = set()
        self._full = False
        self._theme_sector_colors: dict[str, QColor] = {}
        self._accent = QColor("#67e8f9")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_theme_palette(
        self,
        *,
        accent: QColor | str,
        accents: list[QColor | str] | None = None,
    ) -> None:
        self._accent = QColor(accent)
        cols = [QColor(c) for c in (accents or []) if c]
        if not cols:
            cols = [self._accent]
        while len(cols) < 6:
            cols.append(cols[len(cols) % max(1, len(cols))])
        order = ("dist", "78", "31", "73", "455", "mapa")
        self._theme_sector_colors = {sid: cols[i] for i, sid in enumerate(order)}
        self.update()

    def _color_for_sector(self, sid: str) -> QColor:
        if sid in self._theme_sector_colors:
            return QColor(self._theme_sector_colors[sid])
        if sid in _SECTOR_TRACE_COLORS:
            return QColor(_SECTOR_TRACE_COLORS[sid])
        return QColor(self._accent)

    def bind(self, *, brain: BinhoCubesWidget, meters: dict[str, QWidget], main_bar: QWidget | None = None) -> None:
        self._brain = brain
        self._meters = dict(meters or {})
        self._main_bar = main_bar
        self.update()

    def set_activity(self, sectors: set[str] | list[str] | None, *, full: bool = False) -> None:
        self._active = {str(s) for s in (sectors or []) if str(s)}
        self._full = bool(full)
        self.update()

    def _tick(self) -> None:
        self._t += 0.033
        self.update()

    def _map_pt(self, widget: QWidget, local: QPointF) -> QPointF:
        gp = widget.mapTo(self, local.toPoint())
        return QPointF(float(gp.x()), float(gp.y()))

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._brain is None:
            return
        active = set(self._meters.keys()) if self._full else set(self._active)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        t = self._t
        # SEMPRE liga o cerebro a todas as barrinhas (fraco no idle, forte se ativo)
        targets: list[tuple[str, QWidget]] = []
        for sid, meter in self._meters.items():
            if meter is None or not meter.isVisible():
                continue
            bar = getattr(meter, "bar_widget", None) or getattr(meter, "_bar", None) or meter
            targets.append((sid, bar))
        if self._main_bar is not None and self._main_bar.isVisible():
            targets.append(("_main", self._main_bar))
        for sid, dest in targets:
            try:
                if dest.width() < 8 or dest.height() < 4:
                    continue
                if self._brain.width() < 40 or self._brain.height() < 40:
                    continue
                start = self._map_pt(self._brain, self._brain.brain_anchor(sid))
                end = self._map_pt(dest, QPointF(max(6.0, dest.width() * 0.08), dest.height() / 2.0))
            except Exception:
                continue
            # Coord inválida / painel ainda não layoutado (janela solta / resize)
            if (
                start.x() < -20
                or end.x() < -20
                or start.y() < -20
                or end.y() < -20
                or start.x() > self.width() + 40
                or end.x() > self.width() + 40
            ):
                continue
            if abs(end.x() - start.x()) < 12 and abs(end.y() - start.y()) < 8:
                continue
            col = self._color_for_sector(sid)
            lit = self._full or sid in active or (sid == "_main" and (self._full or bool(active)))
            # sai do NO do setor → borda do cerebro → degrau fino → barrinha
            exit_x = start.x() + 14.0
            mid_x = start.x() + max(28.0, (end.x() - start.x()) * 0.42)
            pts = [
                start,
                QPointF(exit_x, start.y()),
                QPointF(mid_x, start.y()),
                QPointF(mid_x, end.y()),
                end,
            ]
            base_a = 200 if lit else 85
            width = 1.15 if lit else 0.85
            # trilha base (fina)
            pen = QPen(QColor(col.red(), col.green(), col.blue(), base_a), width)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            for i in range(len(pts) - 1):
                p.drawLine(pts[i], pts[i + 1])
            # no no ponto do setor
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 220 if lit else 130))
            p.drawEllipse(start, 2.6 if lit else 1.8, 2.6 if lit else 1.8)
            # pulso viajando (sempre)
            segs = []
            total = 0.0
            for i in range(len(pts) - 1):
                L = math.hypot(pts[i + 1].x() - pts[i].x(), pts[i + 1].y() - pts[i].y()) or 1.0
                segs.append((pts[i], pts[i + 1], L))
                total += L
            speed = 0.65 if lit else 0.28
            pos = (t * speed * total + (hash(sid) % 40)) % total
            acc = 0.0
            for a0, b0, L in segs:
                if acc + L >= pos:
                    u = (pos - acc) / L
                    px = a0.x() + (b0.x() - a0.x()) * u
                    py = a0.y() + (b0.y() - a0.y()) * u
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(col.red(), col.green(), col.blue(), 230 if lit else 150))
                    p.drawEllipse(QPointF(px, py), 2.4 if lit else 1.7, 2.4 if lit else 1.7)
                    p.setBrush(QColor(255, 255, 255, 200 if lit else 120))
                    p.drawEllipse(QPointF(px, py), 1.0, 1.0)
                    break
                acc += L
            # conector fino na barrinha
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 220 if lit else 120))
            p.drawEllipse(end, 2.8 if lit else 2.0, 2.8 if lit else 2.0)
        p.end()


class SysMeterRow(QWidget):
    """Barra CPU / MEM / GPU estilo terminal (barra + % à direita, sem texto duplicado)."""

    def __init__(self, title: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._title = QLabel(title)
        self._title.setObjectName("sysMeterTitle")
        self._title.setFixedWidth(44)
        self._bar = QProgressBar()
        self._bar.setObjectName("sysMeter")
        self._bar.setRange(0, 1000)
        self._bar.setValue(0)
        # % só no label da direita — evita texto duplicado / fantasma no fosco
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(16)
        self._val = QLabel("—")
        self._val.setObjectName("sysMeterVal")
        self._val.setFixedWidth(48)
        self._val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self._title)
        lay.addWidget(self._bar, 1)
        lay.addWidget(self._val)
        self._accent = accent
        self._track = "rgba(0, 0, 0, 140)"
        self._track_border = "rgba(255, 255, 255, 28)"
        self._apply_chunk(accent)

    @property
    def bar_widget(self) -> QProgressBar:
        return self._bar

    def apply_chrome(self, *, height: int = 16, track: str = "rgba(0,0,0,140)", border: str = "rgba(255,255,255,28)") -> None:
        self._bar.setFixedHeight(max(12, int(height)))
        self._track = track
        self._track_border = border
        self._apply_chunk(self._accent)

    def _apply_chunk(self, accent: str) -> None:
        self._bar.setStyleSheet(
            f"""
            QProgressBar#sysMeter {{
                background: {self._track};
                border: 1px solid {self._track_border};
                border-radius: 6px;
                text-align: center;
                color: transparent;
                font-size: 1px;
            }}
            QProgressBar#sysMeter::chunk {{
                background: {accent};
                border-radius: 5px;
            }}
            """
        )

    def set_pct(self, pct: float | None, warn: float = 75.0, crit: float = 90.0) -> None:
        if pct is None:
            self._bar.setValue(0)
            self._val.setText("—")
            self._apply_chunk(self._accent)
            return
        v = max(0.0, min(100.0, float(pct)))
        self._bar.setValue(int(round(v * 10)))
        self._val.setText(f"{v:.0f}%")
        color = self._accent
        if v >= crit:
            color = "#ef4444"
        elif v >= warn:
            color = "#f59e0b"
        self._apply_chunk(color)


class QuickCmdButton(QPushButton):
    """Botão de atalho com título + código SSW + o que faz."""

    def __init__(
        self,
        title: str,
        code: str,
        blurb: str,
        cmd: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("quickCmd")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(f"{title} ({code})\n{blurb}\nComando: {cmd}")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        lab_code = QLabel(code)
        lab_code.setObjectName("quickCmdCode")
        lab_title = QLabel(title)
        lab_title.setObjectName("quickCmdTitle")
        top.addWidget(lab_code)
        top.addWidget(lab_title, 1)
        root.addLayout(top)

        lab_blurb = QLabel(blurb)
        lab_blurb.setObjectName("quickCmdBlurb")
        lab_blurb.setWordWrap(True)
        root.addWidget(lab_blurb)

        for w in (lab_code, lab_title, lab_blurb):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)


class SectorMeterRow(QWidget):
    """Barrinha de automação por setor ( Dist / Armazém / … )."""

    _STATE_COLOR = {
        "run": "#38bdf8",
        "wait": "#8cc63f",
        "due": "#f59e0b",
        "ok": "#22c55e",
        "err": "#ef4444",
        "off": "#64748b",
    }

    def __init__(self, sector_id: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sector_id = sector_id
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self._title = QLabel(title)
        self._title.setObjectName("sysMeterTitle")
        self._title.setFixedWidth(108)
        self._bar = QProgressBar()
        self._bar.setObjectName("sysMeter")
        self._bar.setRange(0, 1000)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(14)
        self._val = QLabel("—")
        self._val.setObjectName("sysMeterVal")
        self._val.setFixedWidth(44)
        self._val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self._title)
        top.addWidget(self._bar, 1)
        top.addWidget(self._val)
        root.addLayout(top)
        self._detail = QLabel("—")
        self._detail.setObjectName("hint")
        self._detail.setWordWrap(False)
        root.addWidget(self._detail)
        self._accent = self._STATE_COLOR["off"]
        self._track = "rgba(0, 0, 0, 140)"
        self._track_border = "rgba(255, 255, 255, 28)"
        self._apply_chunk(self._accent)

    @property
    def bar_widget(self) -> QProgressBar:
        return self._bar

    def apply_chrome(self, *, height: int = 14, track: str = "rgba(0,0,0,140)", border: str = "rgba(255,255,255,28)") -> None:
        self._bar.setFixedHeight(max(10, int(height)))
        self._track = track
        self._track_border = border
        self._apply_chunk(self._accent)

    def _apply_chunk(self, accent: str) -> None:
        self._accent = accent
        self._bar.setStyleSheet(
            f"""
            QProgressBar#sysMeter {{
                background: {self._track};
                border: 1px solid {self._track_border};
                border-radius: 6px;
                text-align: center;
                color: transparent;
                font-size: 1px;
            }}
            QProgressBar#sysMeter::chunk {{
                background: {accent};
                border-radius: 5px;
            }}
            """
        )

    def set_row(self, row: dict) -> None:
        state = str(row.get("state") or "off").lower()
        enabled = bool(row.get("enabled", False))
        pct = float(row.get("pct") or 0.0)
        detail = str(row.get("detail") or "")
        interval = str(row.get("interval") or "")
        label = str(row.get("label") or self._title.text())
        self._title.setText(label)
        if not enabled:
            self._bar.setValue(0)
            self._val.setText("off")
            self._detail.setText(detail or "fora do automático")
            self._apply_chunk(self._STATE_COLOR["off"])
            self.setEnabled(False)
            return
        self.setEnabled(True)
        v = max(0.0, min(100.0, pct))
        self._bar.setValue(int(round(v * 10)))
        if state == "run":
            self._val.setText(f"{v:.0f}%")
        elif state in {"due", "wait"}:
            self._val.setText("0%")
        elif state == "ok":
            # Entre ciclos a barra fica em 0; rótulo OK (não 100% fantasma)
            self._val.setText("OK" if v < 1 else f"{v:.0f}%")
        elif state == "err":
            self._val.setText("ERR")
        else:
            self._val.setText(f"{v:.0f}%")
        suffix = f" · {interval}" if interval else ""
        self._detail.setText((detail + suffix)[:110])
        self._apply_chunk(self._STATE_COLOR.get(state, self._STATE_COLOR["wait"]))


def _windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


def apply_windows_acrylic(
    hwnd: int,
    enable: bool,
    tint_aabbggrr: int = 0x401A1A1A,
    accent_state: int = 4,
) -> bool:
    """Blur/acrylic no Windows.

    Win11: SYSTEMBACKDROP acrylic (sem WA_TranslucentBackground — senão fica preto).
    Win10: AccentPolicy blur/acrylic com tint.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        win11 = _windows_build() >= 22000

        class MARGINS(ctypes.Structure):
            _fields_ = (
                ("cxLeftWidth", ctypes.c_int),
                ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int),
                ("cyBottomHeight", ctypes.c_int),
            )

        class ACCENTPOLICY(ctypes.Structure):
            _fields_ = (
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            )

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = (
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            )

        hwnd_w = wintypes.HWND(hwnd)
        margins = MARGINS(-1, -1, -1, -1) if enable else MARGINS(0, 0, 0, 0)
        try:
            dwmapi.DwmExtendFrameIntoClientArea(hwnd_w, ctypes.byref(margins))
        except Exception:
            pass

        # Limpa accent antigo
        accent = ACCENTPOLICY()
        accent.AccentState = 0
        accent.AccentFlags = 0
        accent.GradientColor = 0
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19
        data.Data = ctypes.addressof(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        fn = user32.SetWindowCompositionAttribute
        fn.argtypes = (wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA))
        fn.restype = wintypes.BOOL
        try:
            fn(hwnd_w, ctypes.byref(data))
        except Exception:
            pass

        ok = False
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        if enable and win11:
            try:
                dark = ctypes.c_int(1)
                dwmapi.DwmSetWindowAttribute(
                    hwnd_w,
                    DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(dark),
                    ctypes.sizeof(dark),
                )
            except Exception:
                pass
            # Só SYSTEMBACKDROP no Win11 (Accent+translucent = preto sólido).
            # 3=acrylic · 2=mica · 1=none
            if int(accent_state) <= 2:
                backdrop = ctypes.c_int(1)
            elif int(accent_state) == 3:
                backdrop = ctypes.c_int(2)
            else:
                backdrop = ctypes.c_int(3)
            try:
                hr = dwmapi.DwmSetWindowAttribute(
                    hwnd_w,
                    DWMWA_SYSTEMBACKDROP_TYPE,
                    ctypes.byref(backdrop),
                    ctypes.sizeof(backdrop),
                )
                ok = hr == 0
            except Exception:
                ok = False
        elif enable:
            # Win10: AccentPolicy é o caminho certo
            try:
                backdrop = ctypes.c_int(1)
                dwmapi.DwmSetWindowAttribute(
                    hwnd_w,
                    DWMWA_SYSTEMBACKDROP_TYPE,
                    ctypes.byref(backdrop),
                    ctypes.sizeof(backdrop),
                )
            except Exception:
                pass
            accent.AccentState = int(accent_state)
            accent.AccentFlags = 0x20 | 0x40 | 0x80 | 0x100
            accent.GradientColor = int(tint_aabbggrr) & 0xFFFFFFFF
            ok = bool(fn(hwnd_w, ctypes.byref(data)))
            if not ok and int(accent_state) != 3:
                accent.AccentState = 3
                ok = bool(fn(hwnd_w, ctypes.byref(data)))
        else:
            try:
                backdrop = ctypes.c_int(1)
                dwmapi.DwmSetWindowAttribute(
                    hwnd_w,
                    DWMWA_SYSTEMBACKDROP_TYPE,
                    ctypes.byref(backdrop),
                    ctypes.sizeof(backdrop),
                )
                ok = True
            except Exception:
                ok = False
        return ok
    except Exception:
        return False


def build_crt_stylesheet(
    theme_id: str = DEFAULT_CRT_THEME,
    *,
    frost_alpha: int | None = None,
    frost_blur: int | None = None,
) -> str:
    base = CRT_THEMES.get(theme_id) or CRT_THEMES[DEFAULT_CRT_THEME]
    t = dict(base)
    frost = bool(t.get("frost"))
    log_bg = str(t.get("log_bg") or "#0a0e14")
    if frost:
        fp = frost_params(frost_alpha, frost_blur)
        pa, la, ra = fp["panel_a"], fp["label_a"], fp["root_a"]
        t["panel"] = f"rgba(14, 18, 24, {pa})"
        t["label_bg"] = f"rgba(12, 16, 22, {la})"
        t["input_bg"] = f"rgba(8, 12, 18, {min(255, la + 5)})"
        t["btn_bg"] = f"rgba(22, 28, 38, {min(255, pa + 20)})"
        t["prog_bg"] = f"rgba(6, 10, 16, {min(255, la - 10)})"
        t["bg"] = f"rgba(10, 14, 20, {ra})"
        t["_frost_tint"] = fp["tint"]
        t["_frost_state"] = fp["state"]
    radius = "12px" if frost else "0"
    label_bg = str(t.get("label_bg") or ("rgba(12,16,22,235)" if frost else "transparent"))
    font_stack = (
        f"'{CRT_FONT_FAMILY}', 'Cascadia Mono', Consolas, 'Courier New', monospace"
    )
    # No fosco: raiz com véu leve (blur DWM aparece atrás). Sem WA_Translucent no Win11.
    if frost:
        root_rule = f"""
QWidget#crtRoot {{
    background: {t['bg']};
}}
QSplitter, QSplitter::handle {{
    background: transparent;
}}
QWidget {{
    color: {t['text']};
    font-family: {font_stack};
    font-size: 12px;
    letter-spacing: 0.3px;
    background: transparent;
}}
/* NÃO incluir QAbstractScrollArea aqui — o QTextEdit#crtLog fica fantasma */
QTabWidget, QTabWidget::pane, QScrollArea {{
    background: transparent;
}}
"""
    else:
        root_rule = f"""
QWidget {{
    background: {t['bg']};
    color: {t['text']};
    font-family: {font_stack};
    font-size: 12px;
    letter-spacing: 0.3px;
}}
"""
    return f"""
{root_rule}
QFrame#panel, QFrame#side {{
    background: {t['panel']};
    border: 1px solid {t['line']};
    border-radius: {radius};
}}
QLabel#title {{
    color: {t['text']};
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 2px;
    background: {label_bg if frost else 'transparent'};
}}
QLabel#mode {{
    color: {t['dim']};
    font-size: 11px;
    letter-spacing: 1px;
    background: {label_bg if frost else 'transparent'};
}}
QLabel#status {{
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 3px;
    background: {label_bg if frost else 'transparent'};
    padding: 3px 6px;
}}
QLabel#detail, QLabel#hint {{
    color: {t['dim']};
    font-size: 11px;
    letter-spacing: 0.4px;
    background: {label_bg if frost else 'transparent'};
}}
QLabel#section {{
    color: {t['text']};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 3px 5px;
    background: {label_bg if frost else 'transparent'};
}}
QLabel#foot {{
    color: {t['muted']};
    font-size: 9px;
    background: {label_bg if frost else 'transparent'};
}}
QLabel#sysHost {{
    color: {t['text']};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    background: {label_bg if frost else 'transparent'};
}}
QLabel#sysHostSub {{
    color: {t['dim']};
    font-size: 9px;
    background: {label_bg if frost else 'transparent'};
}}
QLabel#sysMeterTitle {{
    color: {t['dim']};
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
    background: {label_bg if frost else 'transparent'};
}}
QLabel#sysMeterVal {{
    color: {t['text']};
    font-size: 10px;
    font-weight: 700;
    background: {label_bg if frost else 'transparent'};
    font-variant-numeric: tabular-nums;
}}
QProgressBar {{
    background: {t['prog_bg']};
    border: 1px solid {t['line']};
    border-radius: {radius};
    text-align: center;
    color: {t['text']};
    height: {"16px" if frost else "14px"};
    font-size: 9px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {t['chunk0']}, stop:0.55 {t['chunk1']}, stop:1 {t['chunk2']});
    border-radius: {radius};
}}
QPushButton {{
    background: {t['btn_bg']};
    color: {t['text']};
    border: 1px solid {t['line']};
    border-radius: {radius};
    padding: 5px 8px;
    font-size: 12px;
    text-align: left;
}}
QPushButton:hover {{
    background: {t['btn_hover']};
    border-color: {t['text']};
}}
QPushButton:pressed {{
    background: {t['btn_press']};
}}
QPushButton:disabled {{
    color: {t['muted']};
    border-color: {t['btn_dis_bd']};
}}
QPushButton#primary {{
    background: {t['btn_hover']};
    font-weight: 700;
}}
QPushButton#quickCmd {{
    background: {t['btn_bg']};
    border: 1px solid {t['line']};
    border-radius: {radius};
    text-align: left;
    padding: 0;
}}
QPushButton#quickCmd:hover {{
    background: {t['btn_hover']};
    border-color: {t['text']};
}}
QPushButton#quickCmd:pressed {{
    background: {t['btn_press']};
}}
QLabel#quickCmdCode {{
    color: {t['text']};
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.5px;
    min-width: 42px;
    background: transparent;
}}
QLabel#quickCmdTitle {{
    color: {t['text']};
    font-size: 12px;
    font-weight: 700;
    background: transparent;
}}
QLabel#quickCmdBlurb {{
    color: {t['dim']};
    font-size: 10px;
    background: transparent;
}}
QWidget#lockOverlay {{
    background: rgba(3, 7, 18, 210);
}}
QFrame#lockCard {{
    background: {t['panel']};
    border: 1px solid {t['line']};
    border-radius: {radius};
}}
QLabel#lockIcon {{
    font-size: 72px;
    background: transparent;
}}
QLabel#lockTitle {{
    color: {t['text']};
    font-size: 18px;
    font-weight: 800;
    letter-spacing: 2px;
    background: transparent;
}}
QLabel#lockErr {{
    color: #ed1c24;
    font-size: 11px;
    background: transparent;
}}
QPushButton#menuBtn {{
    min-width: 64px;
    padding: 5px 10px;
    text-align: center;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLineEdit, QTextEdit, QComboBox {{
    background: {t['input_bg']};
    color: {t['input_text']};
    border: 1px solid {t['line']};
    border-radius: {radius};
    selection-background-color: {t['sel']};
    padding: 3px 5px;
    font-size: 12px;
}}
QTextEdit {{
    background: {log_bg if frost else t['input_bg']};
    color: {t['text']};
}}
QTextEdit#crtLog, QTextEdit#crtLog::viewport {{
    background-color: {log_bg};
    color: {t['text']};
    border: 1px solid {t['line']};
}}
QAbstractScrollArea::viewport {{
    background: {log_bg if frost else t['input_bg']};
}}
QComboBox QAbstractItemView {{
    background: {t['input_bg']};
    color: {t['input_text']};
    selection-background-color: {t['sel']};
    border: 1px solid {t['line']};
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border-color: {t['text']};
}}
QCheckBox {{
    color: {t['dim']};
    spacing: 6px;
    background: transparent;
    font-size: 11px;
}}
QCheckBox::indicator {{
    width: 13px;
    height: 13px;
    border: 1px solid {t['line']};
    background: {t['input_bg']};
    border-radius: 3px;
}}
QCheckBox::indicator:checked {{
    background: {t['text']};
}}
QTabWidget::pane {{
    border: 1px solid {t['line']};
    background: {t['panel']};
    border-radius: {radius};
}}
QTabBar::tab {{
    background: {t['input_bg']};
    color: {t['dim']};
    border: 1px solid {t['line']};
    border-radius: {radius};
    padding: 6px 11px;
    margin-right: 2px;
    font-size: 11px;
}}
QTabBar::tab:selected {{
    color: {t['text']};
    background: {t['panel']};
    border-bottom-color: {t['panel']};
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QSplitter::handle {{
    background: {t['line']};
    width: 2px;
}}
QSplitter {{
    background: transparent;
}}
"""


# Compat: constantes antigas = tema BINHO (widgets que ainda referem)
BG = str(CRT_THEMES[DEFAULT_CRT_THEME]["bg"])
PANEL = str(CRT_THEMES[DEFAULT_CRT_THEME]["panel"])
LINE = str(CRT_THEMES[DEFAULT_CRT_THEME]["line"])
NEON = str(CRT_THEMES[DEFAULT_CRT_THEME]["text"])
DIM = str(CRT_THEMES[DEFAULT_CRT_THEME]["dim"])
MUTED = str(CRT_THEMES[DEFAULT_CRT_THEME]["muted"])
WARN = "#c4ff4d"
OFF = "#8b1a1a"
SCAN = QColor(0, 0, 0, 90)

# Rótulos amigáveis (sem nomes técnicos de config)
_FIELD_LABELS: dict[str, str] = {
    "url": "Endereço do sistema",
    "domain": "Empresa",
    "document": "Documento",
    "user": "Usuário",
    "password": "Senha",
    "unit": "Unidades",
    "coleta_option": "Opção de coleta",
    "entrega_option": "Opção de entrega",
    "periodo_modo": "Tipo de período",
    "auto_baixar_ao_abrir": "Baixar ao abrir",
    "loop_intervalo": "Intervalo padrão (fallback)",
    "ciclo_paralelo": "Rodar setores juntos (paralelo)",
    "modo_local": "Modo local (sem planilha)",
    "dashboard_lan": "Dashboard na rede (LAN)",
    "dashboard_port": "Porta do dashboard",
    "enable_sheets": "Enviar à planilha",
    "apps_script_url": "Endereço da conexão",
    "apps_script_token": "Chave da conexão",
    "google_sheet_id": "Código da planilha",
    "enable_github_publish": "Publicar site automaticamente",
    "publish_target": "Destino TV (sites|github|local|auto)",
    "google_sites_url": "URL do Google Sites",
    "github_repo": "Pasta do site",
    "github_branch": "Linha do site",
    "github_token_env": "Nome da chave do site",
    "dist_in_loop": "Distribuição no automático",
    "dist_intervalo": "Tempo · distribuição",
    "armazem_in_loop": "Armazém no automático",
    "armazem_intervalo": "Tempo · armazém",
    "pendencia_in_loop": "Pendência no automático",
    "pendencia_intervalo": "Tempo · pendência",
    "contratacao_in_loop": "Contratação no automático",
    "contratacao_intervalo": "Tempo · contratação",
    "emissao_in_loop": "Emissão no automático",
    "emissao_intervalo": "Tempo · emissão",
    "mapa_in_loop": "Mapa no automático",
    "mapa_intervalo": "Tempo · mapa",
    "reciclagem_in_loop": "Reciclagem no automático",
    "reciclagem_intervalo": "Tempo · reciclagem",
    "dashboard_port": "Porta do dashboard",
    "crt_lock_password": "Senha do cadeado (bloquear painel)",
    "headless": "Ocultar navegador",
}

# Digitação livre → comando interno
_FRIENDLY_CMDS: dict[str, str] = {
    "coletas": "50",
    "coleta": "50",
    "limites": "103",
    "limite": "103",
    "entregas": "36",
    "entrega": "36",
    "agendamentos": "225",
    "agendamento": "225",
    "agenda": "225",
    "armazem": "78",
    "armazém": "78",
    "conferentes": "177",
    "nomes": "607",
    "pendencia": "31",
    "pendência": "31",
    "pendencias": "31",
    "pendências": "31",
    "contratacao": "73",
    "contratação": "73",
    "emissao": "455",
    "emissão": "455",
    "mapa": "mapa",
    "mapa operacional": "mapa",
    "mapaop": "mapa",
    "maparotas": "mapa",
    "cybermap": "mapa",
    "reciclagem": "reciclagem",
    "recicla": "reciclagem",
    "019": "reciclagem",
    "081": "reciclagem",
    "atualizar tudo": "sync",
    "sincronizar": "sync",
    "planilha": "sync",
    "abrir painel": "local",
    "painel": "local",
    "telas locais": "local",
    "modo local": "local",
    "local": "local",
    "rede": "lan",
    "wifi": "lan",
    "lan": "lan",
    "atualizacao continua": "automatica",
    "atualização contínua": "automatica",
    "ajuda": "help",
    "publicar": "push",
    "publicar no site": "push",
    "parar": "parar",
    "stop": "parar",
    "bloquear": "bloquear",
    "lock": "bloquear",
    "cadeado": "bloquear",
    "log": "/log",
    "/log": "/log",
    "limpar": "limpar",
    "cls": "cls",
    "clear": "clear",
    "barras": "/bars",
    "barra": "/bars",
    "/bars": "/bars",
    "/barras": "/bars",
    "tela cheia": "tela cheia",
    "tela": "tela cheia",
    "fullscreen": "tela cheia",
    "menu": "menu",
    "config": "menu",
}


def _field_label(key: str) -> str:
    return _FIELD_LABELS.get(key, key.replace("_", " ").capitalize())


def _resolve_friendly_cmd(raw: str) -> str:
    text = (raw or "").strip()
    key = text.lower()
    if key in _FRIENDLY_CMDS:
        return _FRIENDLY_CMDS[key]
    return text


ERR = "#ed1c24"


class Scanlines(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._enabled = True

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        self.setVisible(self._enabled)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._enabled:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        pen = QPen(QColor(0, 0, 0, 40))
        p.setPen(pen)
        for y in range(0, self.height(), 3):
            p.drawLine(0, y, self.width(), y)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(0, 0, 0, 50))
        grad.setColorAt(0.12, QColor(0, 0, 0, 0))
        grad.setColorAt(0.88, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 70))
        p.fillRect(self.rect(), QBrush(grad))
        p.end()


class CmdWorker(QThread):
    finished_ok = Signal(str, object)  # message, payload
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, raw: str, payload: dict) -> None:
        super().__init__()
        self.raw = raw
        self.payload = dict(payload or {})

    def run(self) -> None:
        try:
            from ace_cmd import execute_line
            from ace_stop import clear_stop, LoopStopped

            clear_stop()
            self.status.emit(f"exec · {self.raw}")
            msg, payload = execute_line(self.raw, self.payload)
            self.finished_ok.emit(msg or "OK", payload)
        except Exception as err:  # noqa: BLE001
            try:
                from ace_stop import LoopStopped, stop_requested

                if isinstance(err, LoopStopped) or stop_requested():
                    self.finished_ok.emit("Parado pelo usuário.", self.payload)
                    return
            except Exception:
                pass
            if "parado pelo usuário" in str(err).lower():
                self.finished_ok.emit("Parado pelo usuário.", self.payload)
                return
            self.failed.emit(f"ERRO: {err}\n{traceback.format_exc(limit=4)}")


class AutoLoopWorker(QThread):
    """Atualização contínua dentro do CRT — sem janela CMD extra."""

    finished_ok = Signal(str)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, interval_arg: str | None = None) -> None:
        super().__init__()
        self.interval_arg = interval_arg
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True
        try:
            from ace_stop import request_stop

            request_stop(force_browsers=True)
        except Exception:
            pass

    def run(self) -> None:
        try:
            from ace_loop import resolve_interval_sec, run_loop
            from ace_stop import clear_stop, stop_requested
            from config import load_settings

            clear_stop()
            self._stop = False
            cfg = load_settings()
            sec = resolve_interval_sec(self.interval_arg, settings_intervalo=cfg.loop_intervalo)
            code = run_loop(
                interval_sec=sec,
                should_stop=lambda: self._stop or stop_requested(),
                quiet_banner=True,
            )
            if self._stop or stop_requested():
                self.finished_ok.emit("Atualização contínua parada.")
            else:
                self.finished_ok.emit(f"Loop encerrado (código {code}).")
        except Exception as err:  # noqa: BLE001
            try:
                from ace_stop import stop_requested

                if self._stop or stop_requested():
                    self.finished_ok.emit("Atualização contínua parada.")
                    return
            except Exception:
                pass
            self.failed.emit(f"ERRO no loop: {err}\n{traceback.format_exc(limit=4)}")


class LockOverlay(QWidget):
    """Cadeado em tela cheia — bloqueia UI; automação/workers continuam."""

    unlocked = Signal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("lockOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.hide()
        self._pulse = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addStretch(1)

        card = QFrame()
        card.setObjectName("lockCard")
        card.setMaximumWidth(420)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(28, 28, 28, 28)
        cl.setSpacing(12)

        self._icon = QLabel("🔒")
        self._icon.setObjectName("lockIcon")
        self._icon.setAlignment(Qt.AlignCenter)
        cl.addWidget(self._icon)

        title = QLabel("PAINEL BLOQUEADO")
        title.setObjectName("lockTitle")
        title.setAlignment(Qt.AlignCenter)
        cl.addWidget(title)

        tip = QLabel(
            "Automação e downloads continuam em segundo plano.\n"
            "Digite a senha do cadeado para mexer no CRT."
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        tip.setAlignment(Qt.AlignCenter)
        cl.addWidget(tip)

        self._pwd = QLineEdit()
        self._pwd.setEchoMode(QLineEdit.Password)
        self._pwd.setPlaceholderText("Senha do cadeado")
        self._pwd.returnPressed.connect(self._try_unlock)
        cl.addWidget(self._pwd)

        self._err = QLabel("")
        self._err.setObjectName("lockErr")
        self._err.setAlignment(Qt.AlignCenter)
        self._err.setWordWrap(True)
        cl.addWidget(self._err)

        btn = QPushButton("Desbloquear")
        btn.setObjectName("primary")
        btn.clicked.connect(self._try_unlock)
        cl.addWidget(btn)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        root.addLayout(row)
        root.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_pulse)
        self._expected = ""

    def set_expected_password(self, password: str) -> None:
        self._expected = str(password or "")

    def lock(self, password: str) -> None:
        self.set_expected_password(password)
        self._pwd.clear()
        self._err.setText("")
        self.show()
        self.raise_()
        self._timer.start(50)
        QTimer.singleShot(80, lambda: self._pwd.setFocus(Qt.OtherFocusReason))

    def unlock(self) -> None:
        self._timer.stop()
        self.hide()
        self._pwd.clear()
        self._err.setText("")
        self.unlocked.emit()

    def is_locked(self) -> bool:
        return self.isVisible()

    def _try_unlock(self) -> None:
        typed = self._pwd.text()
        if not self._expected:
            self._err.setText("Defina a senha em Menu → Configuração.")
            return
        if typed == self._expected:
            self.unlock()
            return
        self._err.setText("Senha incorreta.")
        self._pwd.selectAll()
        self._pwd.setFocus(Qt.OtherFocusReason)

    def _tick_pulse(self) -> None:
        self._pulse += 0.05
        # leve “respiração” no ícone
        scale = 1.0 + 0.04 * math.sin(self._pulse)
        self._icon.setStyleSheet(
            f"font-size: {int(72 * scale)}px; background: transparent;"
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())


class AceCrtRapidoWindow(QWidget):
    """Painel à parte para escolher um comando rápido."""

    def __init__(self, owner: "AceCrtConsole", content: QWidget) -> None:
        super().__init__(None)
        self.setObjectName("crtRoot")
        self.setWindowTitle("BINHO · Comandos rápidos")
        self.setWindowFlags(
            Qt.Tool
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
        )
        self._owner = owner
        self.resize(520, 560)
        self.setMinimumSize(400, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        tip = QLabel(
            "Escolha o relatório · fecha sozinho após clicar · "
            "ou digite o comando no ACE>"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        lay.addWidget(scroll, 1)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()


class AceCrtMenuWindow(QWidget):
    """Janela à parte com as abas de configuração (mesmo tema do CRT)."""

    def __init__(self, owner: "AceCrtConsole", content: QWidget) -> None:
        super().__init__(None)
        self.setObjectName("crtRoot")
        self.setWindowTitle("BINHO · Menu")
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self._owner = owner
        self.resize(540, 680)
        self.setMinimumSize(420, 480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        tip = QLabel("Menu · mesmo tema do painel · F2 ou botão Menu para reabrir")
        tip.setObjectName("hint")
        lay.addWidget(tip)
        lay.addWidget(content, 1)

    def closeEvent(self, event) -> None:  # noqa: N802
        # Esconde em vez de destruir (widgets/campos continuam vivos)
        event.ignore()
        self.hide()


class AceCrtConsole(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("crtRoot")
        self.setWindowTitle("BINHO · Gestão")
        # Chrome nativo Windows: minimizar / maximizar / fechar + redimensionar
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.resize(1180, 680)
        self.setMinimumSize(900, 520)
        self._theme_id = DEFAULT_CRT_THEME
        self.setStyleSheet(build_crt_stylesheet(self._theme_id))
        self._normal_geom = None  # geometria do modo janela
        self._pre_fs_state = "normal"  # normal | maximized
        self._frost_active = False
        self._frost_needs_rebuild = False
        self._startup_windowed_done = False

        self.payload: dict = {}
        self._worker: CmdWorker | None = None
        self._worker_cmd: str = ""
        self._pending_cmd: str | None = None
        self._auto_worker: AutoLoopWorker | None = None
        self._fields: dict[str, QWidget] = {}
        self._log_offset = 0
        self._log_seen: set[str] = set()
        self._tv_layout: dict = {}
        self._tv_slot_btns: dict[int, QPushButton] = {}
        self._tv_selected: int = 1
        self._tv_loading = False
        self._cmd_view = "log"  # log | bars (ambos visíveis; marca foco)
        self._sector_meters: dict[str, SectorMeterRow] = {}
        self._menu_win: AceCrtMenuWindow | None = None
        self._rapido_win: AceCrtRapidoWindow | None = None

        # registra PID para spawn_crt não abrir duplicata
        try:
            from crt_bridge import PID_PATH, STATUS_PATH

            STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.title = QLabel("BINHO · GESTÃO")
        self.title.setObjectName("title")
        self.mode = QLabel("MENU")
        self.mode.setObjectName("mode")
        self.mode.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.mode)
        head.addSpacing(8)
        self.btn_lock = QPushButton("Bloquear")
        self.btn_lock.setObjectName("menuBtn")
        self.btn_lock.setToolTip("Trava o painel com cadeado (automação continua)")
        self.btn_lock.clicked.connect(self._lock_panel)
        head.addWidget(self.btn_lock)
        head.addSpacing(6)
        self.btn_menu = QPushButton("Menu")
        self.btn_menu.setObjectName("menuBtn")
        self.btn_menu.setToolTip("Abrir menu de configuração (F2)")
        self.btn_menu.clicked.connect(self._toggle_menu_window)
        head.addWidget(self.btn_menu)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        split.setObjectName("mainSplit")
        split.setChildrenCollapsible(False)
        left = self._build_left()
        left.setMinimumWidth(260)
        split.addWidget(left)
        split.addWidget(self._build_center())
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        split.setSizes([360, 720])
        self._main_split = split
        root.addWidget(split, 1)

        # Abas ficam na janela Menu (escondida até F2 / botão)
        tabs = self._build_right()
        self._menu_win = AceCrtMenuWindow(self, tabs)

        self.foot = QLabel("Gestão operacional")
        self.foot.setObjectName("foot")
        root.addWidget(self.foot)

        self._scan = Scanlines(self)
        self._scan.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._scan.raise_()

        self._circuit_bus = CircuitBusOverlay(self)
        self._circuit_bus.raise_()
        QTimer.singleShot(0, self._wire_circuit_bus)

        self._lock = LockOverlay(self)
        self._lock.unlocked.connect(self._on_panel_unlocked)
        self._ui_locked = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(250)

        self._reload_payload()
        self._append_log("sistema", "Pronto. Este histórico é o CMD — digite ou use os atalhos.", mirror=True)
        # Zera barrinhas na abertura (não herdar 100% do crt_status.json antigo)
        self._seed_sector_bars_from_config(persist=True)
        # carrega histórico recente do CMD (espelho)
        try:
            entries, self._log_offset = read_log_since(0)
            for entry in entries[-80:]:
                self._render_log_entry(entry, from_file=True)
        except Exception:
            pass

    # ── layout blocks ──────────────────────────────────────────────
    def _remember_normal_geom(self) -> None:
        if not self.isFullScreen() and not self.isMaximized():
            self._normal_geom = self.geometry()

    def _center_on_screen(self) -> None:
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            avail = screen.availableGeometry()
            g = self.frameGeometry()
            g.moveCenter(avail.center())
            self.move(g.topLeft())
        except Exception:
            pass

    def _ensure_startup_windowed(self) -> None:
        """Garante abertura utilizável: só sai de fullscreen acidental."""
        if getattr(self, "_startup_windowed_done", False):
            return
        self._startup_windowed_done = True
        try:
            # Não desfaz maximize do usuário — só tela cheia “travada”
            if self.isFullScreen():
                self.showNormal()
                self.resize(1180, 680)
                self._center_on_screen()
            if not self.isMaximized() and not self.isFullScreen():
                if self.width() < 600 or self.height() < 400:
                    self.resize(1180, 680)
                    self._center_on_screen()
            if not self.isFullScreen() and not self.isMaximized():
                self._normal_geom = self.geometry()
            self._clamp_main_splitter()
            QTimer.singleShot(50, self._relayout_chrome)
            QTimer.singleShot(120, self._wire_circuit_bus)
            meta = CRT_THEMES.get(self._theme_id) or {}
            if meta.get("frost"):
                fa, fb = self._frost_alpha_val(), self._frost_blur_val()
                fp = frost_params(fa, fb)
                self._apply_frost_window(
                    True,
                    int(fp["tint"]),
                    int(fp["state"]),
                    opacity=float(fp["opacity"]),
                )
        except Exception:
            pass

    def _frame(self) -> QFrame:
        f = QFrame()
        f.setObjectName("panel")
        return f

    def _build_left(self) -> QWidget:
        box = self._frame()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Cerebro de circuitos + identidade da maquina + medidores
        self.cubes = BinhoCubesWidget()
        lay.addWidget(self.cubes)

        try:
            from sys_monitor import host_info, gpu_name, warmup

            warmup()
            hi = host_info()
        except Exception:
            hi = {"host": "—", "cpu_name": "—", "cores": "—", "ram_total_gb": "—", "os": "—"}
            gpu_name = lambda: None  # noqa: E731

        self.sys_host = QLabel(str(hi.get("host") or "—"))
        self.sys_host.setObjectName("sysHost")
        self.sys_host.setAlignment(Qt.AlignCenter)
        self.sys_host.setWordWrap(True)
        lay.addWidget(self.sys_host)

        cpu_line = str(hi.get("cpu_name") or "CPU")
        cores = str(hi.get("cores") or "—")
        ram = str(hi.get("ram_total_gb") or "—")
        gname = None
        try:
            gname = gpu_name()
        except Exception:
            gname = None
        sub = f"{cpu_line}\n{cores}  ·  RAM {ram}"
        if gname:
            sub += f"\nGPU {gname}"
        else:
            sub += f"\n{hi.get('os') or ''}"
        self.sys_host_sub = QLabel(sub.strip())
        self.sys_host_sub.setObjectName("sysHostSub")
        self.sys_host_sub.setAlignment(Qt.AlignCenter)
        self.sys_host_sub.setWordWrap(True)
        lay.addWidget(self.sys_host_sub)

        lay.addWidget(self._section("RECURSOS"))
        self.meter_cpu = SysMeterRow("CPU", "#8cc63f")
        self.meter_mem = SysMeterRow("MEM", "#29abe2")
        self.meter_gpu = SysMeterRow("GPU", "#fff200")
        lay.addWidget(self.meter_cpu)
        lay.addWidget(self.meter_mem)
        lay.addWidget(self.meter_gpu)
        self._sys_tick = 0

        self.status = QLabel("ONLINE")
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.status)

        self.detail = QLabel("—")
        self.detail.setObjectName("detail")
        self.detail.setAlignment(Qt.AlignCenter)
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFormat("%p%")
        lay.addWidget(self.bar)

        lay.addWidget(self._section("AGORA"))
        agora_tip = QLabel("Sessão + o que cada setor puxa no automático")
        agora_tip.setObjectName("hint")
        agora_tip.setWordWrap(True)
        lay.addWidget(agora_tip)

        self.meta_scroll = QScrollArea()
        self.meta_scroll.setWidgetResizable(True)
        self.meta_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.meta_scroll.setFrameShape(QFrame.NoFrame)
        self.meta_scroll.setMinimumHeight(120)
        self.meta_scroll.setMaximumHeight(260)
        self.meta_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.meta = QLabel("carregando…")
        self.meta.setObjectName("detail")
        self.meta.setWordWrap(True)
        self.meta.setTextFormat(Qt.RichText)
        self.meta.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.meta.setMargin(2)
        self.meta_scroll.setWidget(self.meta)
        lay.addWidget(self.meta_scroll, 1)
        return box

    def _build_center(self) -> QWidget:
        box = self._frame()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Cabeçalho do log + botão que abre o painel de comandos rápidos
        head_log = QHBoxLayout()
        self.cmd_section = self._section("CMD · log")
        head_log.addWidget(self.cmd_section)
        head_log.addStretch(1)
        self.btn_rapido = QPushButton("Comandos rápidos")
        self.btn_rapido.setObjectName("primary")
        self.btn_rapido.setToolTip("Abre o painel para escolher o relatório (50, 78, mapa…)")
        self.btn_rapido.clicked.connect(self._toggle_rapido_window)
        head_log.addWidget(self.btn_rapido)
        lay.addLayout(head_log)

        # Log em cima · barrinhas embaixo (redimensionáveis)
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.setObjectName("centerSplit")

        self.log = QTextEdit()
        self.log.setObjectName("crtLog")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.setAcceptRichText(True)
        self._setup_opaque_log()
        split.addWidget(self.log)

        bars_page = QWidget()
        bars_lay = QVBoxLayout(bars_page)
        bars_lay.setContentsMargins(0, 4, 0, 0)
        bars_lay.setSpacing(6)
        bars_head = QHBoxLayout()
        bars_head.addWidget(self._section("AUTO · setores"))
        bars_head.addStretch(1)
        bars_lay.addLayout(bars_head)
        self.sector_status = QLabel("Automático parado · inicie na aba Automação")
        self.sector_status.setObjectName("hint")
        self.sector_status.setWordWrap(True)
        bars_lay.addWidget(self.sector_status)
        for sid, title in (
            ("dist", "Distribuição"),
            ("78", "Armazém"),
            ("31", "Pendência"),
            ("73", "Contratação"),
            ("455", "Emissão"),
            ("mapa", "Mapa"),
        ):
            meter = SectorMeterRow(sid, title)
            guide = next((g for g in _SECTOR_GUIDE if g["id"] == sid), None)
            if guide:
                meter.setToolTip(
                    f"{guide['title']}\n"
                    f"Relatórios: {guide['reports']}\n"
                    f"{guide['blurb']}"
                )
            self._sector_meters[sid] = meter
            bars_lay.addWidget(meter)
        bars_lay.addStretch(1)
        tip_bars = QLabel(
            "Barrinhas = % da automação + envio Sheets · Comandos rápidos = botão acima"
        )
        tip_bars.setObjectName("hint")
        tip_bars.setWordWrap(True)
        bars_lay.addWidget(tip_bars)
        split.addWidget(bars_page)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([360, 240])
        self._center_split = split
        lay.addWidget(split, 1)

        # Compat: /log e /bars só mudam o foco (ambos ficam visíveis)
        self._apply_cmd_view(self._cmd_view, announce=False)

        prompt_row = QHBoxLayout()
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("ACE>  ex.: /tempo mapa 50s · mapa · parar")
        self.prompt.returnPressed.connect(self._submit_prompt)
        self.btn_run = QPushButton("Enviar")
        self.btn_run.setObjectName("primary")
        self.btn_run.setFixedWidth(80)
        self.btn_run.clicked.connect(self._submit_prompt)
        prompt_row.addWidget(self.prompt, 1)
        prompt_row.addWidget(self.btn_run)
        lay.addLayout(prompt_row)

        hint = QLabel(
            "Log em cima · barrinhas embaixo · Comandos rápidos abre o painel · "
            "Menu = F2 · tela cheia = F11 · “parar” corta tudo"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return box

    def _build_rapido_panel(self) -> QWidget:
        """Comandos rápidos agrupados por setor, com código SSW + o que faz."""
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(6)

        groups: list[tuple[str, list[tuple[str, str, str, str]]]] = [
            (
                "Distribuição",
                [
                    ("Coletas", "50", "Baixa coletas do dia (SSW 0157) e atualiza torres/KPIs.", "50"),
                    ("Torres / limites", "103", "Situação das coletas: limites, status e torres.", "103"),
                    ("Entregas", "36", "Romaneios e entregas do dia na operação.", "36"),
                    ("Agendamentos", "225", "Agenda de amanhã / coletas agendadas.", "225"),
                ],
            ),
            (
                "Armazém · Pendência · Contratação",
                [
                    ("Pátio / veículos", "78", "Veículos no armazém, KPIs e torres do 078.", "78"),
                    ("Pendências / SLA", "31", "Códigos de pendência e ofensores (inclui SLA).", "31"),
                    ("Frete 073→200", "73", "Contratação do dia: 073 cruzado com filiais 200.", "73"),
                ],
            ),
            (
                "Mapa · publicação · local",
                [
                    ("Mapa operacional", "mapa", "Monta rotas (CyberMap) e atualiza a TV do mapa.", "mapa"),
                    ("Só planilha/site", "sync", "Envia 50+103+36+225 já baixados — não abre o SSW.", "sync"),
                    ("Arquivos dashboard", "dash", "Gera/atualiza CSVs e JSON locais do painel.", "dash"),
                    ("Telas locais", "local", "Abre as telas internas (coleta, entrega, armazém…).", "local"),
                ],
            ),
        ]

        for group_title, items in groups:
            outer.addWidget(self._section(group_title))
            grid = QGridLayout()
            grid.setSpacing(6)
            grid.setContentsMargins(0, 0, 0, 0)
            for i, (title, code, blurb, cmd) in enumerate(items):
                btn = QuickCmdButton(title, code, blurb, cmd)
                btn.clicked.connect(lambda _=False, c=cmd: self._run_rapido_cmd(c))
                grid.addWidget(btn, i // 2, i % 2)
            outer.addLayout(grid)
        outer.addStretch(1)
        return wrap

    def _lock_password(self) -> str:
        p = self.payload or {}
        return str(p.get("crt_lock_password") or "binho")

    def _lock_panel(self) -> None:
        """Trava a UI com cadeado no meio. Workers/automação seguem."""
        pwd = self._lock_password().strip()
        if not pwd:
            QMessageBox.information(
                self,
                "Bloquear",
                "Defina a senha do cadeado em Menu → Configuração → Bloqueio do painel.",
            )
            self._show_menu_window("config")
            return
        for attr in ("_menu_win", "_rapido_win"):
            win = getattr(self, attr, None)
            if win is not None and win.isVisible():
                try:
                    win.hide()
                except Exception:
                    pass
        lock = getattr(self, "_lock", None)
        if lock is None:
            return
        self._ui_locked = True
        if hasattr(self, "mode"):
            self.mode.setText("LOCKED")
        if hasattr(self, "btn_lock"):
            self.btn_lock.setEnabled(False)
        lock.setGeometry(self.rect())
        lock.lock(pwd)
        lock.raise_()
        self._append_log(
            "sistema",
            "Painel bloqueado · automação continua · digite a senha no cadeado para liberar.",
            mirror=False,
        )

    def _on_panel_unlocked(self) -> None:
        self._ui_locked = False
        if hasattr(self, "mode"):
            self.mode.setText("MENU")
        if hasattr(self, "btn_lock"):
            self.btn_lock.setEnabled(True)
        self._append_log("ok", "Painel desbloqueado.", mirror=False)
        try:
            if hasattr(self, "prompt"):
                self.prompt.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def _is_ui_locked(self) -> bool:
        lock = getattr(self, "_lock", None)
        return bool(getattr(self, "_ui_locked", False) or (lock is not None and lock.is_locked()))

    def _toggle_rapido_window(self) -> None:
        if self._is_ui_locked():
            return
        win = getattr(self, "_rapido_win", None)
        if win is None:
            panel = self._build_rapido_panel()
            win = AceCrtRapidoWindow(self, panel)
            self._rapido_win = win
            self._sync_rapido_window_chrome()
        if win.isVisible():
            win.raise_()
            win.activateWindow()
            return
        # posiciona perto do botão / canto do CRT
        try:
            btn = getattr(self, "btn_rapido", None)
            if btn is not None:
                g = btn.mapToGlobal(btn.rect().bottomRight())
                win.move(max(40, g.x() - win.width() + 20), g.y() + 8)
            else:
                g = self.mapToGlobal(self.rect().topRight())
                win.move(max(40, g.x() - win.width() - 20), g.y() + 60)
        except Exception:
            pass
        self._sync_rapido_window_chrome()
        win.show()
        win.raise_()
        win.activateWindow()

    def _sync_rapido_window_chrome(self) -> None:
        win = getattr(self, "_rapido_win", None)
        if win is None:
            return
        fa, fb = self._frost_alpha_val(), self._frost_blur_val()
        tid = getattr(self, "_theme_id", DEFAULT_CRT_THEME)
        win.setStyleSheet(build_crt_stylesheet(tid, frost_alpha=fa, frost_blur=fb))
        meta = CRT_THEMES.get(tid) or {}
        frost = bool(meta.get("frost"))
        fp = frost_params(fa, fb) if frost else None
        tint = int(fp["tint"]) if fp else int(meta.get("acrylic_tint") or 0x401A1A1A)
        state = int(fp["state"]) if fp else 4
        opacity = float(fp["opacity"]) if fp else 1.0
        self._apply_frost_on_widget(win, frost, tint, state, opacity=opacity)

    def _run_rapido_cmd(self, cmd: str) -> None:
        win = getattr(self, "_rapido_win", None)
        if win is not None:
            win.hide()
        self.run_command(cmd)

    def _toggle_cmd_view(self) -> None:
        nxt = "bars" if self._cmd_view == "log" else "log"
        self._apply_cmd_view(nxt, announce=True)

    def _apply_cmd_view(self, mode: str, *, announce: bool = True) -> None:
        """Log e barras ficam sempre visíveis; /log e /bars só mudam o foco."""
        mode = "log" if str(mode).lower().strip() in {"log", "/log"} else "bars"
        self._cmd_view = mode
        if hasattr(self, "cmd_section"):
            self.cmd_section.setText("CMD · log" if mode == "log" else "CMD · log (foco barras)")
        try:
            if mode == "log" and hasattr(self, "log"):
                self.log.setFocus(Qt.OtherFocusReason)
                self.log.ensureCursorVisible()
            elif mode == "bars" and hasattr(self, "sector_status"):
                self.sector_status.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass
        if announce:
            if mode == "log":
                self._append_log(
                    "sistema",
                    "Foco no LOG · barrinhas continuam embaixo. Comandos rápidos = botão acima.",
                )
            else:
                if hasattr(self, "sector_status"):
                    self.sector_status.setText(
                        "Vista BARRAS · log continua em cima · use Comandos rápidos para puxar relatório"
                    )
                self._append_log(
                    "sistema",
                    "Foco nas BARRAS · log continua em cima.",
                    mirror=False,
                )
    def _build_right(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_config_tab(), "Configuração")
        tabs.addTab(self._build_automacao_tab(), "Automação")
        tabs.addTab(self._build_local_tab(), "Local")
        tabs.addTab(self._build_tv_tab(), "TV")
        tabs.addTab(self._build_gestao_tab(), "Gestão")
        self._right_tabs = tabs
        return tabs

    def _toggle_menu_window(self) -> None:
        if self._is_ui_locked():
            return
        win = getattr(self, "_menu_win", None)
        if win is None:
            return
        if win.isVisible():
            win.raise_()
            win.activateWindow()
        else:
            self._show_menu_window()

    def _show_menu_window(self, tab: str | int | None = None) -> None:
        if self._is_ui_locked():
            return
        win = getattr(self, "_menu_win", None)
        if win is None:
            return
        if tab is not None:
            self._select_menu_tab(tab)
        self._sync_menu_window_chrome()
        win.show()
        win.raise_()
        win.activateWindow()

    def _select_menu_tab(self, tab: str | int) -> None:
        tabs = getattr(self, "_right_tabs", None)
        if tabs is None:
            return
        if isinstance(tab, int):
            if 0 <= tab < tabs.count():
                tabs.setCurrentIndex(tab)
            return
        key = str(tab).strip().lower()
        aliases = {
            "config": "config",
            "configuração": "config",
            "auto": "autom",
            "automacao": "autom",
            "automação": "autom",
            "local": "local",
            "tv": "tv",
            "marca": "config",
            "logo": "config",
            "brand": "config",
            "gestao": "gest",
            "gestão": "gest",
        }
        prefix = aliases.get(key, key[:4])
        for i in range(tabs.count()):
            if tabs.tabText(i).lower().startswith(prefix):
                tabs.setCurrentIndex(i)
                return

    def _sync_menu_window_chrome(self) -> None:
        """Aplica o mesmo stylesheet + frost/opacidade na janela Menu."""
        win = getattr(self, "_menu_win", None)
        if win is not None:
            fa, fb = self._frost_alpha_val(), self._frost_blur_val()
            tid = getattr(self, "_theme_id", DEFAULT_CRT_THEME)
            win.setStyleSheet(
                build_crt_stylesheet(tid, frost_alpha=fa, frost_blur=fb)
            )
            meta = CRT_THEMES.get(tid) or {}
            frost = bool(meta.get("frost"))
            fp = frost_params(fa, fb) if frost else None
            tint = int(fp["tint"]) if fp else int(meta.get("acrylic_tint") or 0x401A1A1A)
            state = int(fp["state"]) if fp else 4
            opacity = float(fp["opacity"]) if fp else 1.0
            self._apply_frost_on_widget(win, frost, tint, state, opacity=opacity)
        self._sync_rapido_window_chrome()

    def _build_config_tab(self) -> QWidget:
        from ace_cmd import EDITABLE

        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        intro = QLabel(
            "Login SSW, publicação e logo das dashboards.\n"
            "O visual do CRT (tema Circuitos) é fixo — só a logo das TVs/sites muda aqui."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setSpacing(8)

        form.addRow(self._section("Logo das dashboards"))
        logo_tip = QLabel(
            "Altera só a marca nas telas (GitHub Pages, Sites e local). "
            "O cérebro do CRT não muda."
        )
        logo_tip.setObjectName("hint")
        logo_tip.setWordWrap(True)
        form.addRow(logo_tip)

        self._brand_preview = QLabel()
        self._brand_preview.setAlignment(Qt.AlignCenter)
        self._brand_preview.setMinimumHeight(100)
        self._brand_preview.setMaximumHeight(140)
        self._brand_preview.setStyleSheet("background:#030712;border:1px solid #164e63;")
        form.addRow(self._brand_preview)

        self._brand_status = QLabel("—")
        self._brand_status.setObjectName("hint")
        self._brand_status.setWordWrap(True)
        form.addRow(self._brand_status)

        row_logo = QHBoxLayout()
        btn_file = QPushButton("Escolher imagem…")
        btn_file.setObjectName("primary")
        btn_file.clicked.connect(self._brand_pick_file)
        btn_export = QPushButton("Exportar…")
        btn_export.clicked.connect(self._brand_export)
        btn_refresh = QPushButton("Atualizar preview")
        btn_refresh.clicked.connect(self._brand_refresh_preview)
        row_logo.addWidget(btn_file)
        row_logo.addWidget(btn_export)
        row_logo.addWidget(btn_refresh)
        wrap_logo = QWidget()
        wrap_logo.setLayout(row_logo)
        form.addRow(wrap_logo)

        url_row = QHBoxLayout()
        self._brand_url = QLineEdit()
        self._brand_url.setPlaceholderText("https://…/logo.png")
        btn_url = QPushButton("Usar URL")
        btn_url.clicked.connect(self._brand_apply_url)
        url_row.addWidget(self._brand_url, 1)
        url_row.addWidget(btn_url)
        wrap_url = QWidget()
        wrap_url.setLayout(url_row)
        form.addRow("URL online", wrap_url)

        vis = QHBoxLayout()
        btn_show = QPushButton("Mostrar nas dashboards")
        btn_show.clicked.connect(self._brand_show_all)
        btn_hide = QPushButton("Ocultar nas dashboards")
        btn_hide.clicked.connect(self._brand_hide_all)
        btn_pub = QPushButton("Publicar logo")
        btn_pub.setObjectName("primary")
        btn_pub.clicked.connect(self._brand_publish)
        vis.addWidget(btn_show)
        vis.addWidget(btn_hide)
        vis.addWidget(btn_pub)
        wrap_vis = QWidget()
        wrap_vis.setLayout(vis)
        form.addRow(wrap_vis)

        groups = {
            "ssw": ("Acesso ao SSW", "URL, empresa, usuário e senha do sistema."),
            "auto": ("Atualização geral", "Opções de coleta/entrega e período (diário ou sexta)."),
            "local": ("Modo local / rede", "Sem planilha e acesso na Wi‑Fi (detalhes também na aba Local)."),
            "cloud": ("Planilha e site", "Google Sheets, Sites e GitHub Pages."),
            "armazem": ("Armazém", "Ajustes do setor 078."),
            "pendencia": ("Pendência", "Ajustes do setor 031."),
            "contratacao": ("Contratação", "Ajustes 073 → 200."),
            "automacao": ("Automação", "Use a aba Automação para setores e intervalos."),
            "crt": (
                "Bloqueio do painel",
                "Senha do cadeado (botão Bloquear). Automação continua mesmo bloqueado. Padrão: binho",
            ),
        }
        skip_keys = {
            "headless",
            "loop_intervalo",
            "ciclo_paralelo",
            *(k for k, (g, *_r) in EDITABLE.items() if g == "automacao"),
        }
        current_group = None
        for key, (group, typ, secret) in EDITABLE.items():
            if key in skip_keys:
                continue
            if group != current_group:
                current_group = group
                title, hint = groups.get(group, (group, ""))
                form.addRow(self._section(title))
                if hint:
                    h = QLabel(hint)
                    h.setObjectName("hint")
                    h.setWordWrap(True)
                    form.addRow(h)

            if typ == "bool":
                w: QWidget = QCheckBox("sim")
            elif key == "periodo_modo":
                w = QComboBox()
                w.addItem("Diário", "diario")
                w.addItem("A partir da sexta", "sexta")
            elif key == "publish_target":
                w = QComboBox()
                w.addItem("Auto (Sheets→Sites se planilha ligada)", "auto")
                w.addItem("Google Sites", "sites")
                w.addItem("GitHub Pages", "github")
                w.addItem("Só local", "local")
            else:
                w = QLineEdit()
                if secret:
                    w.setEchoMode(QLineEdit.Password)
            self._fields[key] = w
            form.addRow(_field_label(key), w)

        form.addRow(self._section("Navegador (SSW)"))
        viz_hint = QLabel(
            "Desmarcado = roda oculto (mais leve). Marcado = você vê o Chrome/Edge abrindo."
        )
        viz_hint.setObjectName("hint")
        viz_hint.setWordWrap(True)
        form.addRow(viz_hint)
        self.chk_viz = QCheckBox("Mostrar navegador ao trabalhar")
        form.addRow(self.chk_viz)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        row = QHBoxLayout()
        btn_reload = QPushButton("Recarregar")
        btn_reload.setToolTip("Descarta alterações não salvas e lê o arquivo de config")
        btn_reload.clicked.connect(self._reload_payload)
        btn_save = QPushButton("Salvar configuração")
        btn_save.setObjectName("primary")
        btn_save.setToolTip("Grava login, planilha, LAN e demais campos desta aba")
        btn_save.clicked.connect(self._save_config)
        row.addWidget(btn_reload)
        row.addStretch(1)
        row.addWidget(btn_save)
        outer.addLayout(row)

        self._brand_refresh_preview()
        return wrap

    def _build_automacao_tab(self) -> QWidget:
        """Define o que entra no automático e o tempo de cada setor."""
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setSpacing(10)

        tip = QLabel(
            "Marque os setores do modo automático e defina o tempo de cada um "
            "(ex.: 5m, 30m, 1h). Em branco = usa o intervalo padrão."
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        lay.addWidget(self._section("Intervalo padrão"))
        self._fields["loop_intervalo"] = QLineEdit()
        self._fields["loop_intervalo"].setPlaceholderText("ex.: 5m")
        row_fb = QHBoxLayout()
        row_fb.addWidget(QLabel("Fallback"))
        row_fb.addWidget(self._fields["loop_intervalo"], 1)
        lay.addLayout(row_fb)

        self._fields["ciclo_paralelo"] = QCheckBox("Rodar setores juntos quando vencerem ao mesmo tempo")
        lay.addWidget(self._fields["ciclo_paralelo"])

        sectors = (
            ("dist", "Distribuição", "50 · 103 · 36 · 225", "dist_in_loop", "dist_intervalo"),
            ("78", "Armazém", "078 · descarga", "armazem_in_loop", "armazem_intervalo"),
            ("31", "Pendência", "031 · ofensores/SLA", "pendencia_in_loop", "pendencia_intervalo"),
            ("73", "Contratação", "073 → 200", "contratacao_in_loop", "contratacao_intervalo"),
            ("455", "Emissão", "455 · fretes do dia", "emissao_in_loop", "emissao_intervalo"),
            ("mapa", "Mapa Operacional", "36 · rotas na rua", "mapa_in_loop", "mapa_intervalo"),
        )
        lay.addWidget(self._section("Setores no automático"))
        for _sid, title, desc, flag_key, iv_key in sectors:
            box = QFrame()
            box.setObjectName("panel")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(10, 8, 10, 8)
            bl.setSpacing(4)
            chk = QCheckBox(title)
            chk.setToolTip(desc)
            self._fields[flag_key] = chk
            bl.addWidget(chk)
            meta = QLabel(desc)
            meta.setObjectName("hint")
            bl.addWidget(meta)
            row = QHBoxLayout()
            row.addWidget(QLabel("A cada"))
            iv = QLineEdit()
            iv.setPlaceholderText("vazio = padrão")
            iv.setMaximumWidth(120)
            self._fields[iv_key] = iv
            row.addWidget(iv)
            row.addWidget(QLabel("(30s · 5m · 1h · 2d)"))
            row.addStretch(1)
            bl.addLayout(row)
            lay.addWidget(box)

        lay.addWidget(self._section("Controle"))
        row_ctrl = QHBoxLayout()
        btn_save = QPushButton("Salvar")
        btn_save.setObjectName("primary")
        btn_save.clicked.connect(self._save_config)
        btn_start = QPushButton("Iniciar automático")
        btn_start.clicked.connect(self._start_auto_from_tab)
        btn_stop = QPushButton("Parar")
        btn_stop.clicked.connect(self._stop_auto)
        row_ctrl.addWidget(btn_save)
        row_ctrl.addWidget(btn_start)
        row_ctrl.addWidget(btn_stop)
        lay.addLayout(row_ctrl)

        self.auto_status = QLabel("Automático parado.")
        self.auto_status.setObjectName("hint")
        self.auto_status.setWordWrap(True)
        lay.addWidget(self.auto_status)

        lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return wrap

    def _start_auto_from_tab(self) -> None:
        # salva antes de iniciar para o loop ler a config nova
        self._save_config_silent()
        self._start_automatica(None)
        if hasattr(self, "auto_status"):
            p = self.payload or {}
            parts = []
            for flag, label, ivk in (
                ("dist_in_loop", "Dist", "dist_intervalo"),
                ("armazem_in_loop", "Armazém", "armazem_intervalo"),
                ("pendencia_in_loop", "Pendência", "pendencia_intervalo"),
                ("contratacao_in_loop", "Contratação", "contratacao_intervalo"),
                ("emissao_in_loop", "Emissão", "emissao_intervalo"),
                ("mapa_in_loop", "Mapa", "mapa_intervalo"),
            ):
                if p.get(flag, flag == "dist_in_loop"):
                    iv = (p.get(ivk) or p.get("loop_intervalo") or "5m")
                    parts.append(f"{label} {iv}")
            self.auto_status.setText(
                "Automático ligado · " + (" · ".join(parts) if parts else "nenhum setor")
            )

    def _save_config_silent(self) -> bool:
        """Salva config sem popup (usado ao iniciar o automático)."""
        from ace_cmd import EDITABLE, _save_payload

        try:
            for key, (_g, typ, _secret) in EDITABLE.items():
                w = self._fields.get(key)
                if w is None:
                    continue
                if isinstance(w, QCheckBox):
                    self.payload[key] = w.isChecked()
                elif isinstance(w, QComboBox):
                    data = w.currentData()
                    self.payload[key] = str(data if data is not None else w.currentText()).strip()
                elif isinstance(w, QLineEdit):
                    text = w.text().strip()
                    if typ == "int":
                        self.payload[key] = int(text or "0")
                    elif key == "loop_intervalo" or key.endswith("_intervalo"):
                        if not text and key != "loop_intervalo":
                            self.payload[key] = ""
                        else:
                            from interval_parse import format_duration, parse_duration

                            self.payload[key] = format_duration(parse_duration(text or "5m"))
                    else:
                        self.payload[key] = text
            self.payload["headless"] = not self.chk_viz.isChecked()
            self.payload["crt_theme"] = self._theme_id
            self._store_frost_into_payload()
            _save_payload(self.payload)
            self.payload = __import__("ace_cmd", fromlist=["_load_payload"])._load_payload()
            self._update_meta()
            return True
        except Exception as err:  # noqa: BLE001
            self._append_log("erro", f"Falha ao salvar automação: {err}")
            return False

    def _build_local_tab(self) -> QWidget:
        """Modo local: escolhe telas e abre várias janelas internas (sem GitHub)."""
        from ace_local_view import LOCAL_SCREEN_ORDER, screen_label

        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        tip = QLabel(
            "Dashboard interno · sem GitHub e sem planilha.\n"
            "Relatórios ficam em CSV + JSON rápido (data/cache/local).\n"
            "Marque as telas e abra várias ao mesmo tempo."
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        outer.addWidget(tip)

        outer.addWidget(self._section("Armazenamento"))
        self.chk_modo_local = QCheckBox("Não enviar à planilha (só JSON/CSV interno)")
        self.chk_modo_local.setChecked(bool(self.payload.get("modo_local", False)))
        self.chk_modo_local.stateChanged.connect(self._local_toggle_modo)
        outer.addWidget(self.chk_modo_local)
        path_hint = QLabel("Pasta: data/cache/local/*.json")
        path_hint.setObjectName("hint")
        outer.addWidget(path_hint)

        outer.addWidget(self._section("Rede (mesma Wi‑Fi)"))
        self.chk_dashboard_lan = QCheckBox("Liberar acesso na rede (outros aparelhos)")
        self.chk_dashboard_lan.setChecked(bool(self.payload.get("dashboard_lan", False)))
        self.chk_dashboard_lan.stateChanged.connect(self._local_toggle_lan)
        outer.addWidget(self.chk_dashboard_lan)
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Porta"))
        self.edit_dash_port = QLineEdit(str(self.payload.get("dashboard_port") or 8787))
        self.edit_dash_port.setMaximumWidth(80)
        port_row.addWidget(self.edit_dash_port)
        port_row.addStretch(1)
        outer.addLayout(port_row)
        btn_lan = QPushButton("Mostrar links da rede")
        btn_lan.clicked.connect(self._local_show_lan_urls)
        outer.addWidget(btn_lan)
        self._lan_urls = QLabel("")
        self._lan_urls.setObjectName("hint")
        self._lan_urls.setWordWrap(True)
        self._lan_urls.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self._lan_urls)

        outer.addWidget(self._section("Telas"))
        self._local_checks: dict[str, QCheckBox] = {}
        defaults_on = {"coleta", "entrega", "armazem", "pendencia", "contratacao"}
        for sid in LOCAL_SCREEN_ORDER:
            chk = QCheckBox(screen_label(sid))
            chk.setChecked(sid in defaults_on)
            self._local_checks[sid] = chk
            outer.addWidget(chk)

        row_sel = QHBoxLayout()
        btn_all = QPushButton("Todas")
        btn_all.clicked.connect(lambda: self._local_set_all(True))
        btn_none = QPushButton("Nenhuma")
        btn_none.clicked.connect(lambda: self._local_set_all(False))
        row_sel.addWidget(btn_all)
        row_sel.addWidget(btn_none)
        row_sel.addStretch(1)
        outer.addLayout(row_sel)

        outer.addWidget(self._section("Ações"))
        btn_refresh = QPushButton("Atualizar dados (local)")
        btn_refresh.clicked.connect(self._local_refresh_data)
        outer.addWidget(btn_refresh)

        btn_open = QPushButton("Abrir selecionadas")
        btn_open.setObjectName("primary")
        btn_open.clicked.connect(self._local_open_selected)
        outer.addWidget(btn_open)

        btn_tab = QPushButton("Ir para esta aba (atalho: local)")
        btn_tab.setObjectName("hint")
        btn_tab.clicked.connect(lambda: None)
        btn_tab.hide()

        self._local_status = QLabel("")
        self._local_status.setObjectName("hint")
        self._local_status.setWordWrap(True)
        outer.addWidget(self._local_status)
        outer.addStretch(1)
        return wrap

    def _local_set_all(self, checked: bool) -> None:
        for chk in getattr(self, "_local_checks", {}).values():
            chk.setChecked(checked)

    def _local_toggle_modo(self, state: int) -> None:
        """Liga/desliga modo_local e grava na config."""
        on = bool(state)
        self.payload["modo_local"] = on
        try:
            from ace_cmd import _save_payload

            _save_payload(self.payload)
            # espelha no campo da aba Configuração, se existir
            w = self._fields.get("modo_local")
            if isinstance(w, QCheckBox):
                w.blockSignals(True)
                w.setChecked(on)
                w.blockSignals(False)
            self._local_status.setText(
                "Modo local LIGADO — relatórios não vão ao Sheets."
                if on
                else "Modo local desligado — planilha volta a valer (se enable_sheets)."
            )
            self._append_log("sistema", f"modo_local={str(on).lower()}")
        except Exception as err:  # noqa: BLE001
            self._local_status.setText(f"Falha ao salvar modo_local: {err}")

    def _local_toggle_lan(self, state: int) -> None:
        on = bool(state)
        self.payload["dashboard_lan"] = on
        try:
            port_txt = (self.edit_dash_port.text() or "8787").strip()
            self.payload["dashboard_port"] = int(port_txt)
        except ValueError:
            self.payload["dashboard_port"] = 8787
            self.edit_dash_port.setText("8787")
        try:
            from ace_cmd import _save_payload

            _save_payload(self.payload)
            w = self._fields.get("dashboard_lan")
            if isinstance(w, QCheckBox):
                w.blockSignals(True)
                w.setChecked(on)
                w.blockSignals(False)
            if on:
                self._local_show_lan_urls(force_on=True)
            else:
                self._lan_urls.setText("LAN desligada — só este PC (127.0.0.1).")
                self._append_log("sistema", "dashboard_lan=false")
        except Exception as err:  # noqa: BLE001
            self._local_status.setText(f"Falha LAN: {err}")

    def _local_show_lan_urls(
        self,
        force_on: bool = False,
        filter_ids: list[str] | None = None,
    ) -> None:
        try:
            from ace_cmd import _save_payload
            from ace_local_view import screen_label
            from dashboard_server import (
                ensure_dashboard_server,
                get_lan_ip,
                lan_urls_by_screen,
                server_info,
            )
            from publish_dashboard import publish_dashboard

            if force_on or bool(self.payload.get("dashboard_lan")):
                self.payload["dashboard_lan"] = True
                if hasattr(self, "chk_dashboard_lan"):
                    self.chk_dashboard_lan.blockSignals(True)
                    self.chk_dashboard_lan.setChecked(True)
                    self.chk_dashboard_lan.blockSignals(False)
            try:
                self.payload["dashboard_port"] = int(
                    (self.edit_dash_port.text() if hasattr(self, "edit_dash_port") else None)
                    or self.payload.get("dashboard_port")
                    or 8787
                )
            except ValueError:
                self.payload["dashboard_port"] = 8787
            _save_payload(self.payload)
            if not self.payload.get("dashboard_lan"):
                self._lan_urls.setText("Marque “Liberar acesso na rede” primeiro.")
                return

            publish_dashboard(on_status=lambda m: self._append_log("sistema", m), allow_push=False)
            port = ensure_dashboard_server(lan=True, restart_if_needed=True)
            urls = lan_urls_by_screen(port)
            info = server_info()
            wanted = {str(x).strip().lower() for x in (filter_ids or []) if str(x).strip()}
            lines = [
                f"PC na rede: {get_lan_ip()}  ·  porta {port}",
                f"Base: {info.get('lan_url')}/index.html",
                "No outro aparelho (mesma Wi‑Fi), abra:",
            ]
            for sid, url in urls.items():
                if wanted and sid not in wanted:
                    continue
                lines.append(f"• {screen_label(sid)}")
                lines.append(f"  {url}")
            text = "\n".join(lines)
            self._lan_urls.setText(text)
            self._local_status.setText("Links LAN prontos (selecione o texto para copiar).")
            self._append_log("ok", f"LAN {get_lan_ip()}:{port}")
            if hasattr(self, "_right_tabs"):
                try:
                    self._show_menu_window("local")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as err:  # noqa: BLE001
            self._lan_urls.setText(str(err))
            self._append_log("erro", str(err))
            QMessageBox.warning(self, "LAN", str(err))

    def _local_ensure_modo(self) -> None:
        """Ao usar a aba Local, garante modo_local ativo (sem planilha)."""
        if not bool(self.payload.get("modo_local")):
            self.payload["modo_local"] = True
            if hasattr(self, "chk_modo_local"):
                self.chk_modo_local.blockSignals(True)
                self.chk_modo_local.setChecked(True)
                self.chk_modo_local.blockSignals(False)
            try:
                from ace_cmd import _save_payload

                _save_payload(self.payload)
            except Exception:  # noqa: BLE001
                pass
            self._append_log("sistema", "modo_local=true (ativado pelo modo Local)")

    def _local_selected_ids(self) -> list[str]:
        return [
            sid
            for sid, chk in getattr(self, "_local_checks", {}).items()
            if chk.isChecked()
        ]

    def _local_refresh_data(self) -> None:
        try:
            from ace_local_view import refresh_local_data

            self._local_ensure_modo()
            r = refresh_local_data(on_status=lambda m: self._append_log("sistema", m))
            sectors = ((r or {}).get("local") or {}).get("sectors") or {}
            self._local_status.setText(
                f"JSON local atualizado · {len(sectors)} setor(es) · sem Sheets/GitHub."
            )
            self._append_log("ok", "Dashboard + JSON local atualizados (sem planilha).")
        except Exception as err:  # noqa: BLE001
            self._local_status.setText(f"Falha: {err}")
            self._append_log("erro", str(err))

    def _local_open_selected(self) -> None:
        ids = self._local_selected_ids()
        if not ids:
            self._local_status.setText("Marque ao menos uma tela.")
            return
        self._local_ensure_modo()
        self._open_local_screens(ids)

    def _open_local_from_cmd(self, tokens: list[str]) -> None:
        """Comando `local` / `local coleta pendencia` — foca aba e abre telas."""
        self._local_ensure_modo()
        if hasattr(self, "_right_tabs"):
            try:
                self._show_menu_window("local")
            except Exception:  # noqa: BLE001
                pass
        if not tokens and getattr(self, "_local_checks", None):
            ids = self._local_selected_ids()
            if not ids:
                self._append_log("sistema", "Local: nenhuma tela marcada — abrindo todas.")
                ids = None
        else:
            ids = list(tokens) if tokens else None
        self._open_local_screens(ids)

    def _open_local_screens(self, ids: list[str] | None) -> None:
        try:
            from ace_local_view import open_local_screens, screen_label

            result = open_local_screens(
                ids,
                parent=None,  # janelas independentes (não filhas do CRT)
                refresh=True,
                prefer_embed=True,
                on_status=lambda m: self._append_log("sistema", m),
            )
            labels = ", ".join(screen_label(s) for s in (result.get("screens") or []))
            mode = "janelas internas" if result.get("embed") else "navegador"
            msg = f"Local OK · {mode} · {labels}"
            self._local_status.setText(msg)
            self._append_log("ok", msg)
            self.mode.setText("LOCAL")
        except Exception as err:  # noqa: BLE001
            self._local_status.setText(f"Falha local: {err}")
            self._append_log("erro", str(err))
            QMessageBox.warning(self, "Modo local", str(err))

    def _build_tv_tab(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(10)

        tip = QLabel(
            "A configuração da parede e o layout do dashboard "
            "ficam numa tela separada — arraste gráficos e blocos por setor."
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.tv_wall_status = QLabel("Modo: —")
        self.tv_wall_status.setObjectName("hint")
        lay.addWidget(self.tv_wall_status)

        self.tv_url = QLabel("Nas TVs: #tv/distribuicao · posição 1× por aparelho")
        self.tv_url.setObjectName("hint")
        self.tv_url.setWordWrap(True)
        lay.addWidget(self.tv_url)

        b_open = QPushButton("Abrir editor TV / Dashboard")
        b_open.setObjectName("primary")
        b_open.setMinimumHeight(48)
        b_open.clicked.connect(self._open_tv_editor)
        lay.addWidget(b_open)

        row = QHBoxLayout()
        b_reload = QPushButton("Recarregar")
        b_reload.clicked.connect(self._tv_reload)
        b_save = QPushButton("Salvar TV")
        b_save.setObjectName("primary")
        b_save.clicked.connect(self._tv_save)
        row.addWidget(b_reload)
        row.addWidget(b_save)
        lay.addLayout(row)
        lay.addStretch(1)

        self._tv_slot_btns = {}
        self._tv_slot_group = QButtonGroup(self)
        self.tv_sector = QComboBox()
        self.tv_view = QComboBox()
        self.tv_logo = QComboBox()
        self.tv_margins = QComboBox()
        self.tv_sync = QCheckBox()
        self.tv_wall_sector = QComboBox()
        self.dash_sector = QComboBox()
        self.dash_view = QComboBox()
        self.dash_chart = QComboBox()
        self.dash_scale = QComboBox()
        self.dash_kpis = QCheckBox()
        self.dash_chart_on = QCheckBox()
        self.dash_amanha = QCheckBox()
        self.dash_status = QCheckBox()
        self.dash_locked = QCheckBox()
        self._tv_reload()
        return wrap

    def _open_tv_editor(self) -> None:
        from ace_tv_editor import TvEditorDialog

        self._tv_reload()
        dlg = TvEditorDialog(self, self._tv_layout)
        if dlg.exec():
            self._tv_layout = dlg.resulting_layout()
            self._tv_refresh_wall_status()
            self._append_log("config", f"Editor TV · layout v{self._tv_layout.get('version')}.")
            publish(online=True, label="TV", pct=0, detail="editor TV salvo", mode="OK")

    def _tv_reload(self) -> None:
        from tv_layout import load_layout

        self._tv_loading = True
        self._tv_layout = load_layout()
        self._tv_refresh_wall_status()
        self._tv_loading = False
        self._append_log("config", "Layout TV recarregado.")

    def _tv_refresh_wall_status(self) -> None:
        from tv_layout import SECTOR_LABELS

        wall = bool((self._tv_layout or {}).get("wallMode"))
        if wall:
            sec = str((self._tv_layout or {}).get("wallSector") or "distribuicao")
            self.tv_wall_status.setText(
                f"Modo: PAREDE · pedaços de {SECTOR_LABELS.get(sec, sec)}"
            )
        else:
            self.tv_wall_status.setText("Modo: NORMAL · cada TV = setor da grade")

    def _tv_refresh_slot_labels(self) -> None:
        self._tv_refresh_wall_status()

    def _tv_select_slot(self, slot_id: int) -> None:
        self._tv_selected = int(slot_id)

    def _tv_form_changed(self) -> None:
        return

    def _tv_global_changed(self) -> None:
        return

    def _dash_form_changed(self) -> None:
        return

    def _tv_persist(self, *, title: str, body: str) -> bool:
        from tv_layout import push_layout_to_sheets, save_layout

        try:
            self._tv_layout = save_layout(self._tv_layout)
            ok, msg = push_layout_to_sheets(self._tv_layout)
            ver = self._tv_layout.get("version")
            wall = "PAREDE" if self._tv_layout.get("wallMode") else "NORMAL"
            if ok:
                self._append_log("config", f"{title} · v{ver} · planilha OK ({wall}).")
                publish(online=True, label="TV", pct=0, detail=f"{title} OK", mode="OK")
                QMessageBox.information(
                    self,
                    "ACE TV",
                    f"{body}\n\nLayout na planilha: OK (v{ver}).",
                )
                self._tv_refresh_wall_status()
                return True
            self._append_log("erro", f"{title} · local v{ver} mas planilha falhou: {msg}")
            QMessageBox.warning(
                self,
                "ACE TV · TVs não atualizaram",
                f"{body}\n\nSalvo neste PC (v{ver}), mas NÃO chegou na planilha:\n{msg}",
            )
            self._tv_refresh_wall_status()
            return False
        except Exception as err:  # noqa: BLE001
            self._append_log("erro", str(err))
            QMessageBox.warning(self, "ACE TV", f"Falha ao salvar:\n{err}")
            return False

    def _tv_save(self) -> None:
        wall = "PAREDE" if (self._tv_layout or {}).get("wallMode") else "NORMAL"
        self._tv_persist(title="Layout TV", body=f"Layout salvo ({wall}).")

    def _brand_refresh_preview(self) -> None:
        try:
            from brand import load_brand, resolve_dashboard_logo_path, resolve_dashboard_src

            b = load_brand()
            src = resolve_dashboard_src(b)
            mode = b.get("mode")
            vis = b.get("visible", True)
            path = resolve_dashboard_logo_path(b)
            self._brand_status.setText(
                f"modo={mode} · visível={vis} · src={src or '—'} · "
                f"arquivo={path.name if path.is_file() else '—'}"
            )
            if hasattr(self, "_brand_url") and b.get("url"):
                self._brand_url.setText(str(b.get("url") or ""))
            lab = getattr(self, "_brand_preview", None)
            if lab is None:
                return
            if mode == "hidden" or not vis:
                lab.setPixmap(QPixmap())
                lab.setText("logo oculta nas dashboards")
                return
            if path.is_file():
                pm = QPixmap(str(path))
                if not pm.isNull():
                    lab.setText("")
                    lab.setPixmap(
                        pm.scaled(140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                    return
            lab.setText("(sem imagem)")
            lab.setPixmap(QPixmap())
        except Exception as e:  # noqa: BLE001
            if hasattr(self, "_brand_status"):
                self._brand_status.setText(str(e))

    def _brand_after_change(self, note: str) -> None:
        # Só atualiza preview da logo das dashboards — CRT (cérebro) não muda
        self._append_log("ok", note)
        self._brand_refresh_preview()

    def _brand_pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Escolher logo",
            str(_ROOT),
            "Imagens (*.png *.jpg *.jpeg *.webp *.svg);;Todos (*.*)",
        )
        if not path:
            return
        try:
            from brand import apply_logo_file

            apply_logo_file(path)
            self._brand_after_change(f"Logo aplicada: {Path(path).name}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Logo", str(e))

    def _brand_apply_url(self) -> None:
        url = (self._brand_url.text() if hasattr(self, "_brand_url") else "").strip()
        if not url:
            QMessageBox.information(self, "Logo", "Cole uma URL de imagem.")
            return
        try:
            from brand import apply_logo_url

            apply_logo_url(url)
            self._brand_after_change(f"Logo via URL: {url[:80]}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Logo", str(e))

    def _brand_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar logo",
            str(_ROOT / "logo-binho-export.png"),
            "PNG (*.png);;Todos (*.*)",
        )
        if not path:
            return
        try:
            from brand import export_logo

            out = export_logo(path)
            self._append_log("ok", f"Logo exportada: {out}")
            QMessageBox.information(self, "Logo", f"Salvo em:\n{out}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Logo", str(e))

    def _brand_hide_all(self) -> None:
        try:
            from brand import hide_everywhere

            hide_everywhere()
            self._brand_after_change("Logo removida de todas as dashboards")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Logo", str(e))

    def _brand_show_all(self) -> None:
        try:
            from brand import show_everywhere

            show_everywhere()
            self._brand_after_change("Logo visível em todas as dashboards")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Logo", str(e))

    def _brand_publish(self) -> None:
        try:
            from brand import publish_brand

            ok, msg = publish_brand(push_sheets=True, push_git=True)
            kind = "ok" if ok else "erro"
            self._append_log(kind, f"Publicar marca: {msg}")
            QMessageBox.information(self, "Logo · publicar", msg)
            self._brand_refresh_preview()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Logo", str(e))

    def _build_gestao_tab(self) -> QWidget:
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Sem códigos · sem repetir o bloco Rápido do centro
        lay.addWidget(self._section("Equipe"))
        for label, cmd in (
            ("Atualizar conferentes", "177"),
            ("Atualizar nomes", "607"),
        ):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, c=cmd: self.run_command(c))
            lay.addWidget(b)

        lay.addWidget(self._section("Armazém"))
        for label, cmd in (
            ("Enviar só o armazém", "sync78"),
        ):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, c=cmd: self.run_command(c))
            lay.addWidget(b)

        lay.addWidget(self._section("Pendência"))
        for label, cmd in (
            ("Puxar pendência (10 códigos · SLA)", "31"),
            ("Enviar só a pendência", "sync31"),
        ):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, c=cmd: self.run_command(c))
            lay.addWidget(b)

        lay.addWidget(self._section("Mapa Operacional"))
        for label, cmd in (
            ("Puxar mapa (50 · 103 · 36)", "mapa"),
        ):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, c=cmd: self.run_command(c))
            lay.addWidget(b)

        lay.addWidget(self._section("Contratação (hoje)"))
        for label, cmd in (
            ("Puxar 073→filiais 200 (frete)", "73"),
            ("Só 073 hoje (sem frete 200)", "73 so73"),
        ):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, c=cmd: self.run_command(c))
            lay.addWidget(b)

        lay.addWidget(self._section("Publicar"))
        for label, cmd in (
            ("Ver situação da publicação", "status"),
            ("Publicar no site", "push"),
            ("Trazer atualizações", "pull"),
            ("Ajuda", "help"),
        ):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, c=cmd: self.run_command(c))
            lay.addWidget(b)

        # Automático: preferir aba Automação (tempos por setor)
        lay.addWidget(self._section("Atualização contínua"))
        tip = QLabel("Tempos e setores: aba Automação · aqui só liga/para rápido")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        row = QHBoxLayout()
        self.auto_iv = QLineEdit()
        self.auto_iv.setPlaceholderText("opcional · força fallback (ex.: 5m)")
        btn_auto = QPushButton("Iniciar")
        btn_auto.setObjectName("primary")
        btn_auto.clicked.connect(self._start_auto)
        btn_stop = QPushButton("Parar")
        btn_stop.clicked.connect(self._stop_auto)
        btn_goto = QPushButton("Abrir Automação")
        btn_goto.clicked.connect(self._goto_automacao_tab)
        row.addWidget(self.auto_iv, 1)
        row.addWidget(btn_auto)
        row.addWidget(btn_stop)
        lay.addLayout(row)
        lay.addWidget(btn_goto)

        lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        return wrap

    def _goto_automacao_tab(self) -> None:
        self._show_menu_window("automacao")

    def _section(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("section")
        return lab

    # ── data / actions ─────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout_chrome()
        # Debounce: Windows manda vários sizes intermediários ao soltar/redimensionar
        self._resize_layout_token = int(getattr(self, "_resize_layout_token", 0)) + 1
        token = self._resize_layout_token

        def _after() -> None:
            if token != getattr(self, "_resize_layout_token", 0):
                return
            self._clamp_main_splitter()
            self._relayout_chrome()
            self._wire_circuit_bus()
            if hasattr(self, "cubes"):
                self.cubes.update()

        QTimer.singleShot(40, _after)
        QTimer.singleShot(140, _after)

    def _clamp_main_splitter(self) -> None:
        """Mantém o painel esquerdo utilizável em janela normal (não esmaga o cérebro)."""
        sp = getattr(self, "_main_split", None)
        if sp is None:
            return
        try:
            sizes = sp.sizes()
            if len(sizes) < 2:
                return
            total = sum(sizes)
            if total < 200:
                return
            left, right = int(sizes[0]), int(sizes[1])
            min_left = 260
            max_left = max(min_left, int(total * 0.45))
            # Preferência estável ~1/3, só corrige extremos
            if left < min_left:
                left = min_left
            elif left > max_left and total >= 700:
                left = max_left
            else:
                return
            right = max(280, total - left)
            sp.setSizes([left, right])
        except Exception:
            pass

    def _relayout_chrome(self) -> None:
        """Reencaixa scanlines + circuitos após resize / sair de tela cheia."""
        r = self.rect()
        if r.width() < 50 or r.height() < 50:
            return
        if hasattr(self, "_scan") and self._scan is not None:
            self._scan.setGeometry(r)
            self._scan.raise_()
            self._scan.update()
        bus = getattr(self, "_circuit_bus", None)
        if bus is not None:
            # Windows + WA_TranslucentBackground: esconder/mostrar limpa fantasma
            was = bus.isVisible()
            if was:
                bus.hide()
            bus.setGeometry(r)
            if was:
                bus.show()
            bus.raise_()
            bus.update()
        lock = getattr(self, "_lock", None)
        if lock is not None:
            lock.setGeometry(r)
            if lock.is_locked():
                lock.raise_()
        cubes = getattr(self, "cubes", None)
        if cubes is not None:
            cubes.update()
        # força o splitter a recalcular (evita painel esquerdo “esmagado”)
        try:
            for sp in self.findChildren(QSplitter):
                sizes = sp.sizes()
                if sizes and all(s >= 0 for s in sizes):
                    sp.setSizes(sizes)
                sp.updateGeometry()
                sp.update()
        except Exception:
            pass

    def _wire_circuit_bus(self) -> None:
        bus = getattr(self, "_circuit_bus", None)
        if bus is None or not hasattr(self, "cubes"):
            return
        bus.setGeometry(self.rect())
        meters = dict(getattr(self, "_sector_meters", {}) or {})
        # tambem liga nas barras locais CPU/MEM/GPU
        for key, attr in (("cpu", "meter_cpu"), ("mem", "meter_mem"), ("gpu", "meter_gpu")):
            w = getattr(self, attr, None)
            if w is not None:
                meters[key] = w
        bus.bind(
            brain=self.cubes,
            meters=meters,
            main_bar=getattr(self, "bar", None),
        )
        bus.raise_()

    def _sync_brain_activity(self, *, cmd_busy: bool, auto_on: bool, rows: list | None = None) -> None:
        active: set[str] = set()
        full = bool(auto_on)
        if cmd_busy:
            try:
                from ace_cmd import sectors_for_command

                active.update(sectors_for_command(getattr(self, "_worker_cmd", "") or ""))
            except Exception:
                pass
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            if str(r.get("state") or "") == "run":
                sid = str(r.get("id") or "")
                if sid:
                    active.add(sid)
        if full:
            active = set((getattr(self, "_sector_meters", {}) or {}).keys())
        if hasattr(self, "cubes"):
            try:
                self.cubes.set_activity(active, full=full)
            except Exception:
                try:
                    self.cubes.set_busy(cmd_busy or auto_on)
                except Exception:
                    pass
        bus = getattr(self, "_circuit_bus", None)
        if bus is not None:
            try:
                bus.set_activity(active, full=full)
            except Exception:
                pass

    def _load_logo(self) -> None:
        # Mantido por compatibilidade; painel esquerdo usa BinhoCubesWidget.
        return

    def _refresh_sys_meters(self) -> None:
        try:
            from sys_monitor import sample_usage

            u = sample_usage()
        except Exception:
            return
        self.meter_cpu.set_pct(u.get("cpu"))
        self.meter_mem.set_pct(u.get("mem"))
        self.meter_gpu.set_pct(u.get("gpu"))

    def _reload_payload(self) -> None:
        from ace_cmd import EDITABLE, _load_payload

        self.payload = _load_payload()
        for key, (_g, typ, _secret) in EDITABLE.items():
            w = self._fields.get(key)
            if w is None:
                continue
            val = self.payload.get(key, "")
            if isinstance(w, QCheckBox):
                w.setChecked(bool(val))
            elif isinstance(w, QComboBox):
                default = "auto" if key == "publish_target" else "diario"
                s = str(val or default)
                idx = w.findData(s)
                if idx < 0:
                    idx = w.findText(s)
                w.setCurrentIndex(idx if idx >= 0 else 0)
            elif isinstance(w, QLineEdit):
                w.setText("" if val is None else str(val))
        self.chk_viz.setChecked(not bool(self.payload.get("headless", True)))
        # Tema do CRT é fixo (Circuitos)
        self._load_frost_sliders_from_payload()
        self._apply_theme(DEFAULT_CRT_THEME, persist=False)
        self._seed_sector_bars_from_config()
        self._update_meta()
        self._append_log("config", "Configuração recarregada.")

    def _frost_alpha_val(self) -> int:
        if hasattr(self, "sld_frost_alpha"):
            return int(self.sld_frost_alpha.value())
        try:
            return max(0, min(100, int((self.payload or {}).get("crt_frost_alpha", 55))))
        except Exception:
            return 55

    def _frost_blur_val(self) -> int:
        if hasattr(self, "sld_frost_blur"):
            return int(self.sld_frost_blur.value())
        try:
            return max(0, min(100, int((self.payload or {}).get("crt_frost_blur", 70))))
        except Exception:
            return 70

    def _load_frost_sliders_from_payload(self) -> None:
        p = self.payload or {}
        try:
            a = max(0, min(100, int(p.get("crt_frost_alpha", 55))))
        except Exception:
            a = 55
        try:
            b = max(0, min(100, int(p.get("crt_frost_blur", 70))))
        except Exception:
            b = 70
        for sld, val, lbl, suf in (
            (getattr(self, "sld_frost_alpha", None), a, getattr(self, "lbl_frost_alpha", None), "%"),
            (getattr(self, "sld_frost_blur", None), b, getattr(self, "lbl_frost_blur", None), "%"),
        ):
            if sld is None:
                continue
            sld.blockSignals(True)
            sld.setValue(val)
            sld.blockSignals(False)
            if lbl is not None:
                lbl.setText(f"{val}{suf}")
        self._sync_frost_controls_enabled()

    def _sync_frost_controls_enabled(self) -> None:
        on = getattr(self, "_theme_id", "") == "fosco"
        for w in (
            getattr(self, "sld_frost_alpha", None),
            getattr(self, "sld_frost_blur", None),
            getattr(self, "lbl_frost_alpha", None),
            getattr(self, "lbl_frost_blur", None),
        ):
            if w is not None:
                w.setEnabled(on)

    def _on_frost_alpha(self, value: int) -> None:
        if hasattr(self, "lbl_frost_alpha"):
            self.lbl_frost_alpha.setText(f"{int(value)}%")
        if getattr(self, "_theme_id", "") == "fosco":
            self._apply_theme("fosco", persist=False)
            self._schedule_frost_refresh()

    def _on_frost_blur(self, value: int) -> None:
        if hasattr(self, "lbl_frost_blur"):
            self.lbl_frost_blur.setText(f"{int(value)}%")
        if getattr(self, "_theme_id", "") == "fosco":
            self._apply_theme("fosco", persist=False)
            self._schedule_frost_refresh()

    def _schedule_frost_refresh(self) -> None:
        """Reaplica acrylic após o stylesheet (Windows às vezes “come” o blur)."""
        try:
            self._frost_refresh_token = int(getattr(self, "_frost_refresh_token", 0)) + 1
            token = self._frost_refresh_token

            def _go() -> None:
                if token != getattr(self, "_frost_refresh_token", 0):
                    return
                if getattr(self, "_theme_id", "") != "fosco":
                    return
                fp = frost_params(self._frost_alpha_val(), self._frost_blur_val())
                self._apply_frost_window(
                    True,
                    int(fp["tint"]),
                    int(fp["state"]),
                    opacity=float(fp["opacity"]),
                )
                self._sync_menu_window_chrome()

            QTimer.singleShot(50, _go)
            QTimer.singleShot(250, _go)
        except Exception:
            pass

    def _store_frost_into_payload(self) -> None:
        if not isinstance(getattr(self, "payload", None), dict):
            self.payload = {}
        self.payload["crt_frost_alpha"] = self._frost_alpha_val()
        self.payload["crt_frost_blur"] = self._frost_blur_val()

    def _on_theme_combo(self) -> None:
        # Tema travado — seletor removido da UI
        self._apply_theme(DEFAULT_CRT_THEME, persist=True)

    def _on_theme_combo_cfg(self) -> None:
        self._apply_theme(DEFAULT_CRT_THEME, persist=True)

    def _apply_theme(self, theme_id: str, *, persist: bool = True) -> None:
        # Tema do programa é fixo; ignora ids antigos salvos no payload
        tid = DEFAULT_CRT_THEME
        _ = theme_id  # compat assinatura
        self._theme_id = tid
        fa, fb = self._frost_alpha_val(), self._frost_blur_val()
        ss = build_crt_stylesheet(tid, frost_alpha=fa, frost_blur=fb)
        self.setStyleSheet(ss)
        meta = CRT_THEMES[tid]
        frost = bool(meta.get("frost"))
        if hasattr(self, "_scan"):
            on = bool(meta.get("scan", True)) and not frost
            self._scan.set_enabled(on)
            self._scan.setVisible(on)
        if hasattr(self, "cubes"):
            brainish = tid in {"circuitos", "painel"} or bool(meta.get("brain_glow"))
            self.cubes.set_green_glow(not frost and tid == "binho")
            self.cubes.set_cyan_glow(not frost)
            if frost:
                cube_a = max(10, min(120, int(140 - fa * 1.2)))
                fill = QColor(10, 14, 20, cube_a)
            elif tid == "claro":
                fill = QColor("#e8edf2")
            elif tid == "painel":
                fill = QColor("#050a14")
            elif tid == "ops":
                fill = QColor("#080b09")
            elif tid == "circuitos":
                fill = QColor("#030712")
            else:
                fill = QColor(str(meta.get("bg") or "#050505"))
                if fill.alpha() == 0 or str(meta.get("bg")) == "transparent":
                    fill = QColor("#0a0e14")
            accents = [
                str(meta.get("chunk0") or meta.get("text")),
                str(meta.get("chunk1") or meta.get("text")),
                str(meta.get("chunk2") or meta.get("text")),
                str(meta.get("text") or "#67e8f9"),
                str(meta.get("dim") or meta.get("text")),
                str(meta.get("chunk1") or meta.get("text")),
            ]
            # tema claro: usa chunk (mais vivo) em vez do texto escuro
            accent = str(meta.get("chunk1") or meta.get("text") or "#38bdf8")
            if tid == "binho":
                accent = str(meta.get("chunk2") or "#8cc63f")
            tint_alpha = 120 if tid == "claro" else (155 if frost else 180)
            try:
                self.cubes.set_theme_palette(
                    accent=accent,
                    glow=str(meta.get("chunk1") or accent),
                    accents=accents,
                    tint_alpha=tint_alpha,
                    fill=fill,
                )
            except Exception:
                self.cubes.set_fill_color(fill)
            try:
                self.cubes.reload_brand_asset()
            except Exception:
                pass
            bus = getattr(self, "_circuit_bus", None)
            if bus is not None:
                try:
                    bus.set_theme_palette(accent=accent, accents=accents)
                except Exception:
                    pass
        meter_h = int(meta.get("meter_h") or (18 if frost else 14))
        track = "rgba(0,0,0,160)" if frost else "#0a0a0a"
        border = "rgba(255,255,255,30)" if frost else "#222"
        for meter in (
            getattr(self, "meter_cpu", None),
            getattr(self, "meter_mem", None),
            getattr(self, "meter_gpu", None),
        ):
            if meter is not None:
                meter.apply_chrome(height=meter_h, track=track, border=border)
        for meter in (getattr(self, "_sector_meters", {}) or {}).values():
            try:
                meter.apply_chrome(height=max(12, meter_h - 2), track=track, border=border)
            except Exception:
                pass
        if hasattr(self, "bar"):
            self.bar.setTextVisible(True)
            self.bar.setFixedHeight(meter_h + 2 if frost else 16)
        self._harden_frost_widgets(frost)
        fp = frost_params(fa, fb) if frost else None
        tint = int(fp["tint"]) if fp else int(meta.get("acrylic_tint") or 0x401A1A1A)
        state = int(fp["state"]) if fp else 4
        opacity = float(fp["opacity"]) if fp else 1.0
        self._frost_active = frost
        self._apply_frost_window(frost, tint, state, opacity=opacity)
        self._sync_menu_window_chrome()
        if frost:
            self._schedule_frost_refresh()
        self._sync_frost_controls_enabled()
        for cmb in (getattr(self, "cmb_theme", None), getattr(self, "cmb_theme_cfg", None)):
            if cmb is None:
                continue
            cmb.blockSignals(True)
            i = cmb.findData(tid)
            if i >= 0:
                cmb.setCurrentIndex(i)
            cmb.blockSignals(False)
        if persist:
            try:
                from ace_cmd import _load_payload, _save_payload

                self.payload = _load_payload()
                self.payload["crt_theme"] = tid
                self._store_frost_into_payload()
                _save_payload(self.payload)
                lab = str(meta.get("label") or tid)
                self._append_log("config", f"Tema: {lab}")
            except Exception:  # noqa: BLE001
                pass

    def _harden_frost_widgets(self, frost: bool) -> None:
        """Evita fantasma de texto sem tapar o blur (painéis ficam translúcidos)."""
        # Labels que atualizam: fundo via stylesheet; NÃO forçar fill opaco preto
        widgets = []
        for name in (
            "title", "mode", "status", "detail", "foot", "meta",
            "sys_host", "sys_host_sub",
        ):
            w = getattr(self, name, None)
            if w is not None:
                widgets.append(w)
        for meter in (
            getattr(self, "meter_cpu", None),
            getattr(self, "meter_mem", None),
            getattr(self, "meter_gpu", None),
        ):
            if meter is None:
                continue
            widgets.append(meter)
            for child in (
                getattr(meter, "_title", None),
                getattr(meter, "_val", None),
                getattr(meter, "_bar", None),
            ):
                if child is not None:
                    widgets.append(child)
        for w in widgets:
            try:
                w.setAttribute(Qt.WA_StyledBackground, True)
                # OpaquePaintEvent + AutoFillBackground = preto sólido (mata o fosco)
                w.setAttribute(Qt.WA_OpaquePaintEvent, False)
                w.setAutoFillBackground(False)
            except Exception:
                pass
        # Painéis: só stylesheet rgba — deixa o acrylic aparecer
        for fr in self.findChildren(QFrame):
            try:
                if fr.objectName() in {"panel", "side"}:
                    fr.setAttribute(Qt.WA_StyledBackground, True)
                    fr.setAttribute(Qt.WA_OpaquePaintEvent, False)
                    fr.setAutoFillBackground(False)
            except Exception:
                pass
        # Log: opaco de propósito (texto não pode fantasma)
        self._setup_opaque_log()

    def _setup_opaque_log(self) -> None:
        """Fundo sólido no console — evita texto empilhado no tema fosco/transparente."""
        if not hasattr(self, "log"):
            return
        log_bg = QColor("#0a0e14")
        text_col = QColor("#eef3f8")
        try:
            self.log.setAttribute(Qt.WA_OpaquePaintEvent, True)
            self.log.setAutoFillBackground(True)
            self.log.setAttribute(Qt.WA_StyledBackground, True)
            self.log.setAttribute(Qt.WA_TranslucentBackground, False)
            self.log.setAttribute(Qt.WA_NoSystemBackground, False)
            pal = self.log.palette()
            pal.setColor(QPalette.Base, log_bg)
            pal.setColor(QPalette.Window, log_bg)
            pal.setColor(QPalette.Text, text_col)
            self.log.setPalette(pal)
            vp = self.log.viewport()
            vp.setAutoFillBackground(True)
            vp.setAttribute(Qt.WA_OpaquePaintEvent, True)
            vp.setAttribute(Qt.WA_TranslucentBackground, False)
            vpal = vp.palette()
            vpal.setColor(QPalette.Base, log_bg)
            vpal.setColor(QPalette.Window, log_bg)
            vp.setPalette(vpal)
            self.log.document().setDefaultStyleSheet(
                "body { background-color: #0a0e14; color: #eef3f8; }"
            )
            self.log.setStyleSheet(
                "QTextEdit#crtLog, QTextEdit#crtLog::viewport {"
                " background-color: #0a0e14; color: #eef3f8;"
                " border: 1px solid rgba(180,190,205,55); }"
            )
            # Brush do documento (QTextDocument) — reforço contra fantasma
            try:
                root = self.log.document().rootFrame()
                fmt = root.frameFormat()
                fmt.setBackground(QBrush(log_bg))
                root.setFrameFormat(fmt)
            except Exception:
                pass
        except Exception:
            pass

    def _apply_frost_on_widget(
        self,
        widget: QWidget,
        enabled: bool,
        tint: int = 0x401A1A1A,
        accent_state: int = 4,
        *,
        opacity: float = 1.0,
    ) -> None:
        """Aplica translucent/DWM/opacidade em qualquer janela (CRT ou Menu)."""
        win11 = _windows_build() >= 22000
        use_translucent = bool(enabled) and (not win11)
        widget.setAttribute(Qt.WA_TranslucentBackground, use_translucent)
        widget.setAttribute(Qt.WA_NoSystemBackground, use_translucent)
        widget.setAutoFillBackground(False)
        try:
            widget.setAttribute(Qt.WA_OpaquePaintEvent, False)
        except Exception:
            pass
        widget.setAttribute(Qt.WA_StyledBackground, True)
        pal = widget.palette()
        if enabled:
            clear = QColor(0, 0, 0, 0)
            pal.setColor(QPalette.Window, clear)
            pal.setColor(QPalette.Base, clear)
            pal.setBrush(QPalette.Window, QBrush(clear))
            pal.setBrush(QPalette.Base, QBrush(clear))
        widget.setPalette(pal)
        try:
            if enabled:
                widget.setWindowOpacity(max(0.40, min(1.0, float(opacity))))
            else:
                widget.setWindowOpacity(1.0)
        except Exception:
            pass
        try:
            hwnd = int(widget.winId())
        except Exception:
            hwnd = 0
        if hwnd:
            apply_windows_acrylic(
                hwnd, enabled, tint_aabbggrr=tint, accent_state=accent_state
            )
        widget.update()

    def _apply_frost_window(
        self,
        enabled: bool,
        tint: int = 0x401A1A1A,
        accent_state: int = 4,
        *,
        opacity: float = 1.0,
        force_rebuild: bool = False,  # compat
    ) -> None:
        """Fosco DWM + transparência real via setWindowOpacity (CRT + Menu)."""
        _ = force_rebuild
        self._apply_frost_on_widget(
            self, enabled, tint, accent_state, opacity=opacity
        )
        win = getattr(self, "_menu_win", None)
        if win is not None:
            self._apply_frost_on_widget(
                win, enabled, tint, accent_state, opacity=opacity
            )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_startup_windowed_done", False):
            QTimer.singleShot(0, self._ensure_startup_windowed)
        meta = CRT_THEMES.get(self._theme_id) or {}
        if meta.get("frost"):
            self._schedule_frost_refresh()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        try:
            from PySide6.QtCore import QEvent

            if event.type() == QEvent.WindowStateChange:
                # overlay/cérebro ficam desalinhados ao sair de tela cheia
                QTimer.singleShot(0, self._relayout_chrome)
                QTimer.singleShot(60, self._relayout_chrome)
                QTimer.singleShot(80, self._clamp_main_splitter)
                QTimer.singleShot(180, self._wire_circuit_bus)
                meta = CRT_THEMES.get(self._theme_id) or {}
                if meta.get("frost"):
                    # Uma reaplicação após maximizar/tela cheia (evita churn)
                    self._schedule_frost_refresh()
        except Exception:
            pass

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if self._is_ui_locked():
            # Só o overlay do cadeado recebe input (campo senha)
            lock = getattr(self, "_lock", None)
            if lock is not None:
                lock.raise_()
            event.accept()
            return
        if key == Qt.Key_F2:
            self._toggle_menu_window()
            event.accept()
            return
        if key == Qt.Key_F11:
            self.toggle_fullscreen()
            event.accept()
            return
        if key == Qt.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen(force=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def toggle_fullscreen(self, force: bool | None = None) -> None:
        """Liga/desliga tela cheia (comando `tela cheia` / F11 / Esc)."""
        want = (not self.isFullScreen()) if force is None else bool(force)
        if want:
            self._pre_fs_state = "maximized" if self.isMaximized() else "normal"
            if self._pre_fs_state == "normal":
                self._remember_normal_geom()
            self.showFullScreen()
            self._append_log("sistema", "Tela cheia · `tela cheia` ou Esc / F11 para sair")
        else:
            if getattr(self, "_pre_fs_state", "normal") == "maximized":
                self.showMaximized()
            else:
                self.showNormal()
                if self._normal_geom is not None:
                    self.setGeometry(self._normal_geom)
                elif self.width() < 900 or self.height() < 500:
                    self.resize(1180, 680)
                    self._center_on_screen()
            self._append_log("sistema", "Modo janela")
            # relayout atrasado: o Windows ainda reporta tamanho fullscreen no frame seguinte
            QTimer.singleShot(0, self._relayout_chrome)
            QTimer.singleShot(50, self._relayout_chrome)
            QTimer.singleShot(80, self._clamp_main_splitter)
            QTimer.singleShot(120, self._wire_circuit_bus)
            QTimer.singleShot(200, self._relayout_chrome)
            QTimer.singleShot(200, self._relayout_chrome)
        meta = CRT_THEMES.get(self._theme_id) or {}
        if meta.get("frost"):
            # Só reaplica DWM — sem destroy (mantém chrome nativo − □ ✕)
            self._schedule_frost_refresh()
        else:
            QTimer.singleShot(80, self._relayout_chrome)

    def _save_config(self) -> None:
        from ace_cmd import EDITABLE, _save_payload

        try:
            for key, (_g, typ, _secret) in EDITABLE.items():
                w = self._fields.get(key)
                if w is None:
                    continue
                if isinstance(w, QCheckBox):
                    self.payload[key] = w.isChecked()
                elif isinstance(w, QComboBox):
                    data = w.currentData()
                    self.payload[key] = str(data if data is not None else w.currentText()).strip()
                elif isinstance(w, QLineEdit):
                    text = w.text().strip()
                    if typ == "int":
                        try:
                            self.payload[key] = int(text or "0")
                        except ValueError:
                            raise ValueError(f"{key} deve ser número (ex.: 8787)") from None
                    elif key == "loop_intervalo" or key.endswith("_intervalo"):
                        if not text and key != "loop_intervalo":
                            self.payload[key] = ""
                        else:
                            from interval_parse import format_duration, parse_duration

                            try:
                                self.payload[key] = format_duration(parse_duration(text or "5m"))
                            except ValueError as err:
                                raise ValueError(f"{_field_label(key)}: {err}") from err
                    else:
                        self.payload[key] = text
            self.payload["headless"] = not self.chk_viz.isChecked()
            self.payload["crt_theme"] = self._theme_id
            self._store_frost_into_payload()
            _save_payload(self.payload)
            self.payload = __import__("ace_cmd", fromlist=["_load_payload"])._load_payload()
            self._update_meta()
            self._append_log("config", "Configuração salva.")
            idle = self._idle_sector_rows_from_config()
            publish(
                online=True,
                label="ONLINE",
                pct=0,
                detail="configuração salva",
                mode="OK",
                sectors=idle,
            )
            QMessageBox.information(self, "ACE", "Configuração salva.")
        except Exception as err:  # noqa: BLE001
            self._append_log("erro", str(err))
            QMessageBox.warning(self, "ACE", f"Falha ao salvar:\n{err}")

    def _update_meta(self) -> None:
        p = self.payload or {}
        user = str(p.get("user") or "—")
        units = str(p.get("unit") or "—").strip() or "—"
        viz = "navegador ligado" if not p.get("headless", True) else "navegador oculto"
        sheets = "planilha ligada" if p.get("enable_sheets") else "planilha desligada"
        try:
            from config import resolve_publish_target, load_settings

            dest = resolve_publish_target(load_settings())
        except Exception:
            dest = str(p.get("publish_target") or "auto")
        dest_txt = {
            "sites": "Google Sites",
            "github": "GitHub Pages",
            "local": "só local (LAN/CSV)",
            "auto": "auto",
        }.get(str(dest), str(dest))
        modo = str(p.get("periodo_modo") or "diario")
        modo_txt = (
            "período diário (hoje)"
            if modo == "diario"
            else "período a partir da sexta (até hoje)"
        )
        fallback = str(p.get("loop_intervalo") or "5m")

        lines: list[str] = [
            f"<b>Sessão</b> · {user}",
            f"Unidades SSW: <b>{units}</b><br>"
            f"<span style='opacity:0.75'>Filiais em que o login busca os relatórios.</span>",
            f"{sheets} · TV={dest_txt} · {viz}",
            f"Automático · padrão <b>{fallback}</b> · {modo_txt}",
            "<b>Setores no loop</b>",
        ]
        for g in _SECTOR_GUIDE:
            default_on = g["id"] != "455"
            on = bool(p.get(g["flag"], default_on))
            iv = str(p.get(g["interval"]) or "").strip() or fallback
            mark = "ON" if on else "off"
            color = "#67e8f9" if on else "#64748b"
            lines.append(
                f"<span style='color:{color}'><b>{g['title']}</b> [{mark}]</span> "
                f"a cada {iv}<br>"
                f"SSW {g['reports']}<br>"
                f"<span style='opacity:0.8'>{g['blurb']}</span>"
            )
        tip = (
            "<span style='opacity:0.7'>Ajuste setores/tempos em Menu → Automação. "
            "Logo das TVs em Configuração.</span>"
        )
        lines.append(tip)
        self.meta.setText("<br><br>".join(lines))
        self.meta.setToolTip(
            "Resumo da sessão e do que cada setor do automático puxa no SSW."
        )

    def _submit_prompt(self) -> None:
        raw = self.prompt.text().strip()
        if not raw:
            return
        self.prompt.clear()
        self.run_command(raw)

    def _start_auto(self) -> None:
        iv = (self.auto_iv.text() or "").strip()
        self._start_automatica(iv or None)

    def _stop_auto(self) -> None:
        """Compat: botões Parar da UI → para tudo (loop + comando)."""
        self._stop_all()

    def _stop_all(self) -> None:
        """Para QUALQUER comando/loop/processo ACE em andamento."""
        self._pending_cmd = None
        cmd_running = bool(self._worker and self._worker.isRunning())
        auto_running = bool(self._auto_worker and self._auto_worker.isRunning())
        killed = 0
        closed = 0
        try:
            from ace_stop import (
                close_registered_browsers,
                kill_child_browsers,
                request_stop,
                stop_external_loop_process,
            )

            request_stop(force_browsers=True)
            # reforço: mata de novo (alguns drivers sobem atrasados)
            closed = close_registered_browsers()
            killed = kill_child_browsers()
            ext = stop_external_loop_process()
        except Exception:
            ext = False

        if auto_running:
            try:
                self._auto_worker.request_stop()
            except Exception:
                pass

        if cmd_running or auto_running or ext or killed or closed:
            self.mode.setText("STOP")
            detail = []
            if cmd_running:
                detail.append(f"comando `{self._worker_cmd or 'atual'}`")
            if auto_running:
                detail.append("loop contínuo")
            if ext:
                detail.append("loop externo")
            if killed or closed:
                detail.append(f"navegadores ({closed} fechados · {killed} processos)")
            msg = (
                "Parar: interrompe qualquer processo ACE — "
                + (" · ".join(detail) if detail else "sinal enviado")
            )
            self._append_log("sistema", msg)
            if hasattr(self, "auto_status"):
                self.auto_status.setText("Parando todos os processos…")
            idle = self._idle_sector_rows_from_config()
            publish(
                online=True,
                label="STOP",
                pct=0,
                detail="parando tudo",
                mode="STOP",
                sectors=idle,
            )
            self._seed_sector_bars_from_config(persist=False)
            # reabilita prompt mesmo se o worker ainda estiver morrendo
            try:
                self.btn_run.setEnabled(True)
            except Exception:
                pass
        else:
            self._append_log(
                "sistema",
                "Parar: nada em execução (já parado). Sinal limpo para o próximo comando.",
            )
            if hasattr(self, "auto_status"):
                self.auto_status.setText("Automático parado.")
            self._seed_sector_bars_from_config(persist=True)

    def _start_automatica(self, interval_arg: str | None = None) -> None:
        if self._auto_worker and self._auto_worker.isRunning():
            self._append_log("sistema", "Já está em atualização contínua. Use Parar.")
            return
        if self._worker and self._worker.isRunning():
            self._append_log("sistema", "Aguarde o comando atual terminar…")
            return
        tip = interval_arg or "intervalo da config"
        self._append_log("cmd", f"atualização contínua ({tip})")
        self._apply_cmd_view("bars", announce=False)
        self.mode.setText("LOOP")
        idle = self._idle_sector_rows_from_config()
        for row in idle:
            if row.get("enabled"):
                row["state"] = "due"
                row["detail"] = "automático ligado · na fila"
                row["pct"] = 0.0
        publish(
            online=True,
            label="LOOP",
            pct=5,
            detail="atualização contínua",
            mode="RUN",
            sectors=idle,
        )
        if hasattr(self, "sector_status"):
            self.sector_status.setText("Automático ligado · sincronizando setores…")
        for row in idle:
            meter = self._sector_meters.get(str(row.get("id")))
            if meter is not None:
                meter.set_row(row)
        self._auto_worker = AutoLoopWorker(interval_arg)
        self._auto_worker.finished_ok.connect(self._on_auto_ok)
        self._auto_worker.failed.connect(self._on_auto_fail)
        self._auto_worker.start()

    def _on_auto_ok(self, msg: str) -> None:
        self._append_log("ok", msg)
        self.mode.setText("OK")
        idle = self._idle_sector_rows_from_config()
        publish(
            online=True,
            label="ONLINE",
            pct=0,
            detail=msg[:100],
            mode="OK",
            sectors=idle,
        )
        self._seed_sector_bars_from_config(persist=False)
        if hasattr(self, "auto_status"):
            self.auto_status.setText(msg)

    def _on_auto_fail(self, msg: str) -> None:
        self._append_log("erro", msg)
        self.mode.setText("ERR")
        idle = self._idle_sector_rows_from_config()
        publish(
            online=False,
            label="ERR",
            pct=0,
            detail=msg[:100],
            mode="ERR",
            sectors=idle,
        )
        self._seed_sector_bars_from_config(persist=False)
        if hasattr(self, "auto_status"):
            self.auto_status.setText(msg[:160])

    def run_command(self, raw: str) -> None:
        raw = _resolve_friendly_cmd(raw)
        if not raw:
            return
        low = raw.lower().strip()
        if low in {"bloquear", "lock", "cadeado", "/bloquear", "/lock"}:
            self._lock_panel()
            return
        if low in {"desbloquear", "unlock", "/desbloquear", "/unlock"}:
            # Desbloqueio só pelo campo do cadeado (senha)
            if self._is_ui_locked():
                self._append_log(
                    "sistema",
                    "Painel bloqueado · digite a senha no cadeado no centro da tela.",
                    mirror=False,
                )
            return
        # Com painel bloqueado: só parar continua (emergência); resto bloqueado
        if self._is_ui_locked() and low not in {"parar", "stop", "halt"}:
            self._append_log(
                "sistema",
                "Painel bloqueado · use o cadeado no centro para liberar.",
                mirror=False,
            )
            return
        if low in {"cls", "clear", "limpar", "/limpar", "/cls", "/clear"}:
            self._clear_cmd_log()
            return
        if low in {"parar", "stop", "halt"}:
            self._stop_all()
            return
        if low in {"tela", "tela cheia", "fullscreen", "full", "f11"}:
            self.toggle_fullscreen()
            return
        if low in {"menu", "config", "f2"}:
            self._show_menu_window()
            return
        if low in {"log", "/log"}:
            self._apply_cmd_view("log", announce=True)
            return
        if low in {"bars", "/bars", "barras", "/barras", "barra"}:
            self._apply_cmd_view("bars", announce=True)
            return

        parts_probe = raw.split()
        head_probe = parts_probe[0].lower().lstrip("/") if parts_probe else ""
        if head_probe in {"log"}:
            self._apply_cmd_view("log", announce=True)
            return
        if head_probe in {"bars", "barras", "barra"}:
            self._apply_cmd_view("bars", announce=True)
            return
        if head_probe in {"local", "tvlocal", "dashlocal", "telas"}:
            self._open_local_from_cmd(parts_probe[1:])
            return
        if head_probe in {"lan", "rede", "wifi"}:
            self._local_show_lan_urls(force_on=True, filter_ids=parts_probe[1:])
            return

        if self._worker and self._worker.isRunning():
            self._pending_cmd = raw
            self._append_log(
                "sistema",
                f"Fila: `{raw}` depois de `{self._worker_cmd or 'comando atual'}`.",
            )
            return

        # /automatica e atalhos → loop interno (sem janela CMD)
        parts = raw.split()
        head = parts[0].lower().lstrip("/")
        if head in {"automatica", "automática", "auto", "loop", "watch", "7"} or low.startswith(
            "atualiza"
        ):
            from ace_cmd import _parse_interval_arg

            iv = _parse_interval_arg(parts) if head in {
                "automatica", "automática", "auto", "loop", "watch", "7"
            } else None
            if iv is None and len(parts) > 1 and head not in {
                "automatica", "automática", "auto", "loop", "watch", "7"
            }:
                iv = parts[1] if parts[1:] else None
            self._start_automatica(iv)
            return

        if self._auto_worker and self._auto_worker.isRunning():
            self._append_log(
                "sistema",
                "Loop contínuo ativo — digite “parar” antes de puxar 73/31/etc.",
            )
            return

        self._worker_cmd = raw
        self._append_log("cmd", raw)
        self.btn_run.setEnabled(False)
        self.mode.setText("RUN")
        # Barrinha sobe já no clique (antes do worker) — evita tela parada no modo /bars
        try:
            from ace_cmd import begin_manual_sectors, sectors_for_command

            secs = sectors_for_command(raw)
            if secs:
                begin_manual_sectors(secs, detail=f"exec · {raw[:60]}")
            else:
                publish(online=True, label="RUN", pct=5, detail=raw[:80], mode="RUN")
        except Exception:
            publish(online=True, label="RUN", pct=5, detail=raw[:80], mode="RUN")

        self._worker = CmdWorker(raw, self.payload)
        self._worker.status.connect(self._on_worker_status)
        self._worker.finished_ok.connect(self._on_cmd_ok)
        self._worker.failed.connect(self._on_cmd_fail)
        self._worker.start()

    def _drain_pending(self) -> None:
        nxt = self._pending_cmd
        self._pending_cmd = None
        if nxt:
            self._append_log("sistema", f"Iniciando da fila: `{nxt}`")
            QTimer.singleShot(200, lambda: self.run_command(nxt))

    def _on_worker_status(self, msg: str) -> None:
        publish(online=True, label="RUN", pct=20, detail=(msg or "")[:80], mode="RUN")
        # espelha progresso no CMD do CRT (ex.: [31/13] abrindo…)
        if msg and not str(msg).startswith("exec ·"):
            self._append_log("work", msg, mirror=False)

    def _on_cmd_ok(self, msg: str, payload: object) -> None:
        self.btn_run.setEnabled(True)
        self._worker_cmd = ""
        if isinstance(payload, dict):
            self.payload = payload
            # sync form if config changed via /e
            try:
                self._reload_payload_silent()
            except Exception:
                self._update_meta()
        if msg == "__CLEAR__":
            self._clear_cmd_log(announce=False)
        else:
            self._append_log("out", msg)
        online = "erro" not in (msg or "").lower() and "falhou" not in (msg or "").lower()
        # Preserva barrinhas do end_manual (100%/erro); não zera com idle na hora
        try:
            st_now = read_status()
            keep_sectors = st_now.get("sectors") if isinstance(st_now.get("sectors"), list) else None
        except Exception:
            keep_sectors = None
        if not keep_sectors:
            keep_sectors = self._idle_sector_rows_from_config()
        publish(
            online=online,
            label="ONLINE" if online else "ERR",
            pct=100.0 if online else 0.0,
            detail=(msg or "ok")[:100],
            mode="OK" if online else "ERR",
            sectors=keep_sectors,
        )
        self.mode.setText("OK" if online else "ERR")
        self._drain_pending()
        # Depois de 2,5s volta ao idle visual (sem apagar se já começou outro comando)
        QTimer.singleShot(2500, self._maybe_seed_idle_after_cmd)

    def _maybe_seed_idle_after_cmd(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        if self._auto_worker and self._auto_worker.isRunning():
            return
        self._seed_sector_bars_from_config(persist=True)

    def _on_cmd_fail(self, msg: str) -> None:
        self.btn_run.setEnabled(True)
        self._worker_cmd = ""
        self._append_log("erro", msg)
        try:
            st_now = read_status()
            keep_sectors = st_now.get("sectors") if isinstance(st_now.get("sectors"), list) else None
        except Exception:
            keep_sectors = None
        if not keep_sectors:
            keep_sectors = self._idle_sector_rows_from_config()
        publish(
            online=False,
            label="ERR",
            pct=0,
            detail=msg[:100],
            mode="ERR",
            sectors=keep_sectors,
        )
        self.mode.setText("ERR")
        self._drain_pending()
        QTimer.singleShot(2500, self._maybe_seed_idle_after_cmd)

    def _reload_payload_silent(self) -> None:
        """Atualiza campos sem spam no log."""
        from ace_cmd import EDITABLE, _load_payload

        self.payload = _load_payload()
        for key, (_g, typ, _secret) in EDITABLE.items():
            w = self._fields.get(key)
            if w is None:
                continue
            val = self.payload.get(key, "")
            if isinstance(w, QCheckBox):
                w.setChecked(bool(val))
            elif isinstance(w, QComboBox):
                s = str(val or "diario")
                idx = w.findData(s)
                if idx < 0:
                    idx = w.findText(s)
                if idx >= 0:
                    w.setCurrentIndex(idx)
            elif isinstance(w, QLineEdit):
                # não sobrescreve password com vazio se o campo está focado em edição
                if w.hasFocus():
                    continue
                w.setText("" if val is None else str(val))
        if hasattr(self, "chk_viz"):
            self.chk_viz.setChecked(not bool(self.payload.get("headless", True)))
        self._load_frost_sliders_from_payload()
        if getattr(self, "_theme_id", None) != DEFAULT_CRT_THEME:
            self._apply_theme(DEFAULT_CRT_THEME, persist=False)
        self._update_meta()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._is_ui_locked():
            event.ignore()
            QMessageBox.information(
                self,
                "Bloqueado",
                "Desbloqueie o painel (senha no cadeado) antes de fechar.",
            )
            lock = getattr(self, "_lock", None)
            if lock is not None:
                lock.raise_()
            return
        if self._auto_worker and self._auto_worker.isRunning():
            self._auto_worker.request_stop()
            self._auto_worker.wait(3000)
        for attr in ("_menu_win", "_rapido_win"):
            win = getattr(self, attr, None)
            if win is not None:
                try:
                    win.hide()
                    win.deleteLater()
                except Exception:
                    pass
                setattr(self, attr, None)
        super().closeEvent(event)

    def _clear_cmd_log(self, *, announce: bool = True) -> None:
        """Limpa o painel + arquivo espelhado (limpar/cls/clear)."""
        try:
            from crt_bridge import clear_log

            clear_log()
        except Exception:
            pass
        try:
            self.log.clear()
        except Exception:
            pass
        self._log_seen = set()
        self._log_offset = 0
        if announce:
            self._append_log("sistema", "Log limpo.", mirror=False)

    def _append_log(self, kind: str, text: str, *, mirror: bool = True) -> None:
        if mirror:
            try:
                entry = append_log(kind, text, source="crt")
                self._render_log_entry(entry)
                return
            except Exception:
                pass
        self._render_log_entry(
            {
                "stamp": datetime.now().strftime("%H:%M:%S"),
                "kind": kind,
                "text": text,
                "ts": datetime.now().timestamp(),
                "source": "crt",
            }
        )

    def _render_log_entry(self, entry: dict, *, from_file: bool = False) -> None:
        """Mesmo visual do CMD: [HH:MM:SS] ███ OK/ERR/…  mensagem"""
        stamp = str(entry.get("stamp") or datetime.now().strftime("%H:%M:%S"))
        kind = str(entry.get("kind") or "info").lower()
        text = str(entry.get("text") or "")
        key = f"{entry.get('ts')}|{kind}|{text}"
        if key in self._log_seen:
            return
        self._log_seen.add(key)
        if len(self._log_seen) > 2000:
            self._log_seen = set(list(self._log_seen)[-1000:])

        t = CRT_THEMES.get(self._theme_id) or CRT_THEMES[DEFAULT_CRT_THEME]
        accent = str(t["text"])
        dim = str(t["dim"])
        muted = str(t.get("muted") or dim)
        warn = WARN if self._theme_id not in {"claro"} else "#b45309"
        color = {
            "ok": accent,
            "err": ERR,
            "erro": ERR,
            "work": warn,
            "cmd": accent,
            "out": dim,
            "config": warn,
            "sistema": dim,
            "info": dim,
        }.get(kind, dim)
        tag = {
            "ok": "OK ",
            "err": "ERR",
            "erro": "ERR",
            "work": "…  ",
            "cmd": "CMD",
            "out": "OUT",
            "config": "CFG",
            "sistema": "SYS",
            "info": "·  ",
        }.get(kind, "·  ")
        src = str(entry.get("source") or "")
        prefix = " ⌁" if from_file and src == "cmd" else ""
        safe = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe = safe.replace("\n", "<br>")
        # limpa lixo HTML/JS que às vezes vaza do SSW no log
        for junk in (
            "onclick=ajaxEnvia",
            "return(false);",
            "javascript:",
        ):
            if junk.lower() in safe.lower():
                safe = re.sub(
                    r"onclick\s*=\s*ajaxEnvia\([^)]*\);\s*return\s*\(\s*false\s*\);?",
                    "",
                    safe,
                    flags=re.IGNORECASE,
                )
                safe = re.sub(r"\s{2,}", " ", safe).strip()
                break
        html = (
            f'<span style="color:{muted}">[{stamp}]</span> '
            f'<span style="color:{color}"><b>███ {tag}</b></span> '
            f'<span style="color:{color}">{safe}{prefix}</span>'
        )
        # append + repaint do viewport (evita resíduo visual no fosco)
        self.log.append(html)
        self.log.moveCursor(QTextCursor.End)
        try:
            self.log.viewport().update()
        except Exception:
            pass

    def _pull_mirrored_log(self) -> None:
        try:
            entries, self._log_offset = read_log_since(self._log_offset)
            for entry in entries:
                # evita eco do que o próprio CRT acabou de gravar
                if entry.get("source") == "crt":
                    key = f"{entry.get('ts')}|{entry.get('kind')}|{entry.get('text')}"
                    if key in self._log_seen:
                        continue
                self._render_log_entry(entry, from_file=True)
        except Exception:
            pass

    def _refresh_status(self) -> None:
        self._pull_mirrored_log()
        self._sys_tick = getattr(self, "_sys_tick", 0) + 1
        # ~2 Hz para CPU/RAM (timer 250ms); GPU já é cacheado em sys_monitor
        if self._sys_tick % 2 == 0:
            self._refresh_sys_meters()
        st = read_status()
        online = bool(st.get("online", True))
        label = str(st.get("label") or ("ONLINE" if online else "OFFLINE")).upper()
        mode = str(st.get("mode") or "STANDBY").upper()
        detail = str(st.get("detail") or "")
        pct = float(st.get("pct") or 0.0)

        self.status.setText(label[:16])
        theme = CRT_THEMES.get(self._theme_id) or CRT_THEMES[DEFAULT_CRT_THEME]
        accent = str(theme["text"])
        color = accent if online and "OFF" not in label and "ERR" not in label else OFF
        if "ERR" in label or mode == "ERR":
            color = ERR
        # Sempre incluir background no fosco — senão o texto deixa resíduo
        lab_bg = str(theme.get("label_bg") or "transparent")
        if theme.get("frost"):
            self.status.setStyleSheet(
                f"color: {color}; font-family: '{CRT_FONT_FAMILY}', monospace; "
                f"font-size: 28px; font-weight: 800; "
                f"letter-spacing: 4px; background: {lab_bg}; padding: 4px 8px; border-radius: 6px;"
            )
        else:
            self.status.setStyleSheet(
                f"color: {color}; font-family: '{CRT_FONT_FAMILY}', monospace; "
                f"font-size: 28px; font-weight: 800; letter-spacing: 4px; background: transparent;"
            )
        self.detail.setText(detail[:140] if detail else "—")
        self.bar.setValue(int(round(pct * 10)))
        self.bar.setFormat(f"{pct:5.1f}%")
        self._refresh_sector_meters(st)
        if not (self._worker and self._worker.isRunning()):
            if mode and mode not in {"RUN", "BOOT"}:
                self.mode.setText(mode[:18])
        try:
            if STATUS_PATH.is_file():
                mtime = datetime.fromtimestamp(STATUS_PATH.stat().st_mtime).strftime("%H:%M:%S")
                busy = ""
                if self._worker and self._worker.isRunning():
                    busy = " · ocupado"
                elif self._auto_worker and self._auto_worker.isRunning():
                    busy = " · loop"
                self.foot.setText(f"Gestão  ·  {mtime}{busy}")
        except Exception:
            pass

    def _refresh_sector_meters(self, st: dict | None = None) -> None:
        if not getattr(self, "_sector_meters", None):
            return
        auto_on = bool(self._auto_worker and self._auto_worker.isRunning())
        cmd_busy = bool(self._worker and self._worker.isRunning())
        rows: list[dict] = []
        if isinstance(st, dict) and isinstance(st.get("sectors"), list):
            rows = [r for r in st["sectors"] if isinstance(r, dict)]
        self._sync_brain_activity(cmd_busy=cmd_busy, auto_on=auto_on, rows=rows)

        mode_u = str((st or {}).get("mode") or "").upper()
        label_u = str((st or {}).get("label") or "").upper()
        try:
            age = time.time() - float((st or {}).get("ts") or 0)
        except Exception:
            age = 9999.0

        # Loop / comando único (455, 78, …) / CRT: status RUN recente com sectors = válido
        live_loop = mode_u in {"RUN", "LOOP"} or label_u in {"LOOP", "RUN"}
        status_fresh = age < 180.0
        external_or_auto = (
            auto_on
            or cmd_busy
            or (live_loop and status_fresh and bool(rows))
        )

        # Com comando único rodando: NUNCA reseedar idle (zerava a barrinha da emissão)
        if cmd_busy or auto_on:
            if not rows:
                # ainda sem publish do worker — mantém UI; não grava idle
                return
            by_id = {str(r.get("id")): r for r in rows}
            running = False
            for sid, meter in self._sector_meters.items():
                row = by_id.get(sid)
                if row is None:
                    continue
                meter.set_row(row)
                if str(row.get("state") or "") == "run":
                    running = True
            if hasattr(self, "sector_status"):
                if running:
                    self.sector_status.setText(
                        str((st or {}).get("detail") or "Executando…")[:120]
                    )
                elif auto_on:
                    self.sector_status.setText(
                        str(
                            (st or {}).get("detail")
                            or "Automático ligado · aguardando próximos ciclos"
                        )[:120]
                    )
                else:
                    self.sector_status.setText(
                        str((st or {}).get("detail") or "Comando em andamento…")[:120]
                    )
            return

        # Só re-seed idle quando parado de verdade (MENU/STOP/OK / sem sectors)
        if not external_or_auto:
            idle_modes = mode_u in {"MENU", "STOP", "STANDBY", "OK", "ERR", ""}
            if not rows or idle_modes or age > 600:
                self._seed_sector_bars_from_config(persist=bool(not rows or age > 600))
                return

        if not rows:
            self._seed_sector_bars_from_config()
            return
        by_id = {str(r.get("id")): r for r in rows}
        running = False
        for sid, meter in self._sector_meters.items():
            row = by_id.get(sid)
            if row is None:
                continue
            meter.set_row(row)
            if str(row.get("state") or "") == "run":
                running = True
        if hasattr(self, "sector_status"):
            if running:
                self.sector_status.setText(
                    str((st or {}).get("detail") or "Executando setores…")[:120]
                )
            elif live_loop:
                self.sector_status.setText(
                    str(
                        (st or {}).get("detail")
                        or "Automático ligado · aguardando próximos ciclos"
                    )[:120]
                )
            else:
                self.sector_status.setText("Automático parado · inicie na aba Automação")

    def _idle_sector_rows_from_config(self) -> list[dict]:
        p = self.payload or {}
        specs = (
            ("dist", "Distribuição", "dist_in_loop", "dist_intervalo"),
            ("78", "Armazém", "armazem_in_loop", "armazem_intervalo"),
            ("31", "Pendência", "pendencia_in_loop", "pendencia_intervalo"),
            ("73", "Contratação", "contratacao_in_loop", "contratacao_intervalo"),
            ("455", "Emissão", "emissao_in_loop", "emissao_intervalo"),
            ("mapa", "Mapa", "mapa_in_loop", "mapa_intervalo"),
        )
        fallback = str(p.get("loop_intervalo") or "5m")
        rows: list[dict] = []
        for sid, label, flag, ivk in specs:
            default_on = sid in {"dist", "mapa"}
            enabled = bool(p.get(flag, default_on))
            iv = str(p.get(ivk) or "").strip() or fallback
            if enabled:
                rows.append(
                    {
                        "id": sid,
                        "label": label,
                        "enabled": True,
                        "state": "wait",
                        "pct": 0.0,
                        "detail": "pronto · aguardando automático",
                        "interval": iv,
                    }
                )
            else:
                rows.append(
                    {
                        "id": sid,
                        "label": label,
                        "enabled": False,
                        "state": "off",
                        "pct": 0.0,
                        "detail": "fora do automático",
                        "interval": "",
                    }
                )
        return rows

    def _seed_sector_bars_from_config(self, *, persist: bool = False) -> None:
        """Barrinhas em 0% (idle). persist=True grava no crt_status.json."""
        if not getattr(self, "_sector_meters", None):
            return
        rows = self._idle_sector_rows_from_config()
        for row in rows:
            meter = self._sector_meters.get(str(row.get("id")))
            if meter is not None:
                meter.set_row(row)
        if hasattr(self, "sector_status"):
            ons = [r["label"] for r in rows if r.get("enabled")]
            self.sector_status.setText(
                "Setores: " + (" · ".join(ons) if ons else "nenhum") + " · inicie o automático"
            )
        if persist:
            try:
                publish(
                    online=True,
                    label="ONLINE",
                    pct=0,
                    detail="painel aberto",
                    mode="MENU",
                    sectors=rows,
                )
            except Exception:
                pass


def main() -> int:
    # Necessário para QWebEngineView (preview do dashboard no editor TV)
    try:
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    except Exception:  # noqa: BLE001
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("BINHO ACE Gestão CRT")
    load_crt_font()
    app.setFont(crt_font(11))
    w = AceCrtConsole()
    w.show()  # modo janela; maximizar/tela cheia pelos botões do cabeçalho
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
