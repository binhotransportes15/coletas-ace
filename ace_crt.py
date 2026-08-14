"""
BINHO · ACE CRT — painel de gestão widescreen (cara de CMD).

Layout:
  esq  → cubos animados + CPU/MEM/GPU + status
  centro → atalhos + log + prompt de comandos
  dir  → abas Configuração | Automação | Local | TV | Gestão

  python ace_crt.py
  ace.bat crt
"""
from __future__ import annotations

import math
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QPointF
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
    QPixmap,
    QLinearGradient,
    QBrush,
    QTextCursor,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
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
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from crt_bridge import append_log, publish, read_log_since, read_status, STATUS_PATH

_ROOT = Path(__file__).resolve().parent
_CUBES = _ROOT / "assets" / "cubes-binho.png"
_LOGO = _ROOT / "assets" / "logo-binho.png"

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
        "bg": "transparent",
        "panel": "rgba(16, 22, 34, 155)",
        "line": "rgba(148, 163, 184, 95)",
        "text": "#e8eef7",
        "dim": "#9aa8bc",
        "muted": "#6b7a90",
        "input_bg": "rgba(8, 12, 20, 170)",
        "input_text": "#e2e8f0",
        "btn_bg": "rgba(22, 30, 46, 175)",
        "btn_hover": "rgba(40, 56, 82, 210)",
        "btn_press": "rgba(56, 78, 112, 230)",
        "btn_dis_bd": "rgba(50, 60, 78, 110)",
        "sel": "rgba(14, 116, 144, 190)",
        "prog_bg": "rgba(8, 12, 20, 150)",
        "chunk0": "#0e7490",
        "chunk1": "#22d3ee",
        "chunk2": "#a5f3fc",
        "scan": False,
        "frost": True,
        # Windows acrylic: 0xAABBGGRR (alpha + BGR)
        "acrylic_tint": 0xB0121824,
    },
}

DEFAULT_CRT_THEME = "binho"


