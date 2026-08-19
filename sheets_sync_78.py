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
    include_78: bool = True,
    include_177: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """
    Envia abas do Armazém (078 e/ou 177).
    Usar include_177=False para mandar o pátio assim que o 078 ficar pronto.
    force=True: envia mesmo com modo_local (espelho do site público).
    """
    status = on_status or _noop
    cfg = settings or load_settings()
    result: dict[str, Any] = {"ok": False, "via": "apps_script"}

    status("Sheets Armazém: preparando envio…")
    gate = _ensure_apps_script(cfg, status, ping=False, force=force)
    if not gate.get("ok"):
        result.update(gate)
        return result

    url = str(gate["url"])
    token = str(gate["token"])
    veiculos = _read_csv(VEICULOS_CSV) if include_78 else []
    resumo = _read_csv(RESUMO_CSV) if include_78 else []
    conf = _read_csv(CONFERENTES_CSV) if include_177 else []
    resumo177 = _read_csv(RESUMO_177_CSV) if include_177 else []

    items: list[dict[str, Any]] = []
    if include_78:
        items.append(_sheet_item("Veiculos78", VEICULO_FIELDS_OUT, veiculos))
        items.append(_sheet_item("Resumo78", RESUMO_FIELDS, resumo))
    if include_177 and conf:
        items.append(_sheet_item("Conferentes177", CONFERENTE_FIELDS, conf))
        items.append(_sheet_item("Resumo177", RESUMO_177_FIELDS, resumo177))

    if not items:
        status("Sheets Armazém: nada para enviar neste passo.")
        return {"ok": True, "skipped": True, "reason": "empty"}

    try:
        parts = []
        if include_78:
            parts.append(f"{len(veiculos)} veículo(s)")
        if include_177 and conf:
            parts.append(f"{len(conf)} conferente(s)")
        status(f"Sheets Armazém: enviando agora ({', '.join(parts) or 'abas'})…")
        batch = _send_sheets_batch(url, token, items, on_status=status)
        if not batch.get("ok"):
            raise RuntimeError(str(batch.get("error") or "lote Armazém falhou"))

        _ping_cache.update({"ok_at": time.time(), "url": url, "token": token})
        stats = batch.get("stats") or {}
        n_v = len(veiculos) if include_78 else 0
        n_r = len(resumo) if include_78 else 0
        n_c = len(conf) if include_177 else 0
        result.update(
            {
                "ok": True,
                "veiculos": n_v,
                "resumo": n_r,
                "conferentes": n_c,
                "resumo177": len(resumo177) if include_177 and conf else 0,
                "stats": stats,
                "mode": "batch",
            }
        )
        skipped = sum(1 for s in stats.values() if isinstance(s, dict) and s.get("skipped"))
        if skipped == len(stats) and stats:
            status("Sheets Armazém: sem mudança — pulou rede.")
        else:
            status(
                f"Sheets Armazém OK"
                + (f": {n_v} veículo(s)" if include_78 else "")
                + (f", {n_c} conferente(s)" if include_177 and conf else "")
                + "."
            )
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets 078 falhou (cache local ok): {error}")
        return result
