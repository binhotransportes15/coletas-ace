"""Diag rapido 50: testa 2 modos de periodo e captura corpo/dialogs apos ENV."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from config import CACHE_DIR, DOWNLOAD_DIR, load_credentials, load_settings, parse_coleta_units
from dates import periodo_50_coleta_hoje, to_ssw_ddmmyy
from ssw_client import AceSswClient


def _p(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def _snap(popup) -> dict:
    try:
        if popup.is_closed():
            return {"closed": True}
        return popup.evaluate(
            """() => ({
              url: location.href,
              body: (document.body && document.body.innerText || '').slice(0, 1500),
              vals: {
                f2:(document.getElementById('2')||{}).value||'',
                f3:(document.getElementById('3')||{}).value||'',
                f4:(document.getElementById('4')||{}).value||'',
                f5:(document.getElementById('5')||{}).value||'',
                f6:(document.getElementById('6')||{}).value||'',
                f7:(document.getElementById('7')||{}).value||'',
              }
            })"""
        )
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _fill(popup, mode: str, ini: str, fim: str, un: str) -> None:
    popup.evaluate(
        """([mode, ini, fim, un]) => {
          const set = (id, v) => {
            const el = document.getElementById(String(id));
            if (!el) return;
            el.value = v;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
          };
          set(2, un || 'SPO');
          set(3, 'A');
          if (mode === 'coleta') {
            set(4, ''); set(5, '');
            set(6, ini); set(7, fim);
          } else if (mode === 'cadastramento') {
            set(4, ini); set(5, fim);
            set(6, ''); set(7, '');
          } else { // ambos
            set(4, ini); set(5, fim);
            set(6, ini); set(7, fim);
          }
        }""",
        [mode, ini, fim, un],
    )


def _try_download(context, popup, client, mode: str, timeout_ms: int = 45000):
    events = []
    held = []

    def on_dialog(d):
        events.append({"type": "dialog", "msg": d.message, "dtype": d.type})
        _p(f"  DIALOG: {d.message}")
        try:
            d.accept()
        except Exception:
            pass

    def on_dl(d):
        held.append(d)
        events.append({"type": "download", "name": d.suggested_filename})
        _p(f"  DOWNLOAD: {d.suggested_filename}")

    context.on("download", on_dl)
    try:
        popup.on("dialog", on_dialog)
    except Exception:
        pass

    _p(f"== modo={mode} ==")
    _p(f"  before: {_snap(popup).get('vals')}")
    client._clicar_gerar_50(popup)

    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        if held:
            break
        # poll snapshots
        snap = _snap(popup)
        body = str(snap.get("body") or "")
        if body and ("fila" in body.lower() or "gerado" in body.lower() or "erro" in body.lower() or "aviso" in body.lower()):
            _p(f"  body hint @ {time.time()-t0:.1f}s: {body[:300]}")
            events.append({"t": round(time.time()-t0,1), "body": body[:800]})
        # new pages?
        urls = [p.url for p in context.pages]
        if len(urls) > 2:
            _p(f"  pages: {urls}")
        popup.wait_for_timeout(1000)

    path = None
    if held:
        dest = DOWNLOAD_DIR / f"diag_50_{mode}_{datetime.now():%H%M%S}_{held[0].suggested_filename or 'x.sswweb'}"
        held[0].save_as(str(dest))
        path = str(dest)
        _p(f"  OK {dest} ({dest.stat().st_size} bytes)")
    else:
        _p(f"  FALHOU sem download em {timeout_ms}ms")
        _p(f"  after: {_snap(popup)}")
        _p(f"  pages: {[p.url for p in context.pages]}")

    try:
        context.remove_listener("download", on_dl)
    except Exception:
        pass
    return {"mode": mode, "ok": bool(path), "path": path, "events": events, "elapsed": round(time.time()-t0,1)}


def main() -> int:
    creds = load_credentials()
    settings = load_settings()
    ini_ui, fim_ui = periodo_50_coleta_hoje()
    ini, fim = to_ssw_ddmmyy(ini_ui), to_ssw_ddmmyy(fim_ui)
    units = parse_coleta_units(creds.unit)
    un = (units[0] if units else "SPO") or "SPO"

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    client = AceSswClient(
        ini_ui, fim_ui,
        credentials=creds, settings=settings,
        keep_open=False, headless=False, on_status=_p, clean_downloads=False,
    )

    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=40)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))

        client._login(page)
        client._ensure_unit(page)
        client._patch_blank_popup_fix(page)

        for mode in ("cadastramento", "coleta", "ambos"):
            popup = client._open_menu_option(
                page, "50",
                markers=("coleta", "050", "periodo", "ssw0157", "relacao", "cadastr"),
            )
            try:
                popup.on("dialog", lambda d: d.accept())
            except Exception:
                pass
            popup.locator('[id="4"]').wait_for()
            _fill(popup, mode, ini, fim, un)
            popup.wait_for_timeout(400)
            results.append(_try_download(context, popup, client, mode, timeout_ms=50000))
            try:
                if not popup.is_closed():
                    popup.close()
            except Exception:
                pass
            page.wait_for_timeout(800)
            if results[-1]["ok"]:
                _p(f"SUCESSO no modo {mode} — parando testes")
                break

        out = CACHE_DIR / "diag_50_modes.json"
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        _p(f"Salvo: {out}")
        context.close()
        browser.close()
    return 0 if any(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
