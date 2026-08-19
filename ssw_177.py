"""Captura relatório 177 (Produção conferentes SSWBAR) via lista Gerados hoje."""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, ensure_dirs, load_credentials, load_settings
from ssw_client import AceSswClient

StatusCallback = Callable[[str], None]


def _find_local_177() -> Path | None:
    """Fallback: arquivo já baixado (Downloads / cache / data/downloads)."""
    ensure_dirs()
    candidates: list[Path] = []
    roots = [
        DOWNLOAD_DIR,
        Path(__file__).resolve().parent / "data" / "cache",
        Path.home() / "Downloads",
    ]
    patterns = ("*177*.sswweb", "*conferent*.sswweb", "*SSWBAR*.sswweb", "*173729*.sswweb")
    for root in roots:
        if not root.is_dir():
            continue
        for pat in patterns:
            candidates.extend(root.glob(pat))
    extra = Path.home() / "Downloads"
    if extra.is_dir():
        for p in extra.glob("*.sswweb"):
            name = p.name.lower()
            if "177" in name or "confer" in name or "sswbar" in name:
                candidates.append(p)
    alive = [p for p in candidates if p.is_file() and p.stat().st_size > 500]
    if not alive:
        return None
    alive.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in alive:
        try:
            head = p.read_bytes()[:800].decode("latin-1", errors="ignore").upper()
        except Exception:
            head = ""
        if "177" in head and "CONFERENT" in head:
            return p
    return alive[0]


def _make_client(creds, *, headless: bool, on_status: StatusCallback) -> AceSswClient:
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
    return client


def download_177_on_page(
    client: AceSswClient,
    page,
    *,
    dest_name: str,
    on_status: StatusCallback | None = None,
) -> Path:
    """Sessão já logada: opção 56 → Gerados hoje → download 177 mensal."""
    status = on_status or (lambda m: None)
    status("Abrindo opção 56 (relatórios gerados)...")
    popup = client._open_menu_option(
        page,
        "56",
        markers=(
            "gerados",
            "hoje",
            "relatorio",
            "conferent",
            "177",
            "mensal",
            "paginas",
            "periodo",
            "volumes",
            "056",
        ),
    )
    try:
        try:
            popup.on("dialog", lambda d: d.accept())
        except Exception:
            pass
        _ensure_gerados_hoje(popup, status)
        return _download_177_mensal(popup, client, dest_name, status)
    finally:
        try:
            if popup is not None and not popup.is_closed():
                popup.close()
        except Exception:
            pass


def download_report_177(
    *,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    allow_local_fallback: bool = True,
) -> dict[str, Any]:
    """
    Fluxo standalone (login próprio):
      menu opção 56 → aba Gerados hoje → linha 177 (MENSAL) → download .sswweb
    """
    status = on_status or (lambda m: None)
    ensure_dirs()
    creds = load_credentials()
    settings = load_settings()
    use_headless = settings.headless if headless is None else headless
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        local = os.environ.get("LOCALAPPDATA") or ""
        if local:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_name = f"conferentes_177_mensal_{ts}.sswweb"
    path: Path | None = None

    from playwright.sync_api import sync_playwright

    client = _make_client(creds, headless=use_headless, on_status=status)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=use_headless, slow_mo=0 if use_headless else 40
            )
            try:
                from ace_stop import register_browser

                register_browser(browser)
            except Exception:
                pass

            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(60000)
            page.on("dialog", lambda d: d.accept())
            context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))
            try:
                status("Login SSW (177)...")
                client._login(page)
                client._ensure_unit(page)
                patch = getattr(client, "_patch_blank_popup_form", None) or getattr(
                    client, "_patch_blank_popup_forms", None
                )
                if callable(patch):
                    patch(page)
                path = download_177_on_page(
                    client, page, dest_name=dest_name, on_status=status
                )
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

        if path is None or not path.exists():
            raise RuntimeError("177: nenhum arquivo baixado da opção 56")

        status(f"177 baixado: {path.name} ({path.stat().st_size} bytes)")
        return {"ok": True, "path": str(path), "source": "ssw"}
    except Exception as err:  # noqa: BLE001
        status(f"177 SSW falhou: {err}")
        if allow_local_fallback:
            local = _find_local_177()
            if local:
                dest = DOWNLOAD_DIR / dest_name
                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(local.read_bytes())
                status(f"177 usando arquivo local: {local.name}")
                return {
                    "ok": True,
                    "path": str(dest),
                    "source": "local",
                    "from": str(local),
                    "ssw_error": str(err),
                }
        raise


def _tab_gerados_hoje_state(popup) -> dict[str, Any]:
    """Inspeciona se a aba Gerados hoje está ativa / visível."""
    try:
        return popup.evaluate(
            """() => {
              const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
              const body = norm(document.body && document.body.innerText).toLowerCase();
              const nodes = Array.from(document.querySelectorAll('a, button, td, span, div, li'));
              let tabVisible = false;
              let tabActive = false;
              for (const el of nodes) {
                const t = norm(el.innerText || el.textContent).toLowerCase();
                if (!t || t.length > 40) continue;
                if (!/gerados\\s*hoje/.test(t)) continue;
                tabVisible = true;
                const cls = `${el.className || ''} ${el.parentElement ? el.parentElement.className : ''}`.toLowerCase();
                const sel = el.getAttribute('aria-selected') === 'true'
                  || el.classList.contains('selected')
                  || el.classList.contains('ativo')
                  || el.classList.contains('active')
                  || cls.includes('selecion')
                  || cls.includes('active')
                  || cls.includes('atual');
                const bg = (getComputedStyle(el).backgroundColor || '');
                const looksSelected = sel || (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent');
                if (looksSelected) tabActive = true;
              }
              const hasLista = /177|mensal|relat[oó]rio|sswbar|conferent/.test(body);
              const rowCount = document.querySelectorAll('table tr, tr').length;
              return {
                bodyHasGeradosHoje: /gerados\\s*hoje/.test(body),
                tabVisible,
                tabActive,
                hasLista,
                rowCount,
                bodySnippet: body.slice(0, 280),
              };
            }"""
        )
    except Exception as err:  # noqa: BLE001
        return {
            "bodyHasGeradosHoje": False,
            "tabVisible": False,
            "tabActive": False,
            "hasLista": False,
            "rowCount": 0,
            "bodySnippet": "",
            "error": str(err),
        }


