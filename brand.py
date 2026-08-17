"""Marca / logo global do ACE (CRT + dashboards + publish)."""
from __future__ import annotations

import base64
import json
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
DASHBOARD = _ROOT / "dashboard"
ASSETS = _ROOT / "assets"
BRAND_JSON = DASHBOARD / "brand.json"
BRAND_LOGO = DASHBOARD / "brand-logo.png"
DEFAULT_DASH_LOGO = DASHBOARD / "logo-binho.png"
DEFAULT_CRT_BRAIN = ASSETS / "brain-circuit.png"
DEFAULT_CRT_CUBES = ASSETS / "cubes-binho.png"


def default_brand() -> dict[str, Any]:
    return {
        "version": 1,
        "visible": True,
        "mode": "file",  # file | url | hidden
        "file": "brand-logo.png",
        "url": "",
        "crtAsset": "brain-circuit.png",
        "themeHint": "circuitos",
        "updatedAt": "",
    }


def load_brand() -> dict[str, Any]:
    base = default_brand()
    if not BRAND_JSON.is_file():
        return base
    try:
        raw = json.loads(BRAND_JSON.read_text(encoding="utf-8"))
    except Exception:
        return base
    if not isinstance(raw, dict):
        return base
    out = {**base, **raw}
    out["visible"] = bool(out.get("visible", True))
    mode = str(out.get("mode") or "file").strip().lower()
    if mode not in ("file", "url", "hidden"):
        mode = "file"
    if not out["visible"]:
        mode = "hidden"
    out["mode"] = mode
    out["file"] = str(out.get("file") or "brand-logo.png").strip() or "brand-logo.png"
    out["url"] = str(out.get("url") or "").strip()
    out["crtAsset"] = str(out.get("crtAsset") or "brain-circuit.png").strip()
    out["themeHint"] = str(out.get("themeHint") or "circuitos").strip()
    return out


def save_brand(brand: dict[str, Any]) -> dict[str, Any]:
    data = {**default_brand(), **(brand or {})}
    data["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    data["visible"] = bool(data.get("visible", True))
    mode = str(data.get("mode") or "file").strip().lower()
    if mode not in ("file", "url", "hidden"):
        mode = "file"
    if not data["visible"]:
        mode = "hidden"
    data["mode"] = mode
    DASHBOARD.mkdir(parents=True, exist_ok=True)
    BRAND_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return data


def resolve_crt_pixmap_path(brand: dict[str, Any] | None = None) -> Path:
    b = brand or load_brand()
    if b.get("mode") == "hidden" or not b.get("visible", True):
        return Path()  # empty → widget hides / draws nothing
    # Prefer dashboard brand file if custom
    if b.get("mode") == "file":
        local = DASHBOARD / str(b.get("file") or "brand-logo.png")
        if local.is_file():
            return local
    if b.get("mode") == "url" and b.get("url"):
        cached = DASHBOARD / "brand-logo-remote.png"
        if cached.is_file():
            return cached
    for cand in (
        ASSETS / str(b.get("crtAsset") or "brain-circuit.png"),
        DEFAULT_CRT_BRAIN,
        DEFAULT_DASH_LOGO,
        DEFAULT_CRT_CUBES,
    ):
        if cand.is_file():
            return cand
    return Path()


def resolve_dashboard_src(brand: dict[str, Any] | None = None) -> str:
    """Src relativo para <img> no dashboard (ou URL absoluta)."""
    b = brand or load_brand()
    if b.get("mode") == "hidden" or not b.get("visible", True):
        return ""
    if b.get("mode") == "url" and b.get("url"):
        return str(b["url"])
    fname = str(b.get("file") or "brand-logo.png")
    if (DASHBOARD / fname).is_file():
        return fname
    if DEFAULT_DASH_LOGO.is_file():
        return "logo-binho.png"
    return fname


def set_visible(visible: bool) -> dict[str, Any]:
    b = load_brand()
    b["visible"] = bool(visible)
    b["mode"] = "file" if visible else "hidden"
    return save_brand(b)


def apply_logo_file(src: Path | str) -> dict[str, Any]:
    path = Path(src)
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    DASHBOARD.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, BRAND_LOGO)
    # Espelha no nome legado usado pelo HTML e no CRT
    shutil.copy2(path, DEFAULT_DASH_LOGO)
    shutil.copy2(path, DEFAULT_CRT_BRAIN)
    b = load_brand()
    b["visible"] = True
    b["mode"] = "file"
    b["file"] = "brand-logo.png"
    b["url"] = ""
    b["crtAsset"] = "brain-circuit.png"
    return save_brand(b)


