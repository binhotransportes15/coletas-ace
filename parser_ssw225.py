"""Parser do relatorio SSW 225 — Agendamento de entregas (CSV/Excel)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from config import CACHE_DIR, ensure_dirs

AGENDAMENTOS_225_CSV = CACHE_DIR / "agendamentos_225.csv"
RESUMO_225_CSV = CACHE_DIR / "resumo_225.csv"
ALERTAS_225_CSV = CACHE_DIR / "alertas_225.csv"

# Colunas pedidas (0-based no CSV ; ):
# A CTRC, D remetente, L destinatario, F peso, H frete, O agendado em, P agendado para, S status
IDX_CTRC = 0
IDX_REMETENTE = 3
IDX_PESO = 5
IDX_FRETE = 7
IDX_DESTINATARIO = 11
IDX_DESTINO = 13
IDX_AGENDADO_EM = 14
IDX_AGENDADO_PARA = 15
IDX_STATUS = 18

AGENDAMENTO_225_FIELDS = [
    "ctrc",
    "remetente",
    "destinatario",
    "destino",
    "peso",
    "frete",
    "agendado_em",
    "agendado_para",
    "agendado_para_data",
    "status_raw",
    "status_ace",
    "alerta_sem_saida",
]

RESUMO_225_FIELDS = [
    "periodo",
    "total",
    "em_rota",
    "parado",
    "concluido",
    "alerta",
]

ALERTA_225_FIELDS = [
    "ctrc",
    "destinatario",
    "destino",
    "agendado_para",
    "status_raw",
]


def _clean(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ").strip()
    # Remove sufixo odd do SSW em CNPJ/nome
    return text.replace("\x9d", "").strip()


def _norm_ocorr(text: str) -> str:
    t = _clean(text).upper()
    for a, b in (
        ("Á", "A"), ("À", "A"), ("Ã", "A"), ("Â", "A"),
        ("É", "E"), ("Ê", "E"), ("Í", "I"),
        ("Ó", "O"), ("Ô", "O"), ("Õ", "O"),
        ("Ú", "U"), ("Ç", "C"),
    ):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def mapear_status_225(ocorrencia: str) -> str:
    """
    SAIDA PARA ENTREGA → em_rota
    CHEGADA EM UNIDADE / ENTREGA AGENDADA → parado
    ENTREGA REALIZADA → concluido
    """
    o = _norm_ocorr(ocorrencia)
    if "ENTREGA REALIZADA" in o:
        return "concluido"
    if "SAIDA PARA ENTREGA" in o:
        return "em_rota"
    if "CHEGADA EM UNIDADE" in o or "ENTREGA AGENDADA" in o:
        return "parado"
    if not o:
        return "parado"
    return "parado"


def _parse_data_br(value: str) -> date | None:
    raw = _clean(value)
    if not raw:
        return None
    # dd/mm/yy ou dd/mm/yyyy [hh:mm]
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", raw)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _fmt_ddmm(d: date | None) -> str:
    return d.strftime("%d/%m") if d else ""


@dataclass
class Agendamento225:
    ctrc: str = ""
    remetente: str = ""
    destinatario: str = ""
    destino: str = ""
    peso: str = ""
    frete: str = ""
    agendado_em: str = ""
    agendado_para: str = ""
    agendado_para_data: date | None = None
    status_raw: str = ""
    status_ace: str = "parado"
    alerta_sem_saida: bool = False


def parse_ssw225(path: Path | str, *, hoje: date | None = None) -> list[Agendamento225]:
    file_path = Path(path)
    raw = file_path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("latin-1", errors="replace")

    # Detecta delimitador
    sample = text.splitlines()[0] if text.splitlines() else ""
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(text.splitlines(), delimiter=delim)
    rows = list(reader)
    if not rows:
        return []

    # Pula cabecalho se parecer header
    start = 0
    head0 = _clean(rows[0][0]).upper() if rows[0] else ""
    if "CTRC" in head0:
        start = 1

    ref = hoje or date.today()
    out: list[Agendamento225] = []
    for row in rows[start:]:
        if len(row) <= max(IDX_STATUS, IDX_CTRC):
            continue
        ctrc = _clean(row[IDX_CTRC])
        if not ctrc:
            continue
        status_raw = _clean(row[IDX_STATUS]) if len(row) > IDX_STATUS else ""
        status_ace = mapear_status_225(status_raw)
        ag_para_raw = _clean(row[IDX_AGENDADO_PARA]) if len(row) > IDX_AGENDADO_PARA else ""
        ag_para_dt = _parse_data_br(ag_para_raw)
        # Alerta: agendado para HOJE e ainda sem saida / sem entrega realizada
        alerta = bool(
            ag_para_dt == ref
            and status_ace not in {"em_rota", "concluido"}
        )
        out.append(
            Agendamento225(
                ctrc=ctrc,
                remetente=_clean(row[IDX_REMETENTE]) if len(row) > IDX_REMETENTE else "",
                destinatario=_clean(row[IDX_DESTINATARIO]) if len(row) > IDX_DESTINATARIO else "",
                destino=_clean(row[IDX_DESTINO]) if len(row) > IDX_DESTINO else "",
                peso=_clean(row[IDX_PESO]) if len(row) > IDX_PESO else "",
                frete=_clean(row[IDX_FRETE]) if len(row) > IDX_FRETE else "",
                agendado_em=_clean(row[IDX_AGENDADO_EM]) if len(row) > IDX_AGENDADO_EM else "",
                agendado_para=ag_para_raw,
                agendado_para_data=ag_para_dt,
                status_raw=status_raw,
                status_ace=status_ace,
                alerta_sem_saida=alerta,
            )
        )
    return out


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def analyze_report_225(
    path: Path | str,
    *,
    periodo: str = "",
    hoje: date | None = None,
) -> dict[str, Any]:
    ensure_dirs()
    ref = hoje or date.today()
    items = parse_ssw225(path, hoje=ref)

    totais = {"em_rota": 0, "parado": 0, "concluido": 0, "alerta": 0}
    rows = []
    alertas = []
    for a in items:
        totais[a.status_ace] = totais.get(a.status_ace, 0) + 1
        if a.alerta_sem_saida:
            totais["alerta"] += 1
            alertas.append({
                "ctrc": a.ctrc,
                "destinatario": a.destinatario,
                "destino": a.destino,
                "agendado_para": a.agendado_para,
                "status_raw": a.status_raw,
            })
        rows.append({
            "ctrc": a.ctrc,
            "remetente": a.remetente,
            "destinatario": a.destinatario,
            "destino": a.destino,
            "peso": a.peso,
            "frete": a.frete,
            "agendado_em": a.agendado_em,
            "agendado_para": a.agendado_para,
            "agendado_para_data": _fmt_ddmm(a.agendado_para_data),
            "status_raw": a.status_raw,
            "status_ace": a.status_ace,
            "alerta_sem_saida": "1" if a.alerta_sem_saida else "0",
        })

    # Ordena: alertas primeiro, depois data agendada, ctrc
    rows.sort(key=lambda r: (
        0 if r["alerta_sem_saida"] == "1" else 1,
        r.get("agendado_para_data") or "99/99",
        r.get("ctrc") or "",
    ))

    periodo_txt = periodo or f"semana {_fmt_ddmm(ref)}"
    resumo = [{
        "periodo": periodo_txt,
        "total": str(len(items)),
        "em_rota": str(totais.get("em_rota", 0)),
        "parado": str(totais.get("parado", 0)),
        "concluido": str(totais.get("concluido", 0)),
        "alerta": str(totais.get("alerta", 0)),
    }]

    _write_csv(AGENDAMENTOS_225_CSV, AGENDAMENTO_225_FIELDS, rows)
    _write_csv(RESUMO_225_CSV, RESUMO_225_FIELDS, resumo)
    _write_csv(ALERTAS_225_CSV, ALERTA_225_FIELDS, alertas)

    return {
        "ok": True,
        "report": "225",
        "periodo": periodo_txt,
        "total": len(items),
        "em_rota": totais.get("em_rota", 0),
        "parado": totais.get("parado", 0),
        "concluido": totais.get("concluido", 0),
        "alerta": totais.get("alerta", 0),
        "source_file": str(path),
        "cache": str(AGENDAMENTOS_225_CSV),
    }


if __name__ == "__main__":
    import sys
    sample = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"C:\Users\analista.qualidade\Downloads\m.aguir100432BIN[1]100432.csv"
    )
    meta = analyze_report_225(sample, hoje=date(2026, 8, 3))
    print(meta)
