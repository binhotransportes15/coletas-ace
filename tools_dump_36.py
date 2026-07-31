"""Abre SSW opcao 36, dumpa campos/HTML e deixa o browser aberto para inspecao."""
from __future__ import annotations

import json

from config import CACHE_DIR, load_credentials, load_settings
from ssw_client import AceSswClient

OUT = CACHE_DIR / "dump_36_fields.json"
HTML_OUT = CACHE_DIR / "dump_36.html"


def main() -> None:
    creds = load_credentials()
    settings = load_settings()
    client = AceSswClient(
        "300726",
        "310726",
        credentials=creds,
        settings=settings,
        keep_open=True,
        headless=False,
        on_status=print,
    )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(30000)
        client._login(page)
        client._ensure_unit(page)
        client._patch_blank_popup_fix(page)
        popup = client._open_menu_option(
            page,
            "36",
            markers=(
                "romaneio",
                "entrega",
                "ctrc",
                "periodo",
                "unidade",
                "0146",
                "36",
                "relacao",
            ),
        )
        info = popup.evaluate(
            """() => {
              const nearLabel = (el) => {
                let t = '';
                const prev = el.previousElementSibling;
                if (prev) t = (prev.innerText || prev.textContent || '').trim();
                if (!t && el.parentElement) {
                  t = (el.parentElement.innerText || '').trim().slice(0, 80);
                }
                return t;
              };
              const inputs = Array.from(document.querySelectorAll('input, select, textarea')).map((el, i) => ({
                i,
                tag: el.tagName,
                id: el.id || '',
                name: el.name || '',
                type: el.type || '',
                value: el.value || '',
                maxLength: el.maxLength,
                size: el.size,
                className: el.className || '',
                label: nearLabel(el),
              }));
              const bodyText = (document.body && document.body.innerText || '').slice(0, 5000);
              const action = (document.querySelector('form') || {}).action || '';
              const title = document.title || '';
              const links = Array.from(document.querySelectorAll('a')).slice(0, 60).map(a => ({
                text: (a.innerText || a.textContent || '').trim().slice(0, 80),
                href: a.getAttribute('href') || '',
                onclick: (a.getAttribute('onclick') || '').slice(0, 200),
                id: a.id || '',
                className: a.className || '',
              }));
              return {
                url: location.href,
                title,
                action,
                bodyText,
                inputs,
                links,
              };
            }"""
        )
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        HTML_OUT.write_text(popup.content(), encoding="utf-8", errors="replace")
        print(f"DUMP JSON: {OUT}")
        print(f"DUMP HTML: {HTML_OUT}")
        print(f"URL: {info.get('url')}")
        print(f"ACTION: {info.get('action')}")
        print("--- BODY (inicio) ---")
        print((info.get("bodyText") or "")[:2000])
        print("--- INPUTS ---")
        for row in info.get("inputs") or []:
            print(row)
        print("--- LINKS (onclick) ---")
        for row in info.get("links") or []:
            if row.get("onclick") or "gera" in (row.get("text") or "").lower():
                print(row)
        print("Browser fica aberto 3 min para inspecao manual...")
        page.wait_for_timeout(180_000)
        browser.close()


if __name__ == "__main__":
    main()
