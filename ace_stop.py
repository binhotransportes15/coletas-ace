"""Parada cooperativa do modo /automatica (CRT + loop + subprocessos).

- Event em memória (thread do CRT)
- Arquivo flag (subprocesso `ace_cmd automatica`)
- Fecha Chromium filhos via psutil na parada forçada
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from config import CACHE_DIR, ensure_dirs

STOP_FLAG = CACHE_DIR / "loop_stop.flag"
LOOP_PID_FILE = CACHE_DIR / "loop_pid.txt"

_stop = threading.Event()
_lock = threading.Lock()
_browsers: list[object] = []


class LoopStopped(Exception):
    """Ciclo interrompido pelo usuário (Parar / stop)."""


def clear_stop() -> None:
    _stop.clear()
    try:
        ensure_dirs()
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()
    except Exception:
        pass


def request_stop(*, force_browsers: bool = True) -> None:
    """Sinaliza parada imediata (e tenta fechar Chromium em andamento)."""
    _stop.set()
    try:
        ensure_dirs()
        STOP_FLAG.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass
    if force_browsers:
        close_registered_browsers()
        kill_child_browsers()


def stop_requested() -> bool:
    if _stop.is_set():
        return True
    try:
        return STOP_FLAG.exists()
    except Exception:
        return False


def register_browser(browser: object) -> None:
    with _lock:
        if browser not in _browsers:
            _browsers.append(browser)


def unregister_browser(browser: object) -> None:
    with _lock:
        try:
            _browsers.remove(browser)
        except ValueError:
            pass


def close_registered_browsers() -> int:
    with _lock:
        items = list(_browsers)
        _browsers.clear()
    n = 0
    for b in items:
        try:
            b.close()
            n += 1
        except Exception:
            pass
    return n


def kill_child_browsers() -> int:
    """Mata processos chrome/chromium/msedge filhos deste Python."""
    try:
        import psutil
    except Exception:
        return 0
    killed = 0
    try:
        me = psutil.Process(os.getpid())
        for child in me.children(recursive=True):
            try:
                name = (child.name() or "").lower()
                if any(x in name for x in ("chrom", "msedge", "playwright")):
                    child.kill()
                    killed += 1
            except Exception:
                pass
    except Exception:
        pass
    return killed


def write_loop_pid(pid: int | None = None) -> None:
    try:
        ensure_dirs()
        LOOP_PID_FILE.write_text(str(pid or os.getpid()), encoding="utf-8")
    except Exception:
        pass


def clear_loop_pid() -> None:
    try:
        if LOOP_PID_FILE.exists():
            LOOP_PID_FILE.unlink()
    except Exception:
        pass


def stop_external_loop_process() -> bool:
    """Se houver loop em subprocesso (PID file), pede parada / encerra."""
    request_stop(force_browsers=False)
    try:
        if not LOOP_PID_FILE.exists():
            return False
        raw = LOOP_PID_FILE.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except Exception:
        return False
    if pid == os.getpid():
        return False
    try:
        import psutil

        p = psutil.Process(pid)
        # filhos chrome primeiro
        for child in p.children(recursive=True):
            try:
                name = (child.name() or "").lower()
                if any(x in name for x in ("chrom", "msedge", "playwright")):
                    child.kill()
            except Exception:
                pass
        p.terminate()
        try:
            p.wait(timeout=4)
        except Exception:
            p.kill()
        clear_loop_pid()
        return True
    except Exception:
        # fallback Windows
        if os.name == "nt":
            try:
                import subprocess

                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=flags,
                )
                clear_loop_pid()
                return True
            except Exception:
                return False
        return False


def check_stop(msg: str = "parado pelo usuário") -> None:
    """Raise LoopStopped se Parar foi pedido."""
    if stop_requested():
        raise LoopStopped(msg)