class BinhoCubesWidget(QWidget):
    """Cubos Binho com animação contínua (flutuação + pulse + scan)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(132)
        self.setMaximumHeight(168)
        self._t = 0.0
        self._pm = QPixmap(str(_CUBES)) if _CUBES.is_file() else QPixmap()
        self._fill = QColor("#050505")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 fps

    def set_fill_color(self, color: QColor) -> None:
        self._fill = QColor(color)
        self.update()

    def _tick(self) -> None:
        self._t += 0.033
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, self._fill)

        t = self._t
        bob = math.sin(t * 1.35) * 4.0
        sway = math.sin(t * 0.85) * 3.0
        pulse = 0.92 + 0.08 * (0.5 + 0.5 * math.sin(t * 2.2))
        angle = math.sin(t * 0.55) * 2.4

        if not self._pm.isNull():
            target_h = int(min(h - 12, 128) * pulse)
            scaled = self._pm.scaledToHeight(target_h, Qt.SmoothTransformation)
            x = (w - scaled.width()) / 2.0 + sway
            y = (h - scaled.height()) / 2.0 + bob - 2
            p.save()
            p.translate(x + scaled.width() / 2.0, y + scaled.height() / 2.0)
            p.rotate(angle)
            p.translate(-scaled.width() / 2.0, -scaled.height() / 2.0)
            p.setOpacity(0.88 + 0.12 * (0.5 + 0.5 * math.sin(t * 1.7)))
            p.drawPixmap(0, 0, scaled)
            p.restore()
        else:
            self._paint_fallback_cubes(p, w, h, t, bob, sway, pulse)

        # scanline CRT suave
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 28))
        step = 3
        y0 = int((t * 18) % step)
        for yy in range(y0, h, step):
            p.drawRect(0, yy, w, 1)

        # brilho inferior
        glow = QLinearGradient(0, h * 0.55, 0, h)
        glow.setColorAt(0.0, QColor(0, 0, 0, 0))
        glow.setColorAt(1.0, QColor(140, 198, 63, 35))
        p.fillRect(0, int(h * 0.55), w, int(h * 0.45), glow)
        p.end()

    def _paint_fallback_cubes(
        self,
        p: QPainter,
        w: int,
        h: int,
        t: float,
        bob: float,
        sway: float,
        pulse: float,
    ) -> None:
        cx, cy = w / 2.0 + sway, h / 2.0 + bob
        size = 28.0 * pulse
        offsets = ((-38, -18), (22, -28), (-30, 22), (26, 18))
        for i, (ox, oy) in enumerate(offsets):
            phase = t * (1.1 + i * 0.17) + i
            dx = ox + math.sin(phase) * 5
            dy = oy + math.cos(phase * 0.9) * 4
            s = size * (0.85 + 0.15 * math.sin(phase * 1.3))
            self._draw_iso_cube(p, cx + dx, cy + dy, s, _CUBE_COLORS[i % 4])

    @staticmethod
    def _draw_iso_cube(p: QPainter, x: float, y: float, s: float, color: QColor) -> None:
        top = QColor(color)
        top = top.lighter(130)
        left = QColor(color)
        right = QColor(color).darker(125)
        hx, hy = s * 0.55, s * 0.32
        pts_top = [
            QPointF(x, y - hy),
            QPointF(x + hx, y),
            QPointF(x, y + hy),
            QPointF(x - hx, y),
        ]
        pts_left = [
            QPointF(x - hx, y),
            QPointF(x, y + hy),
            QPointF(x, y + hy + s * 0.55),
            QPointF(x - hx, y + s * 0.55),
        ]
        pts_right = [
            QPointF(x + hx, y),
            QPointF(x, y + hy),
            QPointF(x, y + hy + s * 0.55),
            QPointF(x + hx, y + s * 0.55),
        ]
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        for pts, col in ((pts_top, top), (pts_left, left), (pts_right, right)):
            p.setBrush(QBrush(col))
            p.drawPolygon(QPolygonF(pts))


class SysMeterRow(QWidget):
    """Barra CPU / MEM / GPU estilo gerenciador de tarefas."""

    def __init__(self, title: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._title = QLabel(title)
        self._title.setObjectName("sysMeterTitle")
        self._title.setFixedWidth(54)
        self._bar = QProgressBar()
        self._bar.setObjectName("sysMeter")
        self._bar.setRange(0, 1000)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        self._bar.setFormat("—")
        self._bar.setFixedHeight(14)
        self._val = QLabel("—")
        self._val.setObjectName("sysMeterVal")
        self._val.setFixedWidth(42)
        self._val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self._title)
        lay.addWidget(self._bar, 1)
        lay.addWidget(self._val)
        self._accent = accent
        self._apply_chunk(accent)

    def _apply_chunk(self, accent: str) -> None:
        self._bar.setStyleSheet(
            f"""
            QProgressBar#sysMeter {{
                background: #0a0a0a;
                border: 1px solid #222;
                border-radius: 2px;
                text-align: center;
                color: #bbb;
                font-size: 9px;
            }}
            QProgressBar#sysMeter::chunk {{
                background: {accent};
            }}
            """
        )

    def set_pct(self, pct: float | None, warn: float = 75.0, crit: float = 90.0) -> None:
        if pct is None:
            self._bar.setValue(0)
            self._bar.setFormat("—")
            self._val.setText("—")
            self._apply_chunk(self._accent)
            return
        v = max(0.0, min(100.0, float(pct)))
        self._bar.setValue(int(round(v * 10)))
        self._bar.setFormat(f"{v:.0f}%")
        self._val.setText(f"{v:.0f}%")
        color = self._accent
        if v >= crit:
            color = "#ef4444"
        elif v >= warn:
            color = "#f59e0b"
        self._apply_chunk(color)


def apply_windows_acrylic(hwnd: int, enable: bool, tint_aabbggrr: int = 0xB0121824) -> bool:
    """Blur/acrylic no Windows (DWM). Retorna True se aplicou."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

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

        # 3 = blur, 4 = acrylic (Win10+)
        accent = ACCENTPOLICY()
        if enable:
            accent.AccentState = 4
            accent.AccentFlags = 2
            accent.GradientColor = int(tint_aabbggrr) & 0xFFFFFFFF
        else:
            accent.AccentState = 0
            accent.AccentFlags = 0
            accent.GradientColor = 0

        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.addressof(accent)
        data.SizeOfData = ctypes.sizeof(accent)

        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        fn.argtypes = (wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA))
        fn.restype = wintypes.BOOL
        ok = bool(fn(wintypes.HWND(hwnd), ctypes.byref(data)))
        if ok:
            return True
        if enable:
            # fallback blur simples
            accent.AccentState = 3
            return bool(fn(wintypes.HWND(hwnd), ctypes.byref(data)))
        return False
    except Exception:
        return False


