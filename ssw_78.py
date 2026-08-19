"""Captura da tela 078 (ssw1257) — leitura estilo Ctrl+A / tabela HTML."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import SSW_78_PATH, SswCredentials, load_credentials, load_settings
from ssw_client import AceSswClient

StatusCallback = Callable[[str], None]


def _make_client(
    creds: SswCredentials,
    *,
    headless: bool,
    on_status: StatusCallback,
) -> AceSswClient:
    client = AceSswClient(
        "010101",
        "010101",
        keep_open=False,
        headless=headless,
        on_status=on_status,
        clean_downloads=False,
    )
    client.credentials.url = creds.url
    client.credentials.domain = creds.domain
    client.credentials.document = creds.document
    client.credentials.user = creds.user
    client.credentials.password = creds.password
    client.credentials.unit = creds.unit
    client.credentials.menu_unit = getattr(creds, "menu_unit", "") or ""
    return client


def _patch_popups(client: AceSswClient, page) -> None:
    patch = getattr(client, "_patch_blank_popup_form", None) or getattr(
        client, "_patch_blank_popup_forms", None
    )
    if callable(patch):
        patch(page)


def capture_78_on_page(
    client: AceSswClient,
    page,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Usa sessão já logada: abre 78, lê tabela e fecha o popup."""
    status = on_status or (lambda m: None)
    status("Abrindo opção 78 (Descarga de Veículos)...")
    popup = client._open_menu_option(
        page,
        "78",
        markers=("78", "descarg", "cavalo", "carreta", "chegad", "manifesto", "1257"),
    )
    try:
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
    finally:
        try:
            if popup is not None and not popup.is_closed():
                popup.close()
        except Exception:
            pass

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

    client = _make_client(creds, headless=use_headless, on_status=status)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        try:
            from ace_stop import register_browser

            register_browser(browser)
        except Exception:
            pass
        context = browser.new_context(accept_downloads=False)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("dialog", lambda d: d.accept())
        try:
            status("Login SSW...")
            client._login(page)
            client._ensure_unit(page)
            _patch_popups(client, page)
            info = capture_78_on_page(client, page, on_status=status)
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                from ace_stop import unregister_browser

                unregister_browser(browser)
            except Exception:
                pass

    return info


def capture_armazem_78_177(
    *,
    credentials: SswCredentials | None = None,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    allow_local_fallback_177: bool = True,
) -> dict[str, Any]:
    """
    Um único login SSW:
      1) opção 78 (pátio)
      2) opção 56 → Gerados hoje → 177 mensal
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

    from config import DOWNLOAD_DIR as ACE_DOWNLOAD_DIR
    from ssw_177 import _find_local_177, download_177_on_page

    client = _make_client(creds, headless=use_headless, on_status=status)
    capture78: dict[str, Any] = {"ok": False}
    download177: dict[str, Any] = {"ok": False}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_name = f"conferentes_177_mensal_{ts}.sswweb"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        try:
            from ace_stop import register_browser

            register_browser(browser)
        except Exception:
            pass

        # downloads=True: 177 precisa baixar .sswweb na mesma sessão
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))

        try:
            status(f"ACE ARMAZÉM · login único | {datetime.now():%d/%m %H:%M:%S}")
            client._login(page)
            client._ensure_unit(page)
            _patch_popups(client, page)

            status("ACE ARMAZÉM · 78 (mesma sessão)...")
            capture78 = capture_78_on_page(client, page, on_status=status)

            try:
                status("ACE ARMAZÉM · 177 via 56 (mesma sessão)...")
                path = download_177_on_page(
                    client, page, dest_name=dest_name, on_status=status
                )
                download177 = {"ok": True, "path": str(path), "source": "ssw"}
                status(f"177 baixado: {path.name} ({path.stat().st_size} bytes)")
            except Exception as err:  # noqa: BLE001
                status(f"177 SSW falhou: {err}")
                if allow_local_fallback_177:
                    local = _find_local_177()
                    if local:
                        dest = ACE_DOWNLOAD_DIR / dest_name
                        ACE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(local.read_bytes())
                        status(f"177 usando arquivo local: {local.name}")
                        download177 = {
                            "ok": True,
                            "path": str(dest),
                            "source": "local",
                            "from": str(local),
                            "ssw_error": str(err),
                        }
                    else:
                        download177 = {"ok": False, "error": str(err)}
                else:
                    download177 = {"ok": False, "error": str(err)}
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                from ace_stop import unregister_browser

                unregister_browser(browser)
            except Exception:
                pass

    return {
        "ok": bool(capture78.get("ok")),
        "78": capture78,
        "177": download177,
        "shared_login": True,
    }
