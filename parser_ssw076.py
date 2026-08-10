"""Parser SSW 076 — Demonstrativo de remuneração (frete por veículo).

Enriquece veículos do 073: frete / coleta-entrega por placa.
Gera sempre com operação R; placas filtradas pela base do 073.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from config import CACHE_DIR, ensure_dirs
from parser_ssw073 import (
    VEICULO_FIELDS,
    VEICULOS_073_CSV,
    RESUMO_073_CSV,
    RESUMO_FIELDS,
    _fmt_money,
    _fmt_peso,
    _parse_money,
    _publish_local,
    _read_text,
    _write_csv,
    analyze_reports_073,
)

FRETE_076_CSV = CACHE_DIR / "frete_076.csv"

FRETE_FIELDS = [
    "placa",
    "carreta",
    "ctrb",
    "custo",
    "frete",
    "peso",
    "fonte",
]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def parse_ssw076(path: Path | str, *, placas_ok: set[str] | None = None) -> list[dict[str, Any]]:
    """
    Aceita CSV/sswweb/xlsx-exportado do 076.
    Heurística de colunas por cabeçalho (placa / frete / peso / ctrb).
    Filtra só placas presentes no 073 quando `placas_ok` é informado.
    """
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return _parse_076_excel(path, placas_ok=placas_ok)
    text = _read_text(path)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    # Detecta header
    header_idx = 0
    for i, ln in enumerate(lines[:8]):
        low = ln.lower()
        if "placa" in low or "frete" in low or "ctrb" in low:
            header_idx = i
            break
    sep = ";" if lines[header_idx].count(";") >= lines[header_idx].count(",") else ","
    headers = [_clean(h).upper() for h in lines[header_idx].split(sep)]
    # remove marcador tipo 0/1
    if headers and headers[0] in {"0", "1", "2"}:
        pass

    def idx(*names: str) -> int:
        for n in names:
            for i, h in enumerate(headers):
                if n in h:
                    return i
        return -1

    i_placa = idx("PLACA CAVALO", "PLACA", "CAVALO")
    i_carreta = idx("CARRETA", "PLACA CARRETA")
    i_ctrb = idx("CTRB", "OS", "DOCUMENTO")
    i_frete = idx("FRETE", "REMUNER", "VALOR FRETE")
    i_peso = idx("PESO")
    i_custo = idx("CUSTO", "ADIANTAMENTO", "VALOR A PAGAR", "TOTAL")

    rows: list[dict[str, Any]] = []
    for ln in lines[header_idx + 1 :]:
        if ln[:2] in {"0;", "1;", "0,", "1,"}:
            continue
        cols = ln.split(sep)
        if cols and cols[0].strip() in {"0", "1", "2"} and len(cols) > 1:
            # mantém alinhamento com headers que incluem tipo
            pass
        placa = _clean(cols[i_placa]).upper() if i_placa >= 0 and i_placa < len(cols) else ""
        if not placa or placa in {"PLACA", "PLACA CAVALO"}:
            continue
        if placas_ok is not None and placa not in placas_ok:
            continue
        frete = _parse_money(cols[i_frete]) if i_frete >= 0 and i_frete < len(cols) else 0.0
        peso = _parse_money(cols[i_peso]) if i_peso >= 0 and i_peso < len(cols) else 0.0
        custo = _parse_money(cols[i_custo]) if i_custo >= 0 and i_custo < len(cols) else 0.0
        carreta = _clean(cols[i_carreta]).upper() if i_carreta >= 0 and i_carreta < len(cols) else ""
        ctrb = _clean(cols[i_ctrb]) if i_ctrb >= 0 and i_ctrb < len(cols) else ""
        rows.append(
            {
                "placa": placa,
                "carreta": carreta,
                "ctrb": ctrb,
                "custo": round(custo, 2),
                "frete": round(frete, 2),
                "peso": round(peso, 3),
                "fonte": path.name,
            }
        )
    return rows


def _parse_076_excel(path: Path, *, placas_ok: set[str] | None) -> list[dict[str, Any]]:
    try:
        import openpyxl
    except ImportError as err:
        raise RuntimeError("076 xlsx: instale openpyxl") from err
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows_raw = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows_raw:
        return []
    # acha header
    header_i = 0
    for i, row in enumerate(rows_raw[:10]):
        joined = " ".join(str(c or "") for c in row).upper()
        if "PLACA" in joined or "FRETE" in joined:
            header_i = i
            break
    headers = [_clean(c).upper() for c in rows_raw[header_i]]

    def idx(*names: str) -> int:
        for n in names:
            for i, h in enumerate(headers):
                if n in h:
                    return i
        return -1

    i_placa = idx("PLACA CAVALO", "PLACA")
    i_carreta = idx("CARRETA")
    i_ctrb = idx("CTRB", "OS")
    i_frete = idx("FRETE", "REMUNER")
    i_peso = idx("PESO")
    i_custo = idx("CUSTO", "ADIANTAMENTO", "VALOR")

    out: list[dict[str, Any]] = []
    for row in rows_raw[header_i + 1 :]:
        cols = list(row)
        placa = _clean(cols[i_placa] if i_placa >= 0 and i_placa < len(cols) else "").upper()
        if not placa:
            continue
        if placas_ok is not None and placa not in placas_ok:
            continue

        def money_at(i: int) -> float:
            if i < 0 or i >= len(cols):
                return 0.0
            v = cols[i]
            if isinstance(v, (int, float)):
                return float(v)
            return _parse_money(str(v or ""))

        out.append(
            {
                "placa": placa,
                "carreta": _clean(cols[i_carreta] if i_carreta >= 0 and i_carreta < len(cols) else "").upper(),
                "ctrb": _clean(cols[i_ctrb] if i_ctrb >= 0 and i_ctrb < len(cols) else ""),
                "custo": round(money_at(i_custo), 2),
                "frete": round(money_at(i_frete), 2),
                "peso": round(money_at(i_peso), 3),
                "fonte": path.name,
            }
        )
    return out


def merge_frete_076_into_073(
    frete_rows: list[dict[str, Any]],
    *,
    on_status: Any = None,
) -> dict[str, Any]:
    """Soma frete (e peso se vier) do 076 nas linhas de veículos do 073."""
    status = on_status or (lambda _m: None)
    ensure_dirs()
    if not VEICULOS_073_CSV.exists():
        raise RuntimeError("076 merge: rode o 073 antes (sem veiculos_073.csv)")

    with VEICULOS_073_CSV.open(encoding="utf-8-sig", newline="") as fh:
        veiculos = list(csv.DictReader(fh))

    by_placa: dict[str, dict[str, float]] = {}
    for r in frete_rows:
        placa = (r.get("placa") or "").upper()
        if not placa:
            continue
        slot = by_placa.setdefault(placa, {"frete": 0.0, "peso": 0.0, "custo": 0.0})
        slot["frete"] += float(r.get("frete") or 0)
        slot["peso"] += float(r.get("peso") or 0)
        slot["custo"] += float(r.get("custo") or 0)

    updated = 0
    for v in veiculos:
        placa = (v.get("placa") or "").upper()
        extra = by_placa.get(placa)
        if not extra:
            continue
        # 076 manda no frete do carro (substitui o frete residual do 073)
        if extra["frete"] > 0:
            v["frete"] = f"{extra['frete']:.2f}"
            updated += 1
        if extra["peso"] > 0 and float(v.get("peso") or 0) <= 0:
            v["peso"] = f"{extra['peso']:.3f}"

    _write_csv(VEICULOS_073_CSV, VEICULO_FIELDS, veiculos)
    _write_csv(FRETE_076_CSV, FRETE_FIELDS, frete_rows)

    total_frete = sum(float(v.get("frete") or 0) for v in veiculos)
    total_custo = sum(float(v.get("custo") or 0) for v in veiculos)
    total_peso = sum(float(v.get("peso") or 0) for v in veiculos)

    resumo = {}
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
    status(f"076 merge: {updated} placa(s) com frete · total R$ {_fmt_money(total_frete)}")
    return {"ok": True, "updated": updated, "resumo": resumo, "frete_rows": len(frete_rows)}


def analyze_reports_076(
    paths: list[Path | str] | Path | str,
    *,
    placas: list[str] | None = None,
    on_status: Any = None,
) -> dict[str, Any]:
    status = on_status or (lambda _m: None)
    path_list = paths if isinstance(paths, (list, tuple)) else [paths]
    placas_ok = {p.upper() for p in (placas or []) if p} or None
    # Por arquivo → total por placa; entre arquivos → MAX (evita dobrar dump completo repetido)
    by_placa: dict[str, dict[str, Any]] = {}
    for p in path_list:
        path = Path(p)
        if not path.exists():
            status(f"076: ausente {path}")
            continue
        chunk = parse_ssw076(path, placas_ok=placas_ok)
        status(f"076: {len(chunk)} linha(s) em {path.name}")
        file_tot: dict[str, dict[str, float]] = {}
        for r in chunk:
            placa = (r.get("placa") or "").upper()
            if not placa:
                continue
            slot = file_tot.setdefault(placa, {"frete": 0.0, "peso": 0.0, "custo": 0.0, "carreta": "", "ctrb": ""})
            slot["frete"] += float(r.get("frete") or 0)
            slot["peso"] += float(r.get("peso") or 0)
            slot["custo"] += float(r.get("custo") or 0)
            if r.get("carreta") and not slot["carreta"]:
                slot["carreta"] = r.get("carreta") or ""
            if r.get("ctrb") and not slot["ctrb"]:
                slot["ctrb"] = r.get("ctrb") or ""
        for placa, slot in file_tot.items():
            cur = by_placa.get(placa)
            if not cur or float(slot["frete"]) >= float(cur.get("frete") or 0):
                by_placa[placa] = {
                    "placa": placa,
                    "carreta": slot["carreta"],
                    "ctrb": slot["ctrb"],
                    "custo": round(slot["custo"], 2),
                    "frete": round(slot["frete"], 2),
                    "peso": round(slot["peso"], 3),
                    "fonte": path.name,
                }
    all_rows = list(by_placa.values())
    all_rows.sort(key=lambda r: r["placa"])
    return merge_frete_076_into_073(all_rows, on_status=status)
