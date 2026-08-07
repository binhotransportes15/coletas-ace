"""Download SSW 031 (ssw0495) — CTRCs por código de ocorrência → Excel."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_mes_corrente, to_ssw_ddmmyy
from ocorrencias_pendencia import OCORR_PENDENCIA_CODES
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def download_reports_31(
    *,
    codes: tuple[str, ...] | list[str] | None = None,
    period: tuple[str, str] | None = None,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
) -> dict[str, Any]:
    """
    Abre opção 31 e, para cada código, gera Excel (Arquivo excel=S)
    com Data da ocorrência = mês corrente (ou período informado).
    """
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)
    code_list = [str(c).strip() for c in (codes or OCORR_PENDENCIA_CODES) if str(c).strip()]
    if not code_list:
        raise RuntimeError("31: nenhum código de ocorrência")

    ini_ddmm, fim_ddmm = period or periodo_mes_corrente()
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
        f"SSW 31 | {len(code_list)} código(s) um a um | ocorrência {ini}-{fim} | excel=S"
    )
    status("códigos: " + ", ".join(code_list))

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
            client._patch_blank_popup_fix(page)

            # SSW 0495: cada código precisa de uma entrada nova na opção 31
            # (reaproveitar o mesmo popup falha / mistura relatório).
            for idx, code in enumerate(code_list, start=1):
                try:
                    status(f"[31/{code}] ({idx}/{len(code_list)}) abrindo opção 31…")
                    popup = _reopen_31(client, page, popup)
                    status(f"[31/{code}] preenchendo…")
                    _preencher_31(popup, ini=ini, fim=fim, codigo=code)
                    dest_name = f"pendencia_31_{code}_{ts}.xlsx"
                    path = _gerar_download_31(
                        client, context, page, popup, dest_name, code, status
                    )
                    paths[code] = str(path)
                    status(f"[31/{code}] OK {path.name} ({path.stat().st_size} bytes)")
                    page.wait_for_timeout(400)
                except Exception as err:  # noqa: BLE001
                    errors[code] = str(err)
                    status(f"[31/{code}] FALHOU: {err}")
                    try:
                        popup = _reopen_31(client, page, popup)
                    except Exception:
                        popup = None
            try:
                if popup is not None and not popup.is_closed():
                    popup.close()
            except Exception:
                pass
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    if not paths:
        raise RuntimeError(
            "31: nenhum Excel baixado. " + "; ".join(f"{k}:{v}" for k, v in errors.items())
        )
    return {
        "ok": True,
        "paths": paths,
        "errors": errors,
        "period": f"{ini}-{fim}",
        "codes": code_list,
        "download_dir": str(DOWNLOAD_DIR),
    }


def _open_31(client: AceSswClient, page):
    return client._open_menu_option(
        page,
        "31",
        markers=("ocorr", "ctrc", "excel", "pendenc", "31", "arquivo", "0495"),
    )


def _reopen_31(client: AceSswClient, page, popup=None):
    """Fecha a tela 31 anterior (se houver) e abre de novo para o próximo código."""
    if popup is not None:
        try:
            if not popup.is_closed():
                popup.close()
        except Exception:
            pass
        try:
            page.wait_for_timeout(350)
        except Exception:
            pass
    fresh = _open_31(client, page)
    try:
        fresh.on("dialog", lambda d: d.accept())
    except Exception:
        pass
    return fresh


def _preencher_31(popup, *, ini: str, fim: str, codigo: str) -> None:
    """ssw0495: #3/#4 ocorrência, #6 código, #11=T, #12=S. Emissão (#1/#2) vazia."""
    popup.locator('[id="3"]').wait_for(timeout=20000)
    values = popup.evaluate(
        """([ini, fim, codigo]) => {
          const set = (id, v) => {
            const el = document.getElementById(String(id));
            if (!el) return false;
            el.value = v;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          set('1', '');
          set('2', '');
          const okIni = set('3', ini);
          const okFim = set('4', fim);
          const okCod = set('6', String(codigo || '').slice(0, 2));
          const okSit = set('11', 'T');
          const okExcel = set('12', 'S');
          try {
            const el = document.getElementById('12');
            if (el) el.focus();
          } catch (e) {}
          return {
            ok: okIni && okFim && okCod && okSit && okExcel,
            ini: (document.getElementById('3') || {}).value || '',
            fim: (document.getElementById('4') || {}).value || '',
            codigo: (document.getElementById('6') || {}).value || '',
            situacao: (document.getElementById('11') || {}).value || '',
            excel: (document.getElementById('12') || {}).value || '',
          };
        }""",
        [ini, fim, str(codigo).strip()],
    )
    if not values or not values.get("ok"):
        raise RuntimeError(f"31: falha ao preencher: {values}")
    if str(values.get("excel") or "").upper() != "S":
        raise RuntimeError(f"31: excel não ficou S: {values}")
    popup.wait_for_timeout(200)


def _clicar_gerar_31(popup) -> None:
    """Play ► → ajaxEnvia('ENV', 0) (id=13)."""
    clicked = popup.evaluate(
        """() => {
          if (typeof ajaxEnvia === 'function') { ajaxEnvia('ENV', 0); return 'ajax'; }
          const a = document.getElementById('13');
          if (a) { a.click(); return '13'; }
          return '';
        }"""
    )
    if not clicked:
        raise RuntimeError("31: botão gerar (►) não encontrado")


