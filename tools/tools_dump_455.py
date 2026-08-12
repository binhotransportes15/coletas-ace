"""Dump ao vivo: SSW opção 455 (Emissão / CT-e emitidos).

Uso:
  py -3 tools/tools_dump_455.py
"""
from __future__ import annotations

import _root  # noqa: F401

import json
import os
from pathlib import Path

from config import CACHE_DIR, load_credentials, load_settings
from dates import periodo_hoje
from ssw_client import AceSswClient

OUT = CACHE_DIR / "dump_455_fields.json"
HTML_OUT = CACHE_DIR / "dump_455.html"


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def main() -> None:
    _ensure_playwright_path()
    creds = load_credentials()
    settings = load_settings()
    ini, fim = periodo_hoje()
    client = AceSswClient(
        ini,
        fim,
        credentials=creds,
        settings=settings,
        keep_open=True,
        headless=True,
        on_status=print,
    )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, slow_mo=0)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(45000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))
        client._login(page)
        client._ensure_unit(page)
        client._patch_blank_popup_form(page)

        popup = None
        # 1) menu 455
        try:
            popup = client._open_menu_option(
                page,
                "455",
                markers=(
                    "455",
                    "ct-e",
                    "cte",
                    "emitid",
                    "emiss",
                    "frete",
                    "expedid",
                    "recebid",
                    "liquid",
                    "arquivo",
                    "excel",
                    "periodo",
                ),
            )
            print("abriu via menu 455")
        except Exception as err:
            print("menu 455 falhou:", err)

        # 2) tenta programas comuns via ajax
        if popup is None:
            for prog in ("ssw0455", "ssw455", "ssw0120", "ssw401", "ssw0401"):
                try:
                    with context.expect_page(timeout=12000) as pi:
                        page.evaluate(
                            """(prog) => {
                              if (typeof ajaxEnvia === 'function') {
                                try { ajaxEnvia('', 1, prog); return prog; } catch (e) {}
                              }
                              return '';
                            }""",
                            prog,
                        )
                    popup = pi.value
                    print("abriu via ajax", prog, popup.url)
                    break
                except Exception as err:
                    print("ajax", prog, ":", err)

        if popup is None:
            raise RuntimeError("não abriu opção 455")

        try:
            popup.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        info = popup.evaluate(
            """() => {
              const nearLabel = (el) => {
                let t = '';
                const prev = el.previousElementSibling;
                if (prev) t = (prev.innerText || prev.textContent || '').trim();
                if (!t && el.parentElement) {
                  t = (el.parentElement.innerText || '').trim().slice(0, 160);
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
              const bodyText = (document.body && document.body.innerText || '').slice(0, 8000);
              const action = (document.querySelector('form') || {}).action || '';
              const title = document.title || '';
              const links = Array.from(document.querySelectorAll('a, img, button, input[type=image]')).slice(0, 100).map(a => ({
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
        print(f"TITLE: {info.get('title')}")
        print("--- body ---")
        print((info.get("bodyText") or "")[:1500])
        print("--- inputs ---")
        for row in info.get("inputs") or []:
            print(row)
        print("--- links ---")
        for row in (info.get("links") or [])[:40]:
            print(row)
        browser.close()


if __name__ == "__main__":
    main()
