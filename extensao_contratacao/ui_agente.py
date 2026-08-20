"""
ACE · Agente Contratação — UI (estilo CRT)

Extensão visual do CRT: Excel → Sheets, com Atualizar via GitHub.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_HERE = Path(__file__).resolve().parent
_ACE = _HERE.parent
if str(_ACE) not in sys.path:
    sys.path.insert(0, str(_ACE))

from extensao_contratacao.github_updater import (  # noqa: E402
    apply_update,
    check_for_update,
    read_local_version,
)

APP_NAME = "ACE · Contratação"
AGENT_VERSION = read_local_version(_HERE)

# Tema alinhado ao CRT azul (screenshot)
THEME = {
    "bg": "#050a14",
    "grad0": "#0a2744",
    "grad1": "#08101c",
    "panel": "#0a121e",
    "card": "#0d1828",
    "line": "#1a2f4a",
    "text": "#e0f2fe",
    "dim": "#94a3b8",
    "muted": "#64748b",
    "accent": "#38bdf8",
    "ok": "#22c55e",
    "err": "#ef4444",
    "btn": "#0c1624",
    "btn_hover": "#132338",
    "input": "#071018",
}


def _horse_path() -> Path:
    for p in (
        _ACE / "assets" / "ace-horse-azul.png",
        _ACE / "assets" / "ace-horse.png",
        _HERE / "assets" / "ace-horse-azul.png",
        _HERE / "ace-horse-azul.png",
    ):
        if p.exists():
            return p
    return _ACE / "assets" / "ace-horse-azul.png"


def build_stylesheet() -> str:
    t = THEME
    return f"""
    QWidget#root {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0.55,
            stop:0 {t['grad0']}, stop:0.4 {t['grad1']}, stop:1 {t['bg']});
        color: {t['text']};
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 13px;
    }}
    QFrame#side {{
        background: {t['panel']};
        border-right: 1px solid {t['line']};
    }}
    QFrame#sidebarFoot {{
        background: {t['card']};
        border: 1px solid {t['line']};
        border-radius: 12px;
    }}
    QLabel#brandTitle {{
        color: {t['text']};
        font-size: 15px;
        font-weight: 700;
        letter-spacing: 0.5px;
    }}
    QLabel#brandSub {{
        color: {t['muted']};
        font-size: 11px;
    }}
    QLabel#navSection {{
        color: {t['muted']};
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 1.2px;
        padding: 12px 8px 4px 8px;
    }}
    QPushButton#navBtn {{
        text-align: left;
        padding: 10px 14px;
        border: none;
        border-radius: 10px;
        background: transparent;
        color: {t['dim']};
        font-weight: 600;
    }}
    QPushButton#navBtn:hover {{
        background: rgba(56, 189, 248, 0.12);
        color: {t['text']};
    }}
    QPushButton#navBtn[active="true"] {{
        background: rgba(56, 189, 248, 0.26);
        color: {t['text']};
    }}
    QLabel#onlineDot {{
        color: {t['ok']};
        font-weight: 800;
        font-size: 12px;
        letter-spacing: 1px;
    }}
    QLabel#onlineDot[offline="true"] {{
        color: {t['err']};
    }}
    QProgressBar#footMeter {{
        background: {t['input']};
        border: 1px solid {t['line']};
        border-radius: 6px;
        max-height: 10px;
    }}
    QProgressBar#footMeter::chunk {{
        border-radius: 5px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #0369a1, stop:0.55 #38bdf8, stop:1 #7dd3fc);
    }}
    QTableWidget#sheetTable {{
        background: {t['input']};
        alternate-background-color: #0a1524;
        color: {t['text']};
        gridline-color: {t['line']};
        border: 1px solid {t['line']};
        border-radius: 10px;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 12px;
    }}
    QTableWidget#sheetTable::item {{
        padding: 4px 8px;
    }}
    QHeaderView::section {{
        background: {t['panel']};
        color: {t['accent']};
        border: none;
        border-bottom: 1px solid {t['line']};
        border-right: 1px solid {t['line']};
        padding: 6px 8px;
        font-weight: 700;
        font-size: 11px;
    }}
    QLabel#footVersion {{
        color: {t['dim']};
        font-size: 12px;
    }}
    QLabel#pageTitle {{
        font-size: 22px;
        font-weight: 700;
        color: {t['text']};
    }}
    QLabel#pageSub {{
        color: {t['muted']};
        font-size: 12px;
    }}
    QFrame#card {{
        background: {t['card']};
        border: 1px solid {t['line']};
        border-radius: 12px;
    }}
    QPushButton#primary {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #0369a1, stop:1 #38bdf8);
        color: #041018;
        font-weight: 700;
        border: none;
        border-radius: 10px;
        padding: 12px 18px;
        min-height: 20px;
    }}
    QPushButton#primary:hover {{
        background: #7dd3fc;
    }}
    QPushButton#primary:disabled {{
        background: #1a2f4a;
        color: {t['muted']};
    }}
    QPushButton#ghost {{
        background: {t['btn']};
        color: {t['text']};
        border: 1px solid {t['line']};
        border-radius: 10px;
        padding: 10px 16px;
        font-weight: 600;
    }}
    QPushButton#ghost:hover {{
        background: {t['btn_hover']};
    }}
    QTextEdit#log {{
        background: #050a14;
        color: {t['dim']};
        border: 1px solid {t['line']};
        border-radius: 10px;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 12px;
        padding: 8px;
    }}
    QLabel#kpiVal {{
        font-size: 22px;
        font-weight: 700;
        color: {t['accent']};
    }}
    QLabel#kpiLab {{
        color: {t['muted']};
        font-size: 11px;
    }}
    """


class Worker(QThread):
    log = Signal(str)
    progress = Signal(int)
    done = Signal(bool, str, object)

    def __init__(self, kind: str, **kwargs):
        super().__init__()
        self.kind = kind
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            if self.kind == "cycle":
                from extensao_contratacao.pipeline_agente import (
                    run_pipeline_contratacao_excel,
                )

                def st(m: str) -> None:
                    self.log.emit(m)

                result = run_pipeline_contratacao_excel(
                    sync_sheets=True,
                    on_status=st,
                )
                resumo = result.get("resumo") or {}
                msg = (
                    f"OK · veículos={resumo.get('total_veiculos')} "
                    f"custo={resumo.get('custo_fmt')}"
                )
                self.done.emit(True, msg, result)
            elif self.kind == "check":
                info = check_for_update(on_status=lambda m: self.log.emit(m))
                if info.error:
                    self.done.emit(False, info.error, info)
                elif info.has_update:
                    self.done.emit(
                        True,
                        f"Nova versão {info.remote_version} (local {info.local_version})",
                        info,
                    )
                else:
                    self.done.emit(
                        True,
                        f"Atualizado · v{info.local_version}",
                        info,
                    )
            elif self.kind == "update":
                result = apply_update(
                    force=bool(self.kwargs.get("force", False)),
                    on_status=lambda m: self.log.emit(m),
                    on_progress=lambda p: self.progress.emit(p),
                )
                if result.get("ok"):
                    if result.get("skipped"):
                        self.done.emit(True, "Já está na última versão.", result)
                    else:
                        self.done.emit(
                            True,
                            f"Instalado v{result.get('remote')} — reinicie o app.",
                            result,
                        )
                else:
                    self.done.emit(False, str(result.get("error") or "falha"), result)
            else:
                self.done.emit(False, f"job desconhecido: {self.kind}", None)
        except Exception as err:  # noqa: BLE001
            self.log.emit(traceback.format_exc())
            self.done.emit(False, str(err), None)


class AgenteWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(980, 640)
        self.setMinimumSize(820, 520)
        self._worker: Worker | None = None
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._on_auto_tick)
        self._online = True

        icon_path = _horse_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._build_sidebar())
        lay.addWidget(self._build_main(), 1)

        self.setStyleSheet(build_stylesheet())
        self._show_page("home")
        self._append_log(f"Agente Contratação v{AGENT_VERSION} pronto.")
        QTimer.singleShot(600, self._check_update_silent)

    def _build_sidebar(self) -> QWidget:
        box = QFrame()
        box.setObjectName("side")
        box.setFixedWidth(220)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(6)

        brand = QHBoxLayout()
        logo = QLabel()
        pix = QPixmap(str(_horse_path()))
        if not pix.isNull():
            logo.setPixmap(
                pix.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        brand.addWidget(logo)
        titles = QVBoxLayout()
        t1 = QLabel("ACE")
        t1.setObjectName("brandTitle")
        t2 = QLabel("Contratação")
        t2.setObjectName("brandSub")
        titles.addWidget(t1)
        titles.addWidget(t2)
        brand.addLayout(titles, 1)
        lay.addLayout(brand)

        sec = QLabel("PRINCIPAL")
        sec.setObjectName("navSection")
        lay.addWidget(sec)

        self._nav: dict[str, QPushButton] = {}
        for key, label in (
            ("home", "Visão Geral"),
            ("run", "Executar"),
            ("update", "Atualizar"),
            ("logs", "Logs"),
        ):
            if key == "run":
                s2 = QLabel("OPERAÇÃO")
                s2.setObjectName("navSection")
                lay.addWidget(s2)
            btn = QPushButton(label)
            btn.setObjectName("navBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("active", "false")
            btn.clicked.connect(lambda _=False, k=key: self._show_page(k))
            self._nav[key] = btn
            lay.addWidget(btn)

        lay.addStretch(1)

        foot = QFrame()
        foot.setObjectName("sidebarFoot")
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(12, 12, 12, 12)
        fl.setSpacing(8)
        self.status = QLabel("●  ONLINE")
        self.status.setObjectName("onlineDot")
        self.bar = QProgressBar()
        self.bar.setObjectName("footMeter")
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        self.lbl_ver = QLabel(f"Versão {AGENT_VERSION}")
        self.lbl_ver.setObjectName("footVersion")
        fl.addWidget(self.status)
        fl.addWidget(self.bar)
        fl.addWidget(self.lbl_ver)
        lay.addWidget(foot)
        return box

    def _build_main(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(14)

        head = QVBoxLayout()
        self.page_title = QLabel("ACE • CONTRATAÇÃO")
        self.page_title.setObjectName("pageTitle")
        self.page_sub = QLabel("Extensão do CRT · planilha Excel → Sheets")
        self.page_sub.setObjectName("pageSub")
        head.addWidget(self.page_title)
        head.addWidget(self.page_sub)
        lay.addLayout(head)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_home())
        self.stack.addWidget(self._page_run())
        self.stack.addWidget(self._page_update())
        self.stack.addWidget(self._page_logs())
        lay.addWidget(self.stack, 1)
        return wrap

    def _kpi(self, lab: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("card")
        vl = QVBoxLayout(card)
        vl.setContentsMargins(14, 12, 14, 12)
        val = QLabel("—")
        val.setObjectName("kpiVal")
        l = QLabel(lab)
        l.setObjectName("kpiLab")
        vl.addWidget(val)
        vl.addWidget(l)
        return card, val

    def _page_home(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        tip = QLabel(
            "Lê a planilha PRODUTIVIDADE CONTRATAÇÃO.xlsx na Área de Trabalho "
            "e atualiza o Google Sheets da Contratação (custo / frete fechado)."
        )
        tip.setWordWrap(True)
        tip.setObjectName("pageSub")
        lay.addWidget(tip)

        row = QHBoxLayout()
        c1, self.kpi_veic = self._kpi("Veículos")
        c2, self.kpi_custo = self._kpi("Custo (fechado)")
        c3, self.kpi_upd = self._kpi("Última sync")
        for c in (c1, c2, c3):
            row.addWidget(c)
        lay.addLayout(row)

        actions = QHBoxLayout()
        b1 = QPushButton("Rodar ciclo agora")
        b1.setObjectName("primary")
        b1.clicked.connect(lambda: self._start_job("cycle"))
        b2 = QPushButton("Verificar atualização")
        b2.setObjectName("ghost")
        b2.clicked.connect(lambda: self._show_page("update"))
        actions.addWidget(b1)
        actions.addWidget(b2)
        actions.addStretch(1)
        lay.addLayout(actions)

        sheet_lab = QLabel("Planilha (última leitura)")
        sheet_lab.setObjectName("pageSub")
        lay.addWidget(sheet_lab)
        self.sheet = QTableWidget(0, 6)
        self.sheet.setObjectName("sheetTable")
        self.sheet.setHorizontalHeaderLabels(
            ["Placa", "Carreta", "Origem", "Base", "Valor", "Frete"]
        )
        self.sheet.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sheet.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.sheet.setAlternatingRowColors(True)
        self.sheet.setSelectionBehavior(QTableWidget.SelectRows)
        self.sheet.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sheet.verticalHeader().setVisible(False)
        lay.addWidget(self.sheet, 1)
        return w

    def _page_run(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.addWidget(QLabel("Ciclo automático"))
        hint = QLabel(
            "Roda Excel → Sheets em intervalo. "
            "A planilha é sempre buscada na Área de Trabalho deste PC."
        )
        hint.setWordWrap(True)
        hint.setObjectName("pageSub")
        cl.addWidget(hint)
        row = QHBoxLayout()
        self.btn_auto = QPushButton("Iniciar automático (15 min)")
        self.btn_auto.setObjectName("primary")
        self.btn_auto.clicked.connect(self._toggle_auto)
        self.btn_once = QPushButton("Só um ciclo")
        self.btn_once.setObjectName("ghost")
        self.btn_once.clicked.connect(lambda: self._start_job("cycle"))
        row.addWidget(self.btn_auto)
        row.addWidget(self.btn_once)
        row.addStretch(1)
        cl.addLayout(row)
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _page_update(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.addWidget(QLabel("Atualização pelo GitHub"))
        self.upd_msg = QLabel(
            f"Versão instalada: {AGENT_VERSION}\n"
            "O CRT publica novas versões no repositório; aqui você baixa e instala."
        )
        self.upd_msg.setWordWrap(True)
        self.upd_msg.setObjectName("pageSub")
        cl.addWidget(self.upd_msg)
        self.upd_bar = QProgressBar()
        self.upd_bar.setObjectName("footMeter")
        self.upd_bar.setRange(0, 100)
        self.upd_bar.setValue(0)
        self.upd_bar.setTextVisible(False)
        self.upd_bar.setFixedHeight(12)
        cl.addWidget(self.upd_bar)
        row = QHBoxLayout()
        self.btn_check = QPushButton("Verificar")
        self.btn_check.setObjectName("ghost")
        self.btn_check.clicked.connect(lambda: self._start_job("check"))
        self.btn_update = QPushButton("Atualizar agora")
        self.btn_update.setObjectName("primary")
        self.btn_update.clicked.connect(lambda: self._start_job("update"))
        row.addWidget(self.btn_check)
        row.addWidget(self.btn_update)
        row.addStretch(1)
        cl.addLayout(row)
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _page_logs(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.log = QTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        lay.addWidget(self.log)
        return w

    def _show_page(self, key: str) -> None:
        idx = {"home": 0, "run": 1, "update": 2, "logs": 3}.get(key, 0)
        self.stack.setCurrentIndex(idx)
        titles = {
            "home": ("ACE • CONTRATAÇÃO", "PAINEL · Visão Geral"),
            "run": ("OPERAÇÃO", "Ciclo Excel → Sheets"),
            "update": ("ATUALIZAR", "Download direto do GitHub"),
            "logs": ("LOGS", "Histórico desta sessão"),
        }
        t, s = titles.get(key, titles["home"])
        self.page_title.setText(t)
        self.page_sub.setText(s)
        for k, btn in self._nav.items():
            btn.setProperty("active", "true" if k == key else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _set_online(self, online: bool) -> None:
        self._online = online
        self.status.setText("●  ONLINE" if online else "●  OFFLINE")
        self.status.setProperty("offline", "false" if online else "true")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _append_log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        if hasattr(self, "log"):
            self.log.append(line)
        print(line, flush=True)

    def _busy(self, on: bool) -> None:
        for b in (
            getattr(self, "btn_auto", None),
            getattr(self, "btn_once", None),
            getattr(self, "btn_check", None),
            getattr(self, "btn_update", None),
        ):
            if b is not None and b is not self.btn_auto:
                b.setEnabled(not on)
        if on:
            self.bar.setRange(0, 0)  # indeterminado
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(0)

    def _start_job(self, kind: str, **kwargs) -> None:
        if self._worker and self._worker.isRunning():
            self._append_log("Aguarde o job atual terminar.")
            return
        self._busy(True)
        self._set_online(True)
        self._append_log(f"Iniciando: {kind}")
        self._worker = Worker(kind, **kwargs)
        self._worker.log.connect(self._append_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, pct: int) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(max(0, min(100, int(pct))))
        if hasattr(self, "upd_bar"):
            self.upd_bar.setValue(max(0, min(100, int(pct))))

    def _fill_sheet(self, rows: list) -> None:
        if not hasattr(self, "sheet"):
            return
        self.sheet.setRowCount(0)
        for r in rows or []:
            i = self.sheet.rowCount()
            self.sheet.insertRow(i)
            frete = float(r.get("frete") or 0)
            frete_txt = (
                f"{frete:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                if frete > 0
                else "AGUARDANDO FRETE"
            )
            valor = float(r.get("valor") or r.get("custo") or 0)
            valor_txt = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            vals = [
                str(r.get("placa") or ""),
                str(r.get("carreta") or "—") or "—",
                str(r.get("origem") or ""),
                str(r.get("base") or "OUT"),
                valor_txt,
                frete_txt,
            ]
            for col, text in enumerate(vals):
                item = QTableWidgetItem(text)
                if col >= 4:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 5 and frete <= 0:
                    item.setForeground(QColor("#fbbf24"))
                self.sheet.setItem(i, col, item)

    def _on_done(self, ok: bool, msg: str, payload: object) -> None:
        self._busy(False)
        self._set_online(ok)
        self._append_log(("OK · " if ok else "ERRO · ") + msg)
        if ok and isinstance(payload, dict) and payload.get("resumo"):
            r = payload["resumo"]
            self.kpi_veic.setText(str(r.get("total_veiculos") or "—"))
            self.kpi_custo.setText(str(r.get("custo_fmt") or "—"))
            self.kpi_upd.setText(str(r.get("atualizado") or "—"))
            planilha = payload.get("planilha")
            if not planilha and isinstance(payload.get("073"), dict):
                planilha = (payload.get("073") or {}).get("planilha")
            if planilha:
                self._fill_sheet(planilha)
            elif payload.get("placas"):
                # fallback: monta da lista de veículos do resumo interno
                veics = ((payload.get("073") or {}).get("veiculos")) or []
                self._fill_sheet(
                    [
                        {
                            "placa": v.get("placa"),
                            "carreta": v.get("carreta"),
                            "origem": "",
                            "base": v.get("propriedade") or "",
                            "valor": v.get("custo"),
                            "frete": v.get("frete"),
                        }
                        for v in veics
                    ]
                )
        if ok and hasattr(self, "upd_msg") and payload is not None:
            info = payload.get("info") if isinstance(payload, dict) else payload
            if info is not None and hasattr(info, "remote_version"):
                self.upd_msg.setText(
                    f"Local: v{info.local_version}\n"
                    f"GitHub: v{info.remote_version or '—'}\n"
                    + (
                        "Há atualização disponível."
                        if info.has_update
                        else "Você já está na última versão."
                    )
                )
            if isinstance(payload, dict) and payload.get("remote"):
                self.lbl_ver.setText(f"Versão {payload.get('remote')}")
                if payload.get("restart_hint"):
                    QMessageBox.information(
                        self,
                        "Atualização instalada",
                        "Reinicie o Agente Contratação para carregar os novos arquivos.",
                    )

    def _toggle_auto(self) -> None:
        if self._auto_timer.isActive():
            self._auto_timer.stop()
            self.btn_auto.setText("Iniciar automático (15 min)")
            self._append_log("Automático parado.")
            return
        self._auto_timer.start(15 * 60 * 1000)
        self.btn_auto.setText("Parar automático")
        self._append_log("Automático ligado · a cada 15 min.")
        self._start_job("cycle")

    def _on_auto_tick(self) -> None:
        self._start_job("cycle")

    def _check_update_silent(self) -> None:
        self._start_job("check")


def run_ui() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win = AgenteWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_ui())
