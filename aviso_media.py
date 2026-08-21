"""Aviso temporário da TV — arquivos em dashboard/aviso/."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from config import DASHBOARD_DIR

AVISO_DIR = DASHBOARD_DIR / "aviso"
AVISO_JSON = AVISO_DIR / "aviso.json"
GITHUB_MAX_BYTES = 95 * 1024 * 1024

_VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _default_cfg() -> dict[str, Any]:
    return {
        "title": "AVISO",
        "kicker": "COMUNICADO TEMPORÁRIO",
        "fit": "contain",
        "items": [],
    }


def load_aviso() -> dict[str, Any]:
    cfg = _default_cfg()
    if not AVISO_JSON.is_file():
        return cfg
    try:
        raw = json.loads(AVISO_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return cfg
    if not isinstance(raw, dict):
        return cfg
    cfg.update(raw)
    if not isinstance(cfg.get("items"), list):
        cfg["items"] = []
    return cfg


def save_aviso(cfg: dict[str, Any]) -> None:
    AVISO_DIR.mkdir(parents=True, exist_ok=True)
    AVISO_JSON.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _kind_for(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _IMAGE_EXT:
        return "image"
    return None


def _safe_name(name: str) -> str:
    base = _SAFE.sub("_", Path(name).name).strip("._")
    return base or "arquivo.bin"


def _unique_dest(name: str) -> Path:
    AVISO_DIR.mkdir(parents=True, exist_ok=True)
    dest = AVISO_DIR / name
    if not dest.exists():
        return dest
    stem = dest.stem
    suf = dest.suffix
    n = 2
    while True:
        cand = AVISO_DIR / f"{stem}_{n}{suf}"
        if not cand.exists():
            return cand
        n += 1


def status_text(cfg: dict[str, Any] | None = None) -> str:
    data = cfg if cfg is not None else load_aviso()
    items = [it for it in (data.get("items") or []) if isinstance(it, dict)]
    if not items:
        return "Nenhum aviso salvo. Anexe um vídeo/foto e publique no site."
    bits = []
    for it in items:
        kind = str(it.get("type") or "item")
        src = str(it.get("src") or it.get("title") or it.get("text") or "")
        bits.append(f"{kind}: {src}")
    title = str(data.get("title") or "AVISO")
    return f"{title} · {len(items)} item(ns)\n" + "\n".join(bits)


def attach_files(
    paths: list[str],
    *,
    title: str = "",
    kicker: str = "",
    text: str = "",
    replace: bool = True,
) -> dict[str, Any]:
    """Copia mídias para dashboard/aviso e atualiza aviso.json."""
    cfg = load_aviso()
    if title.strip():
        cfg["title"] = title.strip()
    if kicker.strip():
        cfg["kicker"] = kicker.strip()

    copied: list[dict[str, Any]] = []
    errors: list[str] = []
    for raw in paths:
        src = Path(raw)
        if not src.is_file():
            errors.append(f"Não achei: {src}")
            continue
        kind = _kind_for(src)
        if not kind:
            errors.append(f"Tipo não suportado: {src.name}")
            continue
        size = src.stat().st_size
        if size > GITHUB_MAX_BYTES:
            errors.append(
                f"{src.name} tem {size / (1024 * 1024):.1f} MB — GitHub recusa acima de ~100 MB."
            )
            continue
        dest = _unique_dest(_safe_name(src.name))
        shutil.copy2(src, dest)
        rel = f"aviso/{dest.name}"
        item: dict[str, Any] = {"type": kind, "src": rel}
        if kind == "video":
            item["loop"] = True
            item["muted"] = False
        else:
            item["seconds"] = 12
        copied.append(item)

    text_item = None
    if text.strip():
        text_item = {
            "type": "text",
            "title": str(cfg.get("title") or "AVISO"),
            "text": text.strip(),
            "seconds": 12,
        }

    if replace and (copied or text_item):
        cfg["items"] = [*copied]
        if text_item:
            cfg["items"].append(text_item)
    else:
        items = list(cfg.get("items") or [])
        items.extend(copied)
        if text_item:
            items.append(text_item)
        cfg["items"] = items

    if not copied and not text_item and errors:
        raise RuntimeError("\n".join(errors))
    save_aviso(cfg)
    return {"ok": True, "copied": copied, "errors": errors, "cfg": cfg}


def clear_aviso() -> dict[str, Any]:
    cfg = _default_cfg()
    save_aviso(cfg)
    return cfg