def build_crt_stylesheet(theme_id: str = DEFAULT_CRT_THEME) -> str:
    t = CRT_THEMES.get(theme_id) or CRT_THEMES[DEFAULT_CRT_THEME]
    frost = bool(t.get("frost"))
    radius = "10px" if frost else "0"
    root_bg = "transparent" if frost else t["bg"]
    return f"""
QWidget {{
    background: {root_bg};
    color: {t['text']};
    font-family: Consolas, 'Cascadia Mono', 'Courier New', monospace;
    font-size: 12px;
}}
QFrame#panel, QFrame#side {{
    background: {t['panel']};
    border: 1px solid {t['line']};
    border-radius: {radius};
}}
QLabel#title {{
    color: {t['text']};
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 2px;
    background: transparent;
}}
QLabel#mode {{
    color: {t['dim']};
    font-size: 11px;
    letter-spacing: 1px;
    background: transparent;
}}
QLabel#status {{
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 3px;
    background: transparent;
}}
QLabel#detail, QLabel#hint {{
    color: {t['dim']};
    font-size: 11px;
    background: transparent;
}}
QLabel#section {{
    color: {t['text']};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    padding: 4px 0;
    background: transparent;
}}
QLabel#foot {{
    color: {t['muted']};
    font-size: 10px;
    background: transparent;
}}
QLabel#sysHost {{
    color: {t['text']};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    background: transparent;
}}
QLabel#sysHostSub {{
    color: {t['dim']};
    font-size: 10px;
    background: transparent;
}}
QLabel#sysMeterTitle {{
    color: {t['dim']};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    background: transparent;
}}
QLabel#sysMeterVal {{
    color: {t['text']};
    font-size: 10px;
    font-weight: 700;
    background: transparent;
}}
QProgressBar {{
    background: {t['prog_bg']};
    border: 1px solid {t['line']};
    border-radius: {radius};
    text-align: center;
    color: {t['text']};
    height: 16px;
    font-size: 10px;
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
    padding: 7px 10px;
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
QLineEdit, QTextEdit, QComboBox {{
    background: {t['input_bg']};
    color: {t['input_text']};
    border: 1px solid {t['line']};
    border-radius: {radius};
    selection-background-color: {t['sel']};
    padding: 4px 6px;
}}
QComboBox QAbstractItemView {{
    background: {t['input_bg']};
    color: {t['input_text']};
    selection-background-color: {t['sel']};
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border-color: {t['text']};
}}
QCheckBox {{
    color: {t['dim']};
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
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
    padding: 8px 14px;
    margin-right: 2px;
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

            self.status.emit(f"exec · {self.raw}")
            msg, payload = execute_line(self.raw, self.payload)
            self.finished_ok.emit(msg or "OK", payload)
        except Exception as err:  # noqa: BLE001
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


class AceCrtConsole(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BINHO · Gestão")
        self.resize(1180, 680)
        self.setMinimumSize(980, 560)
        self._theme_id = DEFAULT_CRT_THEME
        self.setStyleSheet(build_crt_stylesheet(self._theme_id))

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
        lab_theme = QLabel("Tema")
        lab_theme.setObjectName("mode")
        self.cmb_theme = QComboBox()
        for tid, meta in CRT_THEMES.items():
            self.cmb_theme.addItem(str(meta["label"]), tid)
        self.cmb_theme.setMinimumWidth(140)
        self.cmb_theme.currentIndexChanged.connect(self._on_theme_combo)
        head.addWidget(lab_theme)
        head.addWidget(self.cmb_theme)
        head.addSpacing(12)
        head.addWidget(self.mode)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_left())
        split.addWidget(self._build_center())
        split.addWidget(self._build_right())
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 4)
        split.setStretchFactor(2, 3)
        split.setSizes([280, 520, 360])
        root.addWidget(split, 1)

        self.foot = QLabel("Gestão operacional")
        self.foot.setObjectName("foot")
        root.addWidget(self.foot)

        self._scan = Scanlines(self)
        self._scan.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._scan.raise_()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(250)

        self._reload_payload()
        self._append_log("sistema", "Pronto. Este histórico é o CMD — digite ou use os atalhos.", mirror=True)
        publish(online=True, label="ONLINE", pct=0, detail="painel aberto", mode="MENU")
        # carrega histórico recente do CMD (espelho)
        try:
            entries, self._log_offset = read_log_since(0)
            for entry in entries[-80:]:
                self._render_log_entry(entry, from_file=True)
        except Exception:
            pass

    # ── layout blocks ──────────────────────────────────────────────
    def _frame(self) -> QFrame:
        f = QFrame()
        f.setObjectName("panel")
        return f

    def _build_left(self) -> QWidget:
        box = self._frame()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Cubos animados + identidade da máquina + medidores
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
        self.meta = QLabel("carregando…")
        self.meta.setObjectName("detail")
        self.meta.setWordWrap(True)
        lay.addWidget(self.meta)
        lay.addStretch(1)
        return box

    def _build_center(self) -> QWidget:
        box = self._frame()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Atalhos de reports (ligar/parar automação fica só em Gestão)
        lay.addWidget(self._section("RÁPIDO"))
        grid = QGridLayout()
        grid.setSpacing(6)
        shortcuts = [
            ("Coletas", "50"),
            ("Limites", "103"),
            ("Entregas", "36"),
            ("Agendamentos", "225"),
            ("Armazém", "78"),
            ("Pendência", "31"),
            ("Contratação", "73"),
            ("Atualizar tudo", "sync"),
            ("Atualizar dados", "dash"),
            ("Telas locais", "local"),
        ]
        for i, (label, cmd) in enumerate(shortcuts):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, c=cmd: self.run_command(c))
            grid.addWidget(btn, i // 4, i % 4)
        lay.addLayout(grid)

        lay.addWidget(self._section("CMD"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(220)
        lay.addWidget(self.log, 1)

        prompt_row = QHBoxLayout()
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("ACE>  digite aqui (ex.: Coletas, ajuda, parar)")
        self.prompt.returnPressed.connect(self._submit_prompt)
        self.btn_run = QPushButton("Enviar")
        self.btn_run.setObjectName("primary")
        self.btn_run.setFixedWidth(80)
        self.btn_run.clicked.connect(self._submit_prompt)
        prompt_row.addWidget(self.prompt, 1)
        prompt_row.addWidget(self.btn_run)
        lay.addLayout(prompt_row)

        hint = QLabel(
            "Console · Enter envia · “parar” encerra o loop · Manual: docs/MANUAL.md"
        )
        hint.setObjectName("hint")
        lay.addWidget(hint)
        return box

    def _build_right(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_config_tab(), "Configuração")
        tabs.addTab(self._build_automacao_tab(), "Automação")
        tabs.addTab(self._build_local_tab(), "Local")
        tabs.addTab(self._build_tv_tab(), "TV")
        tabs.addTab(self._build_gestao_tab(), "Gestão")
        self._right_tabs = tabs
        return tabs

    def _build_config_tab(self) -> QWidget:
        from ace_cmd import EDITABLE

        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(8, 8, 8, 8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setSpacing(6)

        groups = {
            "ssw": "Acesso ao sistema",
            "auto": "Atualização",
            "local": "Modo local",
            "cloud": "Planilha e site",
            "armazem": "Armazém",
            "pendencia": "Pendência",
            "contratacao": "Contratação",
            "automacao": "Automação",
        }
        # headless / automação: abas próprias
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
                lab = QLabel(groups.get(group, group))
                lab.setObjectName("section")
                form.addRow(lab)

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

        self.chk_viz = QCheckBox("Mostrar navegador ao trabalhar")
        form.addRow(self._section("Tela"), self.chk_viz)

        form.addRow(self._section("Aparência"))
        self.cmb_theme_cfg = QComboBox()
        for tid, meta in CRT_THEMES.items():
            self.cmb_theme_cfg.addItem(str(meta["label"]), tid)
        self.cmb_theme_cfg.currentIndexChanged.connect(self._on_theme_combo_cfg)
        form.addRow("Tema do painel", self.cmb_theme_cfg)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        row = QHBoxLayout()
        btn_reload = QPushButton("Recarregar")
        btn_reload.clicked.connect(self._reload_payload)
        btn_save = QPushButton("Salvar")
        btn_save.setObjectName("primary")
        btn_save.clicked.connect(self._save_config)
        row.addWidget(btn_reload)
        row.addWidget(btn_save)
        outer.addLayout(row)
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
                    self._right_tabs.setCurrentIndex(1)
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
                self._right_tabs.setCurrentIndex(1)
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
        tabs = getattr(self, "_right_tabs", None)
        if tabs is None:
            return
        for i in range(tabs.count()):
            if tabs.tabText(i).lower().startswith("autom"):
                tabs.setCurrentIndex(i)
                return

    def _section(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("section")
        return lab

    # ── data / actions ─────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._scan.setGeometry(self.rect())

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
        theme = str(self.payload.get("crt_theme") or DEFAULT_CRT_THEME)
        if theme not in CRT_THEMES:
            theme = DEFAULT_CRT_THEME
        self._apply_theme(theme, persist=False)
        self._update_meta()
        self._append_log("config", "Configuração recarregada.")

    def _on_theme_combo(self) -> None:
        tid = str(self.cmb_theme.currentData() or DEFAULT_CRT_THEME)
        self._apply_theme(tid, persist=True)

    def _on_theme_combo_cfg(self) -> None:
        tid = str(self.cmb_theme_cfg.currentData() or DEFAULT_CRT_THEME)
        self._apply_theme(tid, persist=True)

    def _apply_theme(self, theme_id: str, *, persist: bool = True) -> None:
        tid = theme_id if theme_id in CRT_THEMES else DEFAULT_CRT_THEME
        self._theme_id = tid
        self.setStyleSheet(build_crt_stylesheet(tid))
        meta = CRT_THEMES[tid]
        if hasattr(self, "_scan"):
            self._scan.set_enabled(bool(meta.get("scan", True)))
        if hasattr(self, "cubes"):
            if meta.get("frost"):
                self.cubes.set_fill_color(QColor(10, 14, 22, 100))
            elif tid == "claro":
                self.cubes.set_fill_color(QColor("#e8edf2"))
            elif tid == "painel":
                self.cubes.set_fill_color(QColor("#050a14"))
            elif tid == "ops":
                self.cubes.set_fill_color(QColor("#080b09"))
            else:
                self.cubes.set_fill_color(QColor("#050505"))
        self._apply_frost_window(bool(meta.get("frost")), int(meta.get("acrylic_tint") or 0xB0121824))
        # sync combos sem loop
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
                _save_payload(self.payload)
                lab = str(meta.get("label") or tid)
                self._append_log("config", f"Tema: {lab}")
            except Exception:  # noqa: BLE001
                pass

    def _apply_frost_window(self, enabled: bool, tint: int = 0xB0121824) -> None:
        """Fundo transparente + blur fosco (Windows acrylic)."""
        self.setAttribute(Qt.WA_TranslucentBackground, enabled)
        # autoFillBackground opaco atrapalha o vidro
        self.setAutoFillBackground(not enabled)
        try:
            hwnd = int(self.winId())
        except Exception:
            hwnd = 0
        if hwnd:
            apply_windows_acrylic(hwnd, enabled, tint_aabbggrr=tint)
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        meta = CRT_THEMES.get(self._theme_id) or {}
        if meta.get("frost"):
            # winId só fica estável depois do show
            QTimer.singleShot(
                0,
                lambda: self._apply_frost_window(
                    True, int(meta.get("acrylic_tint") or 0xB0121824)
                ),
            )

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
            _save_payload(self.payload)
            self.payload = __import__("ace_cmd", fromlist=["_load_payload"])._load_payload()
            self._update_meta()
            self._append_log("config", "Configuração salva.")
            publish(online=True, label="ONLINE", pct=0, detail="configuração salva", mode="OK")
            QMessageBox.information(self, "ACE", "Configuração salva.")
        except Exception as err:  # noqa: BLE001
            self._append_log("erro", str(err))
            QMessageBox.warning(self, "ACE", f"Falha ao salvar:\n{err}")

    def _update_meta(self) -> None:
        p = self.payload or {}
        viz = "navegador ligado" if not p.get("headless", True) else "navegador oculto"
        sheets = "planilha ligada" if p.get("enable_sheets") else "planilha desligada"
        try:
            from config import resolve_publish_target, load_settings

            dest = resolve_publish_target(load_settings())
        except Exception:
            dest = str(p.get("publish_target") or "auto")
        arm = "armazém on" if p.get("armazem_in_loop", True) else "armazém off"
        pend = "pendência on" if p.get("pendencia_in_loop", True) else "pendência off"
        ctr = "contratação on" if p.get("contratacao_in_loop", True) else "contratação off"
        emi = "emissão on" if p.get("emissao_in_loop", False) else "emissão off"
        dist = "dist on" if p.get("dist_in_loop", True) else "dist off"
        modo = str(p.get("periodo_modo") or "diario")
        modo_txt = "diário" if modo == "diario" else "a partir da sexta"
        self.meta.setText(
            f"usuário {p.get('user') or '—'}  ·  unidades {p.get('unit') or '—'}\n"
            f"{sheets}  ·  TV={dest}  ·  {viz}\n"
            f"auto: {dist} · {arm} · {pend} · {ctr} · {emi}\n"
            f"padrão {p.get('loop_intervalo') or '5m'}  ·  {modo_txt}"
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
        running = bool(self._auto_worker and self._auto_worker.isRunning())
        try:
            from ace_stop import request_stop, stop_external_loop_process

            request_stop(force_browsers=True)
            ext = stop_external_loop_process()
        except Exception:
            ext = False
        if running:
            self._auto_worker.request_stop()
            self.mode.setText("STOP")
            if hasattr(self, "auto_status"):
                self.auto_status.setText("Parando… fechando navegadores do ciclo.")
            self._append_log(
                "sistema",
                "Parar: sinal enviado — interrompe a espera e corta o Chromium em andamento.",
            )
            publish(online=True, label="STOP", pct=0, detail="parando loop", mode="RUN")
        elif ext:
            self.mode.setText("STOP")
            self._append_log("sistema", "Parar: encerrou loop em segundo plano.")
            publish(online=True, label="STOP", pct=0, detail="loop externo parado", mode="OK")
            if hasattr(self, "auto_status"):
                self.auto_status.setText("Automático parado.")
        else:
            self._append_log("sistema", "Nenhuma atualização contínua em andamento.")
            if hasattr(self, "auto_status"):
                self.auto_status.setText("Automático parado.")

    def _start_automatica(self, interval_arg: str | None = None) -> None:
        if self._auto_worker and self._auto_worker.isRunning():
            self._append_log("sistema", "Já está em atualização contínua. Use Parar.")
            return
        if self._worker and self._worker.isRunning():
            self._append_log("sistema", "Aguarde o comando atual terminar…")
            return
        tip = interval_arg or "intervalo da config"
        self._append_log("cmd", f"atualização contínua ({tip})")
        self.mode.setText("LOOP")
        publish(online=True, label="LOOP", pct=5, detail="atualização contínua", mode="RUN")
        self._auto_worker = AutoLoopWorker(interval_arg)
        self._auto_worker.finished_ok.connect(self._on_auto_ok)
        self._auto_worker.failed.connect(self._on_auto_fail)
        self._auto_worker.start()

    def _on_auto_ok(self, msg: str) -> None:
        self._append_log("ok", msg)
        self.mode.setText("OK")
        publish(online=True, label="ONLINE", pct=100, detail=msg[:100], mode="OK")
        if hasattr(self, "auto_status"):
            self.auto_status.setText(msg)

    def _on_auto_fail(self, msg: str) -> None:
        self._append_log("erro", msg)
        self.mode.setText("ERR")
        publish(online=False, label="ERR", pct=0, detail=msg[:100], mode="ERR")
        if hasattr(self, "auto_status"):
            self.auto_status.setText(msg[:160])

    def run_command(self, raw: str) -> None:
        raw = _resolve_friendly_cmd(raw)
        if not raw:
            return
        low = raw.lower().strip()
        if low in {"cls", "clear", "limpar"}:
            self.log.clear()
            return
        if low in {"parar", "stop", "halt"}:
            self._pending_cmd = None
            self._stop_auto()
            return

        parts_probe = raw.split()
        head_probe = parts_probe[0].lower().lstrip("/") if parts_probe else ""
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
            self.log.clear()
        else:
            self._append_log("out", msg)
        online = "erro" not in (msg or "").lower() and "falhou" not in (msg or "").lower()
        publish(
            online=online,
            label="ONLINE" if online else "ERR",
            pct=100 if online else 0,
            detail=(msg or "ok")[:100],
            mode="OK" if online else "ERR",
        )
        self.mode.setText("OK" if online else "ERR")
        self._drain_pending()

    def _on_cmd_fail(self, msg: str) -> None:
        self.btn_run.setEnabled(True)
        self._worker_cmd = ""
        self._append_log("erro", msg)
        publish(online=False, label="ERR", pct=0, detail=msg[:100], mode="ERR")
        self.mode.setText("ERR")
        self._drain_pending()

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
        self.chk_viz.setChecked(not bool(self.payload.get("headless", True)))
        self._update_meta()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._auto_worker and self._auto_worker.isRunning():
            self._auto_worker.request_stop()
            self._auto_worker.wait(3000)
        super().closeEvent(event)

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
        html = (
            f'<span style="color:{MUTED}">[{stamp}]</span> '
            f'<span style="color:{color}"><b>███ {tag}</b></span> '
            f'<span style="color:{color}">{safe}{prefix}</span>'
        )
        self.log.append(html)
        self.log.moveCursor(QTextCursor.End)

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
        accent = str((CRT_THEMES.get(self._theme_id) or CRT_THEMES[DEFAULT_CRT_THEME])["text"])
        color = accent if online and "OFF" not in label and "ERR" not in label else OFF
        if "ERR" in label or mode == "ERR":
            color = ERR
        self.status.setStyleSheet(
            f"color: {color}; font-size: 26px; font-weight: 800; letter-spacing: 3px;"
        )
        self.detail.setText(detail[:140] if detail else "—")
        self.bar.setValue(int(round(pct * 10)))
        self.bar.setFormat(f"{pct:5.1f}%")
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


def main() -> int:
    # Necessário para QWebEngineView (preview do dashboard no editor TV)
    try:
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    except Exception:  # noqa: BLE001
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("BINHO ACE Gestão CRT")
    w = AceCrtConsole()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
