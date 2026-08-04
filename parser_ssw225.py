"""Parser do relatorio SSW 225 — Agendamento de entregas (R=.sswweb / E=CSV)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from config import CACHE_DIR, ensure_dirs
from dates import titulo_agendamento_mes
from ocorrencias_realizadas import is_ocorrencia_realizada

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

# Layout fixo do relatorio R (.sswweb) — fatias [ini:fim)
# CTRC | EMISSAO | REMETENTE | NF | PESO REAL | ... | DESTINATARIO | ... | DESTINO | AGEND EM | AGEND PARA | OCORRENCIA
SSWWEB_SLICE = {
    "ctrc": (0, 11),
    "remetente": (21, 34),
    "peso": (45, 54),
    "frete": (65, 74),
    "destinatario": (92, 105),
    "destino": (135, 149),
    "agendado_em": (150, 158),
    "agendado_para": (159, 173),
    "status": (174, None),
}

CTRC_LINE_RE = re.compile(r"^[A-Z]{3}\d{6}-\d\b")

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
    "peso_total",
    "frete_total",
    "realizado",
    "peso_realizado",
    "frete_realizado",
    "armazem",
    "peso_armazem",
    "frete_armazem",
    "amanha",
    "peso_amanha",
    "frete_amanha",
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
    ENTREGA REALIZADA + códigos 18/53/58/61/93/94/99 → concluido

    No arquivo R (.sswweb) o texto vem truncado + prefixo (ex.: "21 01/08/26 SAIDA PARA ENT").
    """
    o = _norm_ocorr(ocorrencia)
    if "ENTREGA REALIZ" in o:
        return "concluido"
    if is_ocorrencia_realizada(ocorrencia):
        return "concluido"
    if "SAIDA PARA ENT" in o:
        return "em_rota"
    if "CHEGADA EM UNI" in o or "ENTREGA AGENDA" in o:
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


def _parse_num_br(value: str) -> float:
    """Converte '1.331,24' / '552,28' → float."""
    raw = _clean(value)
    if not raw:
        return 0.0
    raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _fmt_num_br(value: float, *, casas: int = 2) -> str:
    if value is None:
        return "0,00"
    txt = f"{float(value):,.{casas}f}"
    return txt.replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_peso_br(value: float) -> str:
    return _fmt_num_br(value, casas=2)


def _fmt_frete_br(value: float) -> str:
    return _fmt_num_br(value, casas=2)


def _slice(line: str, key: str) -> str:
    ini, fim = SSWWEB_SLICE[key]
    if fim is None:
        return _clean(line[ini:] if len(line) > ini else "")
    return _clean(line[ini:fim] if len(line) > ini else "")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _is_sswweb(path: Path, text: str) -> bool:
    suf = path.suffix.lower()
    if suf in {".sswweb", ".txt", ".rel"}:
        return True
    if suf in {".csv", ".xlsx", ".xls"}:
        return False
    head = "\n".join(text.splitlines()[:8]).upper()
    if "AGEND PARA" in head or "AGENDAMENTO DE ENTREGA" in head:
        return True
    if ";" in text.splitlines()[0] if text.splitlines() else "":
        return False
    return bool(re.search(r"^[A-Z]{3}\d{6}-\d", text, re.M))


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


def _build_item(
    *,
    ctrc: str,
    remetente: str,
    destinatario: str,
    destino: str,
    peso: str,
    frete: str,
    agendado_em: str,
    agendado_para: str,
    status_raw: str,
    ref: date,
) -> Agendamento225 | None:
    if not ctrc:
        return None
    status_ace = mapear_status_225(status_raw)
    ag_para_dt = _parse_data_br(agendado_para)
    alerta = bool(ag_para_dt == ref and status_ace not in {"em_rota", "concluido"})
    return Agendamento225(
        ctrc=ctrc,
        remetente=remetente,
        destinatario=destinatario,
        destino=destino,
        peso=peso,
        frete=frete,
        agendado_em=agendado_em,
        agendado_para=agendado_para,
        agendado_para_data=ag_para_dt,
        status_raw=status_raw,
        status_ace=status_ace,
        alerta_sem_saida=alerta,
    )


def parse_ssw225_sswweb(text: str, *, hoje: date | None = None) -> list[Agendamento225]:
    """Layout fixo do arquivo R (relatorio .sswweb) — preserva hora em AGEND PARA."""
    ref = hoje or date.today()
    out: list[Agendamento225] = []
    for line in text.splitlines():
        if not CTRC_LINE_RE.match(line):
            continue
        item = _build_item(
            ctrc=_slice(line, "ctrc"),
            remetente=_slice(line, "remetente"),
            destinatario=_slice(line, "destinatario"),
            destino=_slice(line, "destino"),
            peso=_slice(line, "peso"),
            frete=_slice(line, "frete"),
            agendado_em=_slice(line, "agendado_em"),
            agendado_para=_slice(line, "agendado_para"),
            status_raw=_slice(line, "status"),
            ref=ref,
        )
        if item:
            out.append(item)
    return out


