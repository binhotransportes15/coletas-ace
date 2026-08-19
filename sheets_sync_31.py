"""Sync Google Sheets · Pendência SSW 031."""
from __future__ import annotations

import time
from typing import Any, Callable

from config import AceSettings, load_settings
from parser_ssw31 import (
    OFENSORES_31_CSV,
    OFENSOR_FIELDS,
    PENDENCIA_FIELDS,
    PENDENCIAS_31_CSV,
    RESUMO_31_CSV,
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


def sync_sheets_31(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
    force: bool = False,
) -> dict[str, Any]:
    status = on_status or _noop
    cfg = settings or load_settings()
    result: dict[str, Any] = {"ok": False, "via": "apps_script"}
    status("Sheets Pendência 31: preparando…")
    gate = _ensure_apps_script(cfg, status, ping=False, force=force)
    if not gate.get("ok"):
        result.update(gate)
        return result

    url = str(gate["url"])
    token = str(gate["token"])
    pend = _read_csv(PENDENCIAS_31_CSV)
    resumo = _read_csv(RESUMO_31_CSV)
    ofens = _read_csv(OFENSORES_31_CSV)
    items = [
        _sheet_item("Pendencias31", PENDENCIA_FIELDS, pend),
        _sheet_item("Resumo31", RESUMO_FIELDS, resumo),
        _sheet_item("Ofensores31", OFENSOR_FIELDS, ofens),
    ]
    try:
        status(
            f"Sheets Pendência 31: enviando {len(pend)} CTRC(s), "
            f"{len(ofens)} ofensor(es)…"
        )
        batch = _send_sheets_batch(url, token, items, on_status=status)
        if not batch.get("ok"):
            raise RuntimeError(str(batch.get("error") or "lote 31 falhou"))
        _ping_cache.update({"ok_at": time.time(), "url": url, "token": token})
        result.update(
            {
                "ok": True,
                "pendencias": len(pend),
                "ofensores": len(ofens),
                "stats": batch.get("stats"),
                "mode": "batch",
            }
        )
        status(f"Sheets Pendência 31 OK: {len(pend)} CTRC(s).")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets 31 falhou (cache local ok): {error}")
        return result
