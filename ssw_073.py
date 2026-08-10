"""Download SSW 073 (ssw0332) — Consulta de CTRBs e OSs → Arquivo Excel/CSV.

Fluxo Contratação (mín. 3 relatórios por Propriedade):
  · Unidade emissora = SPO
  · Propriedade F = frota · A = agregado · C = carreteiro
  · Tipo A (ambos CTRB+OS) · Operação T · Considerar T
  · Placas do resultado alimentam o 076
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_mes_ate_hoje, to_ssw_ddmmyy
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]

# F-frota, A-agregado, C-carreteiro — Contratação puxa os 3
PROPRIEDADES_073 = ("F", "A", "C")
PROPRIEDADE_LABEL = {"F": "frota", "A": "agregado", "C": "carreteiro"}

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


def _noop(_: str) -> None:
    return None


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def download_reports_073(
    *,
    period: tuple[str, str] | None = None,
    propriedades: tuple[str, ...] | list[str] | None = None,
    tipo: str = "A",
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
    """
    Abre opção 73 e gera Arquivo Excel para cada Propriedade (F / A / C).
    Retorna paths por chave (F|A|C) + lista consolidada.
    """
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)

    if propriedades is None and propriedade and str(propriedade).strip().upper()[:1] not in {"", "T"}:
        prop_list = [str(propriedade).strip().upper()[:1]]
    elif propriedades is None:
        prop_list = list(PROPRIEDADES_073)
    else:
        prop_list = [str(p).strip().upper()[:1] for p in propriedades if str(p).strip()]
    prop_list = [p for p in prop_list if p in {"F", "A", "C"}]
    if not prop_list:
        prop_list = list(PROPRIEDADES_073)

    tipo_doc = str(tipo or "A").strip().upper()[:1] or "A"
    _ = tipos  # legado ignorado — fluxo oficial é por propriedade

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
    status(
        f"SSW 73 | {len(prop_list)} relatório(s) · prop={','.join(prop_list)} "
        f"(F=frota A=agregado C=carreteiro) | tipo={tipo_doc} | {ini}-{fim} | "
        f"emissora={unidade_emissora} | op={operacao}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))
        popup = None
        try:
            client._login(page)
            client._ensure_unit(page)
            client._patch_blank_popup_form(page)

            for idx, prop in enumerate(prop_list, start=1):
                lab = PROPRIEDADE_LABEL.get(prop, prop)
                key = prop
                try:
                    status(f"[73/{prop}·{lab}] ({idx}/{len(prop_list)}) abrindo opção 73…")
                    popup = _reopen_73(client, page, popup)
                    status(f"[73/{prop}·{lab}] preenchendo formulário…")
                    _preencher_73(
                        popup,
                        ini=ini,
                        fim=fim,
                        tipo=tipo_doc,
                        unidade=unidade_emissora,
                        propriedade=prop,
                        operacao=operacao,
                        considerar=considerar,
                        on_status=status,
                    )
                    status(f"[73/{prop}·{lab}] gerando Arquivo Excel…")
                    dest_name = f"contratacao_073_{prop}_{ts}.sswweb"
                    path = _gerar_download_73(
                        client, context, page, popup, dest_name, key, status
                    )
                    paths[key] = str(path)
                    status(f"[73/{prop}·{lab}] OK {path.name} ({path.stat().st_size} bytes)")
                    page.wait_for_timeout(400)
                except Exception as err:  # noqa: BLE001
                    errors[key] = str(err)
                    status(f"[73/{prop}·{lab}] FALHOU: {err}")
                    try:
                        popup = _reopen_73(client, page, popup)
                    except Exception:
                        popup = None
            try:
                if popup is not None and not popup.is_closed():
                    popup.close()
            except Exception:
                pass
        finally:
            browser.close()

    if not paths and errors:
        raise RuntimeError("073 falhou: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))

    return {
        "ok": bool(paths),
        "paths": paths,
        "files": list(paths.values()),
        "errors": errors,
        "propriedades": prop_list,
        "period": (ini_ddmm, fim_ddmm),
        "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
        "unidade": unidade_emissora,
    }


def _reopen_73(client, page, popup):
    try:
        if popup is not None and not popup.is_closed():
            popup.close()
    except Exception:
        pass
    return client._open_menu_option(page, "73", markers=SSW_073_MARKERS)


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
) -> None:
    """Preenche 073 por rótulo (ids SSW variam)."""
    status = on_status
    lab = PROPRIEDADE_LABEL.get(propriedade, propriedade)
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
    status(f"[73/{propriedade}·{lab}] form {filled}")
    if not filled.get("okProp"):
        raise RuntimeError(f"073: não achei campo Propriedade ({filled})")
    if not filled.get("okTipo"):
        status(f"[73/{propriedade}·{lab}] aviso: Tipo pode não ter preenchido")
    if not (filled.get("okIni") and filled.get("okFim")):
        status(f"[73/{propriedade}·{lab}] aviso: período pode não ter preenchido ({filled})")
    popup.wait_for_timeout(200)


def _clicar_excel_73(popup) -> str:
    """Clica 'Arquivo Excel' (preferência) ou ► / ajaxEnvia."""
    return popup.evaluate(
        """() => {
          const links = Array.from(document.querySelectorAll('a, button, input, img, span'));
          for (const a of links) {
            const t = ((a.innerText || a.textContent || a.alt || a.title || '') + '').toLowerCase();
            if (t.includes('arquivo excel') || t.includes('excel')) {
              a.click();
              return 'excel';
            }
          }
          if (typeof ajaxEnvia === 'function') {
            try { ajaxEnvia('EXC', 0); return 'ajax-EXC'; } catch (_) {}
            try { ajaxEnvia('ENV', 0); return 'ajax-ENV'; } catch (_) {}
          }
          const play = document.getElementById('13') || document.querySelector('[id=\"13\"]');
          if (play) { play.click(); return '13'; }
          return '';
        }"""
    )


def _gerar_download_73(client, context, page, popup, dest_name: str, key: str, status) -> Path:
    try:
        with context.expect_event("download", timeout=25000) as di:
            clicked = _clicar_excel_73(popup)
            if not clicked:
                raise RuntimeError("073: botão Arquivo Excel / ► não encontrado")
            status(f"[73/{key}] clique={clicked}")
        download = di.value
        return _save_named(client, download, dest_name)
    except Exception as direct_err:  # noqa: BLE001
        status(f"[73/{key}] sem download imediato ({direct_err}); Ver fila…")
        try:
            popup.wait_for_timeout(800)
        except Exception:
            pass
        return _baixar_via_fila_73(client, context, page, popup, dest_name, key, status)


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


def _abrir_ver_fila(client, context, page, popup, status):
    fila = None
    try:
        with context.expect_page(timeout=12000) as pi:
            opened = popup.evaluate(
                """() => {
                  const links = Array.from(document.querySelectorAll('a, button, span'));
                  for (const a of links) {
                    const t = ((a.innerText || a.textContent || '') + '').toLowerCase();
                    if (t.includes('ver fila') || t.includes('fila')) { a.click(); return 'fila'; }
                  }
                  if (typeof ajaxEnvia === 'function') {
                    try { ajaxEnvia('', 1, 'ssw1440'); return 'ajax1440'; } catch (_) {}
                  }
                  return '';
                }"""
            )
            if opened:
                fila = pi.value
                status(f"[73] fila via {opened}")
    except Exception:
        fila = None
    if fila is None:
        fila = context.new_page()
        fila.goto("https://sistema.ssw.inf.br/bin/ssw1440", wait_until="domcontentloaded")
        status("[73] fila via goto ssw1440")
    fila.on("dialog", lambda d: d.accept())
    return fila


def _baixar_via_fila_73(client, context, page, popup, dest_name: str, key: str, status) -> Path:
    fila = _abrir_ver_fila(client, context, page, popup, status)
    deadline = time.time() + 150
    last_err = ""
    while time.time() < deadline:
        try:
            if "blank" in (fila.url or "").lower():
                fila.goto("https://sistema.ssw.inf.br/bin/ssw1440", wait_until="domcontentloaded")
            try:
                fila.evaluate(
                    """() => {
                      if (typeof ajaxEnvia === 'function') { ajaxEnvia('ATU', 0); return 'ATU'; }
                      return '';
                    }"""
                )
            except Exception:
                pass
            fila.wait_for_timeout(1200)
            href = fila.evaluate(
                """() => {
                  const as = Array.from(document.querySelectorAll('a[href], a'));
                  const hit = as.find((a) => {
                    const h = (a.getAttribute('href') || '').toLowerCase();
                    const t = ((a.innerText || a.textContent || '') + '').toLowerCase();
                    return h.includes('dow') || h.includes('download') || h.includes('ssw0495')
                      || h.includes('.sswweb') || h.includes('.xlsx') || h.includes('.csv')
                      || t.includes('download') || t.includes('baixar');
                  });
                  return hit ? (hit.getAttribute('href') || '') : '';
                }"""
            )
            if href:
                with context.expect_event("download", timeout=20000) as di:
                    if href.startswith("http") or href.startswith("/"):
                        fila.evaluate(
                            """(h) => {
                              const a = document.createElement('a');
                              a.href = h; a.click();
                            }""",
                            href,
                        )
                    else:
                        fila.click(f'a[href="{href}"]')
                return _save_named(client, di.value, dest_name)
        except Exception as err:  # noqa: BLE001
            last_err = str(err)
        fila.wait_for_timeout(2000)
    raise RuntimeError(f"073/{key}: timeout na fila ({last_err})")