def parse_ssw225_csv(text: str, *, hoje: date | None = None) -> list[Agendamento225]:
    """Layout Excel/CSV (arquivo E) — legado."""
    sample = text.splitlines()[0] if text.splitlines() else ""
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    rows = list(csv.reader(text.splitlines(), delimiter=delim))
    if not rows:
        return []

    start = 0
    head0 = _clean(rows[0][0]).upper() if rows[0] else ""
    if "CTRC" in head0:
        start = 1

    ref = hoje or date.today()
    out: list[Agendamento225] = []
    for row in rows[start:]:
        if len(row) <= max(IDX_STATUS, IDX_CTRC):
            continue
        item = _build_item(
            ctrc=_clean(row[IDX_CTRC]),
            remetente=_clean(row[IDX_REMETENTE]) if len(row) > IDX_REMETENTE else "",
            destinatario=_clean(row[IDX_DESTINATARIO]) if len(row) > IDX_DESTINATARIO else "",
            destino=_clean(row[IDX_DESTINO]) if len(row) > IDX_DESTINO else "",
            peso=_clean(row[IDX_PESO]) if len(row) > IDX_PESO else "",
            frete=_clean(row[IDX_FRETE]) if len(row) > IDX_FRETE else "",
            agendado_em=_clean(row[IDX_AGENDADO_EM]) if len(row) > IDX_AGENDADO_EM else "",
            agendado_para=_clean(row[IDX_AGENDADO_PARA]) if len(row) > IDX_AGENDADO_PARA else "",
            status_raw=_clean(row[IDX_STATUS]) if len(row) > IDX_STATUS else "",
            ref=ref,
        )
        if item:
            out.append(item)
    return out


def parse_ssw225(path: Path | str, *, hoje: date | None = None) -> list[Agendamento225]:
    file_path = Path(path)
    text = _read_text(file_path)
    if _is_sswweb(file_path, text):
        return parse_ssw225_sswweb(text, hoje=hoje)
    return parse_ssw225_csv(text, hoje=hoje)


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
    peso_total = frete_total = 0.0
    peso_realizado = frete_realizado = 0.0
    peso_armazem = frete_armazem = 0.0
    peso_amanha = frete_amanha = 0.0
    amanha = 0
    dia_amanha = ref + timedelta(days=1)

    rows = []
    alertas = []
    for a in items:
        totais[a.status_ace] = totais.get(a.status_ace, 0) + 1
        p = _parse_num_br(a.peso)
        f = _parse_num_br(a.frete)
        peso_total += p
        frete_total += f
        if a.status_ace == "concluido":
            peso_realizado += p
            frete_realizado += f
        if a.status_ace == "parado":
            peso_armazem += p
            frete_armazem += f
        if a.agendado_para_data == dia_amanha:
            amanha += 1
            peso_amanha += p
            frete_amanha += f
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

    rows.sort(key=lambda r: (
        0 if r["alerta_sem_saida"] == "1" else 1,
        r.get("agendado_para") or "99/99/99 99:99",
        r.get("ctrc") or "",
    ))

    periodo_txt = periodo or titulo_agendamento_mes(ref)
    realizado = totais.get("concluido", 0)
    armazem = totais.get("parado", 0)
    resumo = [{
        "periodo": periodo_txt,
        "total": str(len(items)),
        "em_rota": str(totais.get("em_rota", 0)),
        "parado": str(armazem),
        "concluido": str(realizado),
        "alerta": str(totais.get("alerta", 0)),
        "peso_total": _fmt_peso_br(peso_total),
        "frete_total": _fmt_frete_br(frete_total),
        "realizado": str(realizado),
        "peso_realizado": _fmt_peso_br(peso_realizado),
        "frete_realizado": _fmt_frete_br(frete_realizado),
        "armazem": str(armazem),
        "peso_armazem": _fmt_peso_br(peso_armazem),
        "frete_armazem": _fmt_frete_br(frete_armazem),
        "amanha": str(amanha),
        "peso_amanha": _fmt_peso_br(peso_amanha),
        "frete_amanha": _fmt_frete_br(frete_amanha),
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
        "parado": armazem,
        "concluido": realizado,
        "alerta": totais.get("alerta", 0),
        "peso_total": peso_total,
        "frete_total": frete_total,
        "realizado": realizado,
        "armazem": armazem,
        "amanha": amanha,
        "source_file": str(path),
        "cache": str(AGENDAMENTOS_225_CSV),
    }


if __name__ == "__main__":
    import sys

    sample = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"C:\Users\analista.qualidade\Downloads\ssw2862BIN[1]102659.sswweb"
    )
    meta = analyze_report_225(sample, hoje=date(2026, 8, 3))
    print(meta)
    items = parse_ssw225(sample, hoje=date(2026, 8, 3))
    for a in items[:5]:
        print(a.ctrc, "|", a.agendado_para, "|", a.status_raw, "|", a.status_ace)
