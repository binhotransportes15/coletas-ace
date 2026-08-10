"""Monitor de hardware · CPU / RAM / GPU (estilo gerenciador de tarefas)."""
from __future__ import annotations

import os
import platform
import subprocess
import time
from functools import lru_cache
from typing import Any


def _noop_create_no_window() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


@lru_cache(maxsize=1)
def _psutil_mod():
    try:
        import psutil  # type: ignore

        return psutil
    except Exception:
        return None


def host_info() -> dict[str, str]:
    psutil = _psutil_mod()
    info = {
        "host": platform.node() or "localhost",
        "os": f"{platform.system()} {platform.release()}".strip(),
        "arch": platform.machine() or "—",
        "cpu_name": platform.processor() or "CPU",
        "cores": "—",
        "ram_total_gb": "—",
    }
    if psutil is not None:
        try:
            info["cores"] = f"{psutil.cpu_count(logical=True) or '?'} thr"
            phys = psutil.cpu_count(logical=False)
            if phys:
                info["cores"] = f"{phys}c / {psutil.cpu_count(logical=True)}t"
        except Exception:
            pass
        try:
            total = float(psutil.virtual_memory().total)
            info["ram_total_gb"] = f"{total / (1024 ** 3):.1f} GB"
        except Exception:
            pass
        # nome amigável do CPU no Windows
        if os.name == "nt":
            try:
                out = subprocess.check_output(
                    ["wmic", "cpu", "get", "Name"],
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    creationflags=_noop_create_no_window(),
                )
                lines = [
                    ln.strip()
                    for ln in out.decode("utf-8", errors="ignore").splitlines()
                    if ln.strip() and ln.strip().lower() != "name"
                ]
                if lines:
                    info["cpu_name"] = lines[0][:48]
            except Exception:
                pass
    return info


def sample_usage() -> dict[str, Any]:
    """
    Retorna percentuais atuais.
    cpu / mem sempre que possível; gpu None se indisponível.
    """
    psutil = _psutil_mod()
    cpu = None
    mem = None
    mem_used = None
    mem_total = None
    if psutil is not None:
        try:
            # interval=None usa delta desde a última chamada (rápido no timer)
            cpu = float(psutil.cpu_percent(interval=None))
        except Exception:
            cpu = None
        try:
            vm = psutil.virtual_memory()
            mem = float(vm.percent)
            mem_used = float(vm.used)
            mem_total = float(vm.total)
        except Exception:
            pass
    else:
        # fallback mínimo sem psutil
        try:
            load = os.getloadavg()  # type: ignore[attr-defined]
            cpu = min(100.0, float(load[0]) * 25.0)
        except Exception:
            cpu = None

    gpu = _sample_gpu()
    return {
        "cpu": cpu,
        "mem": mem,
        "gpu": gpu,
        "mem_used": mem_used,
        "mem_total": mem_total,
        "ts": time.time(),
    }


_gpu_cache: dict[str, Any] = {"t": 0.0, "val": None, "name": None}


def _sample_gpu() -> float | None:
    """NVIDIA via nvidia-smi; cache 1.2s (comando é mais lento)."""
    now = time.time()
    if now - float(_gpu_cache.get("t") or 0) < 1.2:
        return _gpu_cache.get("val")  # type: ignore[return-value]
    val: float | None = None
    name: str | None = _gpu_cache.get("name")  # type: ignore[assignment]
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,name",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            creationflags=_noop_create_no_window(),
        )
        line = out.decode("utf-8", errors="ignore").strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if parts:
            val = float(parts[0])
        if len(parts) > 1 and parts[1]:
            name = parts[1][:40]
    except Exception:
        val = None
    _gpu_cache["t"] = now
    _gpu_cache["val"] = val
    _gpu_cache["name"] = name
    return val


def gpu_name() -> str | None:
    _sample_gpu()
    return _gpu_cache.get("name")  # type: ignore[return-value]


def warmup() -> None:
    """Primeira leitura de CPU% precisa de duas amostras."""
    psutil = _psutil_mod()
    if psutil is None:
        return
    try:
        psutil.cpu_percent(interval=None)
        time.sleep(0.05)
        psutil.cpu_percent(interval=None)
    except Exception:
        pass
