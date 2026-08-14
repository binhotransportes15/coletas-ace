"""Download SSW 081 — CTRCs disponíveis para entrega (Sem saída).

Regras ACE:
  Trânsito c/ previsão chegada até = amanhã (NÃO altera a hora)
  Excel = S (relatorio_excel quando existir)
  Opção 1: Relacionar as entregas, sem roteirizar
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

SSW_081_PATH = "/bin/ssw0052"  # 081 - CTRCs disponíveis p/ entrega (reciclagem)
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
    "0052",
    "roteir",
    "relatorio_excel",
    "btn_envia",
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
    programa = SSW_081_PATH

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
                programa = (popup.url or "").split("?")[0]
            except Exception:
                url = ""
            # Se blank ou sem marcadores de entrega, tenta path conhecido
            pronto = False
            try:
                pronto = client._popup_pronta(popup, SSW_081_MARKERS)
            except Exception:
                pronto = False
            if "blank" in url or not pronto:
                status(f"[081] navegando {SSW_081_PATH}…")
                popup.goto(
                    f"https://sistema.ssw.inf.br{SSW_081_PATH}",
                    wait_until="domcontentloaded",
                )
                _safe_wait(popup, 800)
                try:
                    programa = (popup.url or "").split("?")[0]
                except Exception:
                    pass

            status("[081] preenchendo…")
            filled = _preencher_081(popup, data_ddmmyy=data_ddmmyy, on_status=status)
            status(f"[081] form {filled}")
            if not (filled or {}).get("dateOk"):
                raise RuntimeError(
                    "081: não achei o campo de data 'previsão chegada até' "
                    f"(form={filled})"
                )

            dest_name = f"reciclagem_081_{ts}.xlsx"
            status("[081] opção 1 + gerar Excel…")
            path = _baixar_081_opcao1(
                popup,
                context=context,
                dest_name=dest_name,
                status=status,
            )
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
        "programa": programa,
    }


def _preencher_081(popup, *, data_ddmmyy: str, on_status: StatusCallback | None = None) -> dict[str, Any]:
    """Previsão chegada até = amanhã (hora intacta) + Excel = S."""
    status = on_status or _noop
    _safe_wait(popup, 500)
    try:
        popup.locator("#relatorio_excel, #t_excel, input").first.wait_for(
            state="attached", timeout=12000
        )
    except Exception:
        status("[081] aviso: formulário ainda não estável")

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

          // IDs reais observados na tela 081 (ssw0052)
          // "até" → preferir fim; senão ini
          const dateIds = [
            'data_prev_ent_fim', 'data_prev_fim', 'data_prev_ate',
            'data_prev_ent_ini', 'data_prev_ent', 'data_prev_man',
          ];
          const hourIds = [
            'hora_prev_man', 'hora_prev_ent', 'hora_prev_tar',
            'hora_prev_ent_ini', 'hora_prev_ent_fim',
          ];

          let dateEl = null;
          for (const id of dateIds) {
            const el = document.getElementById(id);
            if (el) { dateEl = el; break; }
          }
          let hourEl = null;
          for (const id of hourIds) {
            const el = document.getElementById(id);
            if (el) { hourEl = el; break; }
          }

          // Fallback por rótulo se IDs mudarem
          let labelHit = '';
          if (!dateEl) {
            const labels = Array.from(document.querySelectorAll('td, label, span, b, font, div'));
            for (const lab of labels) {
              const t = norm(lab.innerText || lab.textContent || '');
              if (!t || t.length > 140) continue;
              const hit =
                (t.includes('transito') && t.includes('previs')) ||
                (t.includes('previs') && t.includes('chegad')) ||
                (t.includes('chegada') && t.includes('ate'));
              if (!hit) continue;
              labelHit = t.slice(0, 90);
              const row = lab.closest('tr') || lab.parentElement;
              const pool = row ? Array.from(row.querySelectorAll('input')) : [];
              for (const inp of pool) {
                const idn = norm((inp.id || '') + ' ' + (inp.name || ''));
                const v = String(inp.value || '');
                if (/sigla|familia|setor|subcontrat/.test(idn)) continue;
                if (/^\\d{1,2}:\\d{2}/.test(v) || /hora|hr/.test(idn)) {
                  hourEl = hourEl || inp;
                  continue;
                }
                if (/data|dt|prev|cheg/.test(idn) || !v || /^\\d{4,8}$/.test(v.replace(/\\D/g,''))) {
                  dateEl = dateEl || inp;
                }
              }
              break;
            }
          } else {
            labelHit = 'ids:data_prev_ent_*';
          }

          let dateOk = false;
          let dateBefore = '';
          let dateAfter = '';
          let hourKept = hourEl ? String(hourEl.value || '') : '';
          if (dateEl) {
            dateBefore = String(dateEl.value || '');
            let newVal = dataYy;
            if (dateBefore.includes('/')) {
              newVal = dataYy.slice(0, 2) + '/' + dataYy.slice(2, 4) + '/' + dataYy.slice(4, 6);
            }
            dateOk = setVal(dateEl, newVal);
            dateAfter = String(dateEl.value || '');
            // NÃO altera hora
            if (hourEl && hourKept) setVal(hourEl, hourKept);
          }

          // Se existir fim separado e só preenchemos ini, espelha amanhã no fim também
          const fim = document.getElementById('data_prev_ent_fim');
          if (fim && dateEl && fim !== dateEl) {
            setVal(fim, dateAfter || dataYy);
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
          };
        }""",
        data_ddmmyy,
    )
    if not (result or {}).get("excelOk"):
        status("[081] forçando Excel=S")
        popup.evaluate(
            """() => {
              for (const id of ['relatorio_excel','t_excel','arquivo','t_arq']) {
                const el = document.getElementById(id);
                if (!el) continue;
                el.value = 'S';
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
              return false;
            }"""
        )
    _safe_wait(popup, 300)
    return result or {}


