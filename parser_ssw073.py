"""Parser SSW 073 (CSV ssw0332) — CTRBs/OSs · base do painel Contratação.

Colunas Excel (com tipo de linha na col. A):
  B  CTRB
  G  PLACA CAVALO
  L  PLACA CARRETA 1
  AV ADIANTAMENTO  → custo (pedido)
  AJ VALOR A PAGAR / AO TOTAL CTRB → reforço do custo quando AV=0
  BQ PESO CTRCs · BS FRETE CTRCs
"""
from __future__ import annotations

import csv
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from config import CACHE_DIR, DASHBOARD_DIR, ensure_dirs

VEICULOS_073_CSV = CACHE_DIR / "veiculos_073.csv"
RESUMO_073_CSV = CACHE_DIR / "resumo_073.csv"
CTRBS_073_CSV = CACHE_DIR / "ctrbs_073.csv"

DASH_CONTRATACAO = DASHBOARD_DIR / "data" / "contratacao"

# Índices 0-based (A=0) conforme planilha SSW
COL_CTRB = 1          # B
COL_TIPO = 2          # C
COL_SITUACAO = 3      # D
COL_PLACA = 6         # G
COL_PROPRIEDADE = 7   # H
COL_CARRETA = 11      # L
COL_VALOR_PAGAR = 35  # AJ
COL_TOTAL_CTRB = 40   # AO
COL_CUSTO_AV = 47     # AV — ADIANTAMENTO
COL_PESO = 68         # BQ
COL_FRETE = 70        # BS

CTRBS_FIELDS = [
    "ctrb",
    "tipo",
    "situacao",
    "placa",
    "carreta",
    "propriedade",
    "grupo",
    "custo",
    "custo_av",
    "valor_pagar",
    "total_ctrb",
    "peso",
    "frete",
    "origem",
    "fonte",
]

VEICULO_FIELDS = [
    "placa",
    "carreta",
    "propriedade",
    "grupo",
    "qtd_ctrb",
    "custo",
    "custo_av",
    "valor_pagar",
    "peso",
    "frete",
    "ctrbs",
]

