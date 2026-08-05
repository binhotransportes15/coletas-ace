"""Parser Excel SSW 031 — CTRCs com determinada ocorrência."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from config import BASE_DIR, ensure_dirs
from ocorrencias_pendencia import (
    OCORR_PENDENCIA,
    OCORR_PENDENCIA_CODES,
    label_ocorrencia,
    match_codigo_from_text,
)

CACHE_DIR = BASE_DIR / "data" / "cache"
PENDENCIAS_31_CSV = CACHE_DIR / "pendencias_31.csv"
RESUMO_31_CSV = CACHE_DIR / "resumo_31.csv"
OFENSORES_31_CSV = CACHE_DIR / "ofensores_31.csv"
LAST_31_JSON = CACHE_DIR / "last_run_31.json"

# Colunas Excel (1-based) — informado pelo usuário
COL_A_CTRC = 1
COL_B_EMISSAO = 2
COL_R_ULTIMA = 18
COL_S_COMPL_ULTIMA = 19
COL_AM_DESC_OCORR = 39
COL_AN_COMPL_OCORR = 40

PENDENCIA_FIELDS = [
    "ctrc",
    "data_emissao",
    "ultima_ocorrencia",
    "historico",
    "codigo",
    "codigo_consulta",
    "descricao_ocorrencia",
    "complemento_ocorrencia",
    "descricao_codigo",
]

RESUMO_FIELDS = [
    "periodo",
    "atualizado",
    "total_ctrcs",
    "total_codigos",
    "topo_codigo",
    "topo_label",
    "topo_qtd",
]

OFENSOR_FIELDS = ["codigo", "label", "qtd", "pct"]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if hasattr(value, "strftime") and not isinstance(value, str):
        try:
            return value.strftime("%d/%m/%Y")
        except Exception:
            pass
    text = str(value).strip()
    if text.lower() in {"none", "nan", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _cell(row: tuple[Any, ...] | list[Any], idx: int) -> str:
    if idx < 1 or idx > len(row):
        return ""
    return _clean(row[idx - 1])


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def parse_excel_31(path: Path | str, *, codigo_consulta: str = "") -> list[dict[str, str]]:
    """Lê A,B,R,S,AM,AN. Última ocorrência = R; histórico = S (complemento)."""
    from openpyxl import load_workbook

    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"31: arquivo inexistente: {p}")
    wb = load_workbook(p, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows_out: list[dict[str, str]] = []
        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if not row:
                continue
            ctrc = _cell(row, COL_A_CTRC)
            if not ctrc:
                continue
            up = ctrc.upper()
            if i <= 3 and ("CTRC" in up or "CONHEC" in up or "NUMERO" in up or "NÚMERO" in up):
                continue
            ultima = _cell(row, COL_R_ULTIMA)
            historico = _cell(row, COL_S_COMPL_ULTIMA)
            desc = _cell(row, COL_AM_DESC_OCORR)
            compl = _cell(row, COL_AN_COMPL_OCORR)
            codigo = match_codigo_from_text(ultima) or match_codigo_from_text(desc) or str(
                codigo_consulta or ""
            ).strip()
            rows_out.append(
                {
                    "ctrc": ctrc.upper().replace(" ", ""),
                    "data_emissao": _cell(row, COL_B_EMISSAO),
                    "ultima_ocorrencia": ultima,
                    "historico": historico,
                    "codigo": codigo,
                    "codigo_consulta": str(codigo_consulta or "").strip(),
                    "descricao_ocorrencia": desc,
                    "complemento_ocorrencia": compl,
                    "descricao_codigo": label_ocorrencia(codigo),
                }
            )
        return rows_out
    finally:
        wb.close()


def _parse_any(path: Path, codigo: str) -> list[dict[str, str]]:
    suf = path.suffix.lower()
    if suf in {".xlsx", ".xlsm", ".xls"}:
        return parse_excel_31(path, codigo_consulta=codigo)
    # CSV fallback: tenta pelas posições se houver muitas colunas
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        delim = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.reader(fh, delimiter=delim)
        out: list[dict[str, str]] = []
        for i, cols in enumerate(reader, start=1):
            if not cols:
                continue
            ctrc = _clean(cols[0] if cols else "")
            if not ctrc or (i <= 2 and "CTRC" in ctrc.upper()):
                continue

            def g(idx: int) -> str:
                return _clean(cols[idx - 1]) if len(cols) >= idx else ""

            ultima = g(COL_R_ULTIMA)
            historico = g(COL_S_COMPL_ULTIMA)
            desc = g(COL_AM_DESC_OCORR)
            codigo_m = match_codigo_from_text(ultima) or match_codigo_from_text(desc) or codigo
            out.append(
                {
                    "ctrc": ctrc.upper().replace(" ", ""),
                    "data_emissao": g(COL_B_EMISSAO),
                    "ultima_ocorrencia": ultima,
                    "historico": historico,
                    "codigo": codigo_m,
                    "codigo_consulta": codigo,
                    "descricao_ocorrencia": desc,
                    "complemento_ocorrencia": g(COL_AN_COMPL_OCORR),
                    "descricao_codigo": label_ocorrencia(codigo_m),
                }
            )
        return out


def _dedupe(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Um CTRC: mantém a linha cuja última ocorrência for a mais específica / última vista."""
    by: dict[str, dict[str, str]] = {}
    for r in rows:
        key = r.get("ctrc") or ""
        if not key:
            continue
        prev = by.get(key)
        if not prev:
            by[key] = r
            continue
        # Prefer linha com código conhecido na última ocorrência
        score_new = 2 if r.get("codigo") in OCORR_PENDENCIA else 1
        score_old = 2 if prev.get("codigo") in OCORR_PENDENCIA else 1
        if score_new >= score_old:
            by[key] = r
    return list(by.values())


