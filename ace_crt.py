"""
BINHO · ACE CRT — painel de gestão widescreen (cara de CMD).

Layout:
  esq  → logo + status + progresso
  centro → atalhos + log + prompt de comandos
  dir  → abas Configuração | Gestão

  python ace_crt.py
  ace.bat crt
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QLinearGradient, QBrush, QTextCursor
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

BG = "#050805"
PANEL = "#0a100c"
LINE = "#1a3d28"
NEON = "#39ff14"
DIM = "#6b8f71"
MUTED = "#3d5c45"
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
    "loop_intervalo": "Intervalo da atualização",
    "enable_sheets": "Enviar à planilha",
    "apps_script_url": "Endereço da conexão",
    "apps_script_token": "Chave da conexão",
    "google_sheet_id": "Código da planilha",
    "enable_github_publish": "Publicar site automaticamente",
    "github_repo": "Pasta do site",
    "github_branch": "Linha do site",
    "github_token_env": "Nome da chave do site",
    "armazem_in_loop": "Incluir armazém no ciclo",
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
    "atualizar tudo": "sync",
    "sincronizar": "sync",
    "planilha": "sync",
    "abrir painel": "dash",
    "painel": "dash",
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

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {NEON};
    font-family: Consolas, 'Cascadia Mono', 'Courier New', monospace;
    font-size: 12px;
}}
QFrame#panel, QFrame#side {{
    background: {PANEL};
    border: 1px solid {LINE};
}}
QLabel#title {{
    color: {NEON};
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QLabel#mode {{
    color: {DIM};
    font-size: 11px;
    letter-spacing: 1px;
}}
QLabel#status {{
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 3px;
}}
QLabel#detail, QLabel#hint {{
    color: {DIM};
    font-size: 11px;
}}
QLabel#section {{
    color: {NEON};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    padding: 4px 0;
}}
QLabel#foot {{
    color: {MUTED};
    font-size: 10px;
}}
QProgressBar {{
    background: #07110a;
    border: 1px solid {LINE};
    border-radius: 0;
    text-align: center;
    color: {NEON};
    height: 16px;
    font-size: 10px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #009245, stop:0.55 #00ff66, stop:1 #8cc63f);
}}
QPushButton {{
    background: #07140c;
    color: {NEON};
    border: 1px solid {LINE};
    padding: 7px 10px;
    text-align: left;
}}
QPushButton:hover {{
    background: #0d2416;
    border-color: {NEON};
}}
QPushButton:pressed {{
    background: #11301c;
}}
QPushButton:disabled {{
    color: {MUTED};
    border-color: #102016;
}}
QPushButton#primary {{
    background: #0d2416;
    font-weight: 700;
}}
QLineEdit, QTextEdit, QComboBox {{
    background: #07110a;
    color: {OFF};
    border: 1px solid {LINE};
    selection-background-color: #1a5c36;
    padding: 4px 6px;
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border-color: {NEON};
}}
QCheckBox {{
    color: {DIM};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {LINE};
    background: #07110a;
}}
QCheckBox::indicator:checked {{
    background: {NEON};
}}
QTabWidget::pane {{
    border: 1px solid {LINE};
    background: {PANEL};
}}
QTabBar::tab {{
    background: #07110a;
    color: {DIM};
    border: 1px solid {LINE};
    padding: 8px 14px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    color: {NEON};
    background: {PANEL};
    border-bottom-color: {PANEL};
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QSplitter::handle {{
    background: {LINE};
    width: 2px;
}}
"""


class Scanlines(QWidget):
    def paintEvent(self, event) -> None:  # noqa: N802
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

    def __init__(self, interval_arg: str | None = None) -> None:
        super().__init__()
        self.interval_arg = interval_arg
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            from ace_loop import resolve_interval_sec, run_loop
            from config import load_settings

            cfg = load_settings()
            sec = resolve_interval_sec(self.interval_arg, settings_intervalo=cfg.loop_intervalo)
            code = run_loop(
                interval_sec=sec,
                should_stop=lambda: self._stop,
                quiet_banner=True,
            )
            if self._stop:
                self.finished_ok.emit("Atualização contínua parada.")
            else:
                self.finished_ok.emit(f"Loop encerrado (código {code}).")
        except Exception as err:  # noqa: BLE001
            self.failed.emit(f"ERRO no loop: {err}\n{traceback.format_exc(limit=4)}")


