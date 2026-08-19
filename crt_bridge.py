"""
Ponte CMD ↔ janela CRT (status JSON + log espelhado + spawn do painel).

Uso no ACE:
  from crt_bridge import spawn_crt, publish, append_log
  spawn_crt()
  append_log("sistema", "menu pronto")
  publish(online=True, label="ONLINE", pct=0, detail="menu")
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
STATUS_PATH = _ROOT / "data" / "cache" / "crt_status.json"
LOG_PATH = _ROOT / "data" / "cache" / "crt_log.jsonl"
PID_PATH = _ROOT / "data" / "cache" / "crt_pid.txt"
_MAX_LOG_BYTES = 1_500_000


def publish(
    *,
    online: bool = True,
    label: str = "ONLINE",
    pct: float = 0.0,
    detail: str = "",
    title: str = "ACE",
    mode: str = "STANDBY",
    sectors: list[dict[str, Any]] | None = None,
    clear_sectors: bool = False,
) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    mode_u = str(mode or "STANDBY").upper()
    label_u = str(label or "").upper()
    payload: dict[str, Any] = {
        "ts": time.time(),
        "online": bool(online),
        "label": str(label or ("ONLINE" if online else "OFFLINE")),
        "pct": max(0.0, min(100.0, float(pct))),
        "detail": str(detail or ""),
        "title": str(title or "ACE"),
        "mode": str(mode or "STANDBY"),
    }
    if sectors is not None:
        payload["sectors"] = list(sectors)
    elif clear_sectors or mode_u in {"MENU", "OK", "STANDBY", "ERR", "STOP"} or label_u == "STOP":
        # Não herdar barrinhas em 100% de sessão anterior
        payload["sectors"] = []
    else:
        # RUN/LOOP: preserva sectors já publicados pelo loop
        try:
            if STATUS_PATH.is_file():
                old = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
                if isinstance(old.get("sectors"), list):
                    payload["sectors"] = old["sectors"]
        except Exception:
            pass
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATUS_PATH)


def publish_sectors(
    sectors: list[dict[str, Any]],
    *,
    online: bool = True,
    label: str = "LOOP",
    pct: float = 0.0,
    detail: str = "",
    mode: str = "RUN",
) -> None:
    """Atualiza só as barrinhas por setor (sem spam no log)."""
    publish(
        online=online,
        label=label,
        pct=pct,
        detail=detail,
        mode=mode,
        sectors=sectors,
    )


def append_log(kind: str, text: str, *, source: str = "cmd") -> dict[str, Any]:
    """
    Espelha uma linha de histórico CMD ↔ CRT.
    Retorna o registro gravado (para a UI local reutilizar sem reidratar do arquivo).
    """
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H:%M:%S")
    entry = {
        "ts": time.time(),
        "stamp": stamp,
        "kind": str(kind or "info"),
        "text": str(text or ""),
        "source": str(source or "cmd"),
    }
    try:
        if LOG_PATH.is_file() and LOG_PATH.stat().st_size > _MAX_LOG_BYTES:
            # corta metade antiga
            raw = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            keep = raw[len(raw) // 2 :]
            LOG_PATH.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return entry


def clear_log() -> None:
    """Zera o histórico espelhado (crt_log.jsonl) — comando limpar/cls/clear."""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for _ in range(8):
        try:
            # Truncate atômico (melhor que write_text sob arquivo aberto)
            with LOG_PATH.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write("")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:
                    pass
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.04)
    try:
        if LOG_PATH.is_file():
            LOG_PATH.unlink()
        LOG_PATH.write_text("", encoding="utf-8")
    except Exception:
        if last_err:
            pass


def read_log_since(offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Lê linhas novas do jsonl a partir do offset em bytes. Retorna (entries, novo_offset)."""
    if not LOG_PATH.is_file():
        return [], 0
    try:
        size = LOG_PATH.stat().st_size
        # Arquivo encolheu (limpar): não reler do zero — só acompanha o novo fim
        if offset > size:
            offset = size
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            chunk = fh.read()
            new_offset = fh.tell()
        entries: list[dict[str, Any]] = []
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
        return entries, new_offset
    except Exception:
        return [], offset


def read_status() -> dict[str, Any]:
    if not STATUS_PATH.is_file():
        return {
            "online": True,
            "label": "ONLINE",
            "pct": 0.0,
            "detail": "aguardando…",
            "title": "ACE",
            "mode": "STANDBY",
            "ts": time.time(),
        }
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {
            "online": False,
            "label": "OFFLINE",
            "pct": 0.0,
            "detail": "status ilegível",
            "title": "ACE",
            "mode": "ERR",
            "ts": time.time(),
        }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_existing_crt() -> None:
    """Encerra o CRT já aberto (PID gravado) para o próximo ace.bat carregar código novo."""
    pids: set[int] = set()
    if PID_PATH.is_file():
        try:
            pids.add(int(PID_PATH.read_text(encoding="utf-8").strip() or "0"))
        except Exception:
            pass
    for pid in list(pids):
        if not pid or not _pid_alive(pid):
            continue
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.kill(pid, 15)
        except Exception:
            pass
    try:
        if PID_PATH.is_file():
            PID_PATH.unlink()
    except Exception:
        pass


def spawn_crt(*, force: bool = False) -> bool:
    """Abre a janela CRT se ainda não estiver rodando. Retorna True se spawnou/ok."""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not force and PID_PATH.is_file():
        try:
            old = int(PID_PATH.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            old = 0
        if _pid_alive(old):
            return True

    publish(online=True, label="ONLINE", pct=0, detail="abrindo painel", mode="BOOT")
    script = _ROOT / "ace_crt.py"
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        )
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(script)],
            cwd=str(_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        PID_PATH.write_text(str(proc.pid), encoding="utf-8")
        return True
    except Exception:
        return False
