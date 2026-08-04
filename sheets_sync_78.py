"""Sync Google Sheets · Armazém 078 na MESMA planilha/Apps Script da distribuição."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable

from apps_script_client import post_apps_script
from config import AceSettings, load_settings
from parser_ssw78 import RESUMO_CSV, RESUMO_FIELDS, VEICULOS_CSV, VEICULO_FIELDS
from parser_ssw177 import (
    CONFERENTES_CSV,
    CONFERENTE_FIELDS,
    RESUMO_177_CSV,
    RESUMO_177_FIELDS,
)
from sheets_sync import _bump_if_changed, _send_sheet

StatusCallback = Callable[[str], None]
VEICULO_FIELDS_OUT = VEICULO_FIELDS + ["peso_veiculo"]


def _noop(_: str) -> None:
    return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _ensure_apps_script(cfg: AceSettings, status: StatusCallback) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "skipped": False}
    if not cfg.enable_sheets:
        result["skipped"] = True
        result["reason"] = "enable_sheets=false"
        status("Sheets desabilitado — 078 so grava CSV local.")
        return result

    url = (cfg.apps_script_url or "").strip()
    token = (cfg.apps_script_token or "").strip()
    if not url:
        result["skipped"] = True
        status("Sheets: configure apps_script_url (mesmo da distribuição).")
        return result
    if not token:
        result["skipped"] = True
        status("Sheets: configure apps_script_token (coletas-ace).")
        return result

    auth = post_apps_script(
        url,
        {"token": token, "action": "ping", "sheet": "_ping", "headers": ["ok"], "rows": []},
        timeout=45,
        retries=3,
    )
    if auth.get("ok"):
        result.update({"ok": True, "url": url, "token": token})
        return result
    result["error"] = str(auth.get("error") or auth)
    status(f"Sheets falhou no ping: {result['error']}")
    return result


def sync_sheets_78(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    status = on_status or _noop
    cfg = settings or load_settings()
    result: dict[str, Any] = {"ok": False, "via": "apps_script"}
    gate = _ensure_apps_script(cfg, status)
    if not gate.get("ok"):
        result.update(gate)
        return result

    url = str(gate["url"])
    token = str(gate["token"])
    veiculos = _read_csv(VEICULOS_CSV)
    resumo = _read_csv(RESUMO_CSV)

    try:
        status(
            f"Sheets 078: Veiculos78/Resumo78 ({len(veiculos)} linha(s)) "
            "na planilha única da distribuição."
        )
        stats: dict[str, Any] = {}
        r_v = _send_sheet(url, token, "Veiculos78", VEICULO_FIELDS_OUT, veiculos, on_status=status)
        r_r = _send_sheet(url, token, "Resumo78", RESUMO_FIELDS, resumo, on_status=status)
        if not r_v.get("ok") or not r_r.get("ok"):
            raise RuntimeError(str((r_v.get("error") or r_r.get("error") or "078 replace falhou")))
        stats["Veiculos78"] = r_v
        stats["Resumo78"] = r_r
        n_v = int(r_v.get("rows") or len(veiculos))
        n_r = int(r_r.get("rows") or len(resumo))
        result.update({"ok": True, "veiculos": n_v, "resumo": n_r})

        conf = _read_csv(CONFERENTES_CSV)
        resumo177 = _read_csv(RESUMO_177_CSV)
        if conf:
            status(f"Sheets 177: Conferentes177 ({len(conf)} linha(s))...")
            r_c = _send_sheet(
                url, token, "Conferentes177", CONFERENTE_FIELDS, conf, on_status=status
            )
            r_cr = _send_sheet(
                url, token, "Resumo177", RESUMO_177_FIELDS, resumo177, on_status=status
            )
            if not r_c.get("ok") or not r_cr.get("ok"):
                raise RuntimeError(str(r_c.get("error") or r_cr.get("error") or "177 falhou"))
            stats["Conferentes177"] = r_c
            stats["Resumo177"] = r_cr
            n_c = int(r_c.get("rows") or len(conf))
            n_cr = int(r_cr.get("rows") or len(resumo177))
            result.update({"conferentes": n_c, "resumo177": n_cr})
            status(f"Sheets 177 OK: {n_c} conferente(s).")

        _bump_if_changed(url, token, stats, on_status=status)
        status(f"Sheets Armazém OK: {n_v} veículo(s)/linha(s), {n_r} resumo.")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets 078 falhou (cache local ok): {error}")
        return result
