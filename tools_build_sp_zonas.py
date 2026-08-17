# -*- coding: utf-8 -*-
"""Gera data/mapa/sp_zonas.json — distrito → zona (cores CyberMap / MapaCustoRegiaoSP)."""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GEO = ROOT / "dashboard" / "data" / "mapa" / "sp_distritos.geojson"
OUT = ROOT / "dashboard" / "data" / "mapa" / "sp_zonas.json"

# Cores neon cyberpunk claras por zona
ZONAS = {
    "CENTRO": {"label": "Centro", "color": "#FFF56A"},
    "LESTE": {"label": "Leste", "color": "#7CFF4A"},
    "NORTE": {"label": "Norte", "color": "#FFB347"},
    "OESTE": {"label": "Oeste", "color": "#FF5AE8"},
    "SUL": {"label": "Sul", "color": "#C9A0FF"},
}

# Agrupamento CyberMap (nomes sem acento para match flexível)
ZONE_DISTRICTS = {
    "CENTRO": [
        "bela vista",
        "bom retiro",
        "bras",
        "cambuci",
        "consolacao",
        "liberdade",
        "pari",
        "republica",
        "santa cecilia",
        "se",
    ],
    "LESTE": [
        "agua rasa",
        "aricanduva",
        "artur alvim",
        "belem",
        "cangaiba",
        "carrao",
        "cidade lider",
        "cidade tiradentes",
        "ermelino matarazzo",
        "guaianases",
        "iguatemi",
        "itaim paulista",
        "itaquera",
        "jardim helena",
        "jose bonifacio",
        "lajeado",
        "mooca",
        "parque do carmo",
        "penha",
        "ponte rasa",
        "sapopemba",
        "sao lucas",
        "sao mateus",
        "sao miguel",
        "sao rafael",
        "tatuape",
        "vila curuca",
        "vila formosa",
        "vila jacui",
        "vila matilde",
        "vila prudente",
    ],
    "NORTE": [
        "anhanguera",
        "brasilandia",
        "cachoeirinha",
        "casa verde",
        "freguesia do o",
        "jacana",
        "jaragua",
        "limao",
        "mandaqui",
        "perus",
        "pirituba",
        "santana",
        "sao domingos",
        "tremembe",
        "tucuruvi",
        "vila guilherme",
        "vila maria",
        "vila medeiros",
    ],
    "OESTE": [
        "alto de pinheiros",
        "barra funda",
        "butanta",
        "itaim bibi",
        "jaguara",
        "jaguare",
        "jardim paulista",
        "lapa",
        "morumbi",
        "perdizes",
        "pinheiros",
        "raposo tavares",
        "rio pequeno",
        "vila leopoldina",
        "vila sonia",
    ],
    "SUL": [
        "campo belo",
        "campo grande",
        "campo limpo",
        "capao redondo",
        "cidade ademar",
        "cidade dutra",
        "cursino",
        "grajau",
        "ipiranga",
        "jabaquara",
        "jardim sao luis",
        "jardim angela",
        "marsilac",
        "moema",
        "parelheiros",
        "pedreira",
        "sacoma",
        "santo amaro",
        "saude",
        "socorro",
        "vila andrade",
        "vila mariana",
    ],
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("M"))
    return " ".join(s.lower().split())


def main() -> None:
    geo = json.loads(GEO.read_text(encoding="utf-8"))
    rev: dict[str, str] = {}
    for zone, names in ZONE_DISTRICTS.items():
        for n in names:
            rev[_norm(n)] = zone

    districts: dict[str, dict] = {}
    missing: list[str] = []
    for feat in geo["features"]:
        name = str(feat["properties"].get("name") or "")
        zone = rev.get(_norm(name))
        if not zone:
            missing.append(name)
            zone = "LESTE"
        districts[name] = {
            "zona": zone,
            "label": ZONAS[zone]["label"],
            "color": ZONAS[zone]["color"],
        }
        feat["properties"]["zona"] = zone
        feat["properties"]["zona_label"] = ZONAS[zone]["label"]
        feat["properties"]["zona_color"] = ZONAS[zone]["color"]

    payload = {"zonas": ZONAS, "distritos": districts}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp = GEO.with_suffix(".geojson.tmp")
    tmp.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    tmp.replace(GEO)
    print(f"OK {len(districts)} distritos -> {OUT.name}")
    if missing:
        print("sem match:", missing)


if __name__ == "__main__":
    main()
