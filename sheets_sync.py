from __future__ import annotations

import csv
import hashlib
import json
import time
import urllib.error
from pathlib import Path
from typing import Any, Callable

from apps_script_client import post_apps_script
from config import CACHE_DIR, AceSettings, load_settings
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
from parser_ssw225 import (
    AGENDAMENTOS_225_CSV,
    RESUMO_225_CSV,
    ALERTAS_225_CSV,
    AGENDAMENTO_225_FIELDS,
    RESUMO_225_FIELDS,
    ALERTA_225_FIELDS,
)

StatusCallback = Callable[[str], None]

# Apps Script tem limite pratico de tempo/tamanho; envia em fatias.
_CHUNK_ROWS = 350
_HASH_CACHE_PATH = CACHE_DIR / "sheets_hashes.json"
_ping_cache: dict[str, Any] = {"ok_at": 0.0, "url": "", "token": ""}


def _noop(_: str) -> None:
    return None


def _load_local_hashes() -> dict[str, str]:
    try:
        if _HASH_CACHE_PATH.is_file():
            data = json.loads(_HASH_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_local_hash(sheet: str, digest: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = _load_local_hashes()
        data[sheet] = digest
        _HASH_CACHE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def _local_hash_match(sheet: str, digest: str) -> bool:
    if not digest:
        return False
    return _load_local_hashes().get(sheet) == digest


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _chunks(rows: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    if size <= 0:
        return [rows]
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _content_hash(headers: list[str], rows: list[dict[str, str]]) -> str:
    """Hash estável do conteúdo — Apps Script pula rewrite se igual."""
    h = hashlib.sha256()
    h.update(("|" + "|".join(headers) + "|\n").encode("utf-8"))
    for row in rows:
        line = "\t".join(str(row.get(k, "") or "") for k in headers)
        h.update(line.encode("utf-8", errors="replace"))
        h.update(b"\n")
    return h.hexdigest()


def _post_json(url: str, payload: dict[str, Any], *, timeout: int = 120) -> dict[str, Any]:
    # 1 retry: Apps Script lento; mais tentativas só esticam o ciclo
    return post_apps_script(url, payload, timeout=timeout, retries=1)


def _bump_version(url: str, token: str, *, on_status: StatusCallback) -> None:
    try:
        resp = _post_json(
            url,
            {"token": token, "action": "bump", "sheet": "_", "headers": ["ok"], "rows": []},
            timeout=45,
        )
        if resp.get("ok"):
            on_status(f"Sheets: versão dados = {resp.get('version', '?')}")
    except Exception as err:  # noqa: BLE001
        on_status(f"Sheets bump versão (opcional) falhou: {err}")


def _any_sheet_written(stats: dict[str, Any]) -> bool:
    """True se alguma aba foi gravada de fato (não só hash skip)."""
    for item in stats.values():
        if isinstance(item, dict) and not item.get("skipped"):
            return True
    return False


def _bump_if_changed(
    url: str,
    token: str,
    stats: dict[str, Any],
    *,
    on_status: StatusCallback,
) -> None:
    if _any_sheet_written(stats):
        _bump_version(url, token, on_status=on_status)
    else:
        on_status("Sheets: nenhuma aba mudou — versão mantida (TV não precisa reler).")


def _sheet_item(
    sheet: str,
    headers: list[str],
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    digest = _content_hash(headers, rows)
    return {
        "sheet": sheet,
        "headers": headers,
        "rows": rows,
        "content_hash": digest,
    }


def _send_sheets_batch(
    url: str,
    token: str,
    items: list[dict[str, Any]],
    *,
    on_status: StatusCallback,
) -> dict[str, Any]:
    """
    Envia várias abas num POST só (action=replace_many).
    Pula abas iguais ao cache local (sem rede).
    """
    stats: dict[str, Any] = {}
    to_send: list[dict[str, Any]] = []
    for item in items:
        sheet = str(item["sheet"])
        digest = str(item.get("content_hash") or "")
        rows = item.get("rows") or []
        if _local_hash_match(sheet, digest):
            on_status(f"Sheets: {sheet} igual (local) — pulou rede.")
            stats[sheet] = {
                "ok": True,
                "sheet": sheet,
                "rows": len(rows),
                "skipped": True,
                "content_hash": digest,
                "local": True,
            }
            continue
        to_send.append(item)

    if not to_send:
        return {"ok": True, "stats": stats, "skipped_all": True}

    names = ", ".join(i["sheet"] for i in to_send)
    on_status(f"Sheets: enviando lote agora ({len(to_send)}: {names})…")
    resp = _post_json(
        url,
        {
            "token": token,
            "action": "replace_many",
            "sheet": "_batch",
            "headers": ["ok"],
            "rows": [],
            "sheets": to_send,
            "bump_version": True,
        },
        timeout=90,
    )

    # Script antigo sem replace_many → fallback 1 a 1
    if not resp.get("ok"):
        err = str(resp.get("error") or "")
        if "invalida" in err.lower() or "obrigatorio" in err.lower() or "action" in err.lower():
            on_status("Sheets: batch indisponível no Script — fallback aba a aba. Publique Nova versão.")
            for item in to_send:
                one = _send_sheet(
                    url,
                    token,
                    str(item["sheet"]),
                    list(item["headers"]),
                    list(item["rows"]),
                    on_status=on_status,
                )
                stats[str(item["sheet"])] = one
                if not one.get("ok"):
                    return {"ok": False, "error": one.get("error"), "stats": stats}
            _bump_if_changed(url, token, stats, on_status=on_status)
            return {"ok": True, "stats": stats, "fallback": True}
        return {"ok": False, "error": err or str(resp), "stats": stats}

    for r in resp.get("results") or []:
        sheet = str(r.get("sheet") or "")
        skipped = bool(r.get("skipped"))
        digest = next(
            (str(i.get("content_hash") or "") for i in to_send if i["sheet"] == sheet),
            "",
        )
        if sheet and (skipped or r.get("ok", True)):
            if digest:
                _save_local_hash(sheet, digest)
        if skipped:
            on_status(f"Sheets: {sheet} sem mudança (hash) — pulou gravação.")
        else:
            on_status(f"Sheets: {sheet} OK ({r.get('rows', '?')} linhas)")
        stats[sheet] = {
            "ok": bool(r.get("ok", True)),
            "sheet": sheet,
            "rows": r.get("rows"),
            "skipped": skipped,
            "content_hash": digest,
        }

    if resp.get("version") is not None:
        on_status(f"Sheets: versão dados = {resp.get('version')}")
    return {"ok": True, "stats": stats, "version": resp.get("version")}


def _send_sheet(
    url: str,
    token: str,
    sheet: str,
    headers: list[str],
    rows: list[dict[str, str]],
    *,
    on_status: StatusCallback,
) -> dict[str, Any]:
    """Substitui a aba (clear+write). Pula se hash local/remoto igual."""
    digest = _content_hash(headers, rows)
    if _local_hash_match(sheet, digest):
        on_status(f"Sheets: {sheet} igual (local) — pulou rede.")
        return {
            "ok": True,
            "sheet": sheet,
            "rows": len(rows),
            "skipped": True,
            "content_hash": digest,
            "local": True,
        }
    on_status(f"Sheets/Apps Script: atualizando {sheet} ({len(rows)} linhas)...")
    resp = _post_json(
        url,
        {
            "token": token,
            "action": "replace",
            "sheet": sheet,
            "headers": headers,
            "rows": rows,
            "content_hash": digest,
            "bump_version": False,
        },
        timeout=180,
    )
    time.sleep(0.05)
    if not resp.get("ok"):
        return resp
    skipped = bool(resp.get("skipped"))
    if skipped:
        on_status(f"Sheets: {sheet} sem mudança (hash) — pulou gravação.")
    _save_local_hash(sheet, digest)
    return {
        "ok": True,
        "sheet": sheet,
        "rows": resp.get("rows", len(rows)),
        "skipped": skipped,
        "content_hash": digest,
    }


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

    gate = _ensure_apps_script(cfg, status, ping=False)
    if not gate.get("ok"):
        return gate

    url = gate["url"]
    token = gate["token"]

    coletas = _read_csv(COLETAS_CSV)
    historico = _read_csv(HISTORICO_CSV)
    resumo = _read_csv(RESUMO_CSV)

    # Protege planilha: cache vazio (ex.: analisou arquivo errado) nao zera Coletas boas
    if not coletas:
        status(
            "Sheets 50: cache Coletas vazio — nao sobrescreve abas "
            "(mantem dados anteriores)."
        )
        result.update({"ok": True, "skipped": True, "reason": "empty_50_cache"})
        return result

    try:
        status(
            f"Sheets: atualizando abas "
            f"({len(coletas)} coletas | {len(historico)} eventos historico)..."
        )
        batch = _send_sheets_batch(
            url,
            token,
            [
                _sheet_item("Coletas", COLETA_FIELDS, coletas),
                _sheet_item("Historico", HIST_FIELDS, historico),
                _sheet_item("ResumoDiario", RESUMO_FIELDS, resumo),
            ],
            on_status=status,
        )
        if not batch.get("ok"):
            result["error"] = batch.get("error")
            status(f"Sheets falhou: {result['error']}")
            return result
        stats = batch.get("stats") or {}
        result.update({
            "ok": True,
            "via": "apps_script",
            "mode": "batch",
            "coletas": len(coletas),
            "historico_eventos": len(historico),
            "stats": stats,
        })
        status(
            f"Sheets atualizada: {len(coletas)} coleta(s), "
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


def _ensure_apps_script(
    cfg: AceSettings,
    status: StatusCallback,
    *,
    ping: bool = True,
) -> dict[str, Any]:
    """Valida config (+ ping opcional). Retorna {ok, url, token} ou erro/skipped."""
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

    # Ping recente (15 min) ou pulado → segue direto (o POST do lote já valida o Script)
    if (
        not ping
        or (
            _ping_cache.get("url") == url
            and _ping_cache.get("token") == token
            and (time.time() - float(_ping_cache.get("ok_at") or 0)) < 900
        )
    ):
        if not ping:
            _ping_cache.update({"ok_at": time.time(), "url": url, "token": token})
        result.update({"ok": True, "url": url, "token": token, "ping_cached": True})
        return result

    status("Sheets: testando conexão…")
    last_error = ""
    try:
        # Timeout curto: Apps Script costuma demorar no cold start
        auth = _post_json(
            url,
            {
                "token": token,
                "action": "ping",
                "sheet": "_ping",
                "headers": ["ok"],
                "rows": [],
            },
            timeout=12,
        )
        if auth.get("ok"):
            _ping_cache.update({"ok_at": time.time(), "url": url, "token": token})
            result.update({"ok": True, "url": url, "token": token})
            return result
        last_error = str(auth.get("error") or auth)
        if "nao autorizado" in last_error.lower() or "autorizado" in last_error.lower():
            status(f"Sheets nao autorizado: {last_error}")
            result["error"] = last_error
            return result
    except Exception as error:  # noqa: BLE001
        last_error = str(error)

    # Não classificar como ERR de ciclo — só atraso do Google
    status(f"Sheets: ping lento ({last_error[:80]}) — enviando direto…")
    result.update({"ok": True, "url": url, "token": token, "ping_soft_fail": True})
    return result


def sync_cycle_sheets(
    settings: AceSettings | None = None,
    *,
    do_50: bool = False,
    do_103: bool = False,
    do_36: bool = False,
    do_225: bool = False,
    include_historico: bool = True,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Um lote só após o ciclo dual — bem mais rápido que 4 syncs separados."""
    status = on_status or _noop
    status("Sheets: preparando envio…")
    cfg = settings or load_settings()
    # Sem ping: o atraso de 30–60s estava aqui, antes de qualquer gravação
    gate = _ensure_apps_script(cfg, status, ping=False)
    if not gate.get("ok"):
        return gate

    url = str(gate["url"])
    token = str(gate["token"])
    items: list[dict[str, Any]] = []

    if do_50:
        coletas = _read_csv(COLETAS_CSV)
        if not coletas:
            status("Sheets 50: Coletas vazio — não inclui no lote.")
        else:
            items.append(_sheet_item("Coletas", COLETA_FIELDS, coletas))
            # Histórico é pesado e a TV não depende dele — só no sync manual
            if include_historico:
                items.append(_sheet_item("Historico", HIST_FIELDS, _read_csv(HISTORICO_CSV)))
            items.append(_sheet_item("ResumoDiario", RESUMO_FIELDS, _read_csv(RESUMO_CSV)))

    if do_103:
        c103 = _read_csv(COLETAS_103_CSV)
        if c103:
            items.append(_sheet_item("Coletas103", COLETA_103_FIELDS, c103))
            items.append(_sheet_item("Resumo103", RESUMO_103_FIELDS, _read_csv(RESUMO_103_CSV)))

    if do_36:
        e36 = _read_csv(ENTREGAS_36_CSV)
        if e36:
            items.append(_sheet_item("Entregas36", ENTREGA_36_FIELDS, e36))
            items.append(_sheet_item("Romaneios36", ROMANEIO_36_FIELDS, _read_csv(ROMANEIOS_36_CSV)))
            items.append(_sheet_item("Resumo36", RESUMO_36_FIELDS, _read_csv(RESUMO_36_CSV)))

    if do_225:
        a225 = _read_csv(AGENDAMENTOS_225_CSV)
        if a225:
            items.append(_sheet_item("Resumo225", RESUMO_225_FIELDS, _read_csv(RESUMO_225_CSV)))
            items.append(_sheet_item("Alertas225", ALERTA_225_FIELDS, _read_csv(ALERTAS_225_CSV)))
            items.append(_sheet_item("Agendamentos225", AGENDAMENTO_225_FIELDS, a225))
        else:
            status("Sheets 225: cache vazio — não inclui no lote.")

    if not items:
        status("Sheets: nada novo para enviar.")
        return {"ok": True, "skipped": True, "reason": "empty_batch"}

    # Só o que mudou (cache local) — se nada mudou, nem chama a rede
    pending = [i for i in items if not _local_hash_match(str(i["sheet"]), str(i.get("content_hash") or ""))]
    if not pending:
        status("Sheets: tudo igual ao último envio — pulou rede.")
        return {"ok": True, "skipped": True, "reason": "local_hash_all", "stats": {
            str(i["sheet"]): {"ok": True, "skipped": True, "local": True} for i in items
        }}

    status(f"Sheets: enviando {len(pending)} aba(s) agora (de {len(items)})…")
    batch = _send_sheets_batch(url, token, items, on_status=status)
    if batch.get("ok"):
        _ping_cache.update({"ok_at": time.time(), "url": url, "token": token})
        status("Sheets ciclo OK.")
    else:
        status(f"Sheets ciclo falhou: {batch.get('error')}")
    return {"ok": bool(batch.get("ok")), "via": "apps_script", "mode": "cycle_batch", **batch}


def sync_google_sheets_103(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Envia cache 103 (Coletas103 + Resumo103) para a planilha."""
    status = on_status or _noop
    cfg = settings or load_settings()
    gate = _ensure_apps_script(cfg, status, ping=False)
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
        batch = _send_sheets_batch(
            url,
            token,
            [
                _sheet_item("Coletas103", COLETA_103_FIELDS, coletas),
                _sheet_item("Resumo103", RESUMO_103_FIELDS, resumo),
            ],
            on_status=status,
        )
        if not batch.get("ok"):
            result["error"] = batch.get("error")
            status(f"Sheets 103 falhou: {result['error']}")
            return result
        result.update({
            "ok": True,
            "via": "apps_script",
            "mode": "batch",
            "coletas": len(coletas),
            "stats": batch.get("stats"),
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
    gate = _ensure_apps_script(cfg, status, ping=False)
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
        batch = _send_sheets_batch(
            url,
            token,
            [
                _sheet_item("Entregas36", ENTREGA_36_FIELDS, entregas),
                _sheet_item("Romaneios36", ROMANEIO_36_FIELDS, romaneios),
                _sheet_item("Resumo36", RESUMO_36_FIELDS, resumo),
            ],
            on_status=status,
        )
        if not batch.get("ok"):
            result["error"] = batch.get("error")
            status(f"Sheets 36 falhou: {result['error']}")
            return result
        result.update({
            "ok": True,
            "via": "apps_script",
            "mode": "batch",
            "entregas": len(entregas),
            "romaneios": len(romaneios),
            "stats": batch.get("stats"),
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


def sync_google_sheets_225(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Envia cache 225 (Resumo225 + Alertas225 + Agendamentos225)."""
    status = on_status or _noop
    cfg = settings or load_settings()
    gate = _ensure_apps_script(cfg, status, ping=False)
    if not gate.get("ok"):
        return gate

    url = gate["url"]
    token = gate["token"]
    rows = _read_csv(AGENDAMENTOS_225_CSV)
    resumo = _read_csv(RESUMO_225_CSV)
    alertas = _read_csv(ALERTAS_225_CSV)
    result: dict[str, Any] = {"ok": False, "skipped": False}

    if not rows:
        status(
            "Sheets 225: cache Agendamentos225 vazio — nao sobrescreve abas 225 "
            "(mantem dados anteriores)."
        )
        result.update({"ok": True, "skipped": True, "reason": "empty_225_cache"})
        return result

    try:
        status(f"Sheets 225: gravando {len(rows)} agendamento(s) / {len(alertas)} alerta(s)...")
        batch = _send_sheets_batch(
            url,
            token,
            [
                _sheet_item("Resumo225", RESUMO_225_FIELDS, resumo),
                _sheet_item("Alertas225", ALERTA_225_FIELDS, alertas),
                _sheet_item("Agendamentos225", AGENDAMENTO_225_FIELDS, rows),
            ],
            on_status=status,
        )
        if not batch.get("ok"):
            result["error"] = batch.get("error")
            status(f"Sheets 225 falhou: {result['error']}")
            return result
        result.update({
            "ok": True,
            "via": "apps_script",
            "mode": "batch",
            "agendamentos": len(rows),
            "alertas": len(alertas),
            "stats": batch.get("stats"),
        })
        status(f"Sheets 225 atualizada: {len(rows)} agendamento(s).")
        return result
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        status(f"Sheets 225 falhou: {error}")
        return result
