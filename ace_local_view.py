"""
Modo local do dashboard — sem GitHub Pages.

Abre telas (coleta, entrega, armazém…) em janelas internas do CRT
(QWebEngine) ou no navegador, servidas em http://127.0.0.1.
"""
from __future__ import annotations

import webbrowser
from typing import Any, Callable

from dashboard_server import dashboard_screen_url, ensure_dashboard_server

StatusCallback = Callable[[str], None]

# id → (rótulo, hash da rota TV)
LOCAL_SCREENS: dict[str, tuple[str, str]] = {
    "coleta": ("Coletas", "tv/distribuicao/coleta"),
    "entrega": ("Entregas", "tv/distribuicao/entrega"),
    "agendamento": ("Agendamentos", "tv/distribuicao/agendamento"),
    "armazem": ("Armazém · Pátio", "tv/armazem/patio"),
    "patio": ("Armazém · Pátio", "tv/armazem/patio"),
    "conferentes": ("Armazém · Conferentes", "tv/armazem/conferentes"),
    "pendencia": ("Pendência", "tv/pendencia"),
    "contratacao": ("Contratação", "tv/contratacao"),
    "reciclagem": ("Reciclagem", "tv/reciclagem"),
    "emissao": ("Emissão", "tv/emissao"),
}

# Ordem na UI (ids canônicos, sem aliases)
LOCAL_SCREEN_ORDER: tuple[str, ...] = (
    "coleta",
    "entrega",
    "agendamento",
    "armazem",
    "conferentes",
    "pendencia",
    "contratacao",
    "reciclagem",
    "emissao",
)

_ALIASES: dict[str, str] = {
    "50": "coleta",
    "coletas": "coleta",
    "dist": "coleta",
    "distribuicao": "coleta",
    "36": "entrega",
    "entregas": "entrega",
    "225": "agendamento",
    "agenda": "agendamento",
    "agendamentos": "agendamento",
    "78": "armazem",
    "arm": "armazem",
    "pátio": "armazem",
    "patio": "armazem",
    "177": "conferentes",
    "31": "pendencia",
    "pendência": "pendencia",
    "pendencias": "pendencia",
    "73": "contratacao",
    "contratação": "contratacao",
    "ctr": "contratacao",
    "19": "reciclagem",
    "019": "reciclagem",
    "81": "reciclagem",
    "081": "reciclagem",
    "recicla": "reciclagem",
    "455": "emissao",
    "emissão": "emissao",
}

_open_windows: list[Any] = []

try:
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import QWebEngineSettings

        _HAS_WEBENGINE = True
    except Exception:  # noqa: BLE001
        QWebEngineView = None  # type: ignore[misc, assignment]
        QWebEngineSettings = None  # type: ignore[misc, assignment]
        _HAS_WEBENGINE = False
except Exception:  # noqa: BLE001
    _HAS_WEBENGINE = False
    QWebEngineView = None  # type: ignore[misc, assignment]


def normalize_screen_id(raw: str) -> str | None:
    key = (raw or "").strip().lower().replace(" ", "")
    if not key:
        return None
    if key in LOCAL_SCREENS and key in LOCAL_SCREEN_ORDER:
        return key
    if key in LOCAL_SCREENS:
        # alias patio → armazem
        if key == "patio":
            return "armazem"
        return key
    mapped = _ALIASES.get(key)
    return mapped if mapped in LOCAL_SCREENS else None


def parse_screen_ids(tokens: list[str] | tuple[str, ...] | None) -> list[str]:
    """Resolve lista de ids; vazio → todas as telas prontas."""
    if not tokens:
        return list(LOCAL_SCREEN_ORDER)
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        for part in str(tok).replace(";", ",").split(","):
            sid = normalize_screen_id(part)
            if sid and sid not in seen:
                seen.add(sid)
                out.append(sid)
    return out or list(LOCAL_SCREEN_ORDER)


def refresh_local_data(on_status: StatusCallback | None = None) -> dict[str, Any]:
    """Copia CSVs + grava JSON interno — sem Sheets e sem GitHub."""
    from local_store import persist_all
    from publish_dashboard import publish_dashboard

    status = on_status or (lambda _m: None)
    status("Local: atualizando CSVs do dashboard (sem GitHub/Sheets)…")
    dash = publish_dashboard(on_status=status, allow_push=False)
    snap = persist_all(on_status=status)
    return {"ok": True, "dashboard": dash, "local": snap}