RESUMO_FIELDS = [
    "periodo",
    "atualizado",
    "unidade",
    "total_veiculos",
    "total_ctrbs",
    "custo",
    "custo_fmt",
    "frete",
    "frete_fmt",
    "peso",
    "peso_fmt",
    "agregado",
    "frota",
    "contratados",
    "terceiro",  # legado (= contratados)
]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _parse_money(raw: str) -> float:
    text = _clean(raw).replace(" ", "")
    if not text or text in {"-", "."}:
        return 0.0
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        # "5373.88" ou milhar "5.373"
        if text.count(".") == 1 and len(text.split(".")[-1]) == 2:
            pass
        else:
            text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _fmt_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_peso(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("cp1252", "latin-1", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _grupo_from_fonte(name: str) -> str | None:
    """Detecta grupo pela chave do arquivo (contratacao_073_F_|AC_|AO_)."""
    n = (name or "").upper().replace("-", "_")
    if "073_AC" in n or "_AC_" in n:
        return "contratados"
    if "073_AO" in n or "_AO_" in n:
        return "agregado"
    if "073_F_" in n or n.endswith("073_F") or "_073_F_" in n:
        return "frota"
    return None


def _grupo_propriedade(prop: str, tipo: str = "", fonte: str = "") -> str:
    """frota | contratados | agregado."""
    from_file = _grupo_from_fonte(fonte)
    if from_file:
        return from_file
    p = _clean(prop).upper()
    t = _clean(tipo).upper()
    if p in {"FROTA", "F"} or "FROTA" in p:
        return "frota"
    if p in {"AGREGADO", "A"} or "AGREG" in p:
        # A + Tipo C = contratados · A + Tipo O = agregados
        if t in {"C", "CTRB", "CTR"}:
            return "contratados"
        if t in {"O", "OS"}:
            return "agregado"
        return "agregado"
    if p in {"CARRETEIRO", "TERCEIRO", "TERCEIROS"} or "CARRET" in p or "TERC" in p:
        return "contratados"
    return "outro"


def _cell(cols: list[str], idx: int) -> str:
    return _clean(cols[idx]) if idx < len(cols) else ""


def parse_ssw073(path: Path | str, *, fonte: str = "") -> list[dict[str, Any]]:
    """Lê CSV/sswweb do 073 (ssw0332). Linhas tipo 2 = dados."""
    text = _read_text(Path(path))
    rows: list[dict[str, Any]] = []
    src = fonte or Path(path).name
    for line in text.splitlines():
        if not line.startswith("2;") and not line.startswith("2,"):
            # alguns exports usam só dados sem prefixo após header
            if not line or line[:2] in {"0;", "1;", "0,", "1,"}:
                continue
            # tenta se parece CTRB (SIGLA+numero)
            if not re.match(r"^[A-Z]{2,3}\d", line.split(";")[0] if ";" in line else ""):
                continue
            cols = ["2"] + line.split(";")
        else:
            cols = line.split(";")
        ctrb = _cell(cols, COL_CTRB)
        placa = _cell(cols, COL_PLACA).upper()
        if not ctrb and not placa:
            continue
        if ctrb.upper() in {"CTRB", "TIPO"}:
            continue
        custo_av = _parse_money(_cell(cols, COL_CUSTO_AV))
        valor_pagar = _parse_money(_cell(cols, COL_VALOR_PAGAR))
        total_ctrb = _parse_money(_cell(cols, COL_TOTAL_CTRB))
        # Custo do painel: AV (pedido); se zerado, cai no valor a pagar / total CTRB
        custo = custo_av if custo_av > 0 else (valor_pagar or total_ctrb)
        prop = _cell(cols, COL_PROPRIEDADE)
        tipo_doc = _cell(cols, COL_TIPO)
        rows.append(
            {
                "ctrb": ctrb,
                "tipo": tipo_doc,
                "situacao": _cell(cols, COL_SITUACAO),
                "placa": placa,
                "carreta": _cell(cols, COL_CARRETA).upper(),
                "propriedade": prop,
                "grupo": _grupo_propriedade(prop, tipo_doc, src),
                "custo": round(custo, 2),
                "custo_av": round(custo_av, 2),
                "valor_pagar": round(valor_pagar, 2),
                "total_ctrb": round(total_ctrb, 2),
                "peso": round(_parse_money(_cell(cols, COL_PESO)), 3),
                "frete": round(_parse_money(_cell(cols, COL_FRETE)), 2),
                "origem": _cell(cols, 17),
                "fonte": src,
            }
        )
    return rows


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _publish_local() -> None:
    DASH_CONTRATACAO.mkdir(parents=True, exist_ok=True)
    for src, name in (
        (VEICULOS_073_CSV, "veiculos_073.csv"),
        (RESUMO_073_CSV, "resumo_073.csv"),
        (CTRBS_073_CSV, "ctrbs_073.csv"),
    ):
        if src.exists():
            shutil.copy2(src, DASH_CONTRATACAO / name)


def analyze_reports_073(
    paths: list[Path | str] | Path | str,
    *,
    periodo: str = "",
    unidade: str = "SPO",
    on_status: Any = None,
) -> dict[str, Any]:
    """Agrega 073 → CTRBs + veículos + resumo (qtd / custo / frete / peso)."""
    ensure_dirs()
    status = on_status or (lambda _m: None)
    path_list = paths if isinstance(paths, (list, tuple)) else [paths]
    all_rows: list[dict[str, Any]] = []
    for p in path_list:
        path = Path(p)
        if not path.exists():
            status(f"073: arquivo ausente {path}")
            continue
        chunk = parse_ssw073(path)
        status(f"073: {len(chunk)} CTRB(s) em {path.name}")
        all_rows.extend(chunk)

    # dedupe por CTRB
    by_ctrb: dict[str, dict[str, Any]] = {}
    for r in all_rows:
        key = r.get("ctrb") or f"{r.get('placa')}|{r.get('carreta')}|{r.get('fonte')}"
        prev = by_ctrb.get(key)
        if not prev or float(r.get("custo") or 0) >= float(prev.get("custo") or 0):
            by_ctrb[key] = r
    ctrbs = list(by_ctrb.values())

    # agrega por placa (cavalo)
    by_placa: dict[str, dict[str, Any]] = {}
    for r in ctrbs:
        placa = (r.get("placa") or "").strip().upper()
        if not placa:
            continue
        slot = by_placa.get(placa)
        if not slot:
            slot = {
                "placa": placa,
                "carreta": r.get("carreta") or "",
                "propriedade": r.get("propriedade") or "",
                "grupo": r.get("grupo") or "outro",
                "qtd_ctrb": 0,
                "custo": 0.0,
                "custo_av": 0.0,
                "valor_pagar": 0.0,
                "peso": 0.0,
                "frete": 0.0,
                "ctrbs": [],
            }
            by_placa[placa] = slot
        if r.get("carreta") and not slot["carreta"]:
            slot["carreta"] = r["carreta"]
        # preserva grupo mais específico quando a mesma placa aparece em vários jobs
        g = r.get("grupo") or "outro"
        if g != "outro" and (
            slot["grupo"] in {"", "outro"}
            or (g == "frota" and slot["grupo"] != "frota")
        ):
            if slot["grupo"] in {"", "outro"} or g == "frota":
                slot["grupo"] = g
            elif slot["grupo"] not in {"frota", "contratados", "agregado"}:
                slot["grupo"] = g
        slot["qtd_ctrb"] += 1
        slot["custo"] += float(r.get("custo") or 0)
        slot["custo_av"] += float(r.get("custo_av") or 0)
        slot["valor_pagar"] += float(r.get("valor_pagar") or 0)
        slot["peso"] += float(r.get("peso") or 0)
        slot["frete"] += float(r.get("frete") or 0)
        if r.get("ctrb"):
            slot["ctrbs"].append(r["ctrb"])

    veiculos = []
    for slot in by_placa.values():
        slot["custo"] = round(slot["custo"], 2)
        slot["custo_av"] = round(slot["custo_av"], 2)
        slot["valor_pagar"] = round(slot["valor_pagar"], 2)
        slot["peso"] = round(slot["peso"], 3)
        slot["frete"] = round(slot["frete"], 2)
        slot["ctrbs"] = ",".join(slot["ctrbs"][:12])
        veiculos.append(slot)
    veiculos.sort(key=lambda v: (-float(v["custo"]), v["placa"]))

    total_custo = sum(float(v["custo"]) for v in veiculos)
    total_frete = sum(float(v["frete"]) for v in veiculos)
    total_peso = sum(float(v["peso"]) for v in veiculos)
    grupos = defaultdict(int)
    for v in veiculos:
        grupos[v.get("grupo") or "outro"] += 1

    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    resumo = {
        "periodo": periodo or "",
        "atualizado": now,
        "unidade": unidade or "SPO",
        "total_veiculos": len(veiculos),
        "total_ctrbs": len(ctrbs),
        "custo": round(total_custo, 2),
        "custo_fmt": _fmt_money(total_custo),
        "frete": round(total_frete, 2),
        "frete_fmt": _fmt_money(total_frete),
        "peso": round(total_peso, 3),
        "peso_fmt": _fmt_peso(total_peso),
        "agregado": grupos.get("agregado", 0),
        "frota": grupos.get("frota", 0),
        "contratados": grupos.get("contratados", 0) or grupos.get("terceiro", 0),
        "terceiro": grupos.get("contratados", 0) or grupos.get("terceiro", 0),
    }

    _write_csv(CTRBS_073_CSV, CTRBS_FIELDS, ctrbs)
    _write_csv(VEICULOS_073_CSV, VEICULO_FIELDS, veiculos)
    _write_csv(RESUMO_073_CSV, RESUMO_FIELDS, [resumo])
    _publish_local()
    status(
        f"073 análise: {resumo['total_veiculos']} veículo(s) · "
        f"custo R$ {resumo['custo_fmt']} · frete R$ {resumo['frete_fmt']} · "
        f"peso {resumo['peso_fmt']} kg"
    )
    return {
        "ok": True,
        "resumo": resumo,
        "veiculos": veiculos,
        "ctrbs": ctrbs,
        "placas": [v["placa"] for v in veiculos],
        "files": {
            "veiculos": str(VEICULOS_073_CSV),
            "resumo": str(RESUMO_073_CSV),
            "ctrbs": str(CTRBS_073_CSV),
        },
    }