class AceCrtConsole(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BINHO · Gestão")
        self.resize(1180, 680)
        self.setMinimumSize(980, 560)
        self.setStyleSheet(STYLESHEET)

        self.payload: dict = {}
        self._worker: CmdWorker | None = None
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

        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignCenter)
        self.logo.setMinimumHeight(180)
        self._load_logo()
        lay.addWidget(self.logo)

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
            ("Atualizar tudo", "sync"),
            ("Abrir painel", "dash"),
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

        hint = QLabel("Este histórico é o console · Enter envia · “parar” encerra a atualização contínua")
        hint.setObjectName("hint")
        lay.addWidget(hint)
        return box

    def _build_right(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_config_tab(), "Configuração")
        tabs.addTab(self._build_tv_tab(), "TV")
        tabs.addTab(self._build_gestao_tab(), "Gestão")
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
            "cloud": "Planilha e site",
            "armazem": "Armazém",
        }
        # headless: controlado só pelo “Mostrar navegador”
        skip_keys = {"headless"}
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
            else:
                w = QLineEdit()
                if secret:
                    w.setEchoMode(QLineEdit.Password)
            self._fields[key] = w
            form.addRow(_field_label(key), w)

        self.chk_viz = QCheckBox("Mostrar navegador ao trabalhar")
        form.addRow(self._section("Tela"), self.chk_viz)

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
        lay = QVBoxLayout(wrap)
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

        # Intervalo padrão vem da Configuração (loop_intervalo); aqui só liga/para
        lay.addWidget(self._section("Atualização contínua"))
        tip = QLabel("Intervalo padrão: Configuração → Intervalo da atualização")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        row = QHBoxLayout()
        self.auto_iv = QLineEdit()
        self.auto_iv.setPlaceholderText("opcional · ex.: 5m (vazio = config)")
        btn_auto = QPushButton("Iniciar")
        btn_auto.setObjectName("primary")
        btn_auto.clicked.connect(self._start_auto)
        btn_stop = QPushButton("Parar")
        btn_stop.clicked.connect(self._stop_auto)
        row.addWidget(self.auto_iv, 1)
        row.addWidget(btn_auto)
        row.addWidget(btn_stop)
        lay.addLayout(row)

        lay.addStretch(1)
        return wrap

    def _section(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("section")
        return lab

    # ── data / actions ─────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._scan.setGeometry(self.rect())

    def _load_logo(self) -> None:
        path = _CUBES if _CUBES.is_file() else _LOGO
        if not path.is_file():
            self.logo.setText("BINHO")
            return
        pm = QPixmap(str(path))
        if pm.isNull():
            self.logo.setText("BINHO")
            return
        self.logo.setPixmap(pm.scaled(240, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation))

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
                s = str(val or "diario")
                idx = w.findData(s)
                if idx < 0:
                    idx = w.findText(s)
                w.setCurrentIndex(idx if idx >= 0 else 0)
            elif isinstance(w, QLineEdit):
                w.setText("" if val is None else str(val))
        self.chk_viz.setChecked(not bool(self.payload.get("headless", True)))
        self._update_meta()
        self._append_log("config", "Configuração recarregada.")

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
                    self.payload[key] = w.text().strip()
            self.payload["headless"] = not self.chk_viz.isChecked()
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
        arm = "armazém no ciclo" if p.get("armazem_in_loop", True) else "armazém fora do ciclo"
        modo = str(p.get("periodo_modo") or "diario")
        modo_txt = "diário" if modo == "diario" else "a partir da sexta"
        self.meta.setText(
            f"usuário {p.get('user') or '—'}  ·  unidades {p.get('unit') or '—'}\n"
            f"{sheets}  ·  {viz}\n"
            f"{arm}  ·  a cada {p.get('loop_intervalo') or '5m'}  ·  {modo_txt}"
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
        if self._auto_worker and self._auto_worker.isRunning():
            self._auto_worker.request_stop()
            self._append_log("sistema", "Pedindo parada da atualização contínua…")
            publish(online=True, label="STOP", pct=0, detail="parando loop", mode="RUN")
        else:
            self._append_log("sistema", "Nenhuma atualização contínua em andamento.")

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

    def _on_auto_fail(self, msg: str) -> None:
        self._append_log("erro", msg)
        self.mode.setText("ERR")
        publish(online=False, label="ERR", pct=0, detail=msg[:100], mode="ERR")

    def run_command(self, raw: str) -> None:
        if self._worker and self._worker.isRunning():
            self._append_log("sistema", "Aguarde o comando atual terminar…")
            return
        raw = _resolve_friendly_cmd(raw)
        if not raw:
            return
        low = raw.lower().strip()
        if low in {"cls", "clear", "limpar"}:
            self.log.clear()
            return
        if low in {"parar", "stop", "halt"}:
            self._stop_auto()
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
                "Loop contínuo ativo — comando único aguarda o ciclo. Ou digite “parar”.",
            )
            # ainda permite comandos leves? safer to block pipelines
            # allow status/help via execute if not pipeline - keep simple: block all
            return

        self._append_log("cmd", raw)
        self.btn_run.setEnabled(False)
        self.mode.setText("RUN")
        publish(online=True, label="RUN", pct=5, detail=raw[:80], mode="RUN")

        self._worker = CmdWorker(raw, self.payload)
        self._worker.status.connect(lambda m: publish(online=True, label="RUN", pct=20, detail=m[:80], mode="RUN"))
        self._worker.finished_ok.connect(self._on_cmd_ok)
        self._worker.failed.connect(self._on_cmd_fail)
        self._worker.start()

    def _on_cmd_ok(self, msg: str, payload: object) -> None:
        self.btn_run.setEnabled(True)
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

    def _on_cmd_fail(self, msg: str) -> None:
        self.btn_run.setEnabled(True)
        self._append_log("erro", msg)
        publish(online=False, label="ERR", pct=0, detail=msg[:100], mode="ERR")
        self.mode.setText("ERR")

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

        color = {
            "ok": NEON,
            "err": ERR,
            "erro": ERR,
            "work": WARN,
            "cmd": NEON,
            "out": DIM,
            "config": WARN,
            "sistema": DIM,
            "info": DIM,
        }.get(kind, DIM)
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
        st = read_status()
        online = bool(st.get("online", True))
        label = str(st.get("label") or ("ONLINE" if online else "OFFLINE")).upper()
        mode = str(st.get("mode") or "STANDBY").upper()
        detail = str(st.get("detail") or "")
        pct = float(st.get("pct") or 0.0)

        self.status.setText(label[:16])
        color = NEON if online and "OFF" not in label and "ERR" not in label else OFF
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
    app = QApplication(sys.argv)
    app.setApplicationName("BINHO ACE Gestão CRT")
    w = AceCrtConsole()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
