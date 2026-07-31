from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import AceSettings, SswCredentials, save_all
from dates import format_period, normalize_date, sugestao_periodo, to_ssw_ddmmyy
from pipeline import find_latest_103, run_analysis_103, run_full_pipeline_103


class Worker103(QObject):
    finished = Signal(dict)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, mode: str, **kwargs) -> None:
        super().__init__()
        self.mode = mode
        self.kwargs = kwargs

    @Slot()
    def run(self) -> None:
        try:
            if self.mode == "full":
                result = run_full_pipeline_103(**self.kwargs, on_status=self.status.emit)
            else:
                result = run_analysis_103(**self.kwargs, on_status=self.status.emit)
            self.finished.emit(result)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class Relatorio103Tab(QWidget):
    """Aba 103 · Coletas normais (Excel) — tempo real Parado / Em rota / Realizada."""

    def __init__(
        self,
        *,
        get_credentials,
        get_settings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._get_credentials = get_credentials
        self._get_settings = get_settings
        self.thread: QThread | None = None
        self.worker: Worker103 | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        tip = QLabel(
            "103 · Excel · Por data de limite (L) · HOJE. "
            "Torres: Parado (cadastrada) · Em rota (comandada) · Realizada (coletada)."
        )
        tip.setObjectName("subtitle")
        tip.setWordWrap(True)
        root.addWidget(tip)

        form_box = QGroupBox("Periodo de pesquisa (data limite)")
        form = QFormLayout(form_box)
        self.start_edit = QLineEdit()
        self.end_edit = QLineEdit()
        self.start_edit.setPlaceholderText("DDMMYY")
        self.end_edit.setPlaceholderText("DDMMYY")
        form.addRow("De", self.start_edit)
        form.addRow("a", self.end_edit)
        self.keep_open = QCheckBox("Manter navegador aberto")
        form.addRow(self.keep_open)
        root.addWidget(form_box)

        btns = QHBoxLayout()
        self.btn_analyze = QPushButton("Analisar ultimo Excel 103")
        self.btn_analyze.clicked.connect(self._analyze_latest)
        self.btn_run = QPushButton("Baixar 103 + analisar")
        self.btn_run.setObjectName("primary")
        self.btn_run.clicked.connect(self._run_full)
        btns.addWidget(self.btn_analyze)
        btns.addStretch(1)
        btns.addWidget(self.btn_run)
        root.addLayout(btns)

        # Torres
        towers = QHBoxLayout()
        self.t_parado = self._make_tower("Parado", "#f59e0b")
        self.t_rota = self._make_tower("Em rota", "#38bdf8")
        self.t_real = self._make_tower("Realizada", "#22c55e")
        self.t_canc = self._make_tower("Cancelada", "#ef4444")
        for w in (self.t_parado, self.t_rota, self.t_real, self.t_canc):
            towers.addWidget(w)
        root.addLayout(towers)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Coleta", "Situação (AE)", "Status", "Hora (AF)", "Placa (AK)", "Carreta (AL)", "Motorista (AN)"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        root.addWidget(self.table, 2)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        root.addWidget(self.log)

        self.apply_periodo_d2()

    def _make_tower(self, title: str, color: str) -> QGroupBox:
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        val = QLabel("0")
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val.setStyleSheet(f"font-size: 28px; font-weight: 800; color: {color};")
        lay.addWidget(val)
        box._value_label = val  # noqa: SLF001
        return box

    def apply_periodo_d2(self) -> None:
        ini, fim = sugestao_periodo("diario")
        self.start_edit.setText(to_ssw_ddmmyy(ini))
        self.end_edit.setText(to_ssw_ddmmyy(fim))

    def _log(self, msg: str) -> None:
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {msg}")

    def _set_busy(self, busy: bool) -> None:
        self.btn_run.setEnabled(not busy)
        self.btn_analyze.setEnabled(not busy)
        self.btn_run.setText("Processando..." if busy else "Baixar 103 + analisar")

    def _start_worker(self, worker: Worker103) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "103", "Ja existe um processo rodando.")
            return
        self._set_busy(True)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.status.connect(self._log)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._set_busy(False))
        self.thread = thread
        self.worker = worker
        thread.start()

    def _run_full(self) -> None:
        creds: SswCredentials = self._get_credentials()
        settings: AceSettings = self._get_settings()
        if not (creds.user and creds.password):
            QMessageBox.warning(self, "Login", "Configure o login SSW (Ctrl+L).")
            return
        try:
            start = normalize_date(self.start_edit.text())
            end = normalize_date(self.end_edit.text())
        except ValueError as error:
            QMessageBox.warning(self, "Data", str(error))
            return
        self.start_edit.setText(to_ssw_ddmmyy(start))
        self.end_edit.setText(to_ssw_ddmmyy(end))
        save_all(creds, settings)
        self._log(
            f"103 download | limite {to_ssw_ddmmyy(start)} a {to_ssw_ddmmyy(end)}"
        )
        self._start_worker(
            Worker103(
                "full",
                start_date=start,
                end_date=end,
                credentials=creds,
                settings=settings,
                keep_open=self.keep_open.isChecked(),
                headless=False,
            )
        )

    def _analyze_latest(self) -> None:
        report = find_latest_103()
        if not report:
            QMessageBox.warning(self, "103", "Nenhum Excel 103 em downloads.")
            return
        try:
            start = normalize_date(self.start_edit.text())
            end = normalize_date(self.end_edit.text())
            periodo = format_period(start, end)
        except Exception:
            periodo = ""
        self._start_worker(
            Worker103("analyze", report_path=report, periodo=periodo)
        )

    def _on_finished(self, result: dict) -> None:
        analysis = result.get("analysis") or {}
        records = analysis.get("records") or []
        tot = analysis.get("totais") or {}
        self.t_parado._value_label.setText(str(tot.get("parado", 0)))  # noqa: SLF001
        self.t_rota._value_label.setText(str(tot.get("em_rota", 0)))  # noqa: SLF001
        self.t_real._value_label.setText(str(tot.get("realizada", 0)))  # noqa: SLF001
        self.t_canc._value_label.setText(str(tot.get("cancelada", 0)))  # noqa: SLF001

        self.table.setRowCount(0)
        for rec in records:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [
                rec.get("coleta_id") or "",
                rec.get("situacao_atual") or "",
                rec.get("status_ace") or "",
                rec.get("hora") or "",
                rec.get("placa") or "",
                rec.get("placa_carreta") or "",
                rec.get("motorista") or "",
            ]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))

        self._log(
            f"OK 103: {analysis.get('lote')} coletas | {tot}"
        )
        QMessageBox.information(
            self,
            "ACE 103",
            f"Analise 103 concluida.\n\n"
            f"Total: {tot.get('total', 0)}\n"
            f"Parado: {tot.get('parado', 0)}\n"
            f"Em rota: {tot.get('em_rota', 0)}\n"
            f"Realizada: {tot.get('realizada', 0)}\n"
            f"Cancelada: {tot.get('cancelada', 0)}",
        )

    def _on_failed(self, message: str) -> None:
        self._log(f"ERRO 103: {message}")
        QMessageBox.critical(self, "ACE 103", message)