def analyze_reports_31(
    paths_by_code: dict[str, str | Path],
    *,
    periodo: str = "",
    on_status: Any = None,
) -> dict[str, Any]:
    status = on_status or (lambda _m: None)
    ensure_dirs()
    all_rows: list[dict[str, str]] = []
    for code, path in (paths_by_code or {}).items():
        p = Path(path)
        if not p.is_file():
            status(f"[31/{code}] arquivo ausente — pulou")
            continue
        try:
            chunk = _parse_any(p, str(code))
            status(f"[31/{code}] {len(chunk)} linha(s) em {p.name}")
            all_rows.extend(chunk)
        except Exception as err:  # noqa: BLE001
            status(f"[31/{code}] parse falhou: {err}")

    uniq = _dedupe(all_rows)
    # Garante classificação pela última ocorrência
    for r in uniq:
        if not r.get("codigo"):
            r["codigo"] = match_codigo_from_text(r.get("ultima_ocorrencia") or "") or r.get(
                "codigo_consulta", ""
            )
        r["descricao_codigo"] = label_ocorrencia(r.get("codigo") or "")

    counts = Counter(str(r.get("codigo") or "").strip() for r in uniq if r.get("codigo"))
    total = len(uniq)
    ofensores: list[dict[str, Any]] = []
    for code, qtd in counts.most_common():
        if code not in OCORR_PENDENCIA and code not in set(OCORR_PENDENCIA_CODES):
            # ainda mostra códigos “outros” com volume
            lab = label_ocorrencia(code) if code else "OUTROS"
        else:
            lab = label_ocorrencia(code)
        ofensores.append(
            {
                "codigo": code or "?",
                "label": lab,
                "qtd": qtd,
                "pct": f"{(100.0 * qtd / total):.1f}".replace(".", ",") if total else "0,0",
            }
        )

    topo = ofensores[0] if ofensores else {"codigo": "", "label": "—", "qtd": 0}
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    resumo = [
        {
            "periodo": periodo or "",
            "atualizado": now,
            "total_ctrcs": total,
            "total_codigos": len(ofensores),
            "topo_codigo": topo.get("codigo") or "",
            "topo_label": topo.get("label") or "—",
            "topo_qtd": int(topo.get("qtd") or 0),
        }
    ]

    _write_csv(PENDENCIAS_31_CSV, PENDENCIA_FIELDS, uniq)
    _write_csv(RESUMO_31_CSV, RESUMO_FIELDS, resumo)
    _write_csv(OFENSORES_31_CSV, OFENSOR_FIELDS, ofensores)
    meta = {
        "ok": True,
        "total": total,
        "ofensores": ofensores[:12],
        "resumo": resumo[0],
        "periodo": periodo,
        "files": {
            "pendencias": str(PENDENCIAS_31_CSV),
            "resumo": str(RESUMO_31_CSV),
            "ofensores": str(OFENSORES_31_CSV),
        },
    }
    LAST_31_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    status(f"31 análise: {total} CTRC(s) · topo={topo.get('codigo')} ({topo.get('qtd')})")
    return meta
