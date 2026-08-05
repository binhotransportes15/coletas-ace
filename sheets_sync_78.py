"""Sync Google Sheets · Armazém 078 na MESMA planilha/Apps Script da distribuição."""
from __future__ import annotations

from typing import Any, Callable

from config import AceSettings, load_settings
from parser_ssw78 import RESUMO_CSV, RESUMO_FIELDS, VEICULOS_CSV, VEICULO_FIELDS
from parser_ssw177 import (
    CONFERENTES_CSV,
    CONFERENTE_FIELDS,
    RESUMO_177_CSV,
    RESUMO_177_FIELDS,
)
from sheets_sync import (
    _ensure_apps_script,
    _ping_cache,
    _read_csv,
    _send_sheets_batch,
    _sheet_item,
)
import time

StatusCallback = Callable[[str], None]
VEICULO_FIELDS_OUT = VEICULO_FIELDS + ["peso_veiculo"]


def _noop(_: str) -> None:
    return None


def sync_sheets_78(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """
    Envia Veiculos78 / Resumo78 / Conferentes177 / Resumo177 num lote só.
    Sem ping extra (o ciclo Dist já validou a conexão): isso era a 'trava' de ~1 min.
    """
    status = on_status or _noop
    cfg = settings or load_settings()
    result: dict[str, Any] = {"ok": False, "via": "apps_script"}

    status("Sheets Armazém: preparando envio…")
    gate = _ensure_apps_script(cfg, status, ping=False)
    if not gate.get("ok"):
        result.update(gate)
        return result

    url = str(gate["url"])
    token = str(gate["token"])
    veiculos = _read_csv(VEICULOS_CSV)
    resumo = _read_csv(RESUMO_CSV)
    conf = _read_csv(CONFERENTES_CSV)
    resumo177 = _read_csv(RESUMO_177_CSV)

    items: list[dict[str, Any]] = [
        _sheet_item("Veiculos78", VEICULO_FIELDS_OUT, veiculos),
        _sheet_item("Resumo78", RESUMO_FIELDS, resumo),
    ]
    if conf:
        items.append(_sheet_item("Conferentes177", CONFERENTE_FIELDS, conf))
        items.append(_sheet_item("Resumo177", RESUMO_177_FIELDS, resumo177))

    try:
        status(
            f"Sheets Armazém: lote ({len(veiculos)} veículo(s)"
            + (f", {len(conf)} conferente(s)" if conf else "")
            + ")…"
        )
        batch = _send_sheets_batch(url, token, items, on_status=status)
        if not batch.get("ok"):
            raise RuntimeError(str(batch.get("error") or "lote Armazém falhou"))

        _ping_cache.update({"ok_at": time.time(), "url": url, "token": token})
        stats = batch.get("stats") or {}
        n_v = len(veiculos)
        n_r = len(resumo)
        n_c = len(conf)
        result.update(
            {
                "ok": True,
                "veiculos": n_v,
                "resumo": n_r,
                "conferentes": n_c,
                "resumo177": len(resumo177) if conf else 0,
                "stats": stats,
                "mode": "batch",
            }
        )
        skipped = sum(1 for s in stats.values() if isinstance(s, dict) and s.get("skipped"))
        if skipped == len(stats) and stats:
            status("Sheets Armazém: sem mudança — pulou rede.")
        else:
            status(
                f"Sheets Armazém OK: {n_v} veículo(s)"
                + (f", {n_c} conferente(s)" if conf else "")
                + "."
            )
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets 078 falhou (cache local ok): {error}")
        return result
