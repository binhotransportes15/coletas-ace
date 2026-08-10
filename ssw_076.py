"""Download SSW 076 — Demonstrativo de remuneração (frete coleta/entrega por carro).

Sempre gera com operação R. Usa placas vindas do 073.
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

SSW_076_MARKERS = (
    "076",
    "remuner",
    "demonstrativo",
    "placa",
    "frete",
    "arquivo excel",
    "periodo",
)


def _noop(_: str) -> None:
    return None


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def download_reports_076(
    *,
    placas: list[str] | tuple[str, ...] | None = None,
    period: tuple[str, str] | None = None,
    operacao: str = "R",
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    # sessão existente (1 login compartilhado com o 073)
    client: AceSswClient | None = None,
    context=None,
    page=None,
) -> dict[str, Any]:
    """
    Gera 076 (operação R). Se `page`/`client` forem passados, reusa a sessão
    (sem novo login). Senão abre browser próprio.
    """
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)
    plate_list = [str(p).strip().upper() for p in (placas or []) if str(p).strip()]
    runs = plate_list or [""]

    ini_ddmm, fim_ddmm = period or periodo_mes_ate_hoje()
    ini = to_ssw_ddmmyy(ini_ddmm)
    fim = to_ssw_ddmmyy(fim_ddmm)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    reuse = page is not None and context is not None and client is not None
    own_client = client or AceSswClient(
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

    paths: list[str] = []
    errors: dict[str, str] = {}
    status(
        f"SSW 76 | op={operacao} | {ini}-{fim} | {len(plate_list) or 'todas'} placa(s)"
        + (" · sessão reusada" if reuse else "")
    )

    def _run(sess_client, sess_context, sess_page) -> None:
        nonlocal paths, errors
        popup = None
        try:
            status("[76] abrindo opção 76 (lote)…")
            popup = _reopen_76(sess_client, sess_page, popup)
            _preencher_76(popup, ini=ini, fim=fim, operacao=operacao, placa="", on_status=status)
            dest = f"contratacao_076_ALL_{ts}.sswweb"
            path = _gerar_download_76(sess_client, sess_context, sess_page, popup, dest, "ALL", status)
            paths.append(str(path))
            status(f"[76/ALL] OK {path.name}")
        except Exception as batch_err:  # noqa: BLE001
            status(f"[76] lote falhou ({batch_err}); tentando por placa…")
            for idx, placa in enumerate(runs[:40], start=1):
                key = placa or "ALL"
                try:
                    status(f"[76/{key}] ({idx}) abrindo…")
                    popup = _reopen_76(sess_client, sess_page, popup)
                    _preencher_76(
                        popup, ini=ini, fim=fim, operacao=operacao, placa=placa, on_status=status
                    )
                    dest = f"contratacao_076_{key or 'ALL'}_{ts}.sswweb"
                    path = _gerar_download_76(
                        sess_client, sess_context, sess_page, popup, dest, key, status
                    )
                    paths.append(str(path))
                except Exception as err:  # noqa: BLE001
                    errors[key] = str(err)
                    status(f"[76/{key}] FALHOU: {err}")
        try:
            if popup is not None and not popup.is_closed():
                popup.close()
        except Exception:
            pass

    if reuse:
        _run(own_client, context, page)
    else:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
            ctx = browser.new_context(accept_downloads=True)
            pg = ctx.new_page()
            pg.set_default_timeout(60000)
            pg.on("dialog", lambda d: d.accept())
            ctx.on("page", lambda p2: p2.on("dialog", lambda d: d.accept()))
            try:
                own_client._login(pg)
                own_client._ensure_unit(pg)
                own_client._patch_blank_popup_form(pg)
                _run(own_client, ctx, pg)
            finally:
                browser.close()

    if not paths and errors:
        raise RuntimeError("076 falhou: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))

    return {
        "ok": bool(paths),
        "files": paths,
        "errors": errors,
        "period": (ini_ddmm, fim_ddmm),
        "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
        "operacao": operacao,
        "placas": plate_list,
    }


def _reopen_76(client, page, popup):
    try:
        if popup is not None and not popup.is_closed():
            popup.close()
    except Exception:
        pass
    return client._open_menu_option(page, "76", markers=SSW_076_MARKERS)


def _preencher_76(popup, *, ini: str, fim: str, operacao: str, placa: str, on_status) -> None:
    status = on_status
    popup.wait_for_timeout(400)
    filled = popup.evaluate(
        """({ ini, fim, operacao, placa }) => {
          const norm = (s) => (s || '').toLowerCase().normalize('NFD')
            .replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim();
          const inputs = Array.from(document.querySelectorAll('input[type=text], input:not([type])'));
          const near = (el) => {
            let t = '';
            if (el.previousElementSibling) t = el.previousElementSibling.innerText || '';
            if (!t && el.parentElement) t = el.parentElement.innerText || '';
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
            el.focus(); el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          const ddmmyy = inputs.filter((el) => Number(el.maxLength) === 6 || Number(el.size) === 6);
          let okIni = false, okFim = false;
          if (ddmmyy.length >= 2) {
            okIni = set(ddmmyy[0], ini);
            okFim = set(ddmmyy[1], fim);
          }
          const okOp = set(byHint(['operacao', 'opera', 'tipo']), operacao);
          const okPlaca = placa ? set(byHint(['placa', 'cavalo']), placa) : true;
          return { okIni, okFim, okOp, okPlaca, n: inputs.length };
        }""",
        {"ini": ini, "fim": fim, "operacao": operacao, "placa": placa},
    )
    status(f"[76] form {filled}")
    if not filled.get("okOp"):
        # ainda tenta ids comuns
        for cand in ("5", "6", "7", "8"):
            try:
                loc = popup.locator(f'[id="{cand}"]')
                if loc.count():
                    loc.first.fill(operacao)
                    status(f"[76] operação via #{cand}={operacao}")
                    break
            except Exception:
                continue
    popup.wait_for_timeout(200)


def _clicar_excel_76(popup) -> str:
    return popup.evaluate(
        """() => {
          const links = Array.from(document.querySelectorAll('a, button, input, img, span'));
          for (const a of links) {
            const t = ((a.innerText || a.textContent || a.alt || a.title || '') + '').toLowerCase();
            if (t.includes('arquivo excel') || t.includes('excel')) { a.click(); return 'excel'; }
          }
          if (typeof ajaxEnvia === 'function') {
            try { ajaxEnvia('EXC', 0); return 'ajax-EXC'; } catch (_) {}
            try { ajaxEnvia('ENV', 0); return 'ajax-ENV'; } catch (_) {}
            try { ajaxEnvia('REL', 0); return 'ajax-REL'; } catch (_) {}
          }
          return '';
        }"""
    )


def _gerar_download_76(client, context, page, popup, dest_name: str, key: str, status) -> Path:
    try:
        with context.expect_event("download", timeout=25000) as di:
            clicked = _clicar_excel_76(popup)
            if not clicked:
                raise RuntimeError("076: Arquivo Excel não encontrado")
            status(f"[76/{key}] clique={clicked}")
        return client._save_download(di.value, dest_name)
    except Exception as err:  # noqa: BLE001
        status(f"[76/{key}] download direto falhou ({err}); fila…")
        # reusa lógica simples de fila
        from ssw_073 import _baixar_via_fila_73

        return _baixar_via_fila_73(client, context, page, popup, dest_name, key, status)
