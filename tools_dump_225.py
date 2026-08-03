"""Abre SSW opcao 225, dumpa campos/HTML para descobrir o formulario."""
from __future__ import annotations

import json

from config import CACHE_DIR, load_credentials, load_settings
from ssw_client import AceSswClient

OUT = CACHE_DIR / "dump_225_fields.json"
HTML_OUT = CACHE_DIR / "dump_225.html"


def main() -> None:
    creds = load_credentials()
    settings = load_settings()
    client = AceSswClient(
        "030826",
        "090826",
        credentials=creds,
        settings=settings,
        keep_open=False,
        headless=True,
        on_status=print,
    )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, slow_mo=0)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(45000)
        client._login(page)
        client._ensure_unit(page)
        client._patch_blank_popup_fix(page)
        popup = client._open_menu_option(
            page,
            "225",
            markers=(
                "agend",
                "225",
                "previs",
                "entrega",
                "obrigat",
                "situac",
                "excel",
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
              const links = Array.from(document.querySelectorAll('a')).slice(0, 80).map(a => ({
                text: (a.innerText || a.textContent || '').trim().slice(0, 80),
                href: a.getAttribute('href') || '',
                onclick: (a.getAttribute('onclick') || '').slice(0, 220),
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
        print("--- BODY ---")
        print((info.get("bodyText") or "")[:2500])
        print("--- INPUTS ---")
        for inp in (info.get("inputs") or [])[:40]:
            print(
                f"  id={inp.get('id')!r} name={inp.get('name')!r} "
                f"val={inp.get('value')!r} label={inp.get('label')!r}"
            )
        print("--- LINKS ---")
        for a in (info.get("links") or [])[:30]:
            if a.get("id") or "ajax" in (a.get("onclick") or "").lower() or a.get("text"):
                print(f"  id={a.get('id')!r} text={a.get('text')!r} onclick={a.get('onclick')!r}")
        try:
            popup.close()
        except Exception:
            pass
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
