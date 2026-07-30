"""
Intervalos de tempo para o modo /automatica.

Aceita: 30s | 5m | 1h | 2d | 90 (segundos se so numero)
"""
from __future__ import annotations

import re

_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*([smhd]|seg|segs|segundo|segundos|min|mins|minuto|minutos|"
    r"h|hr|hrs|hora|horas|d|dia|dias)?\s*$",
    re.IGNORECASE,
)

UNIT_SECONDS = {
    "": 1,  # numero puro = segundos
    "s": 1,
    "seg": 1,
    "segs": 1,
    "segundo": 1,
    "segundos": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minuto": 60,
    "minutos": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hora": 3600,
    "horas": 3600,
    "d": 86400,
    "dia": 86400,
    "dias": 86400,
}

MIN_SECONDS = 5
MAX_SECONDS = 30 * 86400  # 30 dias


def parse_duration(raw: str, *, default_unit: str = "s") -> int:
    """Converte texto em segundos. Raises ValueError se invalido."""
    text = str(raw or "").strip().lower().replace(",", ".")
    if not text:
        raise ValueError("Informe um intervalo. Ex.: 30s, 5m, 1h, 2d")
    m = _RE.match(text)
    if not m:
        raise ValueError(
            f"Intervalo invalido: {raw!r}. Use 30s | 5m | 1h | 2d (ou so numero em segundos)."
        )
    qty = float(m.group(1))
    unit = (m.group(2) or default_unit or "s").lower()
    mult = UNIT_SECONDS.get(unit)
    if mult is None:
        raise ValueError(f"Unidade desconhecida: {unit}")
    seconds = int(round(qty * mult))
    if seconds < MIN_SECONDS:
        raise ValueError(f"Minimo e {MIN_SECONDS}s (agora: {seconds}s).")
    if seconds > MAX_SECONDS:
        raise ValueError(f"Maximo e 30 dias ({MAX_SECONDS}s).")
    return seconds


def format_duration(seconds: int) -> str:
    s = max(0, int(seconds or 0))
    if s % 86400 == 0 and s >= 86400:
        d = s // 86400
        return f"{d}d"
    if s % 3600 == 0 and s >= 3600:
        h = s // 3600
        return f"{h}h"
    if s % 60 == 0 and s >= 60:
        m = s // 60
        return f"{m}m"
    return f"{s}s"


def format_duration_long(seconds: int) -> str:
    s = max(0, int(seconds or 0))
    parts: list[str] = []
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    mins, secs = divmod(s, 60)
    if days:
        parts.append(f"{days} dia{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins} min")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)
