"""
Layout das 6 TVs (grade 2×3).

Normal: cada TV da grade tem seu setor (Armazém, Contratação, …) — como na parede física.
Modo parede: TODAS viram pedaços de UM setor escolhido (uma tela só).
Voltar ao normal: cada TV volta ao setor da grade (sem perder a seleção).
"""
from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
LAYOUT_PATH = _ROOT / "dashboard" / "tv_layout.json"

SECTOR_IDS = (
    "distribuicao",
    "armazem",
    "contratacao",
    "pendencia",
    "mapa",
    "emissao",
)
OPS_VIEWS = ("coleta", "entrega", "agendamento")
ARM_VIEWS = ("patio", "conferentes")
REC_VIEWS = ("sem_transferencia", "sem_saida")
SECTOR_LABELS = {
    "distribuicao": "Distribuição",
    "armazem": "Armazém",
    "contratacao": "Contratação",
    "pendencia": "Pendência",
    "mapa": "Mapa Operacional",
    "reciclagem": "Mapa Operacional",  # legado → mapa
    "emissao": "Emissão",
}


def _slot(sid: int, row: int, col: int, sector: str = "distribuicao") -> dict[str, Any]:
    if sector == "distribuicao":
        mode, view = "rotate", "coleta"
    elif sector == "armazem":
        mode, view = "rotate", "patio"
    else:
        mode, view = "fixed", "coleta"
    return {
        "id": sid,
        "row": row,
        "col": col,
        "sector": sector,
        "mode": mode,
        "view": view,
        "showLogo": None,
        "margins": None,
    }


def default_view_ui(chart: str = "towers") -> dict[str, Any]:
    return {
        "chart": chart,  # towers | pizza | bars
        "scale": "large",  # small | normal | large
        "fontZoom": 1.2,  # 0.7 .. 1.6 — TV 43" parede inicia um pouco maior
        "showKpis": True,
        "showChart": True,
        "showAmanha": True,
        "showStatus": True,
        "locked": False,
        "blocks": [],  # preenchido no editor; vazio = layout padrão do dashboard
    }


def _default_view_ui(chart: str = "towers") -> dict[str, Any]:
    return default_view_ui(chart)