def _gerar_download_31(client, context, page, popup, dest_name: str, code: str, status) -> Path:
    """Tenta download direto; se timeout, cai em Ver fila (ssw1440)."""
    try:
        with context.expect_event("download", timeout=90000) as di:
            _clicar_gerar_31(popup)
        download = di.value
        return _save_named(client, download, dest_name)
    except Exception as direct_err:  # noqa: BLE001
        status(f"[31/{code}] download direto falhou ({direct_err}); tentando Ver fila…")
        return _baixar_via_fila_31(client, context, page, popup, dest_name, code, status)


def _save_named(client, download, dest_name: str) -> Path:
    suggested = (download.suggested_filename or "").lower()
    name = dest_name
    if suggested.endswith(".csv"):
        name = Path(dest_name).with_suffix(".csv").name
    elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
        name = Path(dest_name).with_suffix(".xls").name
    elif suggested.endswith(".sswweb"):
        name = Path(dest_name).with_suffix(".sswweb").name
    return client._save_download(download, name)


def _baixar_via_fila_31(client, context, page, popup, dest_name: str, code: str, status) -> Path:
    """Abre Ver fila (ssw1440) e baixa o relatório 0495 mais recente."""
    # Disparar Ver fila a partir do popup 31 ou do menu
    fila = None
    try:
        with context.expect_page(timeout=15000) as pi:
            popup.evaluate(
                """() => {
                  if (typeof ajaxEnvia === 'function') {
                    ajaxEnvia('', 1, 'ssw1440');
                    return 'ajax';
                  }
                  const a = document.getElementById('15');
                  if (a) { a.click(); return '15'; }
                  return '';
                }"""
            )
        fila = pi.value
    except Exception:
        # talvez navegou no mesmo popup
        try:
            if "ssw1440" in (popup.url or ""):
                fila = popup
            else:
                page.evaluate(
                    """() => {
                      if (typeof ajaxEnvia === 'function') ajaxEnvia('', 1, 'ssw1440');
                    }"""
                )
                page.wait_for_timeout(1500)
                for pg in context.pages:
                    if "ssw1440" in (pg.url or ""):
                        fila = pg
                        break
        except Exception:
            pass
    if fila is None:
        raise RuntimeError("31: não abriu Ver fila (ssw1440)")

    fila.wait_for_load_state("domcontentloaded", timeout=30000)
    try:
        fila.on("dialog", lambda d: d.accept())
    except Exception:
        pass

    # Poll até achar link de download do 0495 / excel
    deadline = time.time() + 120
    last_hint = ""
    while time.time() < deadline:
        info = fila.evaluate(
            """() => {
              const text = (document.body && document.body.innerText || '').slice(0, 2500);
              const links = Array.from(document.querySelectorAll('a[onclick], a[href]'));
              const hits = links.map((a, i) => ({
                i,
                text: (a.textContent || '').trim().slice(0, 80),
                onclick: String(a.getAttribute('onclick') || '').slice(0, 160),
                href: String(a.getAttribute('href') || '').slice(0, 120),
              })).filter(x =>
                /DOW|download|ssw0495|0495|xlsx|excel|baixar/i.test(
                  x.onclick + ' ' + x.text + ' ' + x.href
                )
              );
              return { text, hits: hits.slice(0, 12), url: location.href };
            }"""
        )
        last_hint = str(info.get("text") or "")[:200]
        hits = info.get("hits") or []
        # Prefer DOW / 0495
        pick = None
        for h in hits:
            blob = f"{h.get('onclick','')} {h.get('text','')} {h.get('href','')}"
            if re.search(r"DOW|ssw0495|0495", blob, re.I):
                pick = h
                break
        if not pick and hits:
            pick = hits[0]
        if pick is not None:
            status(f"[31/{code}] fila: baixando via {pick.get('text') or pick.get('onclick')}")
            try:
                with context.expect_event("download", timeout=90000) as di:
                    fila.evaluate(
                        """(idx) => {
                          const links = Array.from(document.querySelectorAll('a[onclick], a[href]'));
                          const filtered = links.filter(a => {
                            const blob = String(a.getAttribute('onclick')||'') + ' ' +
                              (a.textContent||'') + ' ' + String(a.getAttribute('href')||'');
                            return /DOW|download|ssw0495|0495|xlsx|excel|baixar/i.test(blob);
                          });
                          const a = filtered[idx];
                          if (!a) return false;
                          a.click();
                          return true;
                        }""",
                        hits.index(pick) if pick in hits else 0,
                    )
                download = di.value
                path = _save_named(client, download, dest_name)
                try:
                    if fila != popup and not fila.is_closed():
                        fila.close()
                except Exception:
                    pass
                return path
            except Exception as err:  # noqa: BLE001
                status(f"[31/{code}] clique fila falhou: {err}")
        fila.wait_for_timeout(2500)

    raise RuntimeError(f"31: Ver fila sem download em 120s. Hint: {last_hint}")
