from __future__ import annotations

import csv
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from config import AceSettings, load_settings
from parser_ssw0157 import (
    COLETAS_CSV,
    HISTORICO_CSV,
    RESUMO_CSV,
    COLETA_FIELDS,
    HIST_FIELDS,
    RESUMO_FIELDS,
)
from parser_ssw103 import (
    COLETAS_103_CSV,
    RESUMO_103_CSV,
    COLETA_103_FIELDS,
    RESUMO_103_FIELDS,
)

StatusCallback = Callable[[str], None]

# Apps Script tem limite pratico de tempo/tamanho; envia em fatias.
_CHUNK_ROWS = 350


def _noop(_: str) -> None:
    return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _chunks(rows: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    if size <= 0:
        return [rows]
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _post_json(url: str, payload: dict[str, Any], *, timeout: int = 180) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Algumas respostas do Google redirecionam / devolvem HTML
        return {"ok": False, "error": f"resposta nao-JSON: {raw[:200]}"}


def _send_sheet(
    url: str,
    token: str,
    sheet: str,
    headers: list[str],
    rows: list[dict[str, str]],
    *,
    on_status: StatusCallback,
) -> dict[str, Any]:
    """Limpa a aba e envia linhas em chunks (clear + append)."""
    on_status(f"Sheets/Apps Script: limpando {sheet}...")
    clear_resp = _post_json(
        url,
        {
            "token": token,
            "action": "clear",
            "sheet": sheet,
            "headers": headers,
            "rows": [],
        },
    )
    if not clear_resp.get("ok"):
        return clear_resp

    total = 0
    parts = _chunks(rows, _CHUNK_ROWS) or [[]]
    for idx, part in enumerate(parts, start=1):
        on_status(f"Sheets/Apps Script: {sheet} lote {idx}/{len(parts)} ({len(part)} linhas)...")
        resp = _post_json(
            url,
            {
                "token": token,
                "action": "append",
                "sheet": sheet,
                "headers": headers,
                "rows": part,
            },
        )
        if not resp.get("ok"):
            return resp
        total += int(resp.get("rows") or len(part))
    return {"ok": True, "sheet": sheet, "rows": total}


def sync_google_sheets(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """
    Envia CSVs locais para a planilha via Google Apps Script (Web App).
    Nao usa conta de servico / gspread.
    """
    status = on_status or _noop
    cfg = settings or load_settings()
    result: dict[str, Any] = {"ok": False, "skipped": False}

    gate = _ensure_apps_script(cfg, status)
    if not gate.get("ok"):
        return gate

    url = gate["url"]
    token = gate["token"]

    coletas = _read_csv(COLETAS_CSV)
    historico = _read_csv(HISTORICO_CSV)
    resumo = _read_csv(RESUMO_CSV)

    try:
        status(
            f"Sheets: apagando abas e gravando so o periodo atual "
            f"({len(coletas)} coletas | {len(historico)} eventos historico)..."
        )
        stats: dict[str, Any] = {}
        # Ordem importa: Coletas = 1 SPO; Historico = log da SPO (nao conta como coleta)
        for sheet, headers, rows in (
            ("Coletas", COLETA_FIELDS, coletas),
            ("Historico", HIST_FIELDS, historico),
            ("ResumoDiario", RESUMO_FIELDS, resumo),
        ):
            resp = _send_sheet(url, token, sheet, headers, rows, on_status=status)
            if not resp.get("ok"):
                result["error"] = resp.get("error") or str(resp)
                status(f"Sheets falhou em {sheet}: {result['error']}")
                return result
            stats[sheet] = resp

        result.update({
            "ok": True,
            "via": "apps_script",
            "mode": "replace",
            "coletas": len(coletas),
            "historico_eventos": len(historico),
            "stats": stats,
        })
        status(
            f"Sheets substituida: {len(coletas)} coleta(s), "
            f"{len(historico)} evento(s) de historico."
        )
        return result
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        result["error"] = f"HTTP {error.code}: {detail}"
        status(f"Sheets falhou (mantendo dados locais): {result['error']}")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets falhou (mantendo dados locais/antigos): {error}")
        return result


def _ensure_apps_script(cfg: AceSettings, status: StatusCallback) -> dict[str, Any]:
    """Valida config + ping. Retorna {ok, url, token} ou erro/skipped."""
    result: dict[str, Any] = {"ok": False, "skipped": False}
    if not cfg.enable_sheets:
        result["skipped"] = True
        result["reason"] = "enable_sheets=false"
        status("Sheets desabilitado na configuracao.")
        return result

    url = (cfg.apps_script_url or "").strip()
    token = (cfg.apps_script_token or "").strip()
    if not url:
        result["skipped"] = True
        result["reason"] = "apps_script_url vazio"
        status("Sheets: configure apps_script_url (URL do App da Web).")
        return result
    if not token:
        result["skipped"] = True
        result["reason"] = "apps_script_token vazio"
        status("Sheets: configure apps_script_token (igual ao SECRET do Apps Script).")
        return result

    try:
        auth = _post_json(
            url,
            {
                "token": token,
                "action": "clear",
                "sheet": "_ace_ping",
                "headers": ["ok"],
                "rows": [],
            },
            timeout=60,
        )
        if not auth.get("ok"):
            result["error"] = auth.get("error") or str(auth)
            hint = auth.get("hint") or (
                "No Apps Script, SECRET deve ser exatamente 'coletas-ace' "
                "(ou o mesmo do config). Depois: Implantar → Gerenciar → Nova versao."
            )
            status(f"Sheets nao autorizado: {result['error']}")
            status(hint)
            result["hint"] = hint
            return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets falhou no ping: {error}")
        return result

    result.update({"ok": True, "url": url, "token": token})
    return result


def sync_google_sheets_103(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Envia cache 103 (Coletas103 + Resumo103) para a planilha."""
    status = on_status or _noop
    cfg = settings or load_settings()
    gate = _ensure_apps_script(cfg, status)
    if not gate.get("ok"):
        return gate

    url = gate["url"]
    token = gate["token"]
    coletas = _read_csv(COLETAS_103_CSV)
    resumo = _read_csv(RESUMO_103_CSV)
    result: dict[str, Any] = {"ok": False, "skipped": False}

    try:
        status(
            f"Sheets 103: gravando {len(coletas)} coleta(s) tempo real "
            f"(Parado / Em rota / Realizada)..."
        )
        stats: dict[str, Any] = {}
        for sheet, headers, rows in (
            ("Coletas103", COLETA_103_FIELDS, coletas),
            ("Resumo103", RESUMO_103_FIELDS, resumo),
        ):
            resp = _send_sheet(url, token, sheet, headers, rows, on_status=status)
            if not resp.get("ok"):
                result["error"] = resp.get("error") or str(resp)
                status(f"Sheets falhou em {sheet}: {result['error']}")
                return result
            stats[sheet] = resp

        result.update({
            "ok": True,
            "via": "apps_script",
            "mode": "replace",
            "coletas": len(coletas),
            "stats": stats,
        })
        status(f"Sheets 103 atualizada: {len(coletas)} coleta(s).")
        return result
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        result["error"] = f"HTTP {error.code}: {detail}"
        status(f"Sheets 103 falhou: {result['error']}")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets 103 falhou: {error}")
        return result
