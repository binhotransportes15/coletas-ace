"""Captura da tela 078 (ssw1257) — leitura estilo Ctrl+A / tabela HTML."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from config import SSW_78_PATH, SswCredentials, load_credentials, load_settings
from ssw_client import AceSswClient

StatusCallback = Callable[[str], None]


def capture_ssw78(
    *,
    credentials: SswCredentials | None = None,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """
    Abre SSW opção 78 e devolve:
      - body_text (Ctrl+A)
      - table_rows (células da tabela)
      - html / url / title
    """
    status = on_status or (lambda m: None)
    creds = credentials or load_credentials()
    settings = load_settings()
    use_headless = bool(getattr(settings, "headless", False)) if headless is None else headless
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        local = os.environ.get("LOCALAPPDATA") or ""
        if local:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")

    from playwright.sync_api import sync_playwright

    client = AceSswClient(
        "010101",
        "010101",
        keep_open=False,
        headless=use_headless,
        on_status=status,
        clean_downloads=False,
    )
    client.credentials.url = creds.url
    client.credentials.domain = creds.domain
    client.credentials.document = creds.document
    client.credentials.user = creds.user
    client.credentials.password = creds.password
    client.credentials.unit = creds.unit

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        context = browser.new_context(accept_downloads=False)
        page = context.new_page()
        page.set_default_timeout(60000)
        status("Login SSW...")
        client._login(page)
        client._ensure_unit(page)
        patch = getattr(client, "_patch_blank_popup_fix", None) or getattr(
            client, "_patch_blank_popup_forms", None
        )
        if callable(patch):
            patch(page)
        status("Abrindo opção 78 (Descarga de Veículos)...")
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
            status(f"Navegando {SSW_78_PATH}...")
            popup.goto(
                f"https://sistema.ssw.inf.br{SSW_78_PATH}",
                wait_until="domcontentloaded",
            )
            popup.wait_for_timeout(1200)

        popup.wait_for_timeout(800)
        info = popup.evaluate(
            """() => {
              const bodyText = (document.body && document.body.innerText) || '';
              const tables = Array.from(document.querySelectorAll('table')).map((tb) =>
                Array.from(tb.rows).map((r) =>
                  Array.from(r.cells).map((c) =>
                    (c.innerText || '').replace(/\\u00a0/g, ' ').trim().replace(/\\s+/g, ' ')
                  )
                )
              );
              let best = [];
              for (const rows of tables) {
                if (!rows.length) continue;
                const head = (rows[0] || []).join(' ').toUpperCase();
                if (head.includes('ORIGEM') && head.includes('CAVALO')) {
                  best = rows;
                  break;
                }
                if (rows.length > best.length) best = rows;
              }
              return {
                url: location.href,
                title: document.title || '',
                bodyText,
                html: document.documentElement ? document.documentElement.outerHTML : '',
                tableRows: best,
              };
            }"""
        )
        try:
            popup.close()
        except Exception:
            pass
        context.close()
        browser.close()

    status(f"78 capturada: {len(info.get('tableRows') or [])} linha(s) de tabela")
    return {
        "ok": True,
        "url": info.get("url") or "",
        "title": info.get("title") or "",
        "body_text": info.get("bodyText") or "",
        "html": info.get("html") or "",
        "table_rows": info.get("tableRows") or [],
        "program": SSW_78_PATH,
    }
