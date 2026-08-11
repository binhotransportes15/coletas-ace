"""
Diagnostico: preenche 50 (ssw0157), clica #21 e registra
download / dialog / nova pagina / timeout / corpo da tela.
"""

from __future__ import annotations

import _root  # noqa: F401

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from config import (
    CACHE_DIR,
    DOWNLOAD_DIR,
    load_credentials,
    load_settings,
    parse_coleta_units,
)
from dates import periodo_50_coleta_hoje, to_ssw_ddmmyy
from ssw_client import AceSswClient


def _p(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def main() -> int:
    unidade = (sys.argv[1] if len(sys.argv) > 1 else "").strip().upper()
    creds = load_credentials()
    settings = load_settings()
    ini_ui, fim_ui = periodo_50_coleta_hoje()
    ini, fim = to_ssw_ddmmyy(ini_ui), to_ssw_ddmmyy(fim_ui)
    units = parse_coleta_units(creds.unit)
    if not unidade:
        unidade = (units[0] if units else "") or ""

    out = CACHE_DIR / "diag_50_download.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    events: list[dict] = []
    client = AceSswClient(
        ini_ui,
        fim_ui,
        credentials=creds,
        settings=settings,
        keep_open=False,
        headless=False,
        on_status=_p,
        clean_downloads=False,
    )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=60)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(30000)

        def on_dialog(d):
            events.append(
                {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "type": "dialog",
                    "dialog_type": d.type,
                    "message": d.message,
                }
            )
            _p(f"DIALOG [{d.type}]: {d.message}")
            try:
                d.accept()
            except Exception:
                pass

        def on_page(pg):
            events.append(
                {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "type": "newpage",
                    "url": pg.url,
                }
            )
            _p(f"NEW PAGE: {pg.url}")
            pg.on("dialog", on_dialog)

        def on_download(dl):
            events.append(
                {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "type": "download",
                    "suggested": dl.suggested_filename,
                    "url": dl.url,
                }
            )
            _p(f"DOWNLOAD EVENT: {dl.suggested_filename} | {dl.url}")

        context.on("page", on_page)
        context.on("download", on_download)
        page.on("dialog", on_dialog)

        client._login(page)
        client._ensure_unit(page)
        client._patch_blank_popup_fix(page)
        popup = client._open_menu_option(
            page,
            "50",
            markers=("coleta", "050", "periodo", "ssw0157", "relacao", "cadastr"),
        )
        popup.on("dialog", on_dialog)

        # snapshot da tela antes
        form_info = popup.evaluate(
            """() => {
              const inputs = Array.from(document.querySelectorAll('input,button,a,img'))
                .slice(0, 80)
                .map(el => ({
                  tag: el.tagName,
                  id: el.id || '',
                  name: el.name || '',
                  type: el.type || '',
                  value: (el.value || '').slice(0, 40),
                  onclick: String(el.getAttribute('onclick') || el.onclick || '').slice(0, 120),
                  title: (el.title || el.alt || el.textContent || '').trim().slice(0, 60),
                }));
              const ajax = typeof ajaxEnvia === 'function' ? String(ajaxEnvia).slice(0, 1500) : null;
              const body = (document.body && document.body.innerText || '').slice(0, 1200);
              return {url: location.href, inputs, ajax, body};
            }"""
        )
        events.append({"ts": datetime.now().isoformat(timespec="seconds"), "type": "form", "form": form_info})
        _p(f"URL: {form_info.get('url')}")
        _p(f"Body hint: {str(form_info.get('body') or '')[:400]}")
        for inp in form_info.get("inputs") or []:
            if inp.get("id") in ("4", "5", "6", "7", "2", "3", "8", "21") or "ajax" in (inp.get("onclick") or "").lower() or inp.get("tag") == "BUTTON":
                _p(f"  el id={inp.get('id')} type={inp.get('type')} val={inp.get('value')!r} onclick={inp.get('onclick')!r} title={inp.get('title')!r}")

        _p(f"Preenchendo un={unidade or 'TODAS'} periodo coleta={ini}-{fim}")
        popup.locator('[id="4"]').wait_for()
        client._preencher_periodo_coleta_50(popup, unidade=unidade)
        vals = popup.evaluate(
            """() => {
              const g = id => (document.getElementById(String(id)) || {}).value || '';
              return {f2:g(2),f3:g(3),f4:g(4),f5:g(5),f6:g(6),f7:g(7),f8:g(8),f21:!!document.getElementById('21')};
            }"""
        )
        _p(f"Valores na tela: {vals}")
        events.append({"ts": datetime.now().isoformat(timespec="seconds"), "type": "filled", "values": vals})
        if not (vals.get("f6") and vals.get("f7")):
            _p("AVISO: periodo de coleta (#6/#7) vazio — mapeamento errado")

        download_path = None
        err = None
        t0 = time.time()
        try:
            with context.expect_event("download", timeout=180000) as download_info:
                _p("Gerando via ajaxEnvia('ENV',0) / #21 ...")
                client._clicar_gerar_50(popup)
                _p("Clique feito; aguardando download no context...")
            dl = download_info.value
            dest = DOWNLOAD_DIR / (
                f"diag_50_{ini}_{fim}_{datetime.now():%H%M%S}_"
                f"{dl.suggested_filename or 'file.sswweb'}"
            )
            dl.save_as(str(dest))
            download_path = str(dest)
            _p(f"OK download via expect_download: {dest} ({dest.stat().st_size} bytes)")
        except Exception as e:  # noqa: BLE001
            err = str(e)
            _p(f"expect_download falhou em {time.time()-t0:.1f}s: {e}")
            for ev in events:
                if ev.get("type") in ("download", "dialog"):
                    _p(f"Evento: {ev}")
            # snapshot pos-falha sem derrubar se pagina fechou
            try:
                if not popup.is_closed():
                    body = popup.evaluate(
                        "() => (document.body && document.body.innerText || '').slice(0, 1200)"
                    )
                    _p(f"Body apos falha: {body[:500]}")
                    events.append(
                        {
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "type": "after_fail",
                            "body": body,
                        }
                    )
            except Exception as se:  # noqa: BLE001
                _p(f"Snapshot falhou: {se}")

        try:
            snap = {
                "url": (popup.url if not popup.is_closed() else "closed"),
                "pages": [pg.url for pg in context.pages],
                "bodyHint": (
                    popup.evaluate(
                        "() => (document.body && document.body.innerText || '').slice(0, 1200)"
                    )
                    if not popup.is_closed()
                    else ""
                ),
            }
        except Exception as e:  # noqa: BLE001
            snap = {"error": str(e)}
        events.append({"ts": datetime.now().isoformat(timespec="seconds"), "type": "after", "snap": snap})
        _p(f"Paginas abertas: {snap.get('pages')}")
        _p(f"Body apos clique: {str(snap.get('bodyHint') or '')[:500]}")

        payload = {
            "ok": bool(download_path),
            "path": download_path,
            "error": err,
            "elapsed_s": round(time.time() - t0, 1),
            "unidade": unidade,
            "periodo": [ini, fim],
            "events": events,
        }
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        _p(f"Diag salvo: {out}")

        try:
            page.wait_for_timeout(1500)
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
        return 0 if download_path else 1


if __name__ == "__main__":
    raise SystemExit(main())
