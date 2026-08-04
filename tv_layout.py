"""
Layout das 6 TVs (grade 2×3).

Modelo:
  - Cada retângulo = TV física (slot 1..6), link #tv/slot/N
  - Setor da TV = o que ela mostra (Distribuição, Armazém, …) — o “hub” É o setor
  - Espelhar parede = só nas TVs que JÁ estão no mesmo setor; liga mosaico 2×3
    (cada TV = um pedaço) sem mudar setor das outras TVs
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
    "rastreamento",
    "emissao",
)
OPS_VIEWS = ("coleta", "entrega", "agendamento")
SECTOR_LABELS = {
    "distribuicao": "Distribuição",
    "armazem": "Armazém",
    "contratacao": "Contratação",
    "pendencia": "Pendência",
    "rastreamento": "Rastreamento",
    "emissao": "Emissão",
}


def _slot(sid: int, row: int, col: int) -> dict[str, Any]:
    return {
        "id": sid,
        "row": row,
        "col": col,
        "sector": "distribuicao",
        "mode": "rotate",
        "view": "coleta",
        "mosaic": False,
        "showLogo": None,
        "margins": None,
    }


def default_layout() -> dict[str, Any]:
    slots = [
        _slot(1, 0, 0),
        _slot(2, 0, 1),
        _slot(3, 0, 2),
        _slot(4, 1, 0),
        _slot(5, 1, 1),
        _slot(6, 1, 2),
    ]
    return {
        "version": 1,
        "updatedAt": "",
        "rows": 2,
        "cols": 3,
        "syncSwap": True,
        "swapMs": 15000,
        "painelUnico": False,
        "sectorDefaults": {
            sid: {
                "showLogo": sid == "distribuicao",
                "margins": "none" if sid == "distribuicao" else "normal",
            }
            for sid in SECTOR_IDS
        },
        "slots": slots,
    }


def _migrate_legacy_slot(s: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    sector = str(s.get("sector") or template["sector"]).lower()
    mode = str(s.get("mode") or "rotate").lower()
    view = str(s.get("view") or "coleta").lower()

    # legado: hub A/B/C ignorado — setor manda
    if sector in OPS_VIEWS:
        view = sector
        sector = "distribuicao"
        if mode not in ("rotate", "fixed"):
            mode = "fixed"
    if sector in ("dist", "ops", "operacao", "a", "b", "c"):
        # hub letters were never sectors
        if sector in ("a", "b", "c"):
            sector = "distribuicao"
        else:
            sector = "distribuicao"
    if sector not in SECTOR_IDS:
        sector = "distribuicao"
    if mode not in ("rotate", "fixed"):
        mode = "rotate"
    if view not in OPS_VIEWS:
        view = "coleta"
    if sector != "distribuicao":
        mode = "fixed"

    show = s.get("showLogo", None)
    if show is not None:
        show = bool(show)
    margins = s.get("margins", None)
    if margins is not None:
        margins = "none" if str(margins) == "none" else "normal"
    mosaic = bool(s.get("mosaic", False))
    if sector != "distribuicao":
        mosaic = False

    return {
        "id": int(template["id"]),
        "row": int(template["row"]),
        "col": int(template["col"]),
        "sector": sector,
        "mode": mode,
        "view": view,
        "mosaic": mosaic,
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
    out["painelUnico"] = bool(raw.get("painelUnico", False))

    defaults_in = raw.get("sectorDefaults") if isinstance(raw.get("sectorDefaults"), dict) else {}
    # legado hubs → defaults do setor distribuicao (logo/margens)
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
        out["sectorDefaults"][sid] = {
            "showLogo": bool(d.get("showLogo", base_d["showLogo"])),
            "margins": "none" if str(d.get("margins", base_d["margins"])) == "none" else "normal",
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
    lay = normalize_layout(layout)
    slot = next((s for s in lay["slots"] if int(s["id"]) == int(slot_id)), lay["slots"][0])
    defaults = (lay.get("sectorDefaults") or {}).get(slot["sector"]) or {
        "showLogo": True,
        "margins": "normal",
    }
    show_logo = defaults["showLogo"] if slot["showLogo"] is None else bool(slot["showLogo"])
    margins = defaults["margins"] if slot["margins"] is None else slot["margins"]
    return {
        "slotId": int(slot["id"]),
        "sector": slot["sector"],
        "mode": slot["mode"],
        "view": slot["view"],
        "mosaic": bool(slot.get("mosaic")),
        "row": int(slot.get("row") or 0),
        "col": int(slot.get("col") or 0),
        "showLogo": show_logo,
        "margins": margins,
        "syncSwap": bool(lay["syncSwap"]),
        "swapMs": int(lay["swapMs"]),
        "painelUnico": bool(lay["painelUnico"]),
        "layoutVersion": int(lay.get("version") or 1),
        "updatedAt": str(lay.get("updatedAt") or ""),
    }


def mirror_hub(
    layout: dict[str, Any],
    source_hub: str | None = None,  # legado, ignorado
    source_slot_id: int | None = None,
) -> dict[str, Any]:
    """
    Espelhar parede NESTE SETOR.

    - Usa a TV selecionada como referência (setor + tela).
    - Só altera TVs que JÁ têm o mesmo setor (não mexe em Armazém se a ref é Dist).
    - Distribuição: liga mosaico 2×3; logo só no canto superior esquerdo do grupo;
      copia mode/view para o grupo ficar igual; sync ligado.
    """
    out = normalize_layout(layout)
    ref = None
    if source_slot_id is not None:
        ref = next(
            (s for s in out["slots"] if int(s["id"]) == int(source_slot_id)),
            None,
        )
    if ref is None:
        ref = out["slots"][0]

    sector = ref["sector"]
    peers = [s for s in out["slots"] if s["sector"] == sector]
    if not peers:
        peers = [ref]

    is_dist = sector == "distribuicao"
    # canto “origem” do mosaico = menor row,col entre as TVs deste setor
    origin = min(peers, key=lambda s: (int(s["row"]), int(s["col"])))

    for s in out["slots"]:
        if s["sector"] != sector:
            continue  # outras TVs / outros setores intactos
        s["mode"] = ref["mode"]
        s["view"] = ref["view"]
        if is_dist:
            s["mosaic"] = True
            s["margins"] = "none"
            s["showLogo"] = (
                int(s["row"]) == int(origin["row"]) and int(s["col"]) == int(origin["col"])
            )
        else:
            s["mosaic"] = False
            s["showLogo"] = None
            s["margins"] = None

    if is_dist:
        out["syncSwap"] = True
        out["painelUnico"] = True
        sd = out.setdefault("sectorDefaults", {})
        sd["distribuicao"] = {"showLogo": True, "margins": "none"}
    return out


def apply_painel_unico(
    layout: dict[str, Any],
    logo_hub: str = "A",  # legado
) -> dict[str, Any]:
    """Atalho: parede Distribuição nas TVs que já estão em Distribuição."""
    out = normalize_layout(layout)
    ref = next((s for s in out["slots"] if s["sector"] == "distribuicao"), None)
    if ref is None:
        # nenhuma TV em Dist ainda → usa TV 1 e marca Dist
        ref = out["slots"][0]
        ref["sector"] = "distribuicao"
        ref["mode"] = "rotate"
        ref["view"] = "coleta"
    return mirror_hub(out, source_slot_id=int(ref["id"]))


def push_layout_to_sheets(layout: dict[str, Any]) -> tuple[bool, str]:
    try:
        from config import load_settings

        cfg = load_settings()
        url = (getattr(cfg, "apps_script_url", None) or "").strip()
        token = (getattr(cfg, "apps_script_token", None) or "").strip()
        if not url or not getattr(cfg, "enable_sheets", False):
            return False, "planilha desligada"
        import urllib.request

        body = json.dumps(
            {"action": "tv_layout_set", "token": token, "layout": normalize_layout(layout)},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw else {}
        if data.get("ok"):
            return True, "ok"
        return False, str(data.get("error") or "resposta sem ok")
    except Exception as err:  # noqa: BLE001
        return False, str(err)
