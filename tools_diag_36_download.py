"""
Diagnostico: preenche 36, clica REL2 e registra o que acontece
(download / dialog / nova pagina / timeout).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from config import CACHE_DIR, DOWNLOAD_DIR, load_credentials, load_settings
from dates import periodo_36_ontem_hoje, to_ssw_ddmmyy
from ssw_client import AceSswClient, login_unit, parse_coleta_units


def _p(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def main() -> int:
    unidade = (sys.argv[1] if len(sys.argv) > 1 else "SPO").strip().upper()
    creds = load_credentials()
    settings = load_settings()
    ini_ui, fim_ui = periodo_36_ontem_hoje()
    ini, fim = to_ssw_ddmmyy(ini_ui), to_ssw_ddmmyy(fim_ui)
    units = parse_coleta_units(creds.unit)
    if not unidade:
        unidade = login_unit(creds.unit) or (units[0] if units else "")

    out = CACHE_DIR / "diag_36_download.json"
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
        browser = p.chromium.launch(headless=False, slow_mo=80)
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
            "36",
            markers=("romaneio", "entrega", "periodo", "unidade", "excel", "0146", "36"),
        )
        popup.on("dialog", on_dialog)

        _p(f"Preenchendo un={unidade or 'TODAS'} periodo={ini}-{fim}")
        # Preenchimento estilo CyberMap (fill) + limpeza
        popup.locator("#t_excel").wait_for()
        for fid in (
            "t_sigla_rom",
            "t_nro_rom",
            "t_cod_barras_rom",
            "t_ciot",
            "t_ser_mdfe",
            "t_nro_mdfe",
            "t_placa_veic",
            "t_cpf_motorista",
        ):
            try:
                popup.locator(f"#{fid}").fill("")
            except Exception:
                pass
        popup.locator("#t_excel").fill("S")
        popup.locator("#t_unidade").fill(unidade or "")
        popup.locator("#t_dt_ini").fill(ini)
        popup.locator("#t_dt_fin").fill(fim)
        vals = popup.evaluate(
            """() => ({
              excel: (document.getElementById('t_excel')||{}).value,
              un: (document.getElementById('t_unidade')||{}).value,
              ini: (document.getElementById('t_dt_ini')||{}).value,
              fim: (document.getElementById('t_dt_fin')||{}).value,
            })"""
        )
        _p(f"Valores na tela: {vals}")
        events.append({"ts": datetime.now().isoformat(timespec="seconds"), "type": "filled", "values": vals})

        btn = popup.locator("#btn_env_periodo")
        _p(f"Botao #btn_env_periodo count={btn.count()} visible={btn.first.is_visible() if btn.count() else False}")

        download_path = None
        err = None
        t0 = time.time()
        try:
            with popup.expect_download(timeout=120000) as download_info:
                _p("Clicando #btn_env_periodo ...")
                btn.first.click()
                _p("Clique feito; aguardando download no popup...")
            dl = download_info.value
            dest = DOWNLOAD_DIR / f"diag_36_{ini}_{fim}_{datetime.now():%H%M%S}_{dl.suggested_filename or 'file.sswweb'}"
            dl.save_as(str(dest))
            download_path = str(dest)
            _p(f"OK download via expect_download: {dest} ({dest.stat().st_size} bytes)")
        except Exception as e:  # noqa: BLE001
            err = str(e)
            _p(f"expect_download falhou em {time.time()-t0:.1f}s: {e}")
            # fallback: se context.on('download') pegou algo
            for ev in events:
                if ev.get("type") == "download":
                    _p(f"Houve evento download no context: {ev}")

        # snapshot pos-clique
        try:
            snap = {
                "url": popup.url,
                "pages": [pg.url for pg in context.pages],
                "bodyHint": popup.evaluate(
                    "() => (document.body && document.body.innerText || '').slice(0, 800)"
                ),
            }
        except Exception as e:  # noqa: BLE001
            snap = {"error": str(e)}
        events.append({"ts": datetime.now().isoformat(timespec="seconds"), "type": "after", "snap": snap})
        _p(f"Paginas abertas: {snap.get('pages')}")
        _p(f"Body hint: {str(snap.get('bodyHint') or '')[:300]}")

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

        page.wait_for_timeout(3000)
        context.close()
        browser.close()
        return 0 if download_path else 1


if __name__ == "__main__":
    raise SystemExit(main())
