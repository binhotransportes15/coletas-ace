"""Sync Google Sheets · só Armazém (planilha SEPARADA da distribuição)."""
from __future__ import annotations

import csv
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from config import AceSettings, load_settings
from parser_ssw78 import RESUMO_CSV, RESUMO_FIELDS, VEICULOS_CSV, VEICULO_FIELDS

StatusCallback = Callable[[str], None]
VEICULO_FIELDS_OUT = VEICULO_FIELDS + ["peso_veiculo"]


def _noop(_: str) -> None:
    return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _parse_apps_script_response(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"resposta nao-JSON: {raw[:200]}"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, N802
        return None


def _apps_script_opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        _NoRedirect,
        urllib.request.HTTPSHandler(context=context),
    )


def _post_json(url: str, payload: dict[str, Any], *, timeout: int = 180) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/plain, */*",
    }
    opener = _apps_script_opener()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return _parse_apps_script_response(raw)
    except urllib.error.HTTPError as error:
        code = int(error.code)
        location = error.headers.get("Location") or error.headers.get("location")
        try:
            error.close()
        except Exception:  # noqa: BLE001
            pass
        if location and code in {301, 302, 303, 307, 308}:
            get_req = urllib.request.Request(
                location,
                headers={"Accept": "application/json, text/plain, */*"},
                method="GET",
            )
            try:
                with opener.open(get_req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                return _parse_apps_script_response(raw)
            except urllib.error.HTTPError as err2:
                detail = err2.read().decode("utf-8", errors="replace")[:180]
                if "<html" in detail.lower() or "google" in detail.lower():
                    raise RuntimeError(
                        "HTTP 302: resposta HTML do Google no echo. Confira "
                        "armazem_apps_script_url (/exec) e publique Nova versao."
                    ) from err2
                raise RuntimeError(f"HTTP {err2.code} no echo: {detail}") from err2
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {code}: {detail}") from error


def _replace_sheet(
    url: str,
    token: str,
    sheet: str,
    headers: list[str],
    rows: list[dict[str, str]],
    *,
    status: StatusCallback,
) -> int:
    status(f"Sheets/Armazém: atualizando {sheet} ({len(rows)} linhas)...")
    resp = _post_json(
        url,
        {
            "token": token,
            "action": "replace",
            "sheet": sheet,
            "headers": headers,
            "rows": rows,
        },
        timeout=180,
    )
    if not resp.get("ok"):
        raise RuntimeError(str(resp.get("error") or resp))
    return int(resp.get("rows") or len(rows))


def _ensure_apps_script(cfg: AceSettings, status: StatusCallback) -> dict[str, Any]:
    import time

    result: dict[str, Any] = {"ok": False, "skipped": False}
    if not cfg.armazem_enable_sheets:
        result["skipped"] = True
        result["reason"] = "armazem_enable_sheets=false"
        status("Sheets Armazém desabilitado.")
        return result

    url = (cfg.armazem_apps_script_url or "").strip()
    token = (cfg.armazem_apps_script_token or "").strip()
    if not url:
        result["skipped"] = True
        status("Sheets Armazém: configure armazem_apps_script_url.")
        return result
    if not token:
        result["skipped"] = True
        status("Sheets Armazém: configure armazem_apps_script_token (padrao: armazem-ace).")
        return result

    last_error = ""
    for attempt in range(1, 4):
        try:
            auth = _post_json(
                url,
                {"token": token, "action": "ping", "sheet": "_ping", "headers": ["ok"], "rows": []},
                timeout=45,
            )
            if auth.get("ok"):
                if attempt > 1:
                    status(f"Sheets Armazém ping OK na tentativa {attempt}.")
                result.update({"ok": True, "url": url, "token": token})
                return result
            last_error = str(auth.get("error") or auth)
            if "nao autorizado" in last_error.lower():
                status(f"Sheets Armazém nao autorizado: {last_error}")
                result["error"] = last_error
                return result
        except Exception as error:  # noqa: BLE001
            last_error = str(error)
        if attempt < 3:
            status(f"Sheets Armazém ping falhou ({last_error}); tentativa {attempt + 1}/3...")
            time.sleep(2.5 * attempt)

    result["error"] = last_error or "ping falhou"
    status(f"Sheets Armazém falhou no ping: {result['error']}")
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
            f"Sheets Armazém: Veiculos78/Resumo78 ({len(veiculos)} linha(s)) "
            "— planilha isolada da distribuição."
        )
        n_v = _replace_sheet(url, token, "Veiculos78", VEICULO_FIELDS_OUT, veiculos, status=status)
        n_r = _replace_sheet(url, token, "Resumo78", RESUMO_FIELDS, resumo, status=status)
        result.update({"ok": True, "veiculos": n_v, "resumo": n_r})
        status(f"Sheets Armazém OK: {n_v} veículo(s)/linha(s), {n_r} resumo.")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets Armazém falhou (cache local ok): {error}")
        return result
