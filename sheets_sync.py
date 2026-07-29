from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable

from config import GOOGLE_SA_PATH, AceSettings, load_settings
from parser_ssw0157 import (
    COLETAS_CSV,
    HISTORICO_CSV,
    RESUMO_CSV,
    COLETA_FIELDS,
    HIST_FIELDS,
    RESUMO_FIELDS,
)

StatusCallback = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _ensure_worksheet(spreadsheet, title: str, headers: list[str]):
    try:
        ws = spreadsheet.worksheet(title)
    except Exception:
        ws = spreadsheet.add_worksheet(title=title, rows=2000, cols=max(len(headers), 10))
        ws.append_row(headers)
        return ws

    values = ws.get_all_values()
    if not values:
        ws.append_row(headers)
    elif values[0] != headers:
        # Mantem cabecalho existente se ja houver dados; so cria se vazio
        if len(values) == 1 and not any(values[0]):
            ws.update("A1", [headers])
    return ws


def _replace_sheet(ws, rows: list[dict[str, str]], headers: list[str]) -> dict[str, int]:
    """Substitui o conteudo da aba pelos dados locais ja mesclados (upsert feito no CSV)."""
    values = [headers] + [[str(row.get(h, "") or "") for h in headers] for row in rows]
    ws.clear()
    if values:
        # gspread update em blocos para planilhas grandes
        chunk = 500
        ws.update("A1", values[:1])
        for start in range(1, len(values), chunk):
            part = values[start : start + chunk]
            cell = f"A{start + 1}"
            ws.update(cell, part)
    return {"rows": max(len(values) - 1, 0)}


def sync_google_sheets(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """
    Sincroniza CSVs locais com Google Sheets.
    Os CSVs locais ja fazem merge/upsert; a planilha recebe o snapshot completo.
    Se desabilitado ou sem credencial, nao quebra o fluxo.
    """
    status = on_status or _noop
    cfg = settings or load_settings()
    result: dict[str, Any] = {"ok": False, "skipped": False}

    if not cfg.enable_sheets:
        result["skipped"] = True
        result["reason"] = "enable_sheets=false"
        status("Sheets desabilitado na configuracao.")
        return result

    if not cfg.google_sheet_id:
        result["skipped"] = True
        result["reason"] = "google_sheet_id vazio"
        status("Sheets: ID da planilha nao configurado.")
        return result

    if not GOOGLE_SA_PATH.exists():
        result["skipped"] = True
        result["reason"] = f"credencial ausente: {GOOGLE_SA_PATH}"
        status(f"Sheets: coloque a conta de servico em {GOOGLE_SA_PATH.name}")
        return result

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as error:
        result["error"] = str(error)
        status("Sheets: instale gspread e google-auth (pip install -r requirements.txt)")
        return result

    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(str(GOOGLE_SA_PATH), scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(cfg.google_sheet_id)

        coletas = _read_csv(COLETAS_CSV)
        historico = _read_csv(HISTORICO_CSV)
        resumo = _read_csv(RESUMO_CSV)

        ws_c = _ensure_worksheet(spreadsheet, "Coletas", COLETA_FIELDS)
        ws_h = _ensure_worksheet(spreadsheet, "Historico", HIST_FIELDS)
        ws_r = _ensure_worksheet(spreadsheet, "ResumoDiario", RESUMO_FIELDS)

        status(f"Sheets: enviando Coletas ({len(coletas)})...")
        stats_c = _replace_sheet(ws_c, coletas, COLETA_FIELDS)
        status(f"Sheets: enviando Historico ({len(historico)})...")
        stats_h = _replace_sheet(ws_h, historico, HIST_FIELDS)
        status(f"Sheets: enviando ResumoDiario ({len(resumo)})...")
        stats_r = _replace_sheet(ws_r, resumo, RESUMO_FIELDS)

        result.update(
            {
                "ok": True,
                "sheet_id": cfg.google_sheet_id,
                "coletas": stats_c,
                "historico": stats_h,
                "resumo": stats_r,
            }
        )
        status("Sheets sincronizado com sucesso.")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets falhou (mantendo dados locais/antigos): {error}")
        return result
