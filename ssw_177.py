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
    # também nome original do sample
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
    # prefer conteúdos que mencionam 177
    for p in alive:
        try:
            head = p.read_bytes()[:800].decode("latin-1", errors="ignore").upper()
        except Exception:
            head = ""
        if "177" in head and "CONFERENT" in head:
            return p
    return alive[0]


def download_report_177(
    *,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    allow_local_fallback: bool = True,
) -> dict[str, Any]:
    """
    Fluxo informado:
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

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=use_headless, slow_mo=0 if use_headless else 40
            )
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(60000)
            status("Login SSW (177)...")
            client._login(page)
            client._ensure_unit(page)
            patch = getattr(client, "_patch_blank_popup_fix", None) or getattr(
                client, "_patch_blank_popup_forms", None
            )
            if callable(patch):
                patch(page)

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
                popup.on("dialog", lambda d: d.accept())
                _click_gerados_hoje(popup, status)
                path = _download_177_mensal(popup, client, dest_name, status)
            finally:
                try:
                    popup.close()
                except Exception:
                    pass
                context.close()
                browser.close()

        status(f"177 baixado: {path.name} ({path.stat().st_size} bytes)")
        return {"ok": True, "path": str(path), "source": "ssw"}
    except Exception as err:  # noqa: BLE001
        status(f"177 SSW falhou: {err}")
        if allow_local_fallback:
            local = _find_local_177()
            if local:
                # copia para downloads do ACE
                dest = DOWNLOAD_DIR / dest_name
                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(local.read_bytes())
                status(f"177 usando arquivo local: {local.name}")
                return {"ok": True, "path": str(dest), "source": "local", "from": str(local)}
        raise


def _click_gerados_hoje(popup, status: StatusCallback) -> None:
    status("Selecionando aba Gerados hoje...")
    candidates = [
        popup.get_by_text(re.compile(r"Gerados\s*hoje", re.I)),
        popup.locator("a, button, td, span, div").filter(
            has_text=re.compile(r"Gerados\s*hoje", re.I)
        ),
    ]
    for loc in candidates:
        try:
            if loc.count() > 0:
                loc.first.click(timeout=4000)
                popup.wait_for_timeout(800)
                return
        except Exception:
            continue
    # já pode estar na aba
    status("Aba Gerados hoje: assume já ativa (ou não encontrada).")


def _download_177_mensal(popup, client: AceSswClient, dest_name: str, status: StatusCallback) -> Path:
    status("Procurando 177 · PRODUCAO DE CONFERENTES · MENSAL...")
    popup.wait_for_timeout(600)
    rows = popup.locator("tr")
    n = rows.count()
    target = None
    for i in range(min(n, 200)):
        row = rows.nth(i)
        try:
            txt = (row.inner_text(timeout=800) or "").upper()
        except Exception:
            continue
        if "177" not in txt:
            continue
        if "CONFERENT" not in txt and "SSWBAR" not in txt:
            continue
        # evita o diário 251
        if "MENSAL" in txt:
            target = row
            break
        if target is None:
            target = row
    if target is None:
        # fallback: link com texto 177
        link = popup.locator("a").filter(has_text=re.compile(r"^\s*177\s*$"))
        if link.count() == 0:
            raise RuntimeError("Linha 177 (mensal) não encontrada em Gerados hoje.")
        clickable = link.first
    else:
        link = target.locator("a").filter(has_text=re.compile(r"177"))
        clickable = link.first if link.count() else target.locator("a").first

    with popup.expect_download(timeout=120000) as download_info:
        clickable.click()
    download = download_info.value
    path = client._save_download(download, dest_name)
    suggested = (download.suggested_filename or "").lower()
    if suggested and not suggested.endswith(".sswweb"):
        # mantém extensão sugerida
        alt = dest_name.rsplit(".", 1)[0] + Path(suggested).suffix
        path2 = client._save_download(download, alt)
        return path2
    return path
