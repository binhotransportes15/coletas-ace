"""Download SSW 019 — CTRCs disponíveis (Transferência / Sem transferência).

Regras ACE:
  CTRCs emitidos até = hoje (mantém a hora já presente no formulário)
  Excel = S
  Gera e baixa o relatório
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import to_ssw_ddmmyy
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]

SSW_019_PATH = "/bin/ssw0019"
SSW_019_MARKERS = (
    "019",
    "19",
    "ctrc",
    "dispon",
    "transfer",
    "emitid",
    "excel",
    "0019",
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


def download_reports_019(
    *,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """1 login · 019 · emitidos até hoje · Excel=S → download."""
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)

    hoje = datetime.now()
    data_ddmmyy = hoje.strftime("%d%m%y")
    data_ui = hoje.strftime("%d/%m/%Y")
    ts = hoje.strftime("%Y%m%d_%H%M%S")
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

    status(f"SSW 019 | CTRCs emitidos até {data_ui} | excel=S")
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

            status("[019] abrindo opção…")
            popup = client._open_menu_option(page, "19", markers=SSW_019_MARKERS)
            try:
                popup.on("dialog", lambda d: d.accept())
            except Exception:
                pass

            # fallback path se blank
            try:
                url = (popup.url or "").lower()
            except Exception:
                url = ""
            if "blank" in url:
                status(f"[019] navegando {SSW_019_PATH}…")
                popup.goto(
                    f"https://sistema.ssw.inf.br{SSW_019_PATH}",
                    wait_until="domcontentloaded",
                )
                _safe_wait(popup, 800)

            status("[019] preenchendo…")
            filled = _preencher_019(popup, data_ddmmyy=data_ddmmyy, on_status=status)
            status(f"[019] form {filled}")

            dest_name = f"reciclagem_019_{ts}.xlsx"
            status("[019] gerando Excel…")
            path = _gerar_download_019(popup, dest_name, status)
            status(f"[019] OK {path.name} ({path.stat().st_size} bytes)")
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
        raise RuntimeError("019: nenhum Excel baixado")
    return {
        "ok": True,
        "files": [str(path)],
        "paths": {"019": str(path)},
        "period": data_ddmmyy,
        "periodo_fmt": data_ui,
        "download_dir": str(DOWNLOAD_DIR),
    }


def _preencher_019(popup, *, data_ddmmyy: str, on_status: StatusCallback | None = None) -> dict[str, Any]:
    """Define data 'emitidos até' = hoje (mantém hora) e Excel = S."""
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
          // Excel = S (vários ids comuns)
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
              const blob = norm((el.id || '') + ' ' + (el.name || '') + ' ' + (el.previousSibling && el.previousSibling.textContent || ''));
              if (blob.includes('excel') || blob.includes('arquivo')) {
                excelOk = setVal(el, 'S');
                excelId = el.id || el.name || 'near-excel';
                break;
              }
            }
          }

          // Campo data: rótulo contendo emitid / ctrcs emitidos
          const labels = Array.from(document.querySelectorAll('td, label, span, b, font, div'));
          let dateEl = null;
          let hourEl = null;
          let labelHit = '';
          for (const lab of labels) {
            const t = norm(lab.innerText || lab.textContent || '');
            if (!t || t.length > 80) continue;
            if (!(t.includes('emitid') || (t.includes('ctrc') && t.includes('ate')))) continue;
            labelHit = t.slice(0, 60);
            // inputs na mesma linha / próximos
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
              // hora hh:mm ou hhmm
              if (/^\\d{1,2}:\\d{2}/.test(v) || (dig.length <= 4 && /hora|hr|time/.test(norm(inp.id + ' ' + inp.name)))) {
                hourEl = hourEl || inp;
                continue;
              }
              if (dig.length >= 4) {
                dateEl = dateEl || inp;
              } else if (!dateEl) {
                dateEl = inp;
              }
            }
            if (dateEl) break;
          }

          let dateOk = false;
          let dateBefore = '';
          let dateAfter = '';
          let hourKept = '';
          if (dateEl) {
            dateBefore = String(dateEl.value || '');
            // preserva hora se estiver no mesmo campo (ex.: DDMMYY HH:MM)
            const m = dateBefore.match(/(\\d{1,2}:\\d{2}(?::\\d{2})?)/);
            const hourPart = m ? m[1] : '';
            let newVal = dataYy;
            // se o campo usa DD/MM/YY
            if (dateBefore.includes('/')) {
              const yy = dataYy.slice(4, 6);
              newVal = dataYy.slice(0, 2) + '/' + dataYy.slice(2, 4) + '/' + yy;
            }
            if (hourPart && /\\d/.test(dateBefore) && dateBefore.length > 6) {
              newVal = newVal + ' ' + hourPart;
            }
            dateOk = setVal(dateEl, newVal);
            dateAfter = String(dateEl.value || '');
            if (hourEl) hourKept = String(hourEl.value || '');
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
        status("[019] aviso: Excel=S não confirmado — tentando ids extras")
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
        status("[019] aviso: data emitidos não localizada por rótulo — tentando padrões")
        popup.evaluate(
            """(dataYy) => {
              const ids = ['t_dt_emi','t_dt_emit','t_emitidos','t_dt_ate','t_data_ate','9','10'];
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


def _gerar_download_019(popup, dest_name: str, status: StatusCallback) -> Path:
    """Clica gerar e salva o download (Excel=S costuma ser direto)."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOWNLOAD_DIR / dest_name

    def _click_gerar() -> str:
        return popup.evaluate(
            """() => {
              if (typeof ajaxEnvia === 'function') {
                for (const code of ['REL', 'REL2', 'E1', 'EXCEL', 'GER']) {
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
                if (/gerar|excel|relat|confirmar|\\u25ba|\\u25b6|>/.test(t) || /ajaxEnvia/.test(a.getAttribute('onclick') || '')) {
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
                raise RuntimeError("019: botão gerar não encontrado")
            status(f"[019] gerar → {clicked}")
        download = di.value
    except Exception as err:
        # às vezes o arquivo cai na pasta sem expect_download
        status(f"[019] expect_download: {err} — varrendo pasta…")
        _safe_wait(popup, 2000)
        candid = _latest_download(DOWNLOAD_DIR)
        if candid is None:
            raise RuntimeError(f"019: download falhou ({err})") from err
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
