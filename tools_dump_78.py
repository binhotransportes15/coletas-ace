"""Dump SSW opção 78 — captura texto da tela (estilo Ctrl+A) + HTML/campos."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")

from config import CACHE_DIR, SSW_78_PATH, ensure_dirs, load_credentials  # noqa: E402
from ssw_client import AceSswClient  # noqa: E402

OUT_DIR = CACHE_DIR
ensure_dirs()


def main() -> None:
    creds = load_credentials()
    client = AceSswClient(
        "030826",
        "030826",
        keep_open=False,
        headless=False,
        on_status=print,
        clean_downloads=False,
    )
    client.credentials.url = creds.url
    client.credentials.domain = creds.domain
    client.credentials.document = creds.document
    client.credentials.user = creds.user
    client.credentials.password = creds.password
    client.credentials.unit = creds.unit

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=80)
        context = browser.new_context(accept_downloads=False)
        page = context.new_page()
        page.set_default_timeout(60000)
        client._login(page)
        client._ensure_unit(page)
        patch = getattr(client, "_patch_blank_popup_forms", None) or getattr(
            client, "_patch_blank_popup_form", None
        )
        if callable(patch):
            patch(page)
        popup = client._open_menu_option(
            page,
            "78",
            markers=("78", "descarg", "cavalo", "carreta", "chegad", "manifesto", "1257"),
        )
        try:
            url = (popup.url or "").lower()
        except Exception:
            url = ""
        if "blank" in url or "ssw1257" not in url:
            popup.goto(
                f"https://sistema.ssw.inf.br{SSW_78_PATH}",
                wait_until="domcontentloaded",
            )
            popup.wait_for_timeout(1200)
        popup.wait_for_timeout(800)
        info = popup.evaluate(
            """() => ({
              url: location.href,
              title: document.title || '',
              bodyText: (document.body && document.body.innerText) || '',
              html: document.documentElement ? document.documentElement.outerHTML : '',
            })"""
        )
        (OUT_DIR / "dump_78.html").write_text(info.get("html") or "", encoding="utf-8", errors="replace")
        (OUT_DIR / "dump_78_full.txt").write_text(
            info.get("bodyText") or "", encoding="utf-8", errors="replace"
        )
        (OUT_DIR / "dump_78_fields.json").write_text(
            json.dumps(
                {"url": info.get("url"), "title": info.get("title"), "bodyText": info.get("bodyText")},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"Dump salvo em {OUT_DIR}")
        try:
            popup.close()
        except Exception:
            pass
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
