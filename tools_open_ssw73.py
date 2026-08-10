"""Abre SSW visível · login · opção 73 (formulário preenchido) · fica aberto.

Uso: py -3 tools_open_ssw73.py
Ctrl+C no terminal fecha.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from config import load_credentials, load_settings
from dates import periodo_mes_ate_hoje, to_ssw_ddmmyy
from ssw_073 import JOBS_073, _preencher_73, _reopen_73
from ssw_client import AceSswClient


def _ensure_playwright_path() -> None:
    """Força o Chromium do usuário (ignora cache sandbox do Cursor)."""
    local = os.environ.get("LOCALAPPDATA") or ""
    if not local:
        return
    target = Path(local) / "ms-playwright"
    chrome = target / "chromium-1228" / "chrome-win64" / "chrome.exe"
    if chrome.is_file() or target.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(target)


def main() -> None:
    _ensure_playwright_path()
    creds = load_credentials()
    cfg = load_settings()
    ini_ui, fim_ui = periodo_mes_ate_hoje()
    ini, fim = to_ssw_ddmmyy(ini_ui), to_ssw_ddmmyy(fim_ui)
    job = JOBS_073[0]  # F + Tipo A

    print(f"Abrindo SSW · 73 · período {ini}-{fim} (mês) · prop={job['propriedade']} tipo={job['tipo']} · SPO")
    print("Deixe esta janela aberta — vamos percorrer Excel / fila juntos.")
    print("Ctrl+C no terminal para fechar.\n")

    client = AceSswClient(
        ini_ui,
        fim_ui,
        credentials=creds,
        settings=cfg,
        keep_open=True,
        headless=False,
        on_status=print,
    )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=60)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))

        client._login(page)
        client._ensure_unit(page)
        client._patch_blank_popup_form(page)

        popup = _reopen_73(client, page, None)
        _preencher_73(
            popup,
            ini=ini,
            fim=fim,
            tipo=job["tipo"],
            unidade="SPO",
            propriedade=job["propriedade"],
            operacao="T",
            considerar="T",
            on_status=print,
            job_key=job["key"],
        )
        try:
            popup.bring_to_front()
        except Exception:
            pass

        print("\n=== Pronto: tela 073 preenchida (F + Tipo A + SPO + mês até hoje) ===")
        print("Passos sugeridos no SSW:")
        print("  1) Confira os campos")
        print("  2) Clique em Arquivo Excel")
        print("  3) Veja se vai pra fila / Ver fila / 156")
        print("  4) Me diga o que aparece (URL, botões, DOW…)")
        print("\nAguardando… (Ctrl+C para sair)")

        try:
            while True:
                alive = False
                for pg in list(context.pages):
                    try:
                        if not pg.is_closed():
                            alive = True
                            pg.wait_for_timeout(1000)
                            break
                    except Exception:
                        continue
                if not alive:
                    break
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\nFechando…")
        finally:
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
