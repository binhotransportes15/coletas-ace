"""Dump de campos da tela SSW opção 31 (pendências / ocorrências)."""
from __future__ import annotations

import json

from config import CACHE_DIR, load_credentials, load_settings
from dates import periodo_mes_corrente, to_ssw_ddmmyy
from ssw_client import AceSswClient

OUT = CACHE_DIR / "dump_31_fields.json"
HTML_OUT = CACHE_DIR / "dump_31.html"


def main() -> None:
    creds = load_credentials()
    settings = load_settings()
    ini, fim = periodo_mes_corrente()
    client = AceSswClient(
        ini,
        fim,
        credentials=creds,
        settings=settings,
        keep_open=True,
        headless=False,
        on_status=print,
    )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(45000)
        client._login(page)
        client._ensure_unit(page)
        client._patch_blank_popup_fix(page)
        popup = client._open_menu_option(
            page,
            "31",
            markers=(
                "ocorr",
                "ctrc",
                "periodo",
                "excel",
                "pendenc",
                "31",
                "arquivo",
            ),
        )
        info = popup.evaluate(
            """() => {
              const nearLabel = (el) => {
                let t = '';
                const prev = el.previousElementSibling;
                if (prev) t = (prev.innerText || prev.textContent || '').trim();
                if (!t && el.parentElement) {
                  t = (el.parentElement.innerText || '').trim().slice(0, 120);
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
              const bodyText = (document.body && document.body.innerText || '').slice(0, 6000);
              const action = (document.querySelector('form') || {}).action || '';
              const title = document.title || '';
              const links = Array.from(document.querySelectorAll('a, img, button, input[type=image]')).slice(0, 80).map(a => ({
                text: (a.innerText || a.textContent || a.alt || a.title || '').trim().slice(0, 80),
                href: a.getAttribute('href') || '',
                onclick: (a.getAttribute('onclick') || '').slice(0, 220),
                id: a.id || '',
                src: a.getAttribute('src') || '',
                className: a.className || '',
              }));
              return { url: location.href, title, action, bodyText, inputs, links };
            }"""
        )
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        HTML_OUT.write_text(popup.content(), encoding="utf-8", errors="replace")
        print(f"DUMP JSON: {OUT}")
        print(f"DUMP HTML: {HTML_OUT}")
        print(f"URL: {info.get('url')}")
        for row in info.get("inputs") or []:
            print(row)
        print("Browser aberto 3 min…")
        page.wait_for_timeout(180_000)
        browser.close()


if __name__ == "__main__":
    main()
