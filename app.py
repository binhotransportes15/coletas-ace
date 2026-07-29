from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from config import (
    CACHE_DIR,
    DASHBOARD_DIR,
    DOWNLOAD_DIR,
    AceSettings,
    SswCredentials,
    load_credentials,
    load_settings,
    save_all,
)
from dates import format_period, normalize_date, sugestao_periodo
from pipeline import find_latest_report, run_analysis_only, run_full_pipeline
from publish_dashboard import ensure_dashboard_files


class PipelineWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, mode: str, **kwargs) -> None:
        super().__init__()
        self.mode = mode  # full | analyze
        self.kwargs = kwargs

    @Slot()
    def run(self) -> None:
        try:
            if self.mode == "analyze":
                result = run_analysis_only(**self.kwargs, on_status=self.status.emit)
            else:
                result = run_full_pipeline(**self.kwargs, on_status=self.status.emit)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))
            return
        self.finished.emit(result)


class LoginDialog(QDialog):
    def __init__(self, credentials: SswCredentials, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Login SSW")
        self.setModal(True)
        self.setFixedWidth(360)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        hint = QLabel("Credenciais usadas na automacao do SSW.")
        hint.setObjectName("subtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)
        self.domain_edit = QLineEdit(credentials.domain)
        self.document_edit = QLineEdit(credentials.document)
        self.user_edit = QLineEdit(credentials.user)
        self.password_edit = QLineEdit(credentials.password)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.unit_edit = QLineEdit(credentials.unit)
        form.addRow("Dominio", self.domain_edit)
        form.addRow("Documento", self.document_edit)
        form.addRow("Usuario", self.user_edit)
        form.addRow("Senha", self.password_edit)
        form.addRow("Unidade", self.unit_edit)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Salvar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def apply_to(self, credentials: SswCredentials) -> SswCredentials:
        credentials.domain = self.domain_edit.text().strip() or credentials.domain
        credentials.document = self.document_edit.text().strip() or credentials.document
        credentials.user = self.user_edit.text().strip() or credentials.user
        credentials.password = self.password_edit.text()
        credentials.unit = self.unit_edit.text().strip().lower() or credentials.unit
        return credentials


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.credentials = load_credentials()
        self.settings = load_settings()
        self.thread: QThread | None = None
        self.worker: PipelineWorker | None = None

        self.setWindowTitle("ACE · Analisador Coleta Entrega")
        self.resize(900, 720)
        self._build_menu()
        self._build_ui()
        self._apply_style()
        self._refresh_login_status()
        self._apply_periodo_sugerido()
        ensure_dashboard_files()

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()
        menu_config = menu_bar.addMenu("Configuracao")
        action_login = QAction("Login SSW...", self)
        action_login.setShortcut("Ctrl+L")
        action_login.triggered.connect(self._open_login_dialog)
        menu_config.addAction(action_login)
        menu_config.addSeparator()
        action_save = QAction("Salvar opcoes", self)
        action_save.triggered.connect(self._save_options)
        menu_config.addAction(action_save)

        menu_arquivos = menu_bar.addMenu("Arquivos")
        action_downloads = QAction("Abrir pasta downloads", self)
        action_downloads.triggered.connect(self._open_downloads)
        menu_arquivos.addAction(action_downloads)
        action_cache = QAction("Abrir pasta cache (CSV)", self)
        action_cache.triggered.connect(self._open_cache)
        menu_arquivos.addAction(action_cache)
        action_dash = QAction("Abrir dashboard local", self)
        action_dash.triggered.connect(self._open_dashboard)
        menu_arquivos.addAction(action_dash)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("ACE · Analisador Coleta Entrega")
        title.setObjectName("title")
        title_col.addWidget(title)
        subtitle = QLabel(
            "Opcao 50 → analise (situacoes + historico) → planilha Google → dashboard. "
            "Periodo diario usa D-2 (cadastro → rua → relatorio)."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        title_col.addWidget(subtitle)
        header.addLayout(title_col, 1)
        self.login_status = QPushButton()
        self.login_status.setObjectName("loginStatus")
        self.login_status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_status.clicked.connect(self._open_login_dialog)
        header.addWidget(self.login_status, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        mid = QHBoxLayout()
        mid.setSpacing(12)

        options_box = QGroupBox("Opcoes SSW")
        options_form = QFormLayout(options_box)
        self.coleta_option_edit = QLineEdit(self.settings.coleta_option or "50")
        self.entrega_option_edit = QLineEdit(self.settings.entrega_option or "")
        self.entrega_option_edit.setPlaceholderText("Em aberto")
        options_form.addRow("Opcao coleta", self.coleta_option_edit)
        options_form.addRow("Opcao entrega", self.entrega_option_edit)
        mid.addWidget(options_box, 1)

        period_box = QGroupBox("Periodo (DDMM)")
        period_layout = QVBoxLayout(period_box)
        mode_row = QHBoxLayout()
        self.mode_diario = QRadioButton("Diario (D-2)")
        self.mode_sexta = QRadioButton("Sexta (cadastro sex)")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mode_diario)
        self.mode_group.addButton(self.mode_sexta)
        if (self.settings.periodo_modo or "diario").lower() == "sexta":
            self.mode_sexta.setChecked(True)
        else:
            self.mode_diario.setChecked(True)
        self.mode_diario.toggled.connect(self._apply_periodo_sugerido)
        self.mode_sexta.toggled.connect(self._apply_periodo_sugerido)
        mode_row.addWidget(self.mode_diario)
        mode_row.addWidget(self.mode_sexta)
        period_layout.addLayout(mode_row)
        period_form = QFormLayout()
        self.start_edit = QLineEdit()
        self.end_edit = QLineEdit()
        self.start_edit.setPlaceholderText("DDMM")
        self.end_edit.setPlaceholderText("DDMM")
        period_form.addRow("Inicio", self.start_edit)
        period_form.addRow("Fim", self.end_edit)
        period_layout.addLayout(period_form)
        mid.addWidget(period_box, 1)
        root.addLayout(mid)

        checks = QHBoxLayout()
        self.keep_open_check = QCheckBox("Manter navegador aberto")
        self.sync_check = QCheckBox("Enviar Sheets + dashboard apos analisar")
        self.sync_check.setChecked(True)
        checks.addWidget(self.keep_open_check)
        checks.addWidget(self.sync_check)
        checks.addStretch(1)
        root.addLayout(checks)

        buttons = QHBoxLayout()
        self.analyze_button = QPushButton("Analisar ultimo relatorio")
        self.analyze_button.clicked.connect(self._start_analyze)
        self.run_button = QPushButton("Baixar + analisar + enviar")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self._start_full)
        self.open_downloads_button = QPushButton("Downloads")
        self.open_downloads_button.clicked.connect(self._open_downloads)
        buttons.addWidget(self.open_downloads_button)
        buttons.addWidget(self.analyze_button)
        buttons.addStretch(1)
        buttons.addWidget(self.run_button)
        root.addLayout(buttons)

        lists = QHBoxLayout()
        files_box = QGroupBox("Arquivos / status")
        files_layout = QVBoxLayout(files_box)
        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(self._open_file_item)
        files_layout.addWidget(self.file_list)
        lists.addWidget(files_box, 1)

        coletas_box = QGroupBox("Coletas analisadas (situacao)")
        coletas_layout = QVBoxLayout(coletas_box)
        self.coletas_list = QListWidget()
        coletas_layout.addWidget(self.coletas_list)
        lists.addWidget(coletas_box, 1)
        root.addLayout(lists, 2)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

        self._log(f"Cache: {CACHE_DIR}")
        self._log("Login em Configuracao → Login SSW (Ctrl+L).")

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #0b1220; color: #e2e8f0; font-family: Segoe UI, sans-serif; font-size: 13px; }
            QMenuBar { background: #020617; color: #e2e8f0; border-bottom: 1px solid #1e293b; }
            QMenuBar::item:selected { background: #1e293b; }
            QMenu { background: #020617; color: #e2e8f0; border: 1px solid #334155; }
            QMenu::item:selected { background: #0369a1; }
            QLabel#title { font-size: 22px; font-weight: 800; color: #f8fafc; }
            QLabel#subtitle { color: #94a3b8; }
            QPushButton#loginStatus {
                color: #67e8f9; font-size: 12px; font-weight: 600; text-align: right;
                padding: 8px 12px; border: 1px solid #164e63; border-radius: 8px; background: #042f2e;
            }
            QPushButton#loginStatus:hover { background: #083344; border-color: #22d3ee; }
            QGroupBox {
                border: 1px solid #1e293b; border-radius: 10px; margin-top: 10px; padding: 12px; font-weight: 700;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #38bdf8; }
            QLineEdit, QPlainTextEdit, QListWidget {
                background: #020617; border: 1px solid #334155; border-radius: 8px; padding: 8px;
            }
            QListWidget::item:selected { background: #0369a1; }
            QPushButton {
                background: #1e293b; border: 1px solid #334155; border-radius: 8px;
                padding: 10px 14px; font-weight: 700;
            }
            QPushButton:hover { background: #334155; }
            QPushButton#primary { background: #0284c7; border: none; color: white; }
            QPushButton#primary:hover { background: #0ea5e9; }
            QPushButton:disabled { color: #64748b; background: #111827; }
            QDialog { background: #0b1220; }
            """
        )

    def _refresh_login_status(self) -> None:
        user = (self.credentials.user or "—").strip()
        unit = (self.credentials.unit or "—").strip().upper()
        self.login_status.setText(f"SSW  {user}  ·  {unit}\nClique para editar login")

    def _log(self, message: str) -> None:
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")

    def _periodo_modo(self) -> str:
        return "sexta" if self.mode_sexta.isChecked() else "diario"

    def _apply_periodo_sugerido(self) -> None:
        ini, fim = sugestao_periodo(self._periodo_modo())
        self.start_edit.setText(ini)
        self.end_edit.setText(fim)

    def _open_login_dialog(self) -> None:
        dialog = LoginDialog(self.credentials, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        dialog.apply_to(self.credentials)
        save_all(self.credentials, self._settings_from_ui())
        self._refresh_login_status()
        self._log(f"Login salvo: {self.credentials.user} / {self.credentials.unit.upper()}")

    def _settings_from_ui(self) -> AceSettings:
        self.settings.coleta_option = self.coleta_option_edit.text().strip() or "50"
        self.settings.entrega_option = self.entrega_option_edit.text().strip()
        self.settings.periodo_modo = self._periodo_modo()
        return self.settings

    def _save_options(self) -> None:
        save_all(self.credentials, self._settings_from_ui())
        self._log("Opcoes salvas.")
        QMessageBox.information(self, "ACE", "Opcoes salvas.")

    def _open_downloads(self) -> None:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(DOWNLOAD_DIR))  # noqa: S606

    def _open_cache(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(CACHE_DIR))  # noqa: S606

    def _open_dashboard(self) -> None:
        ensure_dashboard_files()
        os.startfile(str(DASHBOARD_DIR / "index.html"))  # noqa: S606

    def _open_file_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole) or item.text()
        target = Path(str(path))
        if target.exists():
            os.startfile(str(target))  # noqa: S606

    def _set_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy)
        self.analyze_button.setEnabled(not busy)
        self.run_button.setText("Processando..." if busy else "Baixar + analisar + enviar")

    def _start_worker(self, worker: PipelineWorker) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "Em andamento", "Ja existe um processo rodando.")
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
        thread.finished.connect(self._on_thread_finished)
        self.thread = thread
        self.worker = worker
        thread.start()

    def _start_full(self) -> None:
        if not (self.credentials.user and self.credentials.password):
            QMessageBox.warning(self, "Login", "Configure o login SSW.")
            self._open_login_dialog()
            return
        settings = self._settings_from_ui()
        save_all(self.credentials, settings)
        try:
            start = normalize_date(self.start_edit.text())
            end = normalize_date(self.end_edit.text())
        except ValueError as error:
            QMessageBox.warning(self, "Data invalida", str(error))
            return
        self.start_edit.setText(start)
        self.end_edit.setText(end)
        self.file_list.clear()
        self.coletas_list.clear()
        self._log(f"Inicio fluxo completo | {format_period(start, end)}")
        worker = PipelineWorker(
            "full",
            modo=self._periodo_modo(),
            start_date=start,
            end_date=end,
            credentials=self.credentials,
            settings=settings,
            keep_open=self.keep_open_check.isChecked(),
            headless=False,
        )
        self._start_worker(worker)

    def _start_analyze(self) -> None:
        report = find_latest_report()
        samples = list(Path("data/samples").glob("*.sswweb")) if Path("data/samples").exists() else []
        if not report and samples:
            report = samples[0]
        if not report:
            QMessageBox.warning(self, "Analise", "Nenhum .sswweb encontrado em downloads/samples.")
            return
        settings = self._settings_from_ui()
        self._log(f"Analisando: {report}")
        worker = PipelineWorker(
            "analyze",
            report_path=report,
            settings=settings,
            sync=self.sync_check.isChecked(),
        )
        self._start_worker(worker)

    def _on_finished(self, result: dict) -> None:
        analysis = result.get("analysis") or {}
        paths = analysis.get("paths") or {}
        self.file_list.clear()
        for key, file_path in paths.items():
            item = QListWidgetItem(f"[{key}] {Path(file_path).name}")
            item.setData(Qt.ItemDataRole.UserRole, str(file_path))
            self.file_list.addItem(item)
        if result.get("report"):
            item = QListWidgetItem(f"[relatorio] {Path(result['report']).name}")
            item.setData(Qt.ItemDataRole.UserRole, result["report"])
            self.file_list.addItem(item)

        self.coletas_list.clear()
        for rec in (analysis.get("records") or [])[:300]:
            sit = rec.get("situacao_atual") or "?"
            cid = rec.get("coleta_id") or ""
            self.coletas_list.addItem(f"{cid}  ·  {sit}")

        self._log(
            f"Concluido: lote={analysis.get('lote_atual')} "
            f"coletas_cache={analysis.get('coletas')} "
            f"historico={analysis.get('historico')}"
        )
        QMessageBox.information(
            self,
            "ACE",
            f"Analise concluida.\nColetas no lote: {analysis.get('lote_atual')}\n"
            f"Historico (cache): {analysis.get('historico')}\n"
            f"CSV em:\n{CACHE_DIR}",
        )

    def _on_failed(self, message: str) -> None:
        self._log(f"ERRO: {message}")
        QMessageBox.critical(self, "Falha", message)

    def _on_thread_finished(self) -> None:
        self._set_busy(False)
        self.thread = None
        self.worker = None


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