def apply_logo_url(url: str, *, timeout: float = 25.0) -> dict[str, Any]:
    u = str(url or "").strip()
    if not u.startswith(("http://", "https://")):
        raise ValueError("URL inválida (use http:// ou https://)")
    req = urllib.request.Request(
        u,
        headers={"User-Agent": "ACE-Brand/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = (resp.headers.get("Content-Type") or "").lower()
    if not data:
        raise ValueError("Download vazio")
    if "html" in ctype:
        raise ValueError("URL não parece ser imagem")
    DASHBOARD.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    # Detect extension
    ext = ".png"
    if "jpeg" in ctype or "jpg" in ctype or u.lower().endswith((".jpg", ".jpeg")):
        ext = ".jpg"
    elif "webp" in ctype or u.lower().endswith(".webp"):
        ext = ".webp"
    elif "svg" in ctype or u.lower().endswith(".svg"):
        ext = ".svg"
    dest = DASHBOARD / f"brand-logo-remote{ext}"
    dest.write_bytes(data)
    # Prefer PNG copies for Qt/legacy
    if ext == ".png":
        shutil.copy2(dest, BRAND_LOGO)
        shutil.copy2(dest, DEFAULT_DASH_LOGO)
        shutil.copy2(dest, DEFAULT_CRT_BRAIN)
    else:
        # still point URL mode; keep local cache for offline
        try:
            shutil.copy2(dest, BRAND_LOGO)
        except Exception:
            pass
    b = load_brand()
    b["visible"] = True
    b["mode"] = "url"
    b["url"] = u
    b["file"] = dest.name
    return save_brand(b)


def export_logo(dest: Path | str) -> Path:
    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    b = load_brand()
    src = resolve_crt_pixmap_path(b)
    if not src.is_file():
        for cand in (BRAND_LOGO, DEFAULT_DASH_LOGO, DEFAULT_CRT_BRAIN):
            if cand.is_file():
                src = cand
                break
    if not src.is_file():
        raise FileNotFoundError("Nenhuma logo para exportar")
    shutil.copy2(src, out)
    return out


def hide_everywhere() -> dict[str, Any]:
    """Esconde logo nas dashboards (brand + tv_layout showLogo=false)."""
    b = set_visible(False)
    try:
        from tv_layout import load_layout, save_layout

        lay = load_layout()
        lay["brand"] = b
        defs = lay.setdefault("sectorDefaults", {})
        for sid, conf in list(defs.items()):
            if isinstance(conf, dict):
                conf["showLogo"] = False
        for slot in lay.get("slots") or []:
            if isinstance(slot, dict):
                slot["showLogo"] = False
        save_layout(lay)
    except Exception:
        pass
    return b


def show_everywhere() -> dict[str, Any]:
    b = set_visible(True)
    if b.get("mode") == "hidden":
        b["mode"] = "file"
        b = save_brand(b)
    try:
        from tv_layout import load_layout, save_layout

        lay = load_layout()
        lay["brand"] = b
        defs = lay.setdefault("sectorDefaults", {})
        for sid, conf in list(defs.items()):
            if isinstance(conf, dict):
                conf["showLogo"] = True
        save_layout(lay)
    except Exception:
        pass
    return b


def embed_brand_in_layout(layout: dict[str, Any] | None = None) -> dict[str, Any]:
    from tv_layout import load_layout, save_layout

    lay = layout if isinstance(layout, dict) else load_layout()
    b = load_brand()
    # Inline small preview as data-URL only if file is tiny (<120kb) — else keep path/url
    payload = dict(b)
    src_file = DASHBOARD / str(b.get("file") or "brand-logo.png")
    if b.get("mode") == "file" and src_file.is_file() and src_file.stat().st_size < 120_000:
        try:
            raw = src_file.read_bytes()
            payload["dataUrl"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        except Exception:
            payload.pop("dataUrl", None)
    else:
        payload.pop("dataUrl", None)
    lay["brand"] = payload
    if layout is None:
        save_layout(lay)
    return lay


def publish_brand(*, push_sheets: bool = True, push_git: bool = True) -> tuple[bool, str]:
    """Grava brand.json, espelha no tv_layout e publica conforme destino."""
    notes: list[str] = []
    b = load_brand()
    save_brand(b)
    # Garante arquivos no dashboard/
    if b.get("mode") == "file":
        src = resolve_crt_pixmap_path(b)
        if src.is_file():
            if not BRAND_LOGO.is_file() or src.resolve() != BRAND_LOGO.resolve():
                try:
                    shutil.copy2(src, BRAND_LOGO)
                except Exception:
                    pass
            try:
                shutil.copy2(src, DEFAULT_DASH_LOGO)
            except Exception:
                pass

    lay = embed_brand_in_layout()
    notes.append("brand.json + tv_layout atualizados")

    if push_sheets:
        try:
            from config import resolve_publish_target, load_settings
            from tv_layout import push_layout_to_sheets

            target = resolve_publish_target(load_settings())
            if target in ("sites", "auto"):
                ok, msg = push_layout_to_sheets(lay)
                notes.append(f"Sites/layout: {'ok' if ok else 'falha'} — {msg}")
            else:
                notes.append(f"Sheets pulado (destino={target})")
        except Exception as e:  # noqa: BLE001
            notes.append(f"Sheets: {e}")

    if push_git:
        try:
            from config import resolve_publish_target, load_settings, github_publish_allowed

            settings = load_settings()
            target = resolve_publish_target(settings)
            if target == "github" or (target == "auto" and github_publish_allowed(settings)):
                from git_sync import git_push

                msg = git_push(message="chore(brand): atualiza logo/marca das dashboards")
                notes.append(f"GitHub: {msg}")
            elif target == "local":
                notes.append("Local: arquivos prontos (sem push)")
            else:
                notes.append(f"Git pulado (destino={target})")
        except Exception as e:  # noqa: BLE001
            notes.append(f"Git: {e}")

    return True, " · ".join(notes)
