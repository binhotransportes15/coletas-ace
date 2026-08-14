"""Download SSW 019 — CTRCs disponíveis (Transferência / Sem transferência).

Tela real: /bin/ssw0036
Regras ACE:
  CTRCs emitidos até = hoje (mantém a hora do formulário)
  Excel (relatorio_excel) = S
  Gera e baixa o relatório
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]

# Confirmado na corrida CRT 14/08/2026 (action do menu 19)
SSW_019_PATH = "/bin/ssw0036"
SSW_019_MARKERS = (
    "019",
    "19",
    "ctrc",
    "dispon",
    "transfer",
    "emitid",
    "excel",
    "0036",
    "relatorio_excel",
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
    """1 login · 019 (ssw0036) · emitidos até hoje · Excel=S → download."""
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

            try:
                url = (popup.url or "").lower()
            except Exception:
                url = ""
            if "blank" in url or "ssw0036" not in url:
                status(f"[019] navegando {SSW_019_PATH}…")
                popup.goto(
                    f"https://sistema.ssw.inf.br{SSW_019_PATH}",
                    wait_until="domcontentloaded",
                )
                _safe_wait(popup, 800)

            status("[019] preenchendo…")
            filled = _preencher_019(popup, data_ddmmyy=data_ddmmyy, on_status=status)
            status(f"[019] form {filled}")
            if not (filled or {}).get("dateOk"):
                raise RuntimeError(
                    "019: não achei o campo de data 'CTRCs emitidos até' "
                    f"(form={filled})"
                )

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
        "programa": SSW_019_PATH,
    }


def _preencher_019(popup, *, data_ddmmyy: str, on_status: StatusCallback | None = None) -> dict[str, Any]:
    """Define data 'emitidos até' = hoje (mantém hora) e Excel = S.

    IDs reais observados: relatorio_excel; NÃO tocar em l_siglas_familia.
    """
    status = on_status or _noop
    _safe_wait(popup, 500)
    # Espera o campo Excel conhecido
    try:
        popup.locator("#relatorio_excel").wait_for(state="attached", timeout=12000)
    except Exception:
        status("[019] aviso: #relatorio_excel ainda não visível")

    result = popup.evaluate(
        """(dataYy) => {
          const norm = (s) => String(s || '').toLowerCase()
            .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
            .replace(/\\xa0/g, ' ');
          const setVal = (el, val) => {
            if (!el) return false;
            el.focus();
            el.value = String(val == null ? '' : val);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            return true;
          };
          const looksHour = (v, idn) => {
            const s = String(v || '').trim();
            if (/^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(s)) return true;
            if (/^\\d{3,4}$/.test(s) && (idn.includes('hora') || idn.includes('hr') || idn.includes('time'))) return true;
            // campo hora curto típico SSW (hhmm) ao lado da data
            if (/^\\d{3,4}$/.test(s) && Number(s) <= 2359) return 'maybe';
            return false;
          };
          const looksDate = (v) => {
            const s = String(v || '').trim();
            if (!s) return 'empty';
            if (/\\d{1,2}[\\/.-]\\d{1,2}([\\/.-]\\d{2,4})?/.test(s)) return true;
            if (/^\\d{4,8}$/.test(s.replace(/\\D/g, ''))) return true;
            return false;
          };
          const looksJunk = (v, idn) => {
            const s = String(v || '');
            const id = String(idn || '');
            if (/sigla|familia|unidade|placa|motor|cliente|destino|origem/.test(id)) return true;
            // várias siglas: "RIS FTB CPQ..."
            if (/[A-Za-z]{2,}/.test(s) && /\\s/.test(s)) return true;
            if ((s.match(/[A-Za-z]/g) || []).length >= 3 && !/excel|arquivo/.test(id)) return true;
            return false;
          };

          // Excel = S
          let excelOk = false;
          let excelId = '';
          for (const id of ['relatorio_excel', 't_excel', 'excel', 't_arq', 'arquivo']) {
            const el = document.getElementById(id);
            if (!el) continue;
            excelOk = setVal(el, 'S');
            excelId = id;
            break;
          }

          // Localiza rótulo "CTRCs emitidos até"
          const labels = Array.from(document.querySelectorAll('td, label, span, b, font, div'));
          let labelHit = '';
          let row = null;
          let labEl = null;
          for (const lab of labels) {
            const t = norm(lab.innerText || lab.textContent || '');
            if (!t || t.length > 120) continue;
            if (!(t.includes('emitid') && t.includes('ate'))) continue;
            labelHit = t.slice(0, 80);
            labEl = lab;
            row = lab.closest('tr') || lab.parentElement;
            break;
          }

          let dateEl = null;
          let hourEl = null;
          const pool = [];
          const addInputs = (root) => {
            if (!root) return;
            if (root.matches && root.matches('input')) pool.push(root);
            if (root.querySelectorAll) pool.push(...Array.from(root.querySelectorAll('input')));
          };
          if (row) {
            addInputs(row);
            // linha seguinte (SSW às vezes quebra dia/hora)
            let n = row.nextElementSibling;
            for (let i = 0; i < 2 && n; i++, n = n.nextElementSibling) addInputs(n);
          }
          if (labEl) {
            let sib = labEl.nextElementSibling;
            for (let i = 0; i < 8 && sib; i++, sib = sib.nextElementSibling) addInputs(sib);
            // td seguinte na mesma tr
            const td = labEl.closest('td');
            if (td) {
              let tdn = td.nextElementSibling;
              for (let i = 0; i < 4 && tdn; i++, tdn = tdn.nextElementSibling) addInputs(tdn);
            }
          }
          // varredura ampla: inputs com id/name de data perto do texto emitidos
          for (const inp of Array.from(document.querySelectorAll('input'))) {
            const idn = norm((inp.id || '') + ' ' + (inp.name || ''));
            if (/emit|emi_dt|dt_emi|data_emi|dt_ate|data_ate/.test(idn)) pool.push(inp);
          }
          const uniq = [...new Set(pool)];

          // 1ª passagem: classificar
          const candidates = [];
          for (const inp of uniq) {
            const idn = norm((inp.id || '') + ' ' + (inp.name || ''));
            const v = String(inp.value || '');
            if (looksJunk(v, idn)) continue;
            const h = looksHour(v, idn);
            const d = looksDate(v);
            candidates.push({ inp, idn, v, h, d });
          }

          // hora: preferência explícita
          for (const c of candidates) {
            if (c.h === true) { hourEl = hourEl || c.inp; }
          }
          // data: valor que parece data, ou id com dt/data/emi
          for (const c of candidates) {
            if (c.inp === hourEl) continue;
            if (c.d === true || /\\b(dt|data|emi|emit)/.test(c.idn)) {
              dateEl = dateEl || c.inp;
            }
          }
          // fallback: primeiro não-hora / não-junk; se sobrar um 'maybe' hour e um empty, empty=data
          if (!dateEl) {
            const nonHour = candidates.filter((c) => c.inp !== hourEl && c.h !== true);
            // se há um maybe-hour e um empty, empty é data
            const empties = nonHour.filter((c) => c.d === 'empty');
            const maybes = nonHour.filter((c) => c.h === 'maybe');
            if (empties.length && maybes.length && !hourEl) {
              hourEl = maybes[0].inp;
              dateEl = empties[0].inp;
            } else if (empties.length) {
              dateEl = empties[0].inp;
            } else if (nonHour.length) {
              dateEl = nonHour[0].inp;
            }
          }
          if (!hourEl) {
            for (const c of candidates) {
              if (c.inp === dateEl) continue;
              if (c.h === 'maybe' || c.h === true) { hourEl = c.inp; break; }
            }
          }

          // Varredura global se ainda falhou (ids típicos)
          if (!dateEl) {
            for (const id of ['t_dt_emi', 't_emitidos', 't_dt_ate', 't_data_ate', 'dt_emi', 'data_emi']) {
              const el = document.getElementById(id);
              if (el && !looksJunk(el.value, id)) { dateEl = el; break; }
            }
          }

          let dateOk = false;
          let dateBefore = '';
          let dateAfter = '';
          let hourKept = hourEl ? String(hourEl.value || '') : '';
          if (dateEl) {
            dateBefore = String(dateEl.value || '');
            // Nunca escrever em campo de siglas
            const idn = norm((dateEl.id || '') + ' ' + (dateEl.name || ''));
            if (looksJunk(dateBefore, idn) || /sigla|familia/.test(idn)) {
              dateEl = null;
            }
          }
          if (dateEl) {
            dateBefore = String(dateEl.value || '');
            let newVal = dataYy;
            if (dateBefore.includes('/')) {
              newVal = dataYy.slice(0, 2) + '/' + dataYy.slice(2, 4) + '/' + dataYy.slice(4, 6);
            }
            // se data+hora no mesmo campo, preserva hora
            const mHour = dateBefore.match(/(\\d{1,2}:\\d{2}(?::\\d{2})?)/);
            if (mHour) {
              newVal = newVal + ' ' + mHour[1];
              hourKept = mHour[1];
            }
            dateOk = setVal(dateEl, newVal);
            dateAfter = String(dateEl.value || '');
            // reafirma hora separada (não altera)
            if (hourEl && hourKept) setVal(hourEl, hourKept);
          }

          return {
            excelOk,
            excelId,
            dateOk,
            labelHit,
            dateId: dateEl ? (dateEl.id || dateEl.name || '') : '',
            hourId: hourEl ? (hourEl.id || hourEl.name || '') : '',
            dateBefore,
            dateAfter,
            hourKept,
            url: location.pathname || '',
            pool: uniq.map((i) => (i.id || i.name || '')).slice(0, 12),
          };
        }""",
        data_ddmmyy,
    )
    if not (result or {}).get("excelOk"):
        status("[019] forçando Excel=S em #relatorio_excel")
        popup.evaluate(
            """() => {
              const el = document.getElementById('relatorio_excel');
              if (!el) return false;
              el.value = 'S';
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return true;
            }"""
        )
    _safe_wait(popup, 300)
    return result or {}


def _gerar_download_019(popup, dest_name: str, status: StatusCallback) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOWNLOAD_DIR / dest_name

    def _click_gerar() -> str:
        return popup.evaluate(
            """() => {
              // Preferir botões/links explícitos antes de ajax genérico
              const nodes = Array.from(document.querySelectorAll(
                'a, button, input[type=button], input[type=submit], img[onclick], span[onclick]'
              ));
              for (const a of nodes) {
                const oc = (a.getAttribute('onclick') || '');
                const t = ((a.innerText || a.value || a.title || '') + ' ' + oc).toLowerCase();
                if (/ajaxenvia\\(['\"]rel/i.test(oc) || /gerar|excel|relat|confirmar|\\u25ba|\\u25b6/.test(t)) {
                  a.click();
                  return 'click:' + (a.id || a.innerText || oc).slice(0, 60);
                }
              }
              const btnIds = ['btn_env_periodo','btn_gerar','btn_ok','act_rel','40','btn_rel'];
              for (const id of btnIds) {
                const el = document.getElementById(id);
                if (el) { el.click(); return 'id:' + id; }
              }
              if (typeof ajaxEnvia === 'function') {
                for (const code of ['REL', 'REL2', 'E1', 'EXCEL', 'GER']) {
                  try { ajaxEnvia(code, 0); return 'ajax:' + code; } catch (e) {}
                  try { ajaxEnvia(code, 1); return 'ajax:' + code + ':1'; } catch (e2) {}
                }
              }
              return '';
            }"""
        )

    before = {p.resolve() for p in DOWNLOAD_DIR.glob("*") if p.is_file()}
    try:
        with popup.expect_download(timeout=120000) as di:
            clicked = _click_gerar()
            if not clicked:
                raise RuntimeError("019: botão gerar não encontrado")
            status(f"[019] gerar → {clicked}")
        download = di.value
    except Exception as err:
        status(f"[019] expect_download: {err} — varrendo pasta…")
        _safe_wait(popup, 3000)
        candid = _latest_new_download(DOWNLOAD_DIR, before)
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


def _latest_new_download(folder: Path, before: set[Path]) -> Path | None:
    if not folder.is_dir():
        return None
    cands = []
    for pat in ("*.xlsx", "*.xls", "*.csv", "*.sswweb"):
        for p in folder.glob(pat):
            if p.resolve() in before:
                continue
            cands.append(p)
    if not cands:
        # fallback: mais recente qualquer
        allc = []
        for pat in ("*.xlsx", "*.xls", "*.csv", "*.sswweb"):
            allc.extend(folder.glob(pat))
        if not allc:
            return None
        allc.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return allc[0]
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]
