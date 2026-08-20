"""Sync Google Sheets · Contratação (Excel + 200)."""
from __future__ import annotations

import time
from typing import Any, Callable

from config import AceSettings, load_settings
from parser_ssw073 import (
    DESTINOS_073_CSV,
    DESTINO_FIELDS,
    RESUMO_073_CSV,
    RESUMO_FIELDS,
    VEICULOS_073_CSV,
    VEICULO_FIELDS,
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


def sync_sheets_073(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
    force: bool = False,
) -> dict[str, Any]:
    status = on_status or _noop
    cfg = settings or load_settings()
    result: dict[str, Any] = {"ok": False, "via": "apps_script"}
    status("Sheets Contratação 073: preparando…")
    gate = _ensure_apps_script(cfg, status, ping=False, force=force)
    if not gate.get("ok"):
        result.update(gate)
        return result

    url = str(gate["url"])
    token = str(gate["token"])
    resumo = _read_csv(RESUMO_073_CSV)
    veiculos = _read_csv(VEICULOS_073_CSV)
    destinos = _read_csv(DESTINOS_073_CSV)
    if not veiculos and not resumo:
        status("Sheets CTR: cache vazio — não sobrescreve abas.")
        result.update({"ok": True, "skipped": True, "reason": "empty_ctr_cache"})
        return result

    items = [
        _sheet_item("Resumo073", RESUMO_FIELDS, resumo),
        _sheet_item("Veiculos073", VEICULO_FIELDS, veiculos),
        _sheet_item("Destinos073", DESTINO_FIELDS, destinos),
    ]
    try:
        status(
            f"Sheets Contratação: enviando resumo, "
            f"{len(veiculos)} veículo(s), {len(destinos)} destino(s)…"
        )
        batch = _send_sheets_batch(url, token, items, on_status=status)
        if not batch.get("ok"):
            raise RuntimeError(str(batch.get("error") or "lote 073 falhou"))
        _ping_cache.update({"ok_at": time.time(), "url": url, "token": token})
        result.update(
            {
                "ok": True,
                "resumo": len(resumo),
                "veiculos": len(veiculos),
                "destinos": len(destinos),
                "stats": batch.get("stats"),
                "mode": "batch",
            }
        )
        status(f"Sheets Contratação OK: {len(veiculos)} veículo(s).")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets CTR falhou (cache local ok): {error}")
        return result