def _normalize_view_ui(raw: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    base = deepcopy(fallback or default_view_ui())
    if not isinstance(raw, dict):
        return base
    chart = str(raw.get("chart") or base["chart"]).lower()
    if chart not in ("towers", "pizza", "bars"):
        chart = base["chart"]
    scale = str(raw.get("scale") or base["scale"]).lower()
    if scale not in ("small", "normal", "large"):
        scale = base["scale"]
    try:
        font_zoom = float(raw.get("fontZoom", base.get("fontZoom", 1.0)))
    except (TypeError, ValueError):
        font_zoom = float(base.get("fontZoom", 1.0) or 1.0)
    font_zoom = max(0.7, min(1.6, font_zoom))
    blocks_raw = raw.get("blocks") if isinstance(raw.get("blocks"), list) else base.get("blocks") or []
    blocks: list[dict[str, Any]] = []
    for b in blocks_raw:
        if not isinstance(b, dict) or not b.get("id"):
            continue
        try:
            blocks.append(
                {
                    "id": str(b["id"]),
                    "label": str(b.get("label") or b["id"]),
                    "x": max(0, min(11, int(b.get("x", 0)))),
                    "y": max(0, min(11, int(b.get("y", 0)))),
                    "w": max(1, min(12, int(b.get("w", 4)))),
                    "h": max(1, min(12, int(b.get("h", 4)))),
                    "visible": bool(b.get("visible", True)),
                }
            )
        except (TypeError, ValueError):
            continue
    return {
        "chart": chart,
        "scale": scale,
        "fontZoom": font_zoom,
        "showKpis": bool(raw.get("showKpis", base["showKpis"])),
        "showChart": bool(raw.get("showChart", base["showChart"])),
        "showAmanha": bool(raw.get("showAmanha", base["showAmanha"])),
        "showStatus": bool(raw.get("showStatus", base["showStatus"])),
        "locked": bool(raw.get("locked", base["locked"])),
        "blocks": blocks,
    }


def default_layout() -> dict[str, Any]:
    # Grade padrão espelhando a parede física (exemplo do usuário)
    slots = [
        _slot(1, 0, 0, "armazem"),
        _slot(2, 0, 1, "contratacao"),
        _slot(3, 0, 2, "pendencia"),
        _slot(4, 1, 0, "emissao"),
        _slot(5, 1, 1, "distribuicao"),
        _slot(6, 1, 2, "mapa"),
    ]
    return {
        "version": 1,
        "updatedAt": "",
        "rows": 2,
        "cols": 3,
        "syncSwap": True,
        "swapMs": 15000,
        "wallMode": False,
        "wallSector": "distribuicao",
        "sectorDefaults": {
            sid: {
                "showLogo": True,
                "margins": "none" if sid == "distribuicao" else "normal",
                "ui": _default_view_ui("towers" if sid == "armazem" else "towers"),
                "views": (
                    {
                        "coleta": _default_view_ui("bars"),
                        "entrega": _default_view_ui("pizza"),
                        "agendamento": _default_view_ui("pizza"),
                    }
                    if sid == "distribuicao"
                    else {
                        "patio": _default_view_ui("towers"),
                        "conferentes": _default_view_ui("towers"),
                    }
                    if sid == "armazem"
                    else {}
                ),
            }
            for sid in SECTOR_IDS
        },
        "slots": slots,
    }


def _migrate_legacy_slot(s: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    sector = str(s.get("sector") or template["sector"]).lower()
    mode = str(s.get("mode") or "rotate").lower()
    view = str(s.get("view") or "coleta").lower()

    if sector in OPS_VIEWS:
        view = sector
        sector = "distribuicao"
        if mode not in ("rotate", "fixed"):
            mode = "fixed"
    if sector in ("dist", "ops", "operacao", "a", "b", "c"):
        sector = "distribuicao"
    if sector in ("rastreamento", "recicla", "reciclagem", "019", "081", "19", "81", "mapa"):
        sector = "mapa"
    if sector not in SECTOR_IDS:
        sector = str(template.get("sector") or "distribuicao")
    if mode not in ("rotate", "fixed"):
        mode = "rotate"
    if sector == "mapa":
        view = "coleta"
        mode = "fixed"
    if sector == "armazem":
        if view in ("armazem", "descarga", "078", "veiculos"):
            view = "patio"
        if view in ("conferente", "177"):
            view = "conferentes"
        if view not in ARM_VIEWS:
            view = "patio"
        if mode not in ("rotate", "fixed"):
            mode = "rotate"
    elif view not in OPS_VIEWS:
        view = "coleta"
    if sector not in ("distribuicao", "armazem"):
        mode = "fixed"
        view = "coleta"

    show = s.get("showLogo", None)
    if show is not None:
        show = bool(show)
    margins = s.get("margins", None)
    if margins is not None:
        margins = "none" if str(margins) == "none" else "normal"

    return {
        "id": int(template["id"]),
        "row": int(template["row"]),
        "col": int(template["col"]),
        "sector": sector,
        "mode": mode,
        "view": view,
        "showLogo": show,
        "margins": margins,
    }


def normalize_layout(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = default_layout()
    if not isinstance(raw, dict):
        return base

    out = deepcopy(base)
    out["version"] = int(raw.get("version") or 1)
    out["updatedAt"] = str(raw.get("updatedAt") or "")
    out["syncSwap"] = bool(raw.get("syncSwap", True))
    try:
        out["swapMs"] = max(5000, int(raw.get("swapMs") or 15000))
    except (TypeError, ValueError):
        out["swapMs"] = 15000
    out["wallMode"] = bool(raw.get("wallMode", raw.get("painelUnico", False)))
    ws = str(raw.get("wallSector") or "distribuicao").lower()
    if ws in OPS_VIEWS:
        ws = "distribuicao"
    if ws not in SECTOR_IDS:
        ws = "distribuicao"
    out["wallSector"] = ws

    defaults_in = raw.get("sectorDefaults") if isinstance(raw.get("sectorDefaults"), dict) else {}
    hubs_legacy = raw.get("hubs") if isinstance(raw.get("hubs"), dict) else {}
    if hubs_legacy and "distribuicao" not in defaults_in:
        ha = hubs_legacy.get("A") if isinstance(hubs_legacy.get("A"), dict) else {}
        defaults_in = {
            **defaults_in,
            "distribuicao": {
                "showLogo": bool(ha.get("showLogo", True)),
                "margins": "none" if str(ha.get("margins") or "") == "none" else "normal",
            },
        }
    for sid in SECTOR_IDS:
        d = defaults_in.get(sid) if isinstance(defaults_in.get(sid), dict) else {}
        base_d = out["sectorDefaults"][sid]
        views_in = d.get("views") if isinstance(d.get("views"), dict) else {}
        views_out: dict[str, Any] = {}
        if sid == "distribuicao":
            for v in OPS_VIEWS:
                views_out[v] = _normalize_view_ui(
                    views_in.get(v),
                    (base_d.get("views") or {}).get(v) or _default_view_ui("towers"),
                )
            # Entrega + Agendamento: pizza (legado remoto ainda gravava "towers"/faixas)
            # Coleta: faixas horizontais
            for v in ("entrega", "agendamento"):
                if v in views_out:
                    views_out[v]["chart"] = "pizza"
            if "coleta" in views_out:
                views_out["coleta"]["chart"] = "bars"
        elif sid == "armazem":
            for v in ARM_VIEWS:
                views_out[v] = _normalize_view_ui(
                    views_in.get(v),
                    (base_d.get("views") or {}).get(v) or _default_view_ui("towers"),
                )
        out["sectorDefaults"][sid] = {
            "showLogo": bool(d.get("showLogo", base_d["showLogo"])),
            "margins": "none" if str(d.get("margins", base_d["margins"])) == "none" else "normal",
            "ui": _normalize_view_ui(d.get("ui"), base_d.get("ui") or _default_view_ui("towers")),
            "views": views_out,
        }

    by_id = {
        int(s["id"]): s
        for s in (raw.get("slots") or [])
        if isinstance(s, dict) and str(s.get("id", "")).isdigit()
    }
    slots = []
    for template in base["slots"]:
        sid = int(template["id"])
        s = by_id.get(sid, template)
        slots.append(_migrate_legacy_slot(s, template))
    out["slots"] = slots
    return out


def load_layout() -> dict[str, Any]:
    if LAYOUT_PATH.is_file():
        try:
            data = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
            return normalize_layout(data if isinstance(data, dict) else None)
        except (OSError, json.JSONDecodeError):
            pass
    return default_layout()


def save_layout(layout: dict[str, Any]) -> dict[str, Any]:
    out = normalize_layout(layout)
    out["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out["version"] = int(out.get("version") or 1) + 1
    LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAYOUT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def resolve_slot(layout: dict[str, Any], slot_id: int) -> dict[str, Any]:
    """Resolve o que a TV deve mostrar agora (respeita modo parede)."""
    lay = normalize_layout(layout)
    slot = next((s for s in lay["slots"] if int(s["id"]) == int(slot_id)), lay["slots"][0])
    wall = bool(lay.get("wallMode"))
    home_sector = slot["sector"]
    sector = lay["wallSector"] if wall else home_sector
    defaults = (lay.get("sectorDefaults") or {}).get(sector) or {
        "showLogo": True,
        "margins": "normal",
    }
    if wall:
        show_logo = int(slot["row"]) == 0 and int(slot["col"]) == 0
        margins = "none"
        mosaic = True
        if sector == "distribuicao":
            mode, view = "rotate", "coleta"
        elif sector == "armazem":
            mode, view = "rotate", "patio"
        else:
            mode, view = "fixed", "coleta"
    else:
        show_logo = defaults["showLogo"] if slot["showLogo"] is None else bool(slot["showLogo"])
        margins = defaults["margins"] if slot["margins"] is None else slot["margins"]
        mosaic = False
        if sector == "distribuicao":
            mode = slot["mode"] if slot["mode"] in ("rotate", "fixed") else "rotate"
            view = slot["view"] if slot["view"] in OPS_VIEWS else "coleta"
        elif sector == "armazem":
            mode = slot["mode"] if slot["mode"] in ("rotate", "fixed") else "rotate"
            view = slot["view"] if slot["view"] in ARM_VIEWS else "patio"
        else:
            mode, view = "fixed", "coleta"

    return {
        "slotId": int(slot["id"]),
        "homeSector": home_sector,
        "sector": sector,
        "mode": mode,
        "view": view,
        "mosaic": mosaic,
        "wallMode": wall,
        "row": int(slot.get("row") or 0),
        "col": int(slot.get("col") or 0),
        "showLogo": show_logo,
        "margins": margins,
        "syncSwap": bool(lay["syncSwap"]) and wall and sector in ("distribuicao", "armazem"),
        "swapMs": int(lay["swapMs"]),
        "layoutVersion": int(lay.get("version") or 1),
        "updatedAt": str(lay.get("updatedAt") or ""),
    }


def wall_on(layout: dict[str, Any], sector: str) -> dict[str, Any]:
    """Liga modo parede: grade (setores por TV) permanece; todas mostram pedaços do setor."""
    out = normalize_layout(layout)
    chosen = str(sector or "distribuicao").lower()
    if chosen in OPS_VIEWS:
        chosen = "distribuicao"
    if chosen not in SECTOR_IDS:
        chosen = "distribuicao"
    out["wallMode"] = True
    out["wallSector"] = chosen
    out["syncSwap"] = chosen in ("distribuicao", "armazem")
    sd = out.setdefault("sectorDefaults", {})
    sd[chosen] = {"showLogo": True, "margins": "none"}
    return out


def wall_off(layout: dict[str, Any]) -> dict[str, Any]:
    """Volta ao normal: cada TV com o setor da grade."""
    out = normalize_layout(layout)
    out["wallMode"] = False
    return out


def mirror_hub(
    layout: dict[str, Any],
    source_hub: str | None = None,
    source_slot_id: int | None = None,
    sector: str | None = None,
) -> dict[str, Any]:
    """Compat: Ativar parede = wall_on (não apaga setores da grade)."""
    chosen = sector
    if not chosen and source_slot_id is not None:
        lay = normalize_layout(layout)
        ref = next((s for s in lay["slots"] if int(s["id"]) == int(source_slot_id)), None)
        if ref:
            chosen = ref.get("sector")
    return wall_on(layout, chosen or "distribuicao")


def apply_painel_unico(
    layout: dict[str, Any],
    logo_hub: str = "A",
    sector: str = "distribuicao",
) -> dict[str, Any]:
    return wall_on(layout, sector)


def push_layout_to_sheets(layout: dict[str, Any], *, retries: int = 3) -> tuple[bool, str]:
    """Envia layout às TVs via Apps Script (Properties). Retry — Google costuma demorar."""
    try:
        from config import load_settings

        cfg = load_settings()
        url = (getattr(cfg, "apps_script_url", None) or "").strip()
        token = (getattr(cfg, "apps_script_token", None) or "").strip()
        if not url or not getattr(cfg, "enable_sheets", False):
            return False, "planilha desligada"
        import urllib.error
        import urllib.request

        body = json.dumps(
            {"action": "tv_layout_set", "token": token, "layout": normalize_layout(layout)},
            ensure_ascii=False,
        ).encode("utf-8")
        last_err = "falha"
        for attempt in range(max(1, retries)):
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=90) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw) if raw else {}
                if data.get("ok"):
                    return True, "ok"
                last_err = str(data.get("error") or "resposta sem ok")
                # Token/deploy errado: não adianta retry
                if "autoriz" in last_err.lower() or "token" in last_err.lower():
                    return False, last_err
            except (TimeoutError, urllib.error.URLError, OSError) as err:
                last_err = str(err)
                if attempt + 1 < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return False, last_err
            except Exception as err:  # noqa: BLE001
                return False, str(err)
        return False, last_err
    except Exception as err:  # noqa: BLE001
        return False, str(err)
