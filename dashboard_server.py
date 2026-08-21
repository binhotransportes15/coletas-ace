"""
HTTP do dashboard — localhost e, se ligado, LAN (mesma rede Wi‑Fi/cabo).

Um único IP da máquina + hashes por setor (não precisa de IP por setor):
  http://192.168.x.x:8787/index.html#tv/distribuicao/coleta
  http://192.168.x.x:8787/index.html#tv/armazem
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from config import DASHBOARD_DIR, load_settings

_AVISO_MAX_UPLOAD = 80 * 1024 * 1024
_AVISO_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")

_httpd: ThreadingHTTPServer | None = None
_port: int = 0
_bind_host: str = "127.0.0.1"
_lan: bool = False
_lock = threading.Lock()

DEFAULT_LAN_PORT = 8787


def get_lan_ip() -> str:
    """IP da máquina na LAN (melhor esforço)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.connect(("8.8.8.8", 80))
        ip = str(s.getsockname()[0] or "")
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:  # noqa: BLE001
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            cand = info[4][0]
            if cand and not str(cand).startswith("127."):
                return str(cand)
    except Exception:  # noqa: BLE001
        pass
    return "127.0.0.1"


def _wanted_bind(*, lan: bool | None = None, port: int | None = None) -> tuple[str, int, bool]:
    cfg = load_settings()
    use_lan = bool(getattr(cfg, "dashboard_lan", False)) if lan is None else bool(lan)
    if port is None:
        try:
            p = int(getattr(cfg, "dashboard_port", 0) or 0)
        except (TypeError, ValueError):
            p = 0
    else:
        p = int(port)
    if use_lan:
        return ("0.0.0.0", p if p > 0 else DEFAULT_LAN_PORT, True)
    return ("127.0.0.1", p if p > 0 else 0, False)


def _stop_server_unlocked() -> None:
    global _httpd, _port, _bind_host, _lan
    if _httpd is None:
        return
    try:
        _httpd.shutdown()
    except Exception:  # noqa: BLE001
        pass
    try:
        _httpd.server_close()
    except Exception:  # noqa: BLE001
        pass
    _httpd = None
    _port = 0
    _bind_host = "127.0.0.1"
    _lan = False


def _ensure_firewall_lan(port: int) -> None:
    """Abre porta no Firewall do Windows (melhor esforço, sem admin pode falhar)."""
    if os.name != "nt" or port <= 0:
        return
    try:
        rule = f"ACE Dashboard LAN {port}"
        # remove antiga e cria inbound allow
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}"],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={rule}",
                "dir=in",
                "action=allow",
                "protocol=TCP",
                f"localport={port}",
                "profile=any",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:  # noqa: BLE001
        pass


def ensure_dashboard_server(
    *,
    lan: bool | None = None,
    port: int | None = None,
    restart_if_needed: bool = True,
) -> int:
    """
    Sobe (ou reusa) o servidor do /dashboard.
    lan=True → escuta 0.0.0.0 (aparelhos na mesma rede).
    """
    global _httpd, _port, _bind_host, _lan
    want_host, want_port, want_lan = _wanted_bind(lan=lan, port=port)

    with _lock:
        if _httpd is not None and _port:
            ok = _lan == want_lan and _bind_host == want_host
            if want_lan and want_port > 0 and _port != want_port:
                ok = False
            if ok:
                return _port
            if not restart_if_needed:
                return _port
            _stop_server_unlocked()

        DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

        class _Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
                super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

            def log_message(self, *_args) -> None:  # noqa: ANN002
                return

            def end_headers(self) -> None:
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                super().end_headers()

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Max-Age", "86400")
                self.end_headers()

            def _json(self, code: int, payload: dict) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0 or length > _AVISO_MAX_UPLOAD:
                    self._json(413, {"ok": False, "error": "arquivo grande demais (máx. 80 MB)"})
                    return
                body = self.rfile.read(length) if length else b""
                aviso_dir = DASHBOARD_DIR / "aviso"
                aviso_dir.mkdir(parents=True, exist_ok=True)

                if parsed.path.rstrip("/") == "/api/aviso/upload":
                    qs = parse_qs(parsed.query)
                    raw_name = unquote((qs.get("name") or ["arquivo.bin"])[0])
                    safe = _AVISO_NAME_RE.sub("_", os.path.basename(raw_name)).strip("._")
                    if not safe:
                        safe = "arquivo.bin"
                    dest = aviso_dir / safe
                    dest.write_bytes(body)
                    self._json(200, {"ok": True, "src": f"aviso/{safe}"})
                    return

                if parsed.path.rstrip("/") == "/api/aviso/config":
                    try:
                        data = json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self._json(400, {"ok": False, "error": "JSON inválido"})
                        return
                    if not isinstance(data, dict):
                        self._json(400, {"ok": False, "error": "JSON deve ser objeto"})
                        return
                    dest = aviso_dir / "aviso.json"
                    dest.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    self._json(200, {"ok": True, "src": "aviso/aviso.json"})
                    return

                self._json(404, {"ok": False, "error": "rota não encontrada"})

        bind_host = want_host
        bind_port = want_port
        if bind_port <= 0:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            bind_port = int(probe.getsockname()[1])
            probe.close()

        try:
            httpd = ThreadingHTTPServer((bind_host, bind_port), _Handler)
        except OSError:
            httpd = ThreadingHTTPServer((bind_host, 0), _Handler)
            bind_port = int(httpd.server_address[1])

        thread = threading.Thread(
            target=httpd.serve_forever, daemon=True, name="ace-dash-local"
        )
        thread.start()
        _httpd = httpd
        _port = bind_port
        _bind_host = bind_host
        _lan = want_lan
        if want_lan:
            _ensure_firewall_lan(bind_port)
        return _port


def dashboard_base_url(port: int | None = None, *, for_lan: bool | None = None) -> str:
    p = int(port or ensure_dashboard_server())
    use_lan = _lan if for_lan is None else bool(for_lan)
    if use_lan:
        return f"http://{get_lan_ip()}:{p}"
    return f"http://127.0.0.1:{p}"


def dashboard_screen_url(
    route_hash: str,
    *,
    port: int | None = None,
    for_lan: bool | None = None,
) -> str:
    h = (route_hash or "tv").lstrip("#")
    return f"{dashboard_base_url(port, for_lan=for_lan)}/index.html#{h}"


def lan_urls_by_screen(port: int | None = None) -> dict[str, str]:
    """Mapa id_tela → URL LAN."""
    from ace_local_view import LOCAL_SCREEN_ORDER, screen_hash

    p = int(port or ensure_dashboard_server(lan=True))
    return {
        sid: dashboard_screen_url(screen_hash(sid), port=p, for_lan=True)
        for sid in LOCAL_SCREEN_ORDER
    }


def server_info() -> dict[str, object]:
    return {
        "port": _port,
        "bind": _bind_host,
        "lan": _lan,
        "lan_ip": get_lan_ip() if _lan else "",
        "local_url": f"http://127.0.0.1:{_port}" if _port else "",
        "lan_url": f"http://{get_lan_ip()}:{_port}" if _port and _lan else "",
    }
