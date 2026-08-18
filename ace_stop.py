"""Parada cooperativa de QUALQUER comando ACE (CRT, loop, pipelines SSW).

- Event em memória (thread do CRT / CmdWorker)
- Arquivo flag (subprocesso `ace_cmd automatica`)
- Fecha Chromium/Playwright registrados + mata filhos do processo
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import CACHE_DIR, ensure_dirs

STOP_FLAG = CACHE_DIR / "loop_stop.flag"
LOOP_PID_FILE = CACHE_DIR / "loop_pid.txt"

_stop = threading.Event()
_lock = threading.Lock()
_browsers: list[object] = []


class LoopStopped(Exception):
    """Comando/ciclo interrompido pelo usuário (Parar / stop)."""


def clear_stop() -> None:
    _stop.clear()
    try:
        ensure_dirs()
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()
    except Exception:
        pass


def begin_command() -> None:
    """Início de um novo comando: limpa sinal de parada residual."""
    clear_stop()


def request_stop(*, force_browsers: bool = True) -> None:
    """Sinaliza parada imediata de qualquer comando e corta navegadores."""
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


def check_stop(msg: str = "parado pelo usuário") -> None:
    """Raise LoopStopped se Parar foi pedido (usar em loops longos)."""
    if stop_requested():
        raise LoopStopped(msg)


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


def _is_browserish_process(name: str, cmdline: str) -> bool:
    n = (name or "").lower()
    c = (cmdline or "").lower()
    if any(x in n for x in ("chrom", "msedge", "playwright", "firefox")):
        return True
    # driver Node do Playwright
    if "node" in n and ("playwright" in c or "driver" in c):
        return True
    return False


def kill_child_browsers() -> int:
    """Mata Chromium / Edge / Playwright / driver Node filhos deste Python."""
    try:
        import psutil
    except Exception:
        return 0
    killed = 0
    try:
        me = psutil.Process(os.getpid())
        for child in me.children(recursive=True):
            try:
                name = child.name() or ""
                try:
                    cmdline = " ".join(child.cmdline() or [])
                except Exception:
                    cmdline = ""
                if _is_browserish_process(name, cmdline):
                    child.kill()
                    killed += 1
            except Exception:
                pass
    except Exception:
        pass
    return killed


def launch_tracked_chromium(playwright: Any, **launch_kwargs: Any) -> Any:
    """chromium.launch + registro para o Parar fechar o browser."""
    check_stop("parado antes de abrir o navegador")
    browser = playwright.chromium.launch(**launch_kwargs)
    register_browser(browser)
    return browser


def close_tracked_browser(browser: Any, context: Any = None) -> None:
    try:
        if context is not None:
            context.close()
    except Exception:
        pass
    try:
        if browser is not None:
            browser.close()
    except Exception:
        pass
    try:
        if browser is not None:
            unregister_browser(browser)
    except Exception:
        pass


@contextmanager
def tracked_playwright_browser(playwright: Any, **launch_kwargs: Any) -> Iterator[Any]:
    browser = launch_tracked_chromium(playwright, **launch_kwargs)
    try:
        yield browser
    finally:
        close_tracked_browser(browser)


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
        for child in p.children(recursive=True):
            try:
                name = child.name() or ""
                try:
                    cmdline = " ".join(child.cmdline() or [])
                except Exception:
                    cmdline = ""
                if _is_browserish_process(name, cmdline):
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
