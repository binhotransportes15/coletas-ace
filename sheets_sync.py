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
from parser_ssw0146 import (
    ENTREGAS_36_CSV,
    ROMANEIOS_36_CSV,
    RESUMO_36_CSV,
    ENTREGA_36_FIELDS,
    ROMANEIO_36_FIELDS,
    RESUMO_36_FIELDS,
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


def _parse_apps_script_response(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Algumas respostas do Google redirecionam / devolvem HTML
        return {"ok": False, "error": f"resposta nao-JSON: {raw[:200]}"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Impede o urllib de seguir 302 trocando POST→GET (quebra Apps Script)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, N802
        return None


def _apps_script_opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        _NoRedirect,
        urllib.request.HTTPSHandler(context=context),
    )


def _post_json(url: str, payload: dict[str, Any], *, timeout: int = 180) -> dict[str, Any]:
    """
    POST JSON para Apps Script Web App.

    Fluxo real do Google:
      1) POST /macros/s/.../exec  → 302 para script.googleusercontent.com/macros/echo?...
      2) GET nessa Location (o POST ja foi processado; o echo devolve o JSON)

    Se reenviarmos POST no echo, o Google responde 405/404 HTML do Docs.
    """
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

        if not (location and code in {301, 302, 303, 307, 308}):
            detail = ""
            try:
                # corpo ja pode ter sido consumido no close; reabre nao e preciso
                pass
            except Exception:  # noqa: BLE001
                pass
            return {
                "ok": False,
                "error": (
                    f"HTTP {code}: falha no POST Apps Script. "
                    "Confira apps_script_url (/exec) e publique Nova versao "
                    "(acesso: Qualquer pessoa)."
                ),
            }

        echo = urllib.request.urljoin(url, location)
        get_req = urllib.request.Request(
            echo,
            headers={"Accept": "application/json, text/plain, */*"},
            method="GET",
        )
        try:
            with opener.open(get_req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return _parse_apps_script_response(raw)
        except urllib.error.HTTPError as get_err:
            detail = get_err.read().decode("utf-8", errors="replace")
            try:
                get_err.close()
            except Exception:  # noqa: BLE001
                pass
            if "<!DOCTYPE" in detail or "<html" in detail.lower():
                return {
                    "ok": False,
                    "error": (
                        f"HTTP {get_err.code}: resposta HTML do Google no echo. "
                        "Confira apps_script_url (/exec) e publique Nova versao "
                        "(acesso: Qualquer pessoa)."
                    ),
                }
            try:
                parsed = json.loads(detail)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
            return {"ok": False, "error": f"HTTP {get_err.code}: {detail[:300]}"}


def _send_sheet(
    url: str,
    token: str,
    sheet: str,
    headers: list[str],
    rows: list[dict[str, str]],
    *,
    on_status: StatusCallback,
) -> dict[str, Any]:
    """Substitui a aba em uma unica gravacao (evita painel ver aba vazia no meio do sync)."""
    on_status(f"Sheets/Apps Script: atualizando {sheet} ({len(rows)} linhas)...")
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
        return resp
    return {"ok": True, "sheet": sheet, "rows": resp.get("rows", len(rows))}


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
    """Valida config + ping (com retry). Retorna {ok, url, token} ou erro/skipped."""
    import time

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

    # 1) action=ping (leve, sem criar aba) — exige Code.gs atualizado
    # 2) fallback action=clear em _ace_ping — compativel com versao antiga
    payloads = (
        {
            "token": token,
            "action": "ping",
            "sheet": "_ping",
            "headers": ["ok"],
            "rows": [],
        },
        {
            "token": token,
            "action": "clear",
            "sheet": "_ace_ping",
            "headers": ["ok"],
            "rows": [],
        },
    )

    last_error = ""
    for attempt in range(1, 4):
        for payload in payloads:
            try:
                auth = _post_json(url, payload, timeout=45)
                if auth.get("ok"):
                    result.update({"ok": True, "url": url, "token": token})
                    if attempt > 1:
                        status(f"Sheets ping OK na tentativa {attempt}.")
                    return result
                err = str(auth.get("error") or auth)
                # action ping inexistente na implantacao antiga → tenta fallback clear
                if "invalida" in err.lower() and payload.get("action") == "ping":
                    last_error = err
                    continue
                last_error = err
                hint = auth.get("hint") or (
                    "No Apps Script, SECRET deve ser exatamente 'coletas-ace' "
                    "(ou o mesmo do config). Depois: Implantar → Gerenciar → Nova versao."
                )
                if "nao autorizado" in err.lower() or "autorizado" in err.lower():
                    status(f"Sheets nao autorizado: {err}")
                    status(hint)
                    result["error"] = err
                    result["hint"] = hint
                    return result
                last_error = err
            except Exception as error:  # noqa: BLE001
                last_error = str(error)
        if attempt < 3:
            status(f"Sheets ping falhou ({last_error}); nova tentativa {attempt + 1}/3...")
            time.sleep(2.5 * attempt)

    result["error"] = last_error or "ping falhou"
    status(f"Sheets falhou no ping: {result['error']}")
    status(
        "Dica: se for HTTP 404/405 HTML, abra o Apps Script > Implantar > Gerenciar > "
        "confirme a URL /exec no config (Nova versao apos editar o Code.gs)."
    )
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


def sync_google_sheets_36(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Envia cache 36 (Entregas36 + Romaneios36 + Resumo36) para a planilha."""
    status = on_status or _noop
    cfg = settings or load_settings()
    gate = _ensure_apps_script(cfg, status)
    if not gate.get("ok"):
        return gate

    url = gate["url"]
    token = gate["token"]
    entregas = _read_csv(ENTREGAS_36_CSV)
    romaneios = _read_csv(ROMANEIOS_36_CSV)
    resumo = _read_csv(RESUMO_36_CSV)
    result: dict[str, Any] = {"ok": False, "skipped": False}

    try:
        status(
            f"Sheets 36: gravando {len(entregas)} CTRC(s) / {len(romaneios)} romaneio(s)..."
        )
        stats: dict[str, Any] = {}
        for sheet, headers, rows in (
            ("Entregas36", ENTREGA_36_FIELDS, entregas),
            ("Romaneios36", ROMANEIO_36_FIELDS, romaneios),
            ("Resumo36", RESUMO_36_FIELDS, resumo),
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
            "entregas": len(entregas),
            "romaneios": len(romaneios),
            "stats": stats,
        })
        status(f"Sheets 36 atualizada: {len(entregas)} CTRC(s).")
        return result
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        result["error"] = f"HTTP {error.code}: {detail}"
        status(f"Sheets 36 falhou: {result['error']}")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets 36 falhou: {error}")
        return result
