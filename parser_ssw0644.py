"""Parser SSW 200 / ssw0644 — Relação de Manifestos Operacionais.

Usa coluna FRETE-R$ agregada por PLACA_CAVALO para enriquecer o painel Contratação.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from config import CACHE_DIR, ensure_dirs
from parser_ssw073 import (
    RESUMO_073_CSV,
    RESUMO_FIELDS,
    VEICULO_FIELDS,
    VEICULOS_073_CSV,
    _fmt_money,
    _fmt_peso,
    _parse_money,
    _publish_local,
    _read_text,
    _write_csv,
)

FRETE_200_CSV = CACHE_DIR / "frete_200.csv"

FRETE_FIELDS = [
    "placa",
    "carreta",
    "manifestos",
    "frete",
    "peso",
    "fonte",
]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _norm_placa(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def parse_ssw0644(path: Path | str, *, placas_ok: set[str] | None = None) -> list[dict[str, Any]]:
    """Lê CSV ssw0644 (linha 0=título, 1=header, 2+=dados)."""
    path = Path(path)
    text = _read_text(path)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []

    header_idx = 0
    for i, ln in enumerate(lines[:6]):
        low = ln.upper()
        if "PLACA_CAVALO" in low or "FRETE-R$" in low or "NUM_MANIF" in low:
            header_idx = i
            break

    sep = ";" if lines[header_idx].count(";") >= lines[header_idx].count(",") else ","
    headers = [_clean(h).upper() for h in lines[header_idx].split(sep)]

    def idx(*names: str) -> int:
        for n in names:
            nu = n.upper()
            for i, h in enumerate(headers):
                if nu == h or nu in h:
                    return i
        return -1

    i_placa = idx("PLACA_CAVALO", "PLACA CAVALO")
    i_carreta = idx("PLACA_CARRETA", "PLACA CARRETA")
    i_frete = idx("FRETE-R$", "FRETE")
    i_peso = idx("PESO REAL (KG)", "PESO_CALCULO", "PESO CALCULO (KG)", "PESO")
    i_manif = idx("NUM_MANIF")

    if i_placa < 0 or i_frete < 0:
        raise RuntimeError(f"0644: colunas placa/frete nao encontradas em {path.name}")

    placas_norm = {_norm_placa(p) for p in placas_ok} if placas_ok else None

    # agrega por placa (vários manifestos)
    agg: dict[str, dict[str, Any]] = {}
    for ln in lines[header_idx + 1 :]:
        cols = ln.split(sep)
        if not cols:
            continue
        # pula linhas título (tipo 0/1)
        if cols[0].strip() in {"0", "1"} and len(cols) < 5:
            continue
        placa = _norm_placa(cols[i_placa]) if i_placa < len(cols) else ""
        if not placa or placa in {"PLACACAVALO", "PLACA"}:
            continue
        if placas_norm is not None and placa not in placas_norm:
            continue
        frete = _parse_money(cols[i_frete]) if i_frete < len(cols) else 0.0
        peso = _parse_money(cols[i_peso]) if i_peso >= 0 and i_peso < len(cols) else 0.0
        carreta = _clean(cols[i_carreta]).upper() if i_carreta >= 0 and i_carreta < len(cols) else ""
        manif = _clean(cols[i_manif]) if i_manif >= 0 and i_manif < len(cols) else ""
        slot = agg.setdefault(
            placa,
            {"placa": placa, "carreta": carreta, "manifestos": 0, "frete": 0.0, "peso": 0.0, "fonte": path.name},
        )
        slot["frete"] += frete
        slot["peso"] += peso
        slot["manifestos"] += 1
        if carreta and not slot["carreta"]:
            slot["carreta"] = carreta
        if manif:
            pass

    rows = []
    for slot in agg.values():
        rows.append(
            {
                "placa": slot["placa"],
                "carreta": slot["carreta"],
                "manifestos": slot["manifestos"],
                "frete": round(slot["frete"], 2),
                "peso": round(slot["peso"], 3),
                "fonte": slot["fonte"],
            }
        )
    rows.sort(key=lambda r: r["placa"])
    return rows


def merge_frete_200_into_073(
    frete_rows: list[dict[str, Any]],
    *,
    on_status: Any = None,
) -> dict[str, Any]:
    """Aplica frete do 200 nas placas do 073 (substitui frete quando > 0)."""
    status = on_status or (lambda _m: None)
    ensure_dirs()
    if not VEICULOS_073_CSV.exists():
        raise RuntimeError("200 merge: rode o 073 antes (sem veiculos_073.csv)")

    with VEICULOS_073_CSV.open(encoding="utf-8-sig", newline="") as fh:
        veiculos = list(csv.DictReader(fh))

    by_placa = {_norm_placa(r.get("placa")): r for r in frete_rows if r.get("placa")}

    # frete do 200 só enriquece — nunca zera/altera custo (Excel/dia anterior)
    updated = 0
    for v in veiculos:
        placa = _norm_placa(v.get("placa"))
        extra = by_placa.get(placa)
        if not extra:
            continue
        frete = float(extra.get("frete") or 0)
        if frete > 0:
            v["frete"] = f"{frete:.2f}"
            updated += 1
        # custo permanece como estava (dia anterior / Excel)
    _write_csv(VEICULOS_073_CSV, VEICULO_FIELDS, veiculos)
    _write_csv(FRETE_200_CSV, FRETE_FIELDS, frete_rows)

    total_frete = sum(float(v.get("frete") or 0) for v in veiculos)
    total_custo = sum(float(v.get("custo") or 0) for v in veiculos)
    total_peso = sum(float(v.get("peso") or 0) for v in veiculos)

    resumo: dict[str, Any] = {}
    if RESUMO_073_CSV.exists():
        with RESUMO_073_CSV.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
            resumo = rows[0] if rows else {}
    resumo["atualizado"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    resumo["frete"] = round(total_frete, 2)
    resumo["frete_fmt"] = _fmt_money(total_frete)
    resumo["custo"] = round(total_custo, 2)
    resumo["custo_fmt"] = _fmt_money(total_custo)
    resumo["peso"] = round(total_peso, 3)
    resumo["peso_fmt"] = _fmt_peso(total_peso)
    resumo["total_veiculos"] = len(veiculos)
    _write_csv(RESUMO_073_CSV, RESUMO_FIELDS, [resumo])
    try:
        from parser_ssw073 import refresh_destinos_frete_from_veiculos

        refresh_destinos_frete_from_veiculos()
    except Exception:
        pass
    _publish_local()
    status(f"200 merge: {updated} placa(s) com frete · total R$ {_fmt_money(total_frete)}")
    return {
        "ok": True,
        "updated": updated,
        "resumo": resumo,
        "frete_rows": len(frete_rows),
        "frete_total": round(sum(float(r.get("frete") or 0) for r in frete_rows), 2),
    }


def analyze_reports_200(
    paths: list[Path | str] | Path | str,
    *,
    placas: list[str] | None = None,
    on_status: Any = None,
) -> dict[str, Any]:
    status = on_status or (lambda _m: None)
    path_list = paths if isinstance(paths, (list, tuple)) else [paths]
    placas_ok = {_norm_placa(p) for p in (placas or []) if p} or None
    all_rows: list[dict[str, Any]] = []
    # re-agrega se vários arquivos
    by_placa: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"placa": "", "carreta": "", "manifestos": 0, "frete": 0.0, "peso": 0.0, "fonte": ""}
    )
    for p in path_list:
        path = Path(p)
        if not path.exists():
            status(f"200: ausente {path}")
            continue
        chunk = parse_ssw0644(path, placas_ok=placas_ok)
        status(f"200: {len(chunk)} placa(s) em {path.name}")
        for r in chunk:
            placa = r["placa"]
            slot = by_placa[placa]
            slot["placa"] = placa
            slot["carreta"] = slot["carreta"] or r.get("carreta") or ""
            slot["manifestos"] += int(r.get("manifestos") or 0)
            slot["frete"] += float(r.get("frete") or 0)
            slot["peso"] += float(r.get("peso") or 0)
            slot["fonte"] = r.get("fonte") or path.name
    all_rows = [
        {
            "placa": s["placa"],
            "carreta": s["carreta"],
            "manifestos": s["manifestos"],
            "frete": round(s["frete"], 2),
            "peso": round(s["peso"], 3),
            "fonte": s["fonte"],
        }
        for s in by_placa.values()
    ]
    all_rows.sort(key=lambda r: r["placa"])
    return merge_frete_200_into_073(all_rows, on_status=status)
