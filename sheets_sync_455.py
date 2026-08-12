"""Sync Google Sheets · Emissão SSW 455."""
from __future__ import annotations

import time
from typing import Any, Callable

from config import AceSettings, load_settings
from parser_ssw455 import (
    EXPEDIDORES_455_CSV,
    EXPEDIDOR_FIELDS,
    HORAS_455_CSV,
    HORA_FIELDS,
    RESUMO_455_CSV,
    RESUMO_FIELDS,
)
from sheets_sync import (
    _ensure_apps_script,
    _ping_cache,
    _read_csv,
    _send_sheets_batch,
    _sheet_item,
)

StatusCallback = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def sync_sheets_455(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    status = on_status or _noop
    cfg = settings or load_settings()
    result: dict[str, Any] = {"ok": False, "via": "apps_script"}
    status("Sheets Emissão 455: preparando…")
    gate = _ensure_apps_script(cfg, status, ping=False)
    if not gate.get("ok"):
        result.update(gate)
        return result

    url = str(gate["url"])
    token = str(gate["token"])
    resumo = _read_csv(RESUMO_455_CSV)
    expedidores = _read_csv(EXPEDIDORES_455_CSV)
    horas = _read_csv(HORAS_455_CSV)
    items = [
        _sheet_item("Resumo455", RESUMO_FIELDS, resumo),
        _sheet_item("Expedidores455", EXPEDIDOR_FIELDS, expedidores),
        _sheet_item("Horas455", HORA_FIELDS, horas),
    ]
    try:
        status(
            f"Sheets Emissão 455: enviando resumo, "
            f"{len(expedidores)} expedidor(es), {len(horas)} hora(s)…"
        )
        batch = _send_sheets_batch(url, token, items, on_status=status)
        if not batch.get("ok"):
            raise RuntimeError(str(batch.get("error") or "lote 455 falhou"))
        _ping_cache.update({"ok_at": time.time(), "url": url, "token": token})
        result.update(
            {
                "ok": True,
                "resumo": len(resumo),
                "expedidores": len(expedidores),
                "horas": len(horas),
                "stats": batch.get("stats"),
                "mode": "batch",
            }
        )
        status(f"Sheets Emissão 455 OK: {len(expedidores)} expedidor(es).")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets 455 falhou (cache local ok): {error}")
        return result