def _click_gerados_hoje(popup, status: StatusCallback) -> bool:
    status("Selecionando aba Gerados hoje...")
    candidates = [
        popup.get_by_role("link", name=re.compile(r"Gerados\s*hoje", re.I)),
        popup.get_by_role("tab", name=re.compile(r"Gerados\s*hoje", re.I)),
        popup.get_by_role("button", name=re.compile(r"Gerados\s*hoje", re.I)),
        popup.get_by_text(re.compile(r"^\s*Gerados\s*hoje\s*$", re.I)),
        popup.get_by_text(re.compile(r"Gerados\s*hoje", re.I)),
        popup.locator("a, button, td, span, div, li").filter(
            has_text=re.compile(r"Gerados\s*hoje", re.I)
        ),
    ]
    for loc in candidates:
        try:
            if loc.count() <= 0:
                continue
            loc.first.click(timeout=4000)
            popup.wait_for_timeout(900)
            return True
        except Exception:
            continue
    return False


def _ensure_gerados_hoje(popup, status: StatusCallback) -> None:
    """Clica e confirma que estamos em Gerados hoje (não outra aba da 56)."""
    clicked = _click_gerados_hoje(popup, status)
    state = _tab_gerados_hoje_state(popup)

    if not state.get("bodyHasGeradosHoje") and not state.get("tabVisible"):
        # segunda tentativa após pequeno wait
        popup.wait_for_timeout(500)
        clicked = _click_gerados_hoje(popup, status) or clicked
        state = _tab_gerados_hoje_state(popup)

    ok_tab = bool(
        state.get("bodyHasGeradosHoje")
        or state.get("tabVisible")
        or state.get("tabActive")
    )
    ok_lista = bool(state.get("hasLista") or int(state.get("rowCount") or 0) >= 3)

    if ok_tab and ok_lista:
        status(
            f"Gerados hoje OK · linhas≈{state.get('rowCount')} "
            f"· aba={'ativa' if state.get('tabActive') else 'visível'}"
            f"{' · clicou' if clicked else ''}"
        )
        return

    if ok_tab and not ok_lista:
        status(
            "Gerados hoje: aba encontrada, lista ainda vazia/fraca — "
            "tentando baixar 177 mesmo assim…"
        )
        return

    snippet = (state.get("bodySnippet") or "")[:120]
    raise RuntimeError(
        "Opção 56: não confirmei a aba 'Gerados hoje'. "
        f"clicked={clicked} state={ {k: state.get(k) for k in ('bodyHasGeradosHoje','tabVisible','tabActive','hasLista','rowCount')} } "
        f"snippet={snippet!r}"
    )


def _download_177_mensal(
    popup, client: AceSswClient, dest_name: str, status: StatusCallback
) -> Path:
    status("Procurando 177 · PRODUCAO DE CONFERENTES · MENSAL em Gerados hoje...")
    popup.wait_for_timeout(600)
    rows = popup.locator("tr")
    n = rows.count()
    target = None
    seen: list[str] = []
    for i in range(min(n, 250)):
        row = rows.nth(i)
        try:
            txt = (row.inner_text(timeout=800) or "").upper()
        except Exception:
            continue
        compact = re.sub(r"\s+", " ", txt).strip()
        if re.search(r"\b177\b", compact):
            seen.append(compact[:100])
        if "177" not in compact:
            continue
        if "CONFERENT" not in compact and "SSWBAR" not in compact:
            continue
        if "MENSAL" in compact:
            target = row
            break
        if target is None:
            target = row

    if target is None:
        link = popup.locator("a").filter(has_text=re.compile(r"^\s*177\s*$"))
        if link.count() == 0:
            hint = "; ".join(seen[:6]) if seen else "(nenhuma linha com 177)"
            raise RuntimeError(
                "Linha 177 (mensal) não encontrada em Gerados hoje. "
                f"Vistos: {hint}"
            )
        clickable = link.first
        status("177 encontrado via link direto na lista Gerados hoje")
    else:
        link = target.locator("a").filter(has_text=re.compile(r"177"))
        clickable = link.first if link.count() else target.locator("a").first
        status("177 mensal encontrado na lista Gerados hoje — baixando...")

    with popup.expect_download(timeout=120000) as download_info:
        clickable.click()
    download = download_info.value
    path = client._save_download(download, dest_name)
    suggested = (download.suggested_filename or "").lower()
    if suggested and not suggested.endswith(".sswweb"):
        alt = dest_name.rsplit(".", 1)[0] + Path(suggested).suffix
        return client._save_download(download, alt)
    return path
