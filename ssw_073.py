"""SSW 073 (ssw0332) — Consulta de CTRBs e OSs · só tela (sem download).

Fluxo Contratação (Unidade = SPO · período = mês até hoje):
  1) 1 login · 1 tela 073
     Propriedade=T · Operação=T · Tipo=A · Considerar=T · Unidade=SPO
     → clica ► mostrar tela (ajaxEnvia MOS) e copia a grade (sem baixar nada)
  2) Parser: só CARRETEIRO + TRANSFERÊNCIA (= contratados)
  3) Para cada DESTINO: troca menu → 076+200 · merge frete
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_mes_ate_hoje, to_ssw_ddmmyy
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]

# Form SSW: T/T/A — filtro carreteiro+transf fica no parser
JOBS_073: tuple[dict[str, str], ...] = (
    {"key": "TT", "propriedade": "T", "tipo": "A", "label": "todos"},
)
OPERACAO_073_DEFAULT = "T"  # T-todos (filtra no parser)
CONSIDERAR_073_DEFAULT = "T"
PROPRIEDADE_LABEL = {
    "T": "todos",
    "TT": "todos",
    "TA": "agregado",
    "A": "agregado",
    "F": "frota",
    "C": "carreteiro",
    "CR": "carreteiro+transf",
    "AC": "contratados",
    "AO": "agregados",
}
# legado
PROPRIEDADES_073 = ("T",)

# Programa 073
SSW_073_MARKERS = (
    "ctrb",
    "os",
    "propriedade",
    "operacao",
    "unidade emissora",
    "073",
    "prosseguir",
    "consulta",
)

SSW_FILA_URL = "https://sistema.ssw.inf.br/bin/ssw1440"
SSW_FILA_MARKERS = ("fila", "dow", "156", "1440", "processamento", "lotes")


def _safe_wait(page, ms: int) -> None:
    try:
        if page is None or page.is_closed():
            return
        page.wait_for_timeout(ms)
    except Exception:
        pass


def _noop(_: str) -> None:
    return None


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def _resolve_jobs_073(
    *,
    propriedades: tuple[str, ...] | list[str] | None,
    tipo: str | None,
    tipos: tuple[str, ...] | None,
    propriedade: str | None,
) -> list[dict[str, str]]:
    """Monta a lista de jobs. Default = Propriedade T · Tipo A."""
    _ = tipos  # legado
    # override: uma propriedade + um tipo
    if propriedades is None and propriedade and str(propriedade).strip():
        prop = str(propriedade).strip().upper()[:1] or "T"
        tipo_doc = str(tipo or "A").strip().upper()[:1] or "A"
        return [
            {
                "key": f"{prop}{tipo_doc}",
                "propriedade": prop,
                "tipo": tipo_doc,
                "label": PROPRIEDADE_LABEL.get(f"{prop}{tipo_doc}")
                or PROPRIEDADE_LABEL.get(prop, prop),
            }
        ]
    if propriedades is not None:
        wanted = {str(p).strip().upper()[:1] for p in propriedades if str(p).strip()}
        jobs = [j for j in JOBS_073 if j["propriedade"] in wanted]
        if jobs:
            return [dict(j) for j in jobs]
    return [dict(j) for j in JOBS_073]


def download_reports_073(
    *,
    period: tuple[str, str] | None = None,
    propriedades: tuple[str, ...] | list[str] | None = None,
    tipo: str | None = None,
    unidade_emissora: str = "SPO",
    operacao: str = OPERACAO_073_DEFAULT,
    considerar: str = CONSIDERAR_073_DEFAULT,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
    # legado
    tipos: tuple[str, ...] | None = None,
    propriedade: str | None = None,
) -> dict[str, Any]:
    """1 login · N telas 073 · Prosseguir → copia grade (sem baixar arquivo)."""
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)

    jobs = _resolve_jobs_073(
        propriedades=propriedades,
        tipo=tipo,
        tipos=tipos,
        propriedade=propriedade,
    )
    unidade = (unidade_emissora or "SPO").strip().upper() or "SPO"

    ini_ddmm, fim_ddmm = period or periodo_mes_ate_hoje()
    ini = to_ssw_ddmmyy(ini_ddmm)
    fim = to_ssw_ddmmyy(fim_ddmm)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if clean_downloads:
        cleanup_downloads(DOWNLOAD_DIR, on_status=status)

    client = AceSswClient(
        ini_ddmm,
        fim_ddmm,
        keep_open=False,
        headless=use_headless,
        on_status=status,
        credentials=creds,
        settings=cfg,
        clean_downloads=False,
    )

    from playwright.sync_api import sync_playwright

    paths: dict[str, str] = {}
    errors: dict[str, str] = {}
    queued: list[dict[str, Any]] = []
    desc = " · ".join(f"{j['key']}={j['propriedade']}+{j['tipo']}({j['label']})" for j in jobs)
    status(
        f"SSW 73 | {len(jobs)} tela(s) em paralelo · {desc} | {ini}-{fim} | "
        f"emissora={unidade} | op={operacao}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))
        try:
            client._login(page)
            client._ensure_unit(page)
            client._patch_blank_popup_form(page)
            phase = _run_073_phases(
                client,
                context,
                page,
                jobs=jobs,
                ini=ini,
                fim=fim,
                unidade=unidade,
                operacao=operacao,
                considerar=considerar,
                ts=ts,
                status=status,
            )
            paths = phase["paths"]
            errors = phase["errors"]
            queued = phase["queued"]
        finally:
            browser.close()

    missing = [q["key"] for q in queued if q["key"] not in paths]
    for k in missing:
        errors.setdefault(k, "sem grade CTRB após Prosseguir")

    if not paths and errors:
        raise RuntimeError("073 falhou: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))

    return {
        "ok": bool(paths),
        "paths": paths,
        "files": list(paths.values()),
        "errors": errors,
        "jobs": jobs,
        "propriedades": [j["propriedade"] for j in jobs],
        "period": (ini_ddmm, fim_ddmm),
        "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
        "unidade": unidade,
    }


def download_contratacao_ssw(
    *,
    period: tuple[str, str] | None = None,
    skip_076: bool = False,
    skip_200: bool = False,
    unidade_emissora: str = "SPO",
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """1 login: 073 (SPO) → por cada destino: menu → 076 + 200 (frete)."""
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)
    jobs = [dict(j) for j in JOBS_073]
    unidade = (unidade_emissora or "SPO").strip().upper() or "SPO"
    ini_ddmm, fim_ddmm = period or periodo_mes_ate_hoje()
    ini = to_ssw_ddmmyy(ini_ddmm)
    fim = to_ssw_ddmmyy(fim_ddmm)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if clean_downloads:
        cleanup_downloads(DOWNLOAD_DIR, on_status=status)

    from parser_ssw073 import analyze_reports_073
    from parser_ssw076 import analyze_reports_076
    from parser_ssw0644 import analyze_reports_200

    client = AceSswClient(
        ini_ddmm,
        fim_ddmm,
        keep_open=False,
        headless=use_headless,
        on_status=status,
        credentials=creds,
        settings=cfg,
        clean_downloads=False,
    )

    from playwright.sync_api import sync_playwright

    extras = []
    if not skip_076:
        extras.append("076")
    if not skip_200:
        extras.append("200")
    status(
        f"SSW contratação | 1 login · {len(jobs)} telas 073"
        + (f" + filiais({'/'.join(extras)})" if extras else "")
        + f" | {ini}-{fim} | menu={unidade}"
    )

    dl73: dict[str, Any] = {}
    dl76: dict[str, Any] = {"ok": False, "files": [], "by_filial": {}}
    dl200: dict[str, Any] = {"ok": False, "files": [], "by_filial": {}}
    analysis73: dict[str, Any] = {}
    analysis76: dict[str, Any] = {"ok": False, "skipped": True}
    analysis200: dict[str, Any] = {"ok": False, "skipped": True}
    periodo_fmt = f"{ini_ddmm} – {fim_ddmm}"
    filial_errors: dict[str, str] = {}
    destinos: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))
        try:
            client._login(page)
            client._set_menu_unit(page, unidade)
            client._patch_blank_popup_form(page)

            phase = _run_073_phases(
                client,
                context,
                page,
                jobs=jobs,
                ini=ini,
                fim=fim,
                unidade=unidade,
                operacao=OPERACAO_073_DEFAULT,
                considerar=CONSIDERAR_073_DEFAULT,
                ts=ts,
                status=status,
            )
            dl73 = {
                "ok": bool(phase["paths"]),
                "paths": phase["paths"],
                "files": list(phase["paths"].values()),
                "errors": phase["errors"],
                "jobs": jobs,
                "period": (ini_ddmm, fim_ddmm),
                "periodo_fmt": periodo_fmt,
                "unidade": unidade,
            }
            if not phase["paths"]:
                raise RuntimeError(
                    "073 falhou: "
                    + "; ".join(f"{k}:{v}" for k, v in phase["errors"].items())
                )

            analysis73 = analyze_reports_073(
                dl73["files"],
                periodo=periodo_fmt,
                unidade=unidade,
                on_status=status,
            )

            # Destinos do 073 (siglas) — inclui SPO
            destinos = []
            for d in analysis73.get("destinos") or []:
                sig = str((d.get("destino") if isinstance(d, dict) else d) or "").strip().upper()
                if sig and sig not in destinos and len(sig) <= 4 and sig.isalpha():
                    destinos.append(sig)
            if not destinos:
                destinos = [unidade]
            status(f"Frete por filial: {', '.join(destinos)}")

            files76: list[str] = []
            files200: list[str] = []
            by76: dict[str, Any] = {}
            by200: dict[str, Any] = {}

            if not skip_076 or not skip_200:
                for dest in destinos:
                    try:
                        page.bring_to_front()
                        client._set_menu_unit(page, dest)
                        client._patch_blank_popup_form(page)
                    except Exception as stab_err:  # noqa: BLE001
                        status(f"[{dest}] menu: {stab_err}")

                    try:
                        status(f"[{dest}] abrindo 076+200 juntos…")
                        dual = _download_frete_filial_paralelo(
                            client,
                            context,
                            page,
                            dest=dest,
                            period=(ini_ddmm, fim_ddmm),
                            placas=list(analysis73.get("placas") or []),
                            skip_076=skip_076,
                            skip_200=skip_200,
                            status=status,
                        )
                        got76 = list(dual.get("files76") or [])
                        got200 = list(dual.get("files200") or [])
                        if got76:
                            files76.extend(got76)
                            by76[dest] = {
                                "ok": True,
                                "files": got76,
                                "errors": dual.get("errors76") or {},
                                "tag": dest,
                            }
                            status(f"[{dest}] 076 OK · {len(got76)} arquivo(s)")
                        elif not skip_076:
                            err76 = dual.get("errors76") or {}
                            msg = "; ".join(f"{k}:{v}" for k, v in err76.items()) or "sem arquivo"
                            if "sem base" in msg.lower() or "não selecionou" in msg.lower() or "nao selecionou" in msg.lower():
                                status(f"[{dest}] 076 sem movimento — ok, segue")
                            else:
                                filial_errors[f"{dest}/076"] = msg
                                status(f"[{dest}] 076 avisou: {msg}")
                        if got200:
                            files200.extend(got200)
                            by200[dest] = {
                                "ok": True,
                                "files": got200,
                                "tag": dest,
                            }
                            status(f"[{dest}] 200 OK · {len(got200)} arquivo(s)")
                        elif not skip_200:
                            msg = str(dual.get("error200") or "sem arquivo")
                            low = msg.lower()
                            if any(
                                s in low
                                for s in (
                                    "sem base",
                                    "nenhum registro",
                                    "não selecionou",
                                    "nao selecionou",
                                    "sem movimento",
                                )
                            ):
                                status(f"[{dest}] 200 sem movimento — ok, segue")
                            else:
                                filial_errors[f"{dest}/200"] = msg
                                status(f"[{dest}] 200 avisou: {msg}")
                    except Exception as err:  # noqa: BLE001
                        if not skip_076:
                            filial_errors[f"{dest}/076"] = str(err)
                        if not skip_200:
                            filial_errors[f"{dest}/200"] = str(err)
                        status(f"[{dest}] frete avisou: {err}")

                # Merge frete: 076 depois 200 (200 sobrescreve quando > 0)
                placas = list(analysis73.get("placas") or [])
                if files76 and not skip_076:
                    try:
                        analysis76 = analyze_reports_076(
                            files76, placas=placas, on_status=status
                        )
                    except Exception as err:  # noqa: BLE001
                        status(f"076 merge avisou: {err}")
                        analysis76 = {"ok": False, "error": str(err)}
                elif skip_076:
                    analysis76 = {"ok": False, "skipped": True}

                if files200 and not skip_200:
                    try:
                        analysis200 = analyze_reports_200(
                            files200, placas=placas, on_status=status
                        )
                    except Exception as err:  # noqa: BLE001
                        status(f"200 merge avisou: {err}")
                        analysis200 = {"ok": False, "error": str(err)}
                elif skip_200:
                    analysis200 = {"ok": False, "skipped": True}

                dl76 = {
                    "ok": bool(files76),
                    "files": files76,
                    "by_filial": by76,
                    "errors": {k: v for k, v in filial_errors.items() if k.endswith("/076")},
                }
                dl200 = {
                    "ok": bool(files200),
                    "files": files200,
                    "by_filial": by200,
                    "errors": {k: v for k, v in filial_errors.items() if k.endswith("/200")},
                }

            # volta menu para unidade base
            try:
                client._set_menu_unit(page, unidade)
            except Exception:
                pass
        finally:
            browser.close()

    resumo = (
        analysis200.get("resumo")
        or analysis76.get("resumo")
        or analysis73.get("resumo")
    )
    return {
        "ok": True,
        "073": {"download": dl73, **analysis73},
        "076": {"download": dl76, **analysis76},
        "200": {"download": dl200, **analysis200},
        "filiais": destinos,
        "filial_errors": filial_errors,
        "resumo": resumo,
        "placas": list(analysis73.get("placas") or []),
        "periodo_fmt": periodo_fmt,
    }


def _download_frete_filial_paralelo(
    client,
    context,
    page,
    *,
    dest: str,
    period: tuple[str, str],
    placas: list[str],
    skip_076: bool,
    skip_200: bool,
    status: StatusCallback,
) -> dict[str, Any]:
    """Abre 076 e 200 na mesma sessão (2 guias), preenche e baixa."""
    from ssw_076 import (
        FilaSemDados,
        _gerar_download_76,
        _preencher_76,
        _reopen_76,
    )
    from ssw_200 import (
        SSW_200_MARKERS,
        FilaSemDados as FilaSemDados200,
        _gerar_download_200,
        _preencher_200,
    )
    from dates import to_ssw_ddmmyy

    ini_ddmm, fim_ddmm = period
    ini = to_ssw_ddmmyy(ini_ddmm)
    fim = to_ssw_ddmmyy(fim_ddmm)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = (dest or "ALL").strip().upper() or "ALL"

    popup76 = None
    popup200 = None
    files76: list[str] = []
    files200: list[str] = []
    errors76: dict[str, str] = {}
    error200 = ""

    try:
        # Abrir as duas guias antes de gerar qualquer uma
        if not skip_076:
            status(f"[{tag}] abrindo tela 076…")
            popup76 = _reopen_76(client, page, None)
            try:
                popup76.on("dialog", lambda d: d.accept())
            except Exception:
                pass
            status(f"[{tag}] 076 aberta")
        if not skip_200:
            status(f"[{tag}] abrindo tela 200…")
            popup200 = client._open_menu_option(page, "200", markers=SSW_200_MARKERS)
            try:
                popup200.on("dialog", lambda d: d.accept())
            except Exception:
                pass
            status(f"[{tag}] 200 aberta")

        if popup76 is not None:
            try:
                popup76.bring_to_front()
            except Exception:
                pass
            status(f"[{tag}] preenchendo 076…")
            _preencher_76(
                popup76,
                ini=ini,
                fim=fim,
                unidade="",  # herda menu da filial
                arquivo="E",
                email="N",
                placa="",
                on_status=status,
            )

        if popup200 is not None:
            try:
                popup200.bring_to_front()
            except Exception:
                pass
            status(f"[{tag}] preenchendo 200…")
            _preencher_200(
                popup200,
                ini=ini,
                fim=fim,
                unidade=tag,  # Unidade origem = destino do 073
                tipo="E",
                on_status=status,
            )

        # Gera/baixa (fila 156) — forms já prontos nas duas guias
        if popup76 is not None:
            try:
                try:
                    popup76.bring_to_front()
                except Exception:
                    pass
                dest76 = f"contratacao_076_{tag}_{ts}.sswweb"
                status(f"[{tag}] gerando 076…")
                path76 = _gerar_download_76(
                    client, context, page, popup76, dest76, tag, status
                )
                files76.append(str(path76))
            except FilaSemDados as empty_err:
                errors76[tag] = str(empty_err)
                status(f"[{tag}] 076 sem base — próxima filial ({empty_err})")
            except Exception as batch_err:  # noqa: BLE001
                msg = str(batch_err)
                # Timeout na 156: não queima 40 placas (piora a fila). Próxima filial.
                if "timeout" in msg.lower():
                    errors76[tag] = msg
                    status(f"[{tag}] 076 timeout na fila — segue próxima filial")
                else:
                    status(f"[{tag}] 076 lote falhou ({batch_err}); por placa…")
                    plate_list = [
                        str(p).strip().upper() for p in (placas or []) if str(p).strip()
                    ]
                    runs = plate_list[:12] or [""]
                    for idx, placa in enumerate(runs, start=1):
                        key = placa or tag
                        try:
                            popup76 = _reopen_76(client, page, popup76)
                            _preencher_76(
                                popup76,
                                ini=ini,
                                fim=fim,
                                unidade="",
                                arquivo="E",
                                email="N",
                                placa=placa,
                                on_status=status,
                            )
                            dest76 = f"contratacao_076_{tag}_{key or 'ALL'}_{ts}.sswweb"
                            path76 = _gerar_download_76(
                                client, context, page, popup76, dest76, key, status
                            )
                            files76.append(str(path76))
                        except FilaSemDados as empty_err:
                            errors76[key] = str(empty_err)
                            status(f"[{tag}/76/{key}] sem base — pula")
                        except Exception as err:  # noqa: BLE001
                            errors76[key] = str(err)
                            status(f"[{tag}/76/{key}] FALHOU: {err}")

        if popup200 is not None:
            try:
                try:
                    if popup200.is_closed():
                        popup200 = client._open_menu_option(
                            page, "200", markers=SSW_200_MARKERS
                        )
                        _preencher_200(
                            popup200,
                            ini=ini,
                            fim=fim,
                            unidade=tag,
                            tipo="E",
                            on_status=status,
                        )
                except Exception:
                    pass
                try:
                    popup200.bring_to_front()
                except Exception:
                    pass
                dest200 = f"contratacao_200_{tag}_{ts}.csv"
                status(f"[{tag}] gerando 200…")
                path200 = _gerar_download_200(
                    client, context, page, popup200, dest200, status
                )
                files200.append(str(path200))
            except FilaSemDados200 as empty_err:
                error200 = str(empty_err)
                status(f"[{tag}] 200 sem base — próxima filial ({empty_err})")
            except Exception as err:  # noqa: BLE001
                error200 = str(err)
                status(f"[{tag}] 200 FALHOU: {err}")
    finally:
        for pg in (popup76, popup200):
            try:
                if pg is not None and not pg.is_closed():
                    pg.close()
            except Exception:
                pass

    return {
        "files76": files76,
        "files200": files200,
        "errors76": errors76,
        "error200": error200,
    }


def _run_073_phases(
    client,
    context,
    page,
    *,
    jobs: list[dict[str, str]],
    ini: str,
    fim: str,
    unidade: str,
    operacao: str,
    considerar: str,
    ts: str,
    status: StatusCallback,
) -> dict[str, Any]:
    """Abre N telas 073, preenche e lê a tabela na tela (sem download)."""
    paths: dict[str, str] = {}
    errors: dict[str, str] = {}
    queued: list[dict[str, Any]] = []
    screens: list[tuple[dict[str, str], Any]] = []

    status(f"[73] abrindo {len(jobs)} tela(s) 073…")

    for idx, job in enumerate(jobs, start=1):
        key = job["key"]
        lab = job["label"]
        try:
            status(
                f"[73/{key}·{lab}] ({idx}/{len(jobs)}) abrindo tela "
                f"prop={job['propriedade']} tipo={job['tipo']}…"
            )
            popup = _open_73(client, page)
            screens.append((job, popup))
            status(f"[73/{key}·{lab}] tela aberta")
        except Exception as err:  # noqa: BLE001
            errors[key] = str(err)
            status(f"[73/{key}·{lab}] FALHOU ao abrir: {err}")

    for job, popup in screens:
        key = job["key"]
        lab = job["label"]
        try:
            try:
                popup.bring_to_front()
            except Exception:
                pass
            status(f"[73/{key}·{lab}] preenchendo…")
            _preencher_73(
                popup,
                ini=ini,
                fim=fim,
                tipo=job["tipo"],
                unidade=unidade,
                propriedade=job["propriedade"],
                operacao=operacao,
                considerar=considerar,
                on_status=status,
                job_key=key,
            )
        except Exception as err:  # noqa: BLE001
            errors[key] = str(err)
            status(f"[73/{key}·{lab}] FALHOU no form: {err}")

    # 073: só tela — Prosseguir e copia a grade (sem download)
    status(f"[73] Prosseguir + copiar grade em {len(screens)} tela(s)…")
    for job, popup in screens:
        key = job["key"]
        lab = job["label"]
        if key in errors:
            continue
        dest_name = f"contratacao_073_{key}_{ts}.sswweb"
        try:
            try:
                popup.bring_to_front()
            except Exception:
                pass
            status(f"[73/{key}·{lab}] Prosseguir…")
            path = _ler_tela_73(client, context, popup, dest_name, key, status)
            paths[key] = str(path)
            queued.append({"key": key, "label": lab, "t": time.time(), "idx": len(queued) + 1})
            status(f"[73/{key}·{lab}] OK tela → {path.name} ({path.stat().st_size} bytes)")
        except Exception as err:  # noqa: BLE001
            errors[key] = str(err)
            status(f"[73/{key}·{lab}] FALHOU tela: {err}")

    for _job, popup in screens:
        try:
            if popup is not None and not popup.is_closed():
                popup.close()
        except Exception:
            pass

    return {"paths": paths, "errors": errors, "queued": queued}


def _open_73(client, page):
    """Abre uma nova tela 073 sem fechar as outras (paralelo)."""
    return client._open_menu_option(page, "73", markers=SSW_073_MARKERS)


def _reopen_73(client, page, popup):
    """Fecha a popup anterior (se houver) e abre 073 de novo."""
    try:
        if popup is not None and not popup.is_closed():
            popup.close()
    except Exception:
        pass
    return _open_73(client, page)


def _preencher_73(
    popup,
    *,
    ini: str,
    fim: str,
    tipo: str,
    unidade: str,
    propriedade: str,
    operacao: str,
    considerar: str,
    on_status: StatusCallback,
    job_key: str = "",
) -> None:
    """Preenche ssw0332 pelos IDs fixos da tela 073."""
    status = on_status
    key = job_key or propriedade
    lab = PROPRIEDADE_LABEL.get(key, PROPRIEDADE_LABEL.get(propriedade, propriedade))
    popup.wait_for_timeout(400)
    # Fecha aviso residual (ex.: Unidade emissora) se estiver aberto
    try:
        popup.evaluate(
            """() => {
              try { if (typeof ccx === 'function') ccx(); } catch (e) {}
              try { if (typeof showmsgonclick === 'function') showmsgonclick(); } catch (e2) {}
              const ok = document.querySelector('#errormsg a.dialog, #errormsg a');
              if (ok) { try { ok.click(); } catch (e3) {} }
              const ep = document.getElementById('errorpanel');
              const em = document.getElementById('errormsg');
              if (ep) ep.style.visibility = 'hidden';
              if (em) em.style.visibility = 'hidden';
            }"""
        )
    except Exception:
        pass
    filled = popup.evaluate(
        """({ ini, fim, tipo, unidade, propriedade, operacao, considerar }) => {
          const setId = (id, val) => {
            const el = document.getElementById(id);
            if (!el) return false;
            el.focus();
            el.value = String(val || '');
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          // IDs confirmados no dump ssw0332
          const okIni = setId('per_ini_inc', ini);
          const okFim = setId('per_fin_inc', fim);
          const okProp = setId('tp_propriedade', propriedade);
          const okOp = setId('tp_ctrb_os', operacao);      // Operação (R/C/A/T)
          const okTipo = setId('fg_ctrb_os', tipo);        // Tipo (C/O/A)
          const okCons = setId('fg_cancelados', considerar);
          const okUni = setId('unid_orig', unidade);       // Unidade emissora SPO
          return {
            okIni, okFim, okProp, okOp, okTipo, okCons, okUni,
            vals: {
              ini: (document.getElementById('per_ini_inc') || {}).value || '',
              fim: (document.getElementById('per_fin_inc') || {}).value || '',
              prop: (document.getElementById('tp_propriedade') || {}).value || '',
              op: (document.getElementById('tp_ctrb_os') || {}).value || '',
              tipo: (document.getElementById('fg_ctrb_os') || {}).value || '',
              cons: (document.getElementById('fg_cancelados') || {}).value || '',
              uni: (document.getElementById('unid_orig') || {}).value || '',
            },
          };
        }""",
        {
            "ini": ini,
            "fim": fim,
            "tipo": str(tipo or "A").strip().upper()[:1] or "A",
            "unidade": str(unidade or "SPO").strip().upper()[:3] or "SPO",
            "propriedade": str(propriedade or "T").strip().upper()[:1] or "T",
            "operacao": str(operacao or "T").strip().upper()[:1] or "T",
            "considerar": str(considerar or "T").strip().upper()[:1] or "T",
        },
    )
    status(
        f"[73/{key}·{lab}] form {filled} · prop={propriedade} op={operacao} "
        f"tipo={tipo} uni={unidade}"
    )
    if not filled.get("okProp"):
        raise RuntimeError(f"073: não achei campo Propriedade ({filled})")
    if not filled.get("okOp"):
        raise RuntimeError(f"073: não achei campo Operação ({filled})")
    if not filled.get("okTipo"):
        status(f"[73/{key}·{lab}] aviso: Tipo pode não ter preenchido")
    if not filled.get("okUni"):
        raise RuntimeError(f"073: não achei Unidade emissora unid_orig ({filled})")
    vals = filled.get("vals") or {}
    if str(vals.get("uni") or "").strip().upper() != str(unidade or "SPO").strip().upper():
        raise RuntimeError(f"073: Unidade emissora não gravou SPO ({vals})")
    if not (filled.get("okIni") and filled.get("okFim")):
        status(f"[73/{key}·{lab}] aviso: período pode não ter preenchido ({filled})")
    popup.wait_for_timeout(200)


def _clicar_prosseguir_73(popup) -> str:
    """Clica o ► de 'mostrar tela' (ajaxEnvia MOS) — nunca o ► de CTRB/OS (ENV)."""
    # 1) botão certo: #link_mostra_tela → ajaxEnvia('MOS', 1)
    try:
        how = popup.evaluate(
            """() => {
              // fecha aviso se aberto
              try { if (typeof ccx === 'function') ccx(); } catch (e) {}
              try { if (typeof showmsgonclick === 'function') showmsgonclick(); } catch (e2) {}
              const em = document.getElementById('errormsg');
              if (em) em.style.visibility = 'hidden';
              const ep = document.getElementById('errorpanel');
              if (ep) ep.style.visibility = 'hidden';

              const mos = document.getElementById('link_mostra_tela');
              if (mos) {
                try { mos.click(); return 'link_mostra_tela'; } catch (e3) {}
              }
              if (typeof ajaxEnvia === 'function') {
                try { ajaxEnvia('MOS', 1); return 'ajax-MOS'; } catch (e4) {}
              }
              // fallback: âncora com MOS no onclick (não ENV)
              for (const a of Array.from(document.querySelectorAll('a[onclick]'))) {
                const oc = String(a.getAttribute('onclick') || '');
                if (/ajaxEnvia\\(\\s*['\"]MOS['\"]/i.test(oc)) {
                  try { a.click(); return 'oc:MOS'; } catch (e5) {}
                }
              }
              return '';
            }"""
        )
        if how:
            return str(how)
    except Exception:
        pass
    try:
        loc = popup.locator("#link_mostra_tela")
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            return "locator:link_mostra_tela"
    except Exception:
        pass
    return ""


def _clicar_relatorio_73(popup) -> str:
    """Compat: agora = mostrar tela (MOS), sem Relatório/Excel/download."""
    return _clicar_prosseguir_73(popup)


_CTRB_CELL_RE = re.compile(r"\b[A-Z]{2,3}\s*\d{4,}-?\d?\b", re.I)

# Cabeçalhos da grade após Prosseguir (ssw0332)
_HEADER_HINTS = (
    "CTRB",
    "DESTINO",
    "DESTI",
    "VEÍCULO",
    "VEICULO",
    "PROPRIED",
    "OPERAC",
    "TOTAL CTRB",
    "A RECEBER",
    "MOTORISTA",
    "EMISS",
)


def _extract_grid_from_page(page) -> dict:
    """Extrai grade CTRB da tela após Prosseguir: <table>, <pre> ou texto."""
    try:
        return page.evaluate(
            """() => {
              const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
              const ctrbRe = /\\b[A-Z]{2,3}\\s*\\d{4,}-?\\d?\\b/;
              const headRe = /CTRB|DESTINO|DESTI|VEÍCULO|VEICULO|PROPRIED|OPERAC|TOTAL CTRB|A RECEBER|MOTORISTA/;
              const out = { rows: [], score: 0, how: '', url: location.href || '', nTables: 0, bodyLen: 0, hint: '' };

              const scoreRows = (rows, how) => {
                if (!rows || rows.length < 2) return 0;
                const head = (rows[0] || []).join(' ').toUpperCase();
                const blob = rows.slice(0, 12).map(r => (r || []).join(' ')).join(' ').toUpperCase();
                let score = Math.min(rows.length, 80);
                if (headRe.test(head)) score += 90;
                if (/CARRETEIRO|FROTA|AGREGADO|TRANSFER|COLETA/.test(blob)) score += 35;
                let ctrbHits = 0;
                for (const r of rows.slice(1, 60)) {
                  if (ctrbRe.test((r || []).join(' ').toUpperCase())) ctrbHits++;
                }
                score += Math.min(ctrbHits * 6, 90);
                if (ctrbHits === 0 && !/CTRB/.test(head)) score -= 40;
                out.hint = how + ':hits=' + ctrbHits + ',rows=' + rows.length;
                return score;
              };

              const tables = Array.from(document.querySelectorAll('table'));
              out.nTables = tables.length;
              let best = null, bestScore = 0, bestHow = '';
              for (const tb of tables) {
                const rows = Array.from(tb.querySelectorAll('tr')).map((tr) =>
                  Array.from(tr.querySelectorAll('th,td')).map((c) => norm(c.innerText || c.textContent || ''))
                ).filter((r) => r.some(Boolean));
                if (rows.length < 2) continue;
                const sc = scoreRows(rows, 'table');
                if (sc > bestScore) { bestScore = sc; best = rows; bestHow = 'table'; }
              }

              const preNodes = Array.from(document.querySelectorAll('pre, code, tt, xmp'));
              let textBlob = preNodes.map(n => n.innerText || n.textContent || '').join('\\n');
              if (!textBlob || textBlob.length < 40) {
                textBlob = (document.body && (document.body.innerText || '')) || '';
              }
              out.bodyLen = textBlob.length;
              const lines = textBlob.split(/\\r?\\n/).map(norm).filter(Boolean);
              const dataLines = [];
              let headerLine = null;
              for (const ln of lines) {
                const up = ln.toUpperCase();
                if (!headerLine && /CTRB/.test(up) && headRe.test(up) && (/TIPO|DEST|VEIC|PROPRIED|OPERAC/.test(up))) {
                  headerLine = ln;
                  continue;
                }
                if (ctrbRe.test(up) && (
                  /FROTA|CARRET|AGREG|TRANSFER|COLETA|OS\\b|CTRB|PROPRIED/.test(up)
                  || /\\b[A-Z]{3}\\d[A-Z0-9]\\d{2}\\b/.test(up)
                  || /\\b[A-Z]{3}-\\d{4}\\b/.test(up)
                )) {
                  dataLines.push(ln);
                }
              }
              if (dataLines.length) {
                const splitRow = (s) => {
                  let cells = s.split(/\\t+/).map(norm).filter(Boolean);
                  if (cells.length < 3) cells = s.split(/\\s{2,}/).map(norm).filter(Boolean);
                  if (cells.length < 3) {
                    const m = s.match(/^([A-Z]{2,3}\\s*\\d{4,}-?\\d?)\\s+(.*)$/i);
                    if (m) cells = [norm(m[1])].concat(m[2].split(/\\s+/).filter(Boolean));
                  }
                  return cells;
                };
                const defaultHdr = [
                  'CTRB/OS','Tipo','Destino','Veículo','Propriedade','Operação','Situação',
                  'Emissão','Proprietário','Motorista','Total CTRB/OS','Retenções','Adiantamento',
                  'A Receber','Pedágio','Saldo CCF','Conta Bancária','CIOT','Vale pedagio'
                ];
                const h = headerLine ? splitRow(headerLine) : [];
                const rows = [h.length >= 4 ? h : defaultHdr];
                for (const dl of dataLines) {
                  const cells = splitRow(dl);
                  if (cells.length >= 2) rows.push(cells);
                }
                const sc = scoreRows(rows, 'text') + 10;
                if (sc > bestScore) { bestScore = sc; best = rows; bestHow = headerLine ? 'text' : 'text-nohdr'; }
              }

              if (bestScore < 40) {
                for (const tb of tables) {
                  const rows = Array.from(tb.querySelectorAll('tr')).map((tr) =>
                    Array.from(tr.querySelectorAll('td')).map((c) => norm(c.innerText || ''))
                  ).filter((r) => r.length >= 3 && r.some(Boolean));
                  const data = rows.filter(r => ctrbRe.test(r.join(' ').toUpperCase()));
                  if (data.length < 1) continue;
                  const built = [[
                    'CTRB/OS','Tipo','Destino','Veículo','Propriedade','Operação','Situação',
                    'Emissão','Proprietário','Motorista','Total CTRB/OS','Retenções','Adiantamento',
                    'A Receber','Pedágio','Saldo CCF','Conta Bancária','CIOT','Vale pedagio'
                  ], ...data];
                  const sc = scoreRows(built, 'table-raw');
                  if (sc > bestScore) { bestScore = sc; best = built; bestHow = 'table-raw'; }
                }
              }

              out.rows = best || [];
              out.score = bestScore;
              out.how = bestHow;
              return out;
            }"""
        ) or {"rows": [], "score": 0, "how": ""}
    except Exception as err:
        return {"rows": [], "score": 0, "how": f"err:{err}"}


def _scrape_targets(context, popup) -> tuple[list, object | None]:
    """Lista páginas/frames candidatos a conter o relatório."""
    targets = []
    pages = []
    try:
        pages = list(context.pages)
    except Exception:
        pages = []
    if popup is not None:
        try:
            if popup not in pages:
                pages.append(popup)
        except Exception:
            pass
    # páginas mais novas primeiro
    for pg in reversed(pages):
        try:
            if pg.is_closed():
                continue
        except Exception:
            continue
        targets.append(pg)
        try:
            for fr in pg.frames:
                if fr == pg.main_frame:
                    continue
                targets.append(fr)
        except Exception:
            pass
    return targets, popup


def _dump_73_debug(popup, context, key: str, status) -> None:
    """Salva HTML/texto para diagnóstico quando a grade não aparece."""
    try:
        from config import CACHE_DIR

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        pages = []
        try:
            pages = list(context.pages)
        except Exception:
            pages = [popup] if popup else []
        for i, pg in enumerate(pages):
            try:
                if pg is None or pg.is_closed():
                    continue
                html = pg.content()
                text = pg.evaluate(
                    "() => (document.body && document.body.innerText || '').slice(0, 12000)"
                )
                (CACHE_DIR / f"dump_73_{key}_{stamp}_p{i}.html").write_text(
                    html or "", encoding="utf-8", errors="replace"
                )
                (CACHE_DIR / f"dump_73_{key}_{stamp}_p{i}.txt").write_text(
                    f"URL={pg.url}\nframes={len(pg.frames)}\n\n{text or ''}",
                    encoding="utf-8",
                    errors="replace",
                )
                for fi, fr in enumerate(pg.frames):
                    if fr == pg.main_frame:
                        continue
                    try:
                        ft = fr.evaluate(
                            "() => (document.body && document.body.innerText || '').slice(0, 6000)"
                        )
                        (CACHE_DIR / f"dump_73_{key}_{stamp}_p{i}_f{fi}.txt").write_text(
                            f"URL={fr.url}\n\n{ft or ''}",
                            encoding="utf-8",
                            errors="replace",
                        )
                    except Exception:
                        pass
            except Exception:
                pass
        status(f"[73/{key}] dump debug em data/cache/dump_73_{key}_{stamp}_*")
    except Exception as err:
        status(f"[73/{key}] dump debug falhou: {err}")


def _rows_to_sswweb(rows: list, dest: Path, status, key: str, how: str) -> Path:
    if not rows or len(rows) < 2:
        raise RuntimeError(f"073/{key}: grade vazia ({how})")
    lines = ["0;SSW073;TELA"]
    header = [str(c) for c in rows[0]]
    lines.append("1;" + ";".join(header))
    data_n = 0
    for r in rows[1:]:
        cells = [str(c) for c in list(r) + [""] * max(0, len(header) - len(r))]
        blob = " ".join(cells).upper()
        if not blob.strip():
            continue
        if blob.startswith("CTRB") and "TIPO" in blob:
            continue
        lines.append("2;" + ";".join(cells[: len(header)]))
        data_n += 1
    if data_n <= 0:
        raise RuntimeError(f"073/{key}: tabela sem linhas de dados ({how})")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="latin-1", errors="replace")
    status(f"[73/{key}] copiado da tela ({how}) → {dest.name} ({data_n} linhas)")
    return dest


def _scrape_relatorio_to_sswweb(popup, dest: Path, status, key: str) -> Path:
    """Compat: raspa só a popup atual."""
    info = _extract_grid_from_page(popup)
    return _rows_to_sswweb(info.get("rows") or [], dest, status, key, info.get("how") or "popup")


def _grid_ok(info: dict) -> bool:
    rows = info.get("rows") or []
    score = int(info.get("score") or 0)
    if len(rows) < 2:
        return False
    if score >= 40:
        return True
    # score baixo mas tem CTRB nas linhas
    hits = sum(1 for r in rows[1:] if _CTRB_CELL_RE.search(" ".join(str(c) for c in r)))
    return hits >= 1 and len(rows) >= 2


def _ler_tela_73(client, context, popup, dest_name: str, key: str, status) -> Path:
    """073: ► mostrar tela (MOS) → copia grade (sem download)."""
    dest = Path(client.download_dir) / Path(dest_name).name
    pages_before = list(context.pages)
    new_page = None
    clicked = ""
    held_pages: list = []

    def _on_page(pg) -> None:
        held_pages.append(pg)
        try:
            pg.on("dialog", lambda d: d.accept())
        except Exception:
            pass

    def _aviso_texto(pg) -> str:
        try:
            return str(
                pg.evaluate(
                    """() => {
                      const em = document.getElementById('errormsglabel');
                      if (em && em.offsetParent !== null) return (em.innerText || '').trim();
                      const vis = document.getElementById('errormsg');
                      if (vis && vis.style && vis.style.visibility === 'visible')
                        return (vis.innerText || '').trim().slice(0, 200);
                      return '';
                    }"""
                )
                or ""
            )
        except Exception:
            return ""

    def _dismiss_aviso(pg) -> None:
        try:
            pg.evaluate(
                """() => {
                  try { if (typeof showmsgonclick === 'function') showmsgonclick(); } catch (e) {}
                  try { if (typeof ccx === 'function') ccx(); } catch (e2) {}
                  const a = document.querySelector('#errormsg a.dialog, #errormsg a');
                  if (a) try { a.click(); } catch (e3) {}
                  const ep = document.getElementById('errorpanel');
                  const em = document.getElementById('errormsg');
                  if (ep) ep.style.visibility = 'hidden';
                  if (em) em.style.visibility = 'hidden';
                }"""
            )
        except Exception:
            pass

    try:
        context.on("page", _on_page)
    except Exception:
        pass

    clicked = _clicar_prosseguir_73(popup)
    if not clicked:
        try:
            context.remove_listener("page", _on_page)
        except Exception:
            pass
        raise RuntimeError(f"073/{key}: botão mostrar tela (MOS / ►) não encontrado")
    status(f"[73/{key}] clique={clicked} · aguardando grade…")
    _safe_wait(popup, 1200)

    # Se o SSW pediu unidade / mostrou aviso, corrige SPO e tenta MOS de novo
    aviso = _aviso_texto(popup)
    if aviso:
        status(f"[73/{key}] aviso SSW: {aviso[:80]}")
        _dismiss_aviso(popup)
        try:
            popup.evaluate(
                """() => {
                  const el = document.getElementById('unid_orig');
                  if (el) {
                    el.value = 'SPO';
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                  }
                }"""
            )
        except Exception:
            pass
        clicked = _clicar_prosseguir_73(popup) or clicked
        status(f"[73/{key}] retry MOS={clicked}")
        _safe_wait(popup, 1500)

    try:
        context.remove_listener("page", _on_page)
    except Exception:
        pass

    if held_pages:
        new_page = held_pages[-1]
    else:
        after = [p for p in context.pages if p not in pages_before]
        if after:
            new_page = after[-1]

    if new_page is not None:
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=25000)
        except Exception:
            pass
        try:
            new_page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        _safe_wait(new_page, 600)

    deadline = time.time() + 40
    best = {"rows": [], "score": 0, "how": ""}
    while time.time() < deadline:
        # MOS costuma trocar o HTML da própria popup (não abre janela)
        targets, _ = _scrape_targets(context, popup)
        # prioriza a popup do 073
        ordered = []
        if popup is not None:
            ordered.append(popup)
            try:
                for fr in popup.frames:
                    if fr != popup.main_frame:
                        ordered.append(fr)
            except Exception:
                pass
        if new_page is not None:
            ordered = [new_page] + ordered
        for t in targets:
            if t not in ordered:
                ordered.append(t)
        for tgt in ordered:
            info = _extract_grid_from_page(tgt)
            if int(info.get("score") or 0) > int(best.get("score") or 0):
                best = info
        if _grid_ok(best):
            break
        # ainda no formulário? aguarda AJAX MOS
        try:
            still_form = popup.evaluate(
                "() => !!document.getElementById('link_mostra_tela') || !!document.getElementById('tp_propriedade')"
            )
            if still_form and time.time() > deadline - 25:
                # tenta MOS outra vez no meio da espera
                pass
        except Exception:
            pass
        try:
            after = [p for p in context.pages if p not in pages_before]
            if after and (new_page is None or new_page not in after):
                new_page = after[-1]
        except Exception:
            pass
        time.sleep(0.45)

    if not _grid_ok(best):
        _dismiss_aviso(popup)
        again = _clicar_prosseguir_73(popup)
        if again:
            status(f"[73/{key}] retry final={again}")
            _safe_wait(popup, 2500)
            try:
                after = [p for p in context.pages if p not in pages_before]
                if after:
                    new_page = after[-1]
                    _safe_wait(new_page, 1000)
            except Exception:
                pass
            for tgt in _scrape_targets(context, popup)[0] + ([popup] if popup else []):
                info = _extract_grid_from_page(tgt)
                if int(info.get("score") or 0) > int(best.get("score") or 0):
                    best = info

    rows = best.get("rows") or []
    if not _grid_ok(best):
        _dump_73_debug(popup, context, key, status)
        raise RuntimeError(
            f"073/{key}: nenhuma tabela CTRB após Prosseguir "
            f"(score={best.get('score')} how={best.get('how')!r} "
            f"tables={best.get('nTables')} body={best.get('bodyLen')} "
            f"hint={best.get('hint')!r} clique={clicked!r})"
        )

    path = _rows_to_sswweb(rows, dest, status, key, str(best.get("how") or "tela"))
    if new_page is not None:
        try:
            if not new_page.is_closed():
                new_page.close()
        except Exception:
            pass
    return path


def _download_relatorio_73(
    client, context, popup, dest_name: str, key: str, status
) -> Path:
    """Compat: só lê a tela / nova janela (sem download)."""
    return _ler_tela_73(client, context, popup, dest_name, key, status)


def _clicar_excel_73(popup) -> str:
    """Legado: 'Arquivo Excel' (não usado no fluxo atual)."""
    try:
        loc = popup.get_by_text("Arquivo Excel", exact=True)
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            return "Arquivo Excel"
    except Exception:
        pass
    try:
        loc = popup.locator("a", has_text="Arquivo Excel")
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            return "a:Arquivo Excel"
    except Exception:
        pass
    return popup.evaluate(
        """() => {
          const links = Array.from(document.querySelectorAll('a, span, button'));
          for (const a of links) {
            const t = ((a.innerText || a.textContent || '') + '').replace(/\\s+/g, ' ').trim();
            if (/^Arquivo Excel$/i.test(t)) {
              a.click();
              return 'excel-exact';
            }
          }
          return '';
        }"""
    )


def _download_excel_direto_73(
    client, context, popup, dest_name: str, key: str, status
) -> Path:
    """Legado: Arquivo Excel (fluxo atual usa Relatório)."""
    with context.expect_event("download", timeout=120000) as di:
        clicked = _clicar_excel_73(popup)
        if not clicked:
            raise RuntimeError(f"073/{key}: botão Arquivo Excel / ► não encontrado")
        status(f"[73/{key}] clique={clicked}")
    download = di.value
    return _save_named(client, download, dest_name)


def _enviar_fila_73(popup, status, key: str) -> None:
    """Legado."""
    clicked = _clicar_relatorio_73(popup) or _clicar_excel_73(popup)
    if not clicked:
        raise RuntimeError(f"073/{key}: botão Relatório/Excel não encontrado")
    status(f"[73/{key}] clique={clicked}")
    _safe_wait(popup, 800)


def _save_named(client, download, dest_name: str) -> Path:
    """Salva com o nome pedido (F/AC/AO). SSW sugere CSVssw0332… e apaga a chave."""
    suggested = (download.suggested_filename or "").lower()
    name = Path(dest_name).name
    if suggested.endswith(".csv"):
        name = Path(name).with_suffix(".csv").name
    elif suggested.endswith(".xlsx"):
        name = Path(name).with_suffix(".xlsx").name
    elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
        name = Path(name).with_suffix(".xls").name
    elif suggested.endswith(".sswweb"):
        name = Path(name).with_suffix(".sswweb").name

    dest = Path(client.download_dir) / name
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}_{getattr(client, 'timestamp', '') or 'dup'}{dest.suffix}")
    download.save_as(str(dest))
    return dest


def _abrir_fila_156(client, context, page, status):
    """Abre opção 156 (ssw1440)."""
    status("[73] abrindo fila 156…")
    fila = None
    try:
        fila = client._open_menu_option(page, "156", markers=SSW_FILA_MARKERS)
        status("[73] fila 156 via menu")
    except Exception as err:
        status(f"[73] menu 156: {err}")
        fila = None

    if fila is None:
        try:
            with context.expect_page(timeout=12000) as pi:
                page.evaluate(
                    """() => {
                      if (typeof ajaxEnvia === 'function') ajaxEnvia('', 1, 'ssw1440');
                    }"""
                )
            fila = pi.value
            status("[73] fila 156 via ajaxEnvia ssw1440")
        except Exception:
            fila = context.new_page()
            fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=30000)
            status("[73] fila 156 via goto")

    try:
        fila.on("dialog", lambda d: d.accept())
    except Exception:
        pass

    try:
        url = (fila.url or "").lower()
    except Exception:
        url = ""
    if "blank" in url or "ssw1440" not in url:
        status("[73] recuperando ssw1440…")
        try:
            fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=30000)
            _safe_wait(fila, 800)
        except Exception as err:
            status(f"[73] goto ssw1440: {err}")
    return fila


def _ler_jobs_fila(fila) -> list[dict[str, Any]]:
    return fila.evaluate(
        """() => {
          const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
          const jobs = [];
          for (const tr of Array.from(document.querySelectorAll('tr'))) {
            const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
            if (cells.length < 4) continue;
            const seq = (cells[0] || '').replace(/\\D/g, '');
            if (!seq || seq.length < 4) continue;
            const opcao = cells[1] || '';
            const sit = cells.find(c => /conclu|process|fila|erro|abort/i.test(c)) || cells[6] || '';
            const links = Array.from(tr.querySelectorAll('a[onclick], a[href], img[onclick]')).map(a => {
              const text = norm(a.textContent || a.alt || a.title || '');
              const onclick = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
              return { text, onclick, href, blob };
            });
            const dows = links.filter(x => {
              if (/imprimir|correio|e-mails|emails|retaguarda|voltar|fechar|sair|atualizar/i.test(x.text)) return false;
              return /\\bdow\\b|download\\(|ssw0332|\\.xlsx|\\.xls|\\.csv|\\.sswweb|baixar|arquivo/.test(x.blob)
                || (/0332|073/.test(x.blob) && /dow|href=|http/.test(x.blob));
            });
            const blobAll = (opcao + ' ' + cells.join(' ') + ' ' + links.map(l => l.blob).join(' ')).toLowerCase();
            jobs.push({
              seq,
              opcao,
              situacao: sit,
              concluido: /conclu/i.test(sit),
              is0332: /0332|073\\s*-|ctrb|consulta de ctrb/i.test(blobAll),
              hasDow: dows.length > 0,
              dows,
            });
          }
          if (!jobs.length) {
            const all = Array.from(document.querySelectorAll('a[onclick], a[href], img[onclick]'));
            all.forEach((a, i) => {
              const text = norm(a.textContent || a.alt || a.title || '');
              const onclick = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
              if (/imprimir|correio|atualizar/i.test(text)) return;
              if (!(/\\bdow\\b|download\\(|ssw0332|\\.xlsx|\\.sswweb|baixar/.test(blob))) return;
              jobs.push({
                seq: 'L' + i,
                opcao: text || 'download',
                situacao: 'Concluído',
                concluido: true,
                is0332: /0332|073|ctrb|xlsx|csv|sswweb/.test(blob),
                hasDow: true,
                dows: [{ text, onclick, href, blob }],
                linkIndex: i,
              });
            });
          }
          return jobs;
        }"""
    )


def _atualizar_fila(fila) -> None:
    try:
        fila.evaluate(
            """() => {
              if (typeof ajaxEnvia === 'function') {
                try { ajaxEnvia('', 0); return 'atu'; } catch (e) {}
                try { ajaxEnvia('ATU', 0); return 'ATU'; } catch (e) {}
              }
              const a = document.getElementById('2');
              if (a) { a.click(); return '2'; }
              return '';
            }"""
        )
    except Exception:
        pass


def _snapshot_fila_seqs(client, context, page, status) -> set[str]:
    fila = None
    try:
        fila = _abrir_fila_156(client, context, page, status)
        _safe_wait(fila, 600)
        jobs = _ler_jobs_fila(fila)
        return {str(j.get("seq") or "") for j in jobs if j.get("seq")}
    except Exception as err:
        status(f"[73] snapshot fila: {err}")
        return set()
    finally:
        try:
            if fila is not None and not fila.is_closed():
                fila.close()
        except Exception:
            pass


def _baixar_todos_da_fila(
    client,
    context,
    page,
    fila,
    *,
    queued: list[dict[str, Any]],
    known_before: set[str],
    ts: str,
    status,
) -> dict[str, str]:
    paths: dict[str, str] = {}
    want = len(queued)
    keys_order = [q["key"] for q in queued]
    deadline = time.time() + max(180, 60 * want)
    downloaded_seqs: set[str] = set()

    while time.time() < deadline and len(paths) < want:
        try:
            if fila is None or fila.is_closed():
                fila = _abrir_fila_156(client, context, page, status)
            try:
                fila.bring_to_front()
            except Exception:
                pass
            _atualizar_fila(fila)
            _safe_wait(fila, 1000)
            jobs = _ler_jobs_fila(fila)

            ours = [
                j
                for j in jobs
                if j.get("concluido")
                and j.get("hasDow")
                and str(j.get("seq") or "") not in downloaded_seqs
                and j.get("is0332")
                and str(j.get("seq") or "") not in known_before
            ]
            if len(ours) < (want - len(paths)):
                extras = [
                    j
                    for j in jobs
                    if j.get("concluido")
                    and j.get("hasDow")
                    and str(j.get("seq") or "") not in downloaded_seqs
                    and str(j.get("seq") or "") not in known_before
                    and j not in ours
                    and not j.get("is0495")
                ]
                # evita pegar 0495/031 se o parse marcar errado
                extras = [
                    j
                    for j in extras
                    if not re.search(r"0495|ocorr", str(j.get("opcao") or ""), re.I)
                ]
                ours = ours + extras

            def sort_key(j: dict[str, Any]) -> tuple:
                seq = str(j.get("seq") or "")
                try:
                    num = int(re.sub(r"\D", "", seq) or 0)
                except Exception:
                    num = 0
                return (0 if j.get("is0332") else 1, num)

            ours.sort(key=sort_key)

            if not ours:
                if int(time.time()) % 10 < 3:
                    status(
                        f"[73] fila 156: aguardando conclusão "
                        f"({len(paths)}/{want} baixados)…"
                    )
                _safe_wait(fila, 2500)
                continue

            for job in ours:
                if len(paths) >= want:
                    break
                seq = str(job.get("seq") or "")
                key = None
                for k in keys_order:
                    if k not in paths:
                        key = k
                        break
                if not key:
                    break

                dest_name = f"contratacao_073_{key}_{ts}.sswweb"
                status(
                    f"[73/{key}] baixando da fila 156"
                    + (f" · seq={seq}" if seq else "")
                    + f" · {job.get('opcao') or ''}"
                )
                try:
                    path = _clicar_dow_job(client, context, fila, job, dest_name, status, key)
                    paths[key] = str(path)
                    if seq:
                        downloaded_seqs.add(seq)
                    status(f"[73/{key}] OK {path.name} ({path.stat().st_size} bytes)")
                except Exception as err:  # noqa: BLE001
                    status(f"[73/{key}] download falhou: {err}")
                    _safe_wait(fila, 1500)
        except Exception as err:  # noqa: BLE001
            status(f"[73] fila 156 loop: {err}")
            try:
                fila = _abrir_fila_156(client, context, page, status)
            except Exception:
                pass
            time.sleep(2)

    missing = [k for k in keys_order if k not in paths]
    if missing:
        status(f"[73] sem download para: {', '.join(missing)}")
    return paths


def _clicar_dow_job(client, context, fila, job: dict, dest_name: str, status, key: str) -> Path:
    link_index = job.get("linkIndex")
    with context.expect_event("download", timeout=90000) as di:
        ok = fila.evaluate(
            """({ seq, linkIndex }) => {
              const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
              if (linkIndex != null) {
                const all = Array.from(document.querySelectorAll('a[onclick], a[href], img[onclick]'));
                const a = all[linkIndex];
                if (a) { a.click(); return 'idx'; }
              }
              const rows = Array.from(document.querySelectorAll('tr'));
              for (const tr of rows) {
                const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
                if (!cells.length) continue;
                const s = (cells[0] || '').replace(/\\D/g, '');
                if (seq && s !== String(seq).replace(/\\D/g, '')) continue;
                const links = Array.from(tr.querySelectorAll('a[onclick], a[href], img[onclick]'));
                for (const a of links) {
                  const text = norm(a.textContent || a.alt || a.title || '');
                  const onclick = String(a.getAttribute('onclick') || '');
                  const href = String(a.getAttribute('href') || '');
                  const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
                  if (/imprimir|correio|atualizar|voltar|fechar/i.test(text)) continue;
                  if (/\\bdow\\b|download\\(|ssw0332|\\.xlsx|\\.xls|\\.csv|\\.sswweb|baixar|arquivo/.test(blob)
                      || (/0332|073/.test(blob) && /dow|href=|http/.test(blob))) {
                    a.click();
                    return 'row';
                  }
                }
              }
              const all = Array.from(document.querySelectorAll('a[onclick], a[href], img[onclick]'));
              for (const a of all) {
                const text = norm(a.textContent || a.alt || a.title || '');
                const onclick = String(a.getAttribute('onclick') || '');
                const href = String(a.getAttribute('href') || '');
                const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
                if (/imprimir|correio|atualizar/i.test(text)) continue;
                if (/\\bdow\\b|download\\(|ssw0332|\\.xlsx|\\.sswweb|baixar/.test(blob)) {
                  a.click();
                  return 'first';
                }
              }
              return '';
            }""",
            {"seq": job.get("seq") or "", "linkIndex": link_index},
        )
        if not ok:
            raise RuntimeError(f"073/{key}: DOW não encontrado na linha")
        status(f"[73/{key}] clique DOW={ok}")
    download = di.value
    return _save_named(client, download, dest_name)


# Compat: 076 ainda importa este helper se existir
def _baixar_via_fila_73(client, context, page, popup, dest_name: str, key: str, status) -> Path:
    """Legado (1 arquivo): abre 156 e baixa o próximo 0332 novo."""
    _ = popup
    fila = _abrir_fila_156(client, context, page, status)
    known = set()
    paths = _baixar_todos_da_fila(
        client,
        context,
        page,
        fila,
        queued=[{"key": key, "label": key, "t": time.time(), "idx": 1}],
        known_before=known,
        ts=datetime.now().strftime("%Y%m%d_%H%M%S"),
        status=status,
    )
    if key not in paths:
        raise RuntimeError(f"073/{key}: timeout na fila")
    # renomeia se necessário
    return Path(paths[key])


def _gerar_download_73(client, context, page, popup, dest_name: str, key: str, status) -> Path:
    """Download direto do Excel 073 (sem fila 156)."""
    _ = page
    return _download_excel_direto_73(client, context, popup, dest_name, key, status)
