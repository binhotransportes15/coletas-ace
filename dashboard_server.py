"""
HTTP local do dashboard (127.0.0.1) — evita file:// / CORS no WebEngine e no browser.
"""
from __future__ import annotations

import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from config import DASHBOARD_DIR

_httpd: ThreadingHTTPServer | None = None
_port: int = 0
_lock = threading.Lock()


def ensure_dashboard_server() -> int:
    """Sobe (ou reusa) o servidor em data/dashboard. Retorna a porta."""
    global _httpd, _port
    with _lock:
        if _httpd is not None and _port:
            return _port

        DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

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
        thread = threading.Thread(
            target=httpd.serve_forever, daemon=True, name="ace-dash-local"
        )
        thread.start()
        _httpd = httpd
        _port = port
        return port


def dashboard_base_url(port: int | None = None) -> str:
    p = int(port or ensure_dashboard_server())
    return f"http://127.0.0.1:{p}"


def dashboard_screen_url(route_hash: str, *, port: int | None = None) -> str:
    """route_hash: 'tv/distribuicao/coleta' (com ou sem #)."""
    h = (route_hash or "tv").lstrip("#")
    return f"{dashboard_base_url(port)}/index.html#{h}"
