"""Parser SSW 073 (CSV ssw0332) — CTRBs/OSs · base do painel Contratação.

Pull único: Propriedade=T · Tipo=A · Relatório.
Mix do painel (coluna TIPO do CSV):
  COLETA/ENTREGA → contratados
  TRANSFERÊNCIA  → agregado
  demais (REMUNERACAO, frota etc.) → ignorados

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
DESTINOS_073_CSV = CACHE_DIR / "destinos_073.csv"

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
COL_ORIGEM_CID = 17   # CIDADE/UF ORIGEM
COL_DESTINO = 18      # UNIDADE DESTINO (sigla — torres)
COL_DESTINO_CID = 19  # CIDADE/UF DESTINO
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
    "destino",
    "cidade_destino",
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

DESTINO_FIELDS = [
    "destino",
    "qtd",
    "custo",
    "frete",
    "peso",
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


def _grupo_from_tipo_csv(tipo: str) -> str | None:
    """Classifica pelo TIPO da linha CSV (não pela propriedade F/A).

    COLETA/ENTREGA → contratados
    TRANSFERÊNCIA / TRANSFERE → agregado
    demais (ex.: REMUNERACAO) → None (ignorado no painel)
    """
    t = _clean(tipo).upper()
    if not t:
        return None
    if "COLETA" in t or "ENTREGA" in t:
        return "contratados"
    if "TRANSFER" in t:
        return "agregado"
    return None


def _grupo_from_fonte(name: str) -> str | None:
    """Legado: chave do arquivo AC/AO. Arquivo único TA não define grupo."""
    n = (name or "").upper().replace("-", "_")
    if "073_TA" in n or "_TA_" in n:
        return None
    if "073_AC" in n or "_AC_" in n or n.startswith("AC_") or "/AC_" in n:
        return "contratados"
    if "073_AO" in n or "_AO_" in n or n.startswith("AO_"):
        return "agregado"
    return None


_GRUPO_RANK = {
    "contratados": 0,
    "agregado": 1,
    "outro": 9,
}


def _grupo_propriedade(prop: str, tipo: str = "", fonte: str = "") -> str:
    """contratados (COLETA/EN) | agregado (TRANSFERÊNCIA). Sem frota."""
    _ = prop  # propriedade do formulário não define o mix do painel
    from_tipo = _grupo_from_tipo_csv(tipo)
    if from_tipo:
        return from_tipo
    from_file = _grupo_from_fonte(fonte)
    if from_file:
        return from_file
    return "outro"


def _cell(cols: list[str], idx: int) -> str:
    return _clean(cols[idx]) if idx < len(cols) else ""


def _header_map(text: str) -> dict[str, int]:
    """Mapeia nome de coluna (upper) → índice a partir da linha tipo 1."""
    for line in text.splitlines()[:12]:
        if not (line.startswith("1;") or line.startswith("1,")):
            continue
        sep = ";" if line.count(";") >= line.count(",") else ","
        headers = [_clean(h).upper() for h in line.split(sep)]
        out: dict[str, int] = {}
        for i, h in enumerate(headers):
            if h and h not in out:
                out[h] = i
        return out
    return {}


def _idx_from_header(hmap: dict[str, int], *names: str, default: int = -1) -> int:
    for n in names:
        nu = n.upper()
        if nu in hmap:
            return hmap[nu]
        for key, idx in hmap.items():
            if nu in key:
                return idx
    return default


def parse_ssw073(path: Path | str, *, fonte: str = "") -> list[dict[str, Any]]:
    """Lê CSV/sswweb do 073 (ssw0332). Linhas tipo 2 = dados."""
    text = _read_text(Path(path))
    rows: list[dict[str, Any]] = []
    src = fonte or Path(path).name
    hmap = _header_map(text)

    i_ctrb = _idx_from_header(hmap, "CTRB", default=COL_CTRB)
    i_tipo = _idx_from_header(hmap, "TIPO", default=COL_TIPO)
    i_sit = _idx_from_header(hmap, "SITUACAO", "SITUAÇÃO", default=COL_SITUACAO)
    i_placa = _idx_from_header(hmap, "PLACA CAVALO", "PLACA", default=COL_PLACA)
    i_prop = _idx_from_header(hmap, "PROPRIEDADE", default=COL_PROPRIEDADE)
    i_carreta = _idx_from_header(hmap, "PLACA CARRETA 1", "PLACA CARRETA", default=COL_CARRETA)
    i_pagar = _idx_from_header(hmap, "VALOR A PAGAR", default=COL_VALOR_PAGAR)
    i_total = _idx_from_header(hmap, "TOTAL CTRB", default=COL_TOTAL_CTRB)
    i_av = _idx_from_header(hmap, "ADIANTAMENTO", default=COL_CUSTO_AV)
    # prefer ADIANTAMENTO puro (não CCF/FORNECEDOR) se existir
    for key, idx in hmap.items():
        if key == "ADIANTAMENTO":
            i_av = idx
            break
    i_peso = _idx_from_header(
        hmap, "PESO CTRCs(CTRB COLETA/ENTREGA)", "PESO CTRCS", "PESO", default=COL_PESO
    )
    i_frete = _idx_from_header(
        hmap, "FRETE CTRCs(CTRB COLETA/ENTREGA)", "FRETE CTRCS", "FRETE", default=COL_FRETE
    )
    i_origem = _idx_from_header(hmap, "CIDADE/UF ORIGEM", "ORIGEM", default=COL_ORIGEM_CID)
    i_destino = _idx_from_header(hmap, "UNIDADE DESTINO", default=COL_DESTINO)
    i_cid_dest = _idx_from_header(
        hmap, "CIDADE/UF DESTINO", "CIDADE DESTINO", default=COL_DESTINO_CID
    )

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
        ctrb = _cell(cols, i_ctrb)
        placa = _cell(cols, i_placa).upper()
        if not ctrb and not placa:
            continue
        if ctrb.upper() in {"CTRB", "TIPO"}:
            continue
        custo_av = _parse_money(_cell(cols, i_av))
        valor_pagar = _parse_money(_cell(cols, i_pagar))
        total_ctrb = _parse_money(_cell(cols, i_total))
        # Custo do painel: AV (pedido); se zerado, cai no valor a pagar / total CTRB
        custo = custo_av if custo_av > 0 else (valor_pagar or total_ctrb)
        prop = _cell(cols, i_prop)
        tipo_doc = _cell(cols, i_tipo)
        destino = _cell(cols, i_destino).upper()
        # evita pegar valor monetário por layout errado
        if destino and ("," in destino or destino.replace(".", "", 1).isdigit()):
            destino = ""
        rows.append(
            {
                "ctrb": ctrb,
                "tipo": tipo_doc,
                "situacao": _cell(cols, i_sit),
                "placa": placa,
                "carreta": _cell(cols, i_carreta).upper(),
                "propriedade": prop,
                "grupo": _grupo_propriedade(prop, tipo_doc, src),
                "custo": round(custo, 2),
                "custo_av": round(custo_av, 2),
                "valor_pagar": round(valor_pagar, 2),
                "total_ctrb": round(total_ctrb, 2),
                "peso": round(_parse_money(_cell(cols, i_peso)), 3),
                "frete": round(_parse_money(_cell(cols, i_frete)), 2),
                "origem": _cell(cols, i_origem),
                "destino": destino,
                "cidade_destino": _cell(cols, i_cid_dest),
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
        (DESTINOS_073_CSV, "destinos_073.csv"),
    ):
        if src.exists():
            shutil.copy2(src, DASH_CONTRATACAO / name)
    # stamp — sem isso o painel Contratação fica com CSV novo e versão antiga
    try:
        import json

        atualizado = ""
        if RESUMO_073_CSV.exists():
            with RESUMO_073_CSV.open(encoding="utf-8-sig", newline="") as fh:
                row = next(csv.DictReader(fh), {}) or {}
                atualizado = str(row.get("atualizado") or "")
        stamp = {
            "ts": datetime.now().timestamp(),
            "atualizado": atualizado or datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        }
        (DASH_CONTRATACAO / "stamp.json").write_text(
            json.dumps(stamp, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def refresh_destinos_frete_from_veiculos() -> None:
    """
    Após merge 076/200: redistribui frete das placas nos destinos (proporcional ao custo CTRB).
    Sem isso as torres ficam com frete zerado / só residual do 073.
    """
    if not VEICULOS_073_CSV.exists() or not CTRBS_073_CSV.exists():
        return
    with VEICULOS_073_CSV.open(encoding="utf-8-sig", newline="") as fh:
        veiculos = list(csv.DictReader(fh))
    with CTRBS_073_CSV.open(encoding="utf-8-sig", newline="") as fh:
        ctrbs = list(csv.DictReader(fh))
    if not veiculos or not ctrbs:
        return

    frete_placa = {
        str(v.get("placa") or "").strip().upper(): float(v.get("frete") or 0)
        for v in veiculos
        if str(v.get("placa") or "").strip()
    }
    # custo por (placa, destino) e por placa
    custo_pd: dict[tuple[str, str], float] = defaultdict(float)
    custo_p: dict[str, float] = defaultdict(float)
    qtd_d: dict[str, int] = defaultdict(int)
    custo_d: dict[str, float] = defaultdict(float)
    peso_d: dict[str, float] = defaultdict(float)

    for r in ctrbs:
        placa = str(r.get("placa") or "").strip().upper()
        dest = str(r.get("destino") or "").strip().upper()
        if not placa or not dest or not re.fullmatch(r"[A-Z]{2,4}", dest):
            continue
        c = float(r.get("custo") or 0)
        custo_pd[(placa, dest)] += c
        custo_p[placa] += c
        qtd_d[dest] += 1
        custo_d[dest] += c
        peso_d[dest] += float(r.get("peso") or 0)

    frete_d: dict[str, float] = defaultdict(float)
    for placa, frete in frete_placa.items():
        if frete <= 0:
            continue
        total_c = custo_p.get(placa) or 0.0
        if total_c <= 0:
            # sem custo: divide igual entre destinos da placa
            dests = [d for (p, d) in custo_pd if p == placa]
            if not dests:
                continue
            share = frete / len(dests)
            for d in dests:
                frete_d[d] += share
            continue
        for (p, d), c in custo_pd.items():
            if p != placa or c <= 0:
                continue
            frete_d[d] += frete * (c / total_c)

    destinos = []
    for dest in sorted(set(qtd_d) | set(frete_d), key=lambda d: (-qtd_d.get(d, 0), d)):
        destinos.append(
            {
                "destino": dest,
                "qtd": int(qtd_d.get(dest, 0)),
                "custo": round(custo_d.get(dest, 0.0), 2),
                "frete": round(frete_d.get(dest, 0.0), 2),
                "peso": round(peso_d.get(dest, 0.0), 3),
            }
        )
    destinos.sort(key=lambda d: (-int(d["qtd"]), d["destino"]))
    _write_csv(DESTINOS_073_CSV, DESTINO_FIELDS, destinos)


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
    # Painel: só COLETA/EN (contratados) e TRANSFERÊNCIA (agregado) — ignora frota/REMUNERACAO
    ctrbs = [
        r
        for r in by_ctrb.values()
        if (r.get("grupo") or "") in {"contratados", "agregado"}
    ]

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
        # preferência: contratados (COLETA/EN) > agregado (TRANSFERÊNCIA)
        g = r.get("grupo") or "outro"
        if _GRUPO_RANK.get(g, 9) < _GRUPO_RANK.get(slot.get("grupo") or "outro", 9):
            slot["grupo"] = g
            if r.get("propriedade"):
                slot["propriedade"] = r["propriedade"]
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

    # Torres: agrega por UNIDADE DESTINO (sigla 2–4 letras)
    by_dest: dict[str, dict[str, Any]] = {}
    for r in ctrbs:
        dest = (r.get("destino") or "").strip().upper()
        if not dest or not re.fullmatch(r"[A-Z]{2,4}", dest):
            continue
        slot = by_dest.get(dest)
        if not slot:
            slot = {"destino": dest, "qtd": 0, "custo": 0.0, "frete": 0.0, "peso": 0.0}
            by_dest[dest] = slot
        slot["qtd"] += 1
        slot["custo"] += float(r.get("custo") or 0)
        slot["frete"] += float(r.get("frete") or 0)
        slot["peso"] += float(r.get("peso") or 0)
    destinos = []
    for slot in by_dest.values():
        slot["custo"] = round(slot["custo"], 2)
        slot["frete"] = round(slot["frete"], 2)
        slot["peso"] = round(slot["peso"], 3)
        destinos.append(slot)
    destinos.sort(key=lambda d: (-int(d["qtd"]), d["destino"]))

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
        "frota": 0,  # painel Contratação não usa mais frota
        "contratados": grupos.get("contratados", 0) or grupos.get("terceiro", 0),
        "terceiro": grupos.get("contratados", 0) or grupos.get("terceiro", 0),
    }

    _write_csv(CTRBS_073_CSV, CTRBS_FIELDS, ctrbs)
    _write_csv(VEICULOS_073_CSV, VEICULO_FIELDS, veiculos)
    _write_csv(DESTINOS_073_CSV, DESTINO_FIELDS, destinos)
    _write_csv(RESUMO_073_CSV, RESUMO_FIELDS, [resumo])
    _publish_local()
    status(
        f"073 análise: {resumo['total_veiculos']} veículo(s) · "
        f"{len(destinos)} destino(s) · "
        f"custo R$ {resumo['custo_fmt']} · frete R$ {resumo['frete_fmt']} · "
        f"peso {resumo['peso_fmt']} kg"
    )
    return {
        "ok": True,
        "resumo": resumo,
        "veiculos": veiculos,
        "ctrbs": ctrbs,
        "destinos": destinos,
        "placas": [v["placa"] for v in veiculos],
        "files": {
            "veiculos": str(VEICULOS_073_CSV),
            "resumo": str(RESUMO_073_CSV),
            "ctrbs": str(CTRBS_073_CSV),
            "destinos": str(DESTINOS_073_CSV),
        },
    }