def screen_label(screen_id: str) -> str:
    pair = LOCAL_SCREENS.get(screen_id)
    return pair[0] if pair else screen_id


def screen_hash(screen_id: str) -> str:
    pair = LOCAL_SCREENS.get(screen_id)
    return pair[1] if pair else "tv"


class LocalScreenWindow(QMainWindow):  # type: ignore[misc]
    """Janela interna com o dashboard da tela escolhida."""

    def __init__(self, screen_id: str, *, port: int, parent: Any = None) -> None:
        super().__init__(parent)
        self.screen_id = screen_id
        self._port = port
        label = screen_label(screen_id)
        self.setWindowTitle(f"ACE Local · {label}")
        self.resize(1280, 800)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QWidget()
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(8, 6, 8, 6)
        title = QLabel(f"LOCAL · {label}")
        title.setStyleSheet("font-weight: 600;")
        bar_lay.addWidget(title, 1)
        btn_reload = QPushButton("Recarregar")
        btn_reload.clicked.connect(self.reload)
        btn_browser = QPushButton("No navegador")
        btn_browser.clicked.connect(self.open_in_browser)
        bar_lay.addWidget(btn_reload)
        bar_lay.addWidget(btn_browser)
        lay.addWidget(bar)

        url = dashboard_screen_url(screen_hash(screen_id), port=port)
        if _HAS_WEBENGINE and QWebEngineView is not None:
            self.view = QWebEngineView()
            try:
                settings = self.view.settings()
                settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
                settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
            except Exception:  # noqa: BLE001
                pass
            self.view.setUrl(QUrl(url))
            lay.addWidget(self.view, 1)
        else:
            self.view = None
            tip = QLabel(
                "WebEngine indisponível — use “No navegador”.\n"
                f"{url}"
            )
            tip.setWordWrap(True)
            tip.setAlignment(Qt.AlignCenter)
            lay.addWidget(tip, 1)
            webbrowser.open(url)

    def reload(self) -> None:
        url = dashboard_screen_url(screen_hash(self.screen_id), port=self._port)
        if self.view is not None:
            self.view.setUrl(QUrl(url))
        else:
            webbrowser.open(url)

    def open_in_browser(self) -> None:
        webbrowser.open(dashboard_screen_url(screen_hash(self.screen_id), port=self._port))


def open_local_screens(
    screen_ids: list[str] | tuple[str, ...] | None = None,
    *,
    parent: Any = None,
    refresh: bool = True,
    prefer_embed: bool = True,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """
    Abre uma ou várias telas locais.
    prefer_embed=True e QApplication ativa → janelas Qt; senão → navegador.
    """
    status = on_status or (lambda _m: None)
    ids = parse_screen_ids(list(screen_ids) if screen_ids is not None else None)
    if refresh:
        status("Local: atualizando CSVs do dashboard (sem GitHub)…")
        refresh_local_data(on_status=status)

    port = ensure_dashboard_server()
    info_extra = ""
    try:
        from config import load_settings
        from dashboard_server import get_lan_ip, server_info

        if getattr(load_settings(), "dashboard_lan", False):
            info = server_info()
            info_extra = f" · LAN {get_lan_ip()}:{info.get('port')}"
    except Exception:  # noqa: BLE001
        pass
    status(f"Local: http://127.0.0.1:{port}{info_extra} · {len(ids)} tela(s)")

    opened: list[str] = []
    urls: list[str] = []

    app = None
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
    except Exception:  # noqa: BLE001
        app = None

    use_embed = bool(prefer_embed and app is not None and _HAS_WEBENGINE)

    for sid in ids:
        url = dashboard_screen_url(screen_hash(sid), port=port)
        urls.append(url)
        if use_embed:
            win = LocalScreenWindow(sid, port=port, parent=parent)
            win.show()
            win.raise_()
            _open_windows.append(win)
            try:
                win.destroyed.connect(lambda *_a, w=win: _forget_window(w))
            except Exception:  # noqa: BLE001
                pass
        else:
            webbrowser.open_new(url)
        opened.append(sid)
        status(f"Local abriu: {screen_label(sid)}")

    return {
        "ok": True,
        "port": port,
        "screens": opened,
        "urls": urls,
        "embed": use_embed,
    }


def _forget_window(win: Any) -> None:
    try:
        _open_windows.remove(win)
    except ValueError:
        pass
