"""Download SSW 081 — CTRCs disponíveis para entrega (Sem saída).

Regras ACE:
  Trânsito c/ previsão chegada até = amanhã (NÃO altera a hora do formulário)
  Excel = S
  Seguir opção 1: Relacionar as entregas, sem roteirizar
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]

SSW_081_PATH = "/bin/ssw0081"
SSW_081_MARKERS = (
    "081",
    "81",
    "ctrc",
    "dispon",
    "entrega",
    "transito",
    "trânsito",
    "previs",
    "excel",
    "0081",
    "roteir",
)


def _noop(_: str) -> None:
    return None


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def _safe_wait(page, ms: int) -> None:
    try:
        if page is None or (hasattr(page, "is_closed") and page.is_closed()):
            time.sleep(ms / 1000.0)
            return
        page.wait_for_timeout(ms)
    except Exception:
        time.sleep(ms / 1000.0)


def download_reports_081(
    *,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """1 login · 081 · previsão até amanhã · Excel=S · opção 1 → download."""
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)

    amanha = datetime.now() + timedelta(days=1)
    data_ddmmyy = amanha.strftime("%d%m%y")
    data_ui = amanha.strftime("%d/%m/%Y")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if clean_downloads:
        cleanup_downloads(DOWNLOAD_DIR, on_status=status)

    client = AceSswClient(
        data_ddmmyy,
        data_ddmmyy,
        keep_open=True,
        headless=use_headless,
        on_status=status,
        credentials=creds,
        settings=cfg,
        clean_downloads=False,
    )

    from playwright.sync_api import sync_playwright

    status(f"SSW 081 | trânsito/previsão até {data_ui} | excel=S | opc.1")
    path: Path | None = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        try:
            from ace_stop import register_browser

            register_browser(browser)
        except Exception:
            pass
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

            status("[081] abrindo opção…")
            popup = client._open_menu_option(page, "81", markers=SSW_081_MARKERS)
            try:
                popup.on("dialog", lambda d: d.accept())
            except Exception:
                pass

            try:
                url = (popup.url or "").lower()
            except Exception:
                url = ""
            if "blank" in url:
                status(f"[081] navegando {SSW_081_PATH}…")
                popup.goto(
                    f"https://sistema.ssw.inf.br{SSW_081_PATH}",
                    wait_until="domcontentloaded",
                )
                _safe_wait(popup, 800)

            status("[081] preenchendo…")
            filled = _preencher_081(popup, data_ddmmyy=data_ddmmyy, on_status=status)
            status(f"[081] form {filled}")

            status("[081] opção 1 · relacionar entregas sem roteirizar…")
            _escolher_opcao_1(popup, status)

            dest_name = f"reciclagem_081_{ts}.xlsx"
            status("[081] gerando Excel…")
            path = _gerar_download_081(popup, dest_name, status)
            status(f"[081] OK {path.name} ({path.stat().st_size} bytes)")
        finally:
            try:
                if popup is not None and not popup.is_closed():
                    popup.close()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                from ace_stop import unregister_browser

                unregister_browser(browser)
            except Exception:
                pass

    if path is None or not path.exists():
        raise RuntimeError("081: nenhum Excel baixado")
    return {
        "ok": True,
        "files": [str(path)],
        "paths": {"081": str(path)},
        "period": data_ddmmyy,
        "periodo_fmt": data_ui,
        "download_dir": str(DOWNLOAD_DIR),
    }


def _preencher_081(popup, *, data_ddmmyy: str, on_status: StatusCallback | None = None) -> dict[str, Any]:
    """Previsão chegada até = amanhã (hora intacta) + Excel = S."""
    status = on_status or _noop
    _safe_wait(popup, 400)
    result = popup.evaluate(
        """(dataYy) => {
          const norm = (s) => String(s || '').toLowerCase()
            .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
          const setVal = (el, val) => {
            if (!el) return false;
            el.focus();
            el.value = String(val == null ? '' : val);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            return true;
          };

          // Excel = S
          const excelIds = ['t_excel', 'excel', 't_arq', 'arquivo', 't_arquivo'];
          let excelOk = false;
          let excelId = '';
          for (const id of excelIds) {
            const el = document.getElementById(id);
            if (el) { excelOk = setVal(el, 'S'); excelId = id; break; }
          }
          if (!excelOk) {
            const inputs = Array.from(document.querySelectorAll('input, select'));
            for (const el of inputs) {
              const blob = norm((el.id || '') + ' ' + (el.name || ''));
              if (blob.includes('excel') || blob.includes('arquivo')) {
                excelOk = setVal(el, 'S');
                excelId = el.id || el.name || 'near-excel';
                break;
              }
            }
          }

          // Data: "transito c/ previsao chegada ate" — NÃO muda hora
          const labels = Array.from(document.querySelectorAll('td, label, span, b, font, div'));
          let dateEl = null;
          let hourEl = null;
          let labelHit = '';
          for (const lab of labels) {
            const t = norm(lab.innerText || lab.textContent || '');
            if (!t || t.length > 100) continue;
            const hit =
              (t.includes('transito') && t.includes('previs')) ||
              (t.includes('previs') && t.includes('chegad')) ||
              (t.includes('chegada') && t.includes('ate'));
            if (!hit) continue;
            labelHit = t.slice(0, 70);
            let row = lab.closest('tr') || lab.parentElement;
            const pool = [];
            if (row) pool.push(...Array.from(row.querySelectorAll('input')));
            let sib = lab.nextElementSibling;
            for (let i = 0; i < 4 && sib; i++, sib = sib.nextElementSibling) {
              if (sib.matches && sib.matches('input')) pool.push(sib);
              pool.push(...Array.from(sib.querySelectorAll ? sib.querySelectorAll('input') : []));
            }
            const uniq = [...new Set(pool)];
            for (const inp of uniq) {
              const v = String(inp.value || '');
              const dig = v.replace(/\\D/g, '');
              const idn = norm((inp.id || '') + ' ' + (inp.name || ''));
              if (/^\\d{1,2}:\\d{2}/.test(v) || idn.includes('hora') || idn.includes('hr') || idn.includes('time')) {
                hourEl = hourEl || inp; // NÃO alterar
                continue;
              }
              if (dig.length >= 4) dateEl = dateEl || inp;
              else if (!dateEl) dateEl = inp;
            }
            if (dateEl) break;
          }

          let dateOk = false;
          let dateBefore = '';
          let dateAfter = '';
          let hourKept = hourEl ? String(hourEl.value || '') : '';
          if (dateEl) {
            dateBefore = String(dateEl.value || '');
            // se data e hora no mesmo campo, troca só a parte da data
            const mHour = dateBefore.match(/(\\d{1,2}:\\d{2}(?::\\d{2})?)/);
            let newVal = dataYy;
            if (dateBefore.includes('/')) {
              newVal = dataYy.slice(0, 2) + '/' + dataYy.slice(2, 4) + '/' + dataYy.slice(4, 6);
            }
            if (mHour) {
              // mantém hora original no mesmo campo
              newVal = newVal + ' ' + mHour[1];
              hourKept = mHour[1];
            }
            dateOk = setVal(dateEl, newVal);
            dateAfter = String(dateEl.value || '');
            // reafirma hora separada intacta
            if (hourEl && hourKept) setVal(hourEl, hourKept);
          }

          return {
            excelOk,
            excelId,
            dateOk,
            labelHit,
            dateId: dateEl ? (dateEl.id || dateEl.name || '') : '',
            dateBefore,
            dateAfter,
            hourKept,
            url: location.pathname || '',
          };
        }""",
        data_ddmmyy,
    )
    if not (result or {}).get("excelOk"):
        status("[081] aviso: Excel=S não confirmado")
        popup.evaluate(
            """() => {
              for (const id of ['t_excel','35','arquivo','t_arq']) {
                const el = document.getElementById(id);
                if (!el) continue;
                el.value = 'S';
                el.dispatchEvent(new Event('change', { bubbles: true }));
              }
            }"""
        )
    if not (result or {}).get("dateOk"):
        status("[081] aviso: data previsão não localizada — tentando padrões")
        popup.evaluate(
            """(dataYy) => {
              const ids = ['t_dt_prev','t_previsao','t_dt_ate','t_data_ate','t_dt_cheg','9','10','13','14'];
              for (const id of ids) {
                const el = document.getElementById(id);
                if (!el) continue;
                const prev = String(el.value || '');
                const m = prev.match(/(\\d{1,2}:\\d{2}(?::\\d{2})?)/);
                let v = dataYy;
                if (prev.includes('/')) v = dataYy.slice(0,2)+'/'+dataYy.slice(2,4)+'/'+dataYy.slice(4,6);
                if (m) v = v + ' ' + m[1];
                el.value = v;
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
              return false;
            }""",
            data_ddmmyy,
        )
    _safe_wait(popup, 300)
    return result or {}


def _escolher_opcao_1(popup, status: StatusCallback) -> None:
    """1. Relacionar as entregas, sem roteirizar."""
    clicked = popup.evaluate(
        """() => {
          // radio / option value 1
          const radios = Array.from(document.querySelectorAll('input[type=radio], select, input[type=checkbox]'));
          for (const el of radios) {
            const blob = ((el.value || '') + ' ' + (el.id || '') + ' ' + (el.name || '')).toLowerCase();
            const lab = el.closest('label') || el.parentElement;
            const labT = ((lab && (lab.innerText || lab.textContent)) || '').toLowerCase();
            if (el.value === '1' || /sem\\s*roteir|relacionar.*entrega/.test(labT) || blob === '1') {
              if (el.tagName === 'SELECT') {
                el.value = '1';
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return 'select:1';
              }
              el.checked = true;
              el.click();
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return 'radio:' + (el.id || el.name || '1');
            }
          }
          // links/botões com texto da opção 1
          const nodes = Array.from(document.querySelectorAll('a, button, td, span, font, label'));
          for (const n of nodes) {
            const t = ((n.innerText || n.textContent || '')).toLowerCase();
            if (/1\\s*[.\\-)–].*relacionar.*entrega|relacionar as entregas.*sem roteir|sem roteirizar/.test(t)) {
              n.click();
              return 'text:' + t.slice(0, 50);
            }
          }
          // ajaxEnvia comum
          if (typeof ajaxEnvia === 'function') {
            try { ajaxEnvia('1', 0); return 'ajax:1'; } catch (e) {}
            try { ajaxEnvia('OPC1', 0); return 'ajax:OPC1'; } catch (e2) {}
          }
          return '';
        }"""
    )
    if clicked:
        status(f"[081] opção 1 → {clicked}")
    else:
        status("[081] aviso: opção 1 não clicada (pode já estar default)")
    _safe_wait(popup, 500)


def _gerar_download_081(popup, dest_name: str, status: StatusCallback) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOWNLOAD_DIR / dest_name

    def _click_gerar() -> str:
        return popup.evaluate(
            """() => {
              if (typeof ajaxEnvia === 'function') {
                for (const code of ['REL', 'REL2', 'E1', 'EXCEL', 'GER', '1']) {
                  try { ajaxEnvia(code, 0); return 'ajax:' + code; } catch (e) {}
                  try { ajaxEnvia(code, 1); return 'ajax:' + code + ':1'; } catch (e2) {}
                }
              }
              const btnIds = ['btn_env_periodo','btn_gerar','btn_ok','act_rel','40'];
              for (const id of btnIds) {
                const el = document.getElementById(id);
                if (el) { el.click(); return 'id:' + id; }
              }
              const links = Array.from(document.querySelectorAll('a, button, input[type=button], input[type=submit]'));
              for (const a of links) {
                const t = ((a.innerText || a.value || '') + ' ' + (a.getAttribute('onclick') || '')).toLowerCase();
                if (/gerar|excel|relat|confirmar|relacionar|\\u25ba|\\u25b6|>/.test(t) || /ajaxEnvia/.test(a.getAttribute('onclick') || '')) {
                  a.click();
                  return 'click:' + (a.id || a.innerText || '').slice(0, 40);
                }
              }
              return '';
            }"""
        )

    try:
        with popup.expect_download(timeout=180000) as di:
            clicked = _click_gerar()
            if not clicked:
                raise RuntimeError("081: botão gerar não encontrado")
            status(f"[081] gerar → {clicked}")
        download = di.value
    except Exception as err:
        status(f"[081] expect_download: {err} — varrendo pasta…")
        _safe_wait(popup, 2000)
        candid = _latest_download(DOWNLOAD_DIR)
        if candid is None:
            raise RuntimeError(f"081: download falhou ({err})") from err
        dest = DOWNLOAD_DIR / dest_name
        if candid.suffix.lower() in {".xlsx", ".xls", ".csv", ".sswweb"}:
            dest = dest.with_suffix(candid.suffix.lower() if candid.suffix else ".xlsx")
        try:
            if candid.resolve() != dest.resolve():
                candid.replace(dest)
            return dest
        except Exception:
            return candid

    suggested = (download.suggested_filename or "").lower()
    if suggested.endswith(".csv"):
        dest = dest.with_suffix(".csv")
    elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
        dest = dest.with_suffix(".xls")
    elif suggested.endswith(".sswweb"):
        dest = dest.with_suffix(".sswweb")
    download.save_as(str(dest))
    return dest


def _latest_download(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    cands = []
    for pat in ("*.xlsx", "*.xls", "*.csv", "*.sswweb"):
        cands.extend(folder.glob(pat))
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]
