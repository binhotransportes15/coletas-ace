"""
Controle online/offline do dashboard no GitHub Pages.

Escreve dashboard/status.json e faz push.
O site consulta esse arquivo e mostra tela de interrupcao.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import BASE_DIR, DASHBOARD_DIR

StatusCallback = Callable[[str], None]
STATUS_PATH = DASHBOARD_DIR / "status.json"


def _noop(_: str) -> None:
    return None


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )


def read_site_status() -> dict:
    if not STATUS_PATH.exists():
        return {
            "online": True,
            "message": "",
            "updated_at": None,
        }
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return {
            "online": bool(data.get("online", True)),
            "message": str(data.get("message") or ""),
            "updated_at": data.get("updated_at"),
        }
    except Exception:
        return {"online": True, "message": "", "updated_at": None}


def write_site_status(*, online: bool, message: str = "") -> dict:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "online": bool(online),
        "message": (message or "").strip(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not online and not payload["message"]:
        payload["message"] = "Sistema temporariamente interrompido."
    STATUS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload


def publish_site_status(
    *,
    online: bool,
    message: str = "",
    on_status: StatusCallback | None = None,
) -> str:
    status = on_status or _noop
    payload = write_site_status(online=online, message=message)
    estado = "ONLINE" if online else "INTERROMPIDO"
    status(f"Status local: {estado}")
    if payload.get("message"):
        status(f"Mensagem: {payload['message']}")

    status("Subindo status.json para o GitHub Pages...")
    add = _run(["git", "add", "dashboard/status.json"])
    if add.returncode != 0:
        return f"git add falhou: {(add.stderr or add.stdout)[:300]}"

    msg = f"site: {estado.lower()} {datetime.now():%Y-%m-%d %H:%M}"
    commit = _run(["git", "commit", "-m", msg])
    cout = ((commit.stdout or "") + (commit.stderr or "")).strip()
    if commit.returncode != 0 and "nothing to commit" not in cout.lower():
        # pode ja estar staged igual — tenta push mesmo assim se so mudou timestamp
        if "nothing to commit" not in cout.lower() and "no changes" not in cout.lower():
            print(cout)
            # se nao commitou porque identico, ainda ok
            if "nothing to commit" not in cout.lower():
                pass

    push = _run(["git", "push", "-u", "origin", "HEAD"])
    pout = ((push.stdout or "") + (push.stderr or "")).strip()
    print(pout or "(sem saida)")
    if push.returncode != 0:
        return (
            f"Status salvo local ({estado}), mas push falhou:\n{pout[:400]}\n"
            "Tente /push depois."
        )

    # Garante arquivos do fluxo index -> offline/app
    offline = DASHBOARD_DIR / "offline.html"
    app = DASHBOARD_DIR / "app.html"
    extra = []
    if offline.exists():
        extra.append("dashboard/offline.html")
    if app.exists():
        extra.append("dashboard/app.html")
    if (DASHBOARD_DIR / "index.html").exists():
        extra.append("dashboard/index.html")
    if extra:
        _run(["git", "add", *extra])

    pages = "https://binhotransportes15.github.io/coletas-ace/dashboard/"
    if online:
        return f"SITE LIGADO · {pages}"
    return (
        f"SITE INTERROMPIDO · {pages}\n"
        "Abra de novo (ou aguarde ~5s). Deve ir para a tela INTERROMPIDO."
    )
