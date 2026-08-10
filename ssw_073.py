"""Download SSW 073 (ssw0332) — Consulta de CTRBs e OSs → Arquivo Excel/CSV.

Fluxo Contratação (3 relatórios · Unidade emissora = SPO · período = mês até hoje):
  1) 1 login · abre N telas 073 (F+A · A+C · A+O)
  2) Arquivo Excel → download direto (073 não usa fila 156)
  3) Mesma sessão segue pro 076
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

# Jobs oficiais da Contratação
JOBS_073: tuple[dict[str, str], ...] = (
    {"key": "F", "propriedade": "F", "tipo": "A", "label": "frota"},
    {"key": "AC", "propriedade": "A", "tipo": "C", "label": "contratados"},
    {"key": "AO", "propriedade": "A", "tipo": "O", "label": "agregados"},
)
PROPRIEDADE_LABEL = {
    "F": "frota",
    "A": "agregado",
    "C": "carreteiro",
    "AC": "contratados",
    "AO": "agregados",
}
# legado
PROPRIEDADES_073 = ("F", "A", "C")

# Programa do Excel CSVssw0332…
SSW_073_MARKERS = (
    "ctrb",
    "os",
    "propriedade",
    "operacao",
    "unidade emissora",
    "073",
    "arquivo excel",
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
    """Monta a lista de jobs (prop+tipo). Default = JOBS_073 oficiais."""
    _ = tipos  # legado
    # override legado: uma propriedade + um tipo
    if propriedades is None and propriedade and str(propriedade).strip().upper()[:1] not in {"", "T"}:
        prop = str(propriedade).strip().upper()[:1]
        tipo_doc = str(tipo or "A").strip().upper()[:1] or "A"
        return [
            {
                "key": prop,
                "propriedade": prop,
                "tipo": tipo_doc,
                "label": PROPRIEDADE_LABEL.get(prop, prop),
            }
        ]
    if propriedades is not None:
        # se pediram só F/A (sem C legado), usa jobs oficiais filtrados
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
    operacao: str = "T",
    considerar: str = "T",
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    # legado
    tipos: tuple[str, ...] | None = None,
    propriedade: str | None = None,
) -> dict[str, Any]:
    """1 login · N telas 073 em paralelo · Excel com download direto."""
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
        errors.setdefault(k, "sem download do Excel")

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
    unidade_emissora: str = "SPO",
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
) -> dict[str, Any]:
    """1 login: 073 em N telas paralelas + 076 na mesma sessão."""
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
    cleanup_downloads(DOWNLOAD_DIR, on_status=status)

    from parser_ssw073 import analyze_reports_073
    from parser_ssw076 import analyze_reports_076
    from ssw_076 import download_reports_076

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

    status(
        f"SSW contratação | 1 login · {len(jobs)} telas 073"
        + (" + 076" if not skip_076 else "")
        + f" | {ini}-{fim} | {unidade}"
    )

    dl73: dict[str, Any] = {}
    dl76: dict[str, Any] = {}
    analysis73: dict[str, Any] = {}
    analysis76: dict[str, Any] = {"ok": False, "skipped": True}
    periodo_fmt = f"{ini_ddmm} – {fim_ddmm}"

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
                operacao="T",
                considerar="T",
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

            if not skip_076:
                try:
                    status("[76] mesma sessão — abrindo demonstrativo…")
                    try:
                        page.bring_to_front()
                        client._ensure_unit(page)
                        client._patch_blank_popup_form(page)
                    except Exception as stab_err:  # noqa: BLE001
                        status(f"[76] estabilizando menu: {stab_err}")
                    dl76 = download_reports_076(
                        placas=list(analysis73.get("placas") or []),
                        period=(ini_ddmm, fim_ddmm),
                        operacao="R",
                        on_status=status,
                        client=client,
                        context=context,
                        page=page,
                    )
                    analysis76 = analyze_reports_076(
                        dl76.get("files") or [],
                        placas=list(analysis73.get("placas") or []),
                        on_status=status,
                    )
                except Exception as err:  # noqa: BLE001
                    status(f"076 avisou: {err} (mantendo frete do 073)")
                    analysis76 = {"ok": False, "error": str(err)}
        finally:
            browser.close()

    return {
        "ok": True,
        "073": {"download": dl73, **analysis73},
        "076": {"download": dl76, **analysis76},
        "resumo": analysis76.get("resumo") or analysis73.get("resumo"),
        "placas": list(analysis73.get("placas") or []),
        "periodo_fmt": periodo_fmt,
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
    """Abre N telas 073, preenche e baixa Excel direto (073 não usa fila 156)."""
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

    # 073: Arquivo Excel já dispara download direto (não vai pra 156)
    status(f"[73] baixando Excel direto em {len(screens)} tela(s)…")
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
            status(f"[73/{key}·{lab}] Arquivo Excel…")
            path = _download_excel_direto_73(
                client, context, popup, dest_name, key, status
            )
            paths[key] = str(path)
            queued.append({"key": key, "label": lab, "t": time.time(), "idx": len(queued) + 1})
            status(f"[73/{key}·{lab}] OK {path.name} ({path.stat().st_size} bytes)")
        except Exception as err:  # noqa: BLE001
            errors[key] = str(err)
            status(f"[73/{key}·{lab}] FALHOU download: {err}")

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
    """Preenche 073 por rótulo (ids SSW variam). Unidade emissora sempre SPO."""
    status = on_status
    key = job_key or propriedade
    lab = PROPRIEDADE_LABEL.get(key, PROPRIEDADE_LABEL.get(propriedade, propriedade))
    popup.wait_for_timeout(400)
    filled = popup.evaluate(
        """({ ini, fim, tipo, unidade, propriedade, operacao, considerar }) => {
          const norm = (s) => (s || '').toLowerCase().normalize('NFD')
            .replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim();
          const inputs = Array.from(document.querySelectorAll('input[type=text], input:not([type]), input[type=tel]'));
          const near = (el) => {
            let t = '';
            let p = el.previousElementSibling;
            if (p) t = (p.innerText || p.textContent || '');
            if (!t && el.parentElement) t = el.parentElement.innerText || '';
            const id = el.id;
            if (id) {
              const labs = Array.from(document.querySelectorAll('.texto, label, td'));
              for (const lab of labs) {
                const lt = (lab.innerText || '').trim();
                if (!lt || lt.length > 80) continue;
                try {
                  const er = el.getBoundingClientRect();
                  const lr = lab.getBoundingClientRect();
                  if (Math.abs(lr.top - er.top) < 14 && lr.right <= er.left + 8) {
                    return lt;
                  }
                } catch (_) {}
              }
            }
            return t;
          };
          const byHint = (hints) => {
            const hs = hints.map(norm);
            for (const el of inputs) {
              const lab = norm(near(el));
              if (hs.some((h) => lab.includes(h))) return el;
            }
            return null;
          };
          const set = (el, val) => {
            if (!el) return false;
            el.focus();
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };

          const periodInputs = inputs.filter((el) => {
            const lab = norm(near(el));
            return lab.includes('periodo') || lab.includes('data');
          });
          const ddmmyy = inputs.filter((el) => Number(el.maxLength) === 6 || Number(el.size) === 6);
          let okIni = false, okFim = false;
          if (periodInputs.length >= 2) {
            okIni = set(periodInputs[0], ini);
            okFim = set(periodInputs[1], fim);
          } else if (ddmmyy.length >= 2) {
            okIni = set(ddmmyy[0], ini);
            okFim = set(ddmmyy[1], fim);
          }

          const okProp = set(byHint(['propriedade']), propriedade);
          const okOp = set(byHint(['operacao', 'opera']), operacao);
          const okTipo = set(byHint(['tipo']), tipo);
          const okCons = set(byHint(['considerar']), considerar);
          const okUni = set(byHint(['unidade emissora', 'unidade emiss', 'emissora']), unidade);

          return {
            okIni, okFim, okProp, okOp, okTipo, okCons, okUni,
            nInputs: inputs.length,
          };
        }""",
        {
            "ini": ini,
            "fim": fim,
            "tipo": tipo,
            "unidade": unidade,
            "propriedade": propriedade,
            "operacao": operacao,
            "considerar": considerar,
        },
    )
    status(f"[73/{key}·{lab}] form {filled} · prop={propriedade} tipo={tipo} uni={unidade}")
    if not filled.get("okProp"):
        raise RuntimeError(f"073: não achei campo Propriedade ({filled})")
    if not filled.get("okTipo"):
        status(f"[73/{key}·{lab}] aviso: Tipo pode não ter preenchido")
    if not filled.get("okUni"):
        status(f"[73/{key}·{lab}] aviso: Unidade emissora pode não ter preenchido (esperado SPO)")
    if not (filled.get("okIni") and filled.get("okFim")):
        status(f"[73/{key}·{lab}] aviso: período pode não ter preenchido ({filled})")
    popup.wait_for_timeout(200)


def _clicar_excel_73(popup) -> str:
    """Clica somente o link 'Arquivo Excel' (barra do SSW)."""
    # 1) Playwright por texto exato (mais confiável que evaluate genérico)
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
    # 2) evaluate: só o link cujo texto é exatamente Arquivo Excel
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
    """073: Arquivo Excel gera download imediato (não usa fila 156)."""
    with context.expect_event("download", timeout=120000) as di:
        clicked = _clicar_excel_73(popup)
        if not clicked:
            raise RuntimeError(f"073/{key}: botão Arquivo Excel / ► não encontrado")
        status(f"[73/{key}] clique={clicked}")
    download = di.value
    return _save_named(client, download, dest_name)


def _enviar_fila_73(popup, status, key: str) -> None:
    """Legado: só clica Excel (sem esperar). Preferir `_download_excel_direto_73`."""
    clicked = _clicar_excel_73(popup)
    if not clicked:
        raise RuntimeError(f"073/{key}: botão Arquivo Excel / ► não encontrado")
    status(f"[73/{key}] clique={clicked}")
    _safe_wait(popup, 800)


def _save_named(client, download, dest_name: str) -> Path:
    suggested = (download.suggested_filename or "").lower()
    name = dest_name
    if suggested.endswith(".csv"):
        name = Path(dest_name).with_suffix(".csv").name
    elif suggested.endswith(".xlsx"):
        name = Path(dest_name).with_suffix(".xlsx").name
    elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
        name = Path(dest_name).with_suffix(".xls").name
    elif suggested.endswith(".sswweb"):
        name = Path(dest_name).with_suffix(".sswweb").name
    return client._save_download(download, name)


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