def _baixar_081_opcao1(popup, *, context, dest_name: str, status: StatusCallback) -> Path:
    """081: #btn_envia = opção 1 = ajaxEnvia('ENV',0).

    O download vem no CONTEXT (igual coleta 50), não em popup.expect_download —
    por isso ficava minutos parado.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOWNLOAD_DIR / dest_name
    before = {p.resolve() for p in DOWNLOAD_DIR.glob("*") if p.is_file()}
    before_mtime = {p.name: p.stat().st_mtime for p in DOWNLOAD_DIR.glob("*") if p.is_file()}

    def _trigger() -> str:
        # Confirmado no CRT: btn_envia onclick = ajaxEnvia('ENV', 0)
        return str(
            popup.evaluate(
                """() => {
                  if (typeof ajaxEnvia === 'function') {
                    try { ajaxEnvia('ENV', 0); return 'ajax:ENV'; } catch (e) {}
                  }
                  const btn = document.getElementById('btn_envia');
                  if (btn) { btn.click(); return 'btn_envia'; }
                  const norm = (s) => String(s || '').toLowerCase()
                    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/\\xa0/g, ' ');
                  for (const n of Array.from(document.querySelectorAll('a, button, span, font'))) {
                    const t = norm(n.innerText || n.textContent || '');
                    const oc = n.getAttribute('onclick') || '';
                    if (/ajaxenvia\\(['\"]env/i.test(oc)) {
                      n.click();
                      return 'click:ENV';
                    }
                    if (/1\\s*[.\\-)].*relacionar|sem roteirizar/.test(t)) {
                      n.click();
                      return 'opcao1:' + t.slice(0, 40);
                    }
                  }
                  return '';
                }"""
            )
            or ""
        )

    def _poll(wait_s: float) -> Path | None:
        deadline = time.time() + wait_s
        while time.time() < deadline:
            for p in DOWNLOAD_DIR.glob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in {".xlsx", ".xls", ".csv", ".sswweb"}:
                    continue
                mtime = p.stat().st_mtime
                if p.resolve() in before and mtime <= before_mtime.get(p.name, 0) + 0.2:
                    continue
                if p.stat().st_size < 32:
                    continue
                out = dest.with_suffix(p.suffix.lower())
                try:
                    if p.resolve() != out.resolve():
                        if out.exists():
                            out.unlink()
                        p.replace(out)
                    return out
                except Exception:
                    return p
            time.sleep(0.4)
        return None

    def _save(download) -> Path:
        suggested = (download.suggested_filename or "").lower()
        out = dest
        if suggested.endswith(".csv"):
            out = dest.with_suffix(".csv")
        elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
            out = dest.with_suffix(".xls")
        elif suggested.endswith(".sswweb"):
            out = dest.with_suffix(".sswweb")
        elif suggested.endswith(".xlsx"):
            out = dest.with_suffix(".xlsx")
        download.save_as(str(out))
        return out

    status("[081] opção 1 = ENV → download no context")
    try:
        with context.expect_event("download", timeout=90000) as di:
            how = _trigger()
            if not how:
                raise RuntimeError("081: btn_envia / ENV não encontrado")
            status(f"[081] gerar → {how} (aguardando arquivo…)")
        path = _save(di.value)
        status(f"[081] download OK · {path.name}")
        return path
    except Exception as err:
        status(f"[081] sem evento download ({err}) — poll/aba…")
        got = _poll(3.0)
        if got:
            status(f"[081] arquivo no disco · {got.name}")
            return got

    pages_before = list(context.pages)
    new_page = None
    try:
        with context.expect_page(timeout=12000) as pi:
            how = _trigger()
            status(f"[081] re-gerar(aba) → {how}")
        new_page = pi.value
    except Exception:
        after = [p for p in context.pages if p not in pages_before]
        if after:
            new_page = after[-1]

    if new_page is not None:
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        try:
            with context.expect_event("download", timeout=30000) as di:
                # se a aba já disparou, só espera; senão tenta ENV de novo na aba
                try:
                    new_page.evaluate(
                        """() => {
                          if (typeof ajaxEnvia === 'function') {
                            try { ajaxEnvia('ENV', 0); return true; } catch (e) {}
                          }
                          const b = document.getElementById('btn_envia');
                          if (b) { b.click(); return true; }
                          const a = document.querySelector(
                            'a[href*=\".xls\"], a[href*=\".csv\"], a[href*=\".sswweb\"], a[download]'
                          );
                          if (a) { a.click(); return true; }
                          return false;
                        }"""
                    )
                except Exception:
                    pass
            path = _save(di.value)
            try:
                new_page.close()
            except Exception:
                pass
            return path
        except Exception:
            try:
                new_page.close()
            except Exception:
                pass

    status("[081] última varredura na pasta de downloads…")
    got = _poll(10.0)
    if got:
        status(f"[081] arquivo no disco · {got.name}")
        return got
    raise RuntimeError("081: timeout sem download (ENV)")


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
        allc = []
        for pat in ("*.xlsx", "*.xls", "*.csv", "*.sswweb"):
            allc.extend(folder.glob(pat))
        if not allc:
            return None
        allc.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return allc[0]
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]
