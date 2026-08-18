"""Parser do relatorio SSW 36 (ssw0146) — romaneios e CTRCs de entrega."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from config import BASE_DIR, ensure_dirs
from dates import datetime_corte_emissao_36

CACHE_DIR = BASE_DIR / "data" / "cache"
ENTREGAS_36_CSV = CACHE_DIR / "entregas_36.csv"
ROMANEIOS_36_CSV = CACHE_DIR / "romaneios_36.csv"
RESUMO_36_CSV = CACHE_DIR / "resumo_36.csv"
LAST_36_JSON = CACHE_DIR / "last_run_36.json"

# Indices 0-based no CSV com coluna tipo na posicao 0 (= Excel A)
# B=1 ROMANEIO, C=2 DATA EMISSAO, D=3 HORA EMISSAO, E=4 SITUACAO, F=5 PLACA,
# H=7 MOTORISTA, M=12 CTRC, P=15 DESTINATARIO,
# Z=25 DESC OCORR ROM, AA=26 DATA OCORR ROM, AB=27 HORA OCORR ROM,
# AD=29 DESC OCORR CTRC, AE=30 DATA OCORR CTRC, AF=31 HORA OCORR CTRC
IDX_ROMANEIO = 1
IDX_DATA_EMISSAO = 2  # Excel C
IDX_HORA_EMISSAO = 3  # Excel D — horário de emissão
IDX_SITUACAO = 4
IDX_PLACA = 5
IDX_CARRETA = 6
IDX_MOTORISTA = 7
IDX_CTRC = 12
IDX_DESTINATARIO = 15
IDX_DESC_OCORR_ROM = 25
IDX_DATA_OCORR_ROM = 26
IDX_HORA_OCORR_ROM = 27
IDX_DESC_OCORR_CTRC = 29
IDX_DATA_OCORR_CTRC = 30
IDX_HORA_OCORR_CTRC = 31

ENTREGA_36_FIELDS = [
    "ctrc_id",
    "romaneio",
    "situacao",
    "status_ace",
    "placa",
    "placa_carreta",
    "motorista",
    "destinatario",
    "ocorrencia",
    "data_ocorrencia",
    "hora_ocorrencia",
    "data_emissao",
    "hora_emissao",
    "excluido",
    "motivo_exclusao",
]

ROMANEIO_36_FIELDS = [
    "romaneio",
    "placa",
    "placa_carreta",
    "motorista",
    "total",
    "realizada",
    "em_rota",
    "pendencia",
    "pct",
]

RESUMO_36_FIELDS = [
    "periodo",
    "total",
    "realizada",
    "em_rota",
    "pendencia",
    "excluido",
]


@dataclass
class EntregaCtrc:
    ctrc_id: str
    romaneio: str = ""
    situacao: str = ""
    status_ace: str = ""  # realizada | em_rota | pendencia | excluido
    placa: str = ""
    placa_carreta: str = ""
    motorista: str = ""
    destinatario: str = ""
    ocorrencia: str = ""
    data_ocorrencia: str = ""
    hora_ocorrencia: str = ""
    data_emissao: str = ""
    hora_emissao: str = ""
    excluido: bool = False
    motivo_exclusao: str = ""
    extras: dict[str, str] = field(default_factory=dict)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "nan", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _cell(row: list[str], idx: int) -> str:
    if idx < 0 or idx >= len(row):
        return ""
    return _clean(row[idx])


def _norm_hora(raw: str) -> str:
    text = _clean(raw)
    if not text:
        return ""
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return text[:5]


def _parse_data_ocorr(raw: str) -> date | None:
    text = _clean(raw)
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        try:
            return datetime.strptime(digits, "%d%m%Y").date()
        except ValueError:
            pass
    if len(digits) == 6:
        try:
            return datetime.strptime(digits, "%d%m%y").date()
        except ValueError:
            pass
    return None


def mapear_status_entrega(
    situacao: str,
    ocorrencia: str,
    data_ocorr: date | None,
    *,
    hoje: date | None = None,
) -> tuple[str, bool, str]:
    """
    Status pela coluna AD (DESC OCORR CTRC), com fallback romaneio:

    - SAIDA PARA ENTREGA (ou em branco) → em_rota
    - ENTREGA REALIZADA / PRE-ENTREGUE / CTE BAIXADO → realizada
    - qualquer outra ocorrência → pendencia

    O corte por emissão (≥19h do dia-base) é feito em parse_ssw0146.
    """
    _ = (situacao, data_ocorr, hoje)
    ocorr = _clean(ocorrencia).upper()
    ocorr_n = (
        ocorr.replace("Á", "A")
        .replace("À", "A")
        .replace("Ã", "A")
        .replace("Â", "A")
        .replace("É", "E")
        .replace("Ê", "E")
        .replace("Í", "I")
        .replace("Ó", "O")
        .replace("Ô", "O")
        .replace("Õ", "O")
        .replace("Ú", "U")
        .replace("Ç", "C")
    )

    # Realizada (col. AD)
    if "ENTREGA REALIZADA" in ocorr_n:
        return "realizada", False, ""
    if "PRE-ENTREGUE" in ocorr_n or "PRE ENTREGUE" in ocorr_n:
        return "realizada", False, ""
    if "CTE BAIXADO" in ocorr_n:
        return "realizada", False, ""
    if re.search(r"\bREALIZAD[OA]\b", ocorr_n):
        return "realizada", False, ""

    if not ocorr_n:
        return "em_rota", False, ""

    if "SAIDA PARA ENTREGA" in ocorr_n:
        return "em_rota", False, ""

    return "pendencia", False, ""


def _hora_to_time(raw: str) -> time | None:
    text = _norm_hora(raw)
    if not text:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return None
    return time(hh, mm)


def _combine_emissao(data_raw: str, hora_raw: str) -> datetime | None:
    d = _parse_data_ocorr(data_raw)
    if d is None:
        return None
    t = _hora_to_time(hora_raw) or time(0, 0)
    return datetime.combine(d, t)


def _header_index_map(header: list[str] | None) -> dict[str, int]:
    """Resolve índices por nome do cabeçalho SSW (após coluna tipo)."""
    out: dict[str, int] = {
        "romaneio": IDX_ROMANEIO,
        "data_emissao": IDX_DATA_EMISSAO,
        "hora_emissao": IDX_HORA_EMISSAO,
        "situacao": IDX_SITUACAO,
        "placa": IDX_PLACA,
        "carreta": IDX_CARRETA,
        "motorista": IDX_MOTORISTA,
        "ctrc": IDX_CTRC,
        "destinatario": IDX_DESTINATARIO,
        "desc_ocorr_rom": IDX_DESC_OCORR_ROM,
        "data_ocorr_rom": IDX_DATA_OCORR_ROM,
        "hora_ocorr_rom": IDX_HORA_OCORR_ROM,
        "desc_ocorr_ctrc": IDX_DESC_OCORR_CTRC,
        "data_ocorr_ctrc": IDX_DATA_OCORR_CTRC,
        "hora_ocorr_ctrc": IDX_HORA_OCORR_CTRC,
    }
    if not header:
        return out
    # header inclui a coluna tipo em [0]
    for i, name in enumerate(header):
        key = re.sub(r"\s+", " ", _clean(name).upper())
        key_n = (
            key.replace("Á", "A")
            .replace("À", "A")
            .replace("Ã", "A")
            .replace("Â", "A")
            .replace("É", "E")
            .replace("Ê", "E")
            .replace("Í", "I")
            .replace("Ó", "O")
            .replace("Ô", "O")
            .replace("Õ", "O")
            .replace("Ú", "U")
            .replace("Ç", "C")
        )
        if key_n in {"ROMANEIO"}:
            out["romaneio"] = i
        elif "DATA" in key_n and "EMISSAO" in key_n:
            out["data_emissao"] = i
        elif ("HORA" in key_n and "EMISSAO" in key_n) or key_n in {"HORA", "HR EMISSAO"}:
            # Coluna D costuma ser só HORA / HORA EMISSAO
            out["hora_emissao"] = i
        elif key_n == "SITUACAO":
            out["situacao"] = i
        elif key_n == "PLACA":
            out["placa"] = i
        elif "CARRETA" in key_n:
            out["carreta"] = i
        elif key_n == "MOTORISTA":
            out["motorista"] = i
        elif key_n == "CTRC":
            out["ctrc"] = i
        elif "DESTINATARIO" in key_n or "NOME DESTINATARIO" in key_n:
            out["destinatario"] = i
        elif "DESC OCORR ROM" in key_n:
            out["desc_ocorr_rom"] = i
        elif "DATA OCORR ROM" in key_n:
            out["data_ocorr_rom"] = i
        elif "HORA OCORR ROM" in key_n:
            out["hora_ocorr_rom"] = i
        elif "DESC OCORR CTRC" in key_n:
            out["desc_ocorr_ctrc"] = i
        elif "DATA OCORR CTRC" in key_n:
            out["data_ocorr_ctrc"] = i
        elif "HORA OCORR CTRC" in key_n:
            out["hora_ocorr_ctrc"] = i
    # Se não achou HORA EMISSAO por nome, mantém Excel D (=3)
    return out


def _read_ssw_rows(path: Path) -> tuple[list[str] | None, list[list[str]]]:
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")

    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(";")]
        if not parts:
            continue
        kind = parts[0]
        if kind == "1" and header is None:
            header = parts
            continue
        if kind == "0":
            continue
        if kind == "2" or (kind and kind[0].isalpha()):
            # linhas de dados (tipo 2) ou sem prefixo
            if kind != "2" and kind and kind[0].isalpha():
                parts = [""] + parts  # desloca para alinhar com indices B=1
            rows.append(parts)
    return header, rows


def parse_ssw0146(
    path: Path | str,
    *,
    hoje: date | None = None,
) -> list[EntregaCtrc]:
    file_path = Path(path)
    header, rows = _read_ssw_rows(file_path)
    idx = _header_index_map(header)
    ref = hoje or date.today()
    corte = datetime_corte_emissao_36(ref)
    out: list[EntregaCtrc] = []
    seen: set[str] = set()

    for row in rows:
        romaneio = _cell(row, idx["romaneio"])
        ctrc = _cell(row, idx["ctrc"])
        if not romaneio and not ctrc:
            continue
        ctrc_id = ctrc or f"{romaneio}#"
        # dedupe por CTRC (mantem primeira)
        key = ctrc_id.upper().replace(" ", "")
        if key in seen:
            continue
        seen.add(key)

        data_emi = _cell(row, idx["data_emissao"])
        hora_emi = _norm_hora(_cell(row, idx["hora_emissao"]))
        emi_dt = _combine_emissao(data_emi, hora_emi)

        desc_ctrc = _cell(row, idx["desc_ocorr_ctrc"])
        data_ctrc = _cell(row, idx["data_ocorr_ctrc"])
        hora_ctrc = _cell(row, idx["hora_ocorr_ctrc"])
        desc_rom = _cell(row, idx["desc_ocorr_rom"])
        data_rom = _cell(row, idx["data_ocorr_rom"])
        hora_rom = _cell(row, idx["hora_ocorr_rom"])

        # Texto da ocorrencia: CTRC (linha) com fallback ROM
        ocorrencia = desc_ctrc or desc_rom
        data_txt = data_rom or data_ctrc
        if desc_ctrc:
            hora_txt = _norm_hora(hora_ctrc) or _norm_hora(hora_rom)
        else:
            hora_txt = _norm_hora(hora_rom) or _norm_hora(hora_ctrc)
        data_d = _parse_data_ocorr(data_txt)

        situacao = _cell(row, idx["situacao"])
        status, excluido, motivo = mapear_status_entrega(
            situacao, ocorrencia, data_d, hoje=ref
        )

        # Corte operacional: só emissão ≥ 19:00 do dia-base (sex na seg / ontem).
        # Realizadas NÃO entram no corte — entrega concluída permanece no painel.
        if not excluido and status != "realizada":
            if emi_dt is None:
                # Sem data/hora de emissão: mantém só se ocorrência for hoje
                if data_d is None or data_d != ref:
                    excluido = True
                    motivo = "sem_emissao_fora_ciclo"
                    status = "excluido"
            elif emi_dt < corte:
                excluido = True
                motivo = "emissao_antes_19h"
                status = "excluido"

        # PENDENTE + sem realizada → em_rota
        if not excluido and situacao.upper() == "PENDENTE" and status != "realizada":
            if not ocorrencia:
                status = "em_rota"

        out.append(
            EntregaCtrc(
                ctrc_id=ctrc_id,
                romaneio=romaneio,
                situacao=situacao,
                status_ace=status,
                placa=_cell(row, idx["placa"]),
                placa_carreta=_cell(row, idx["carreta"]),
                motorista=_cell(row, idx["motorista"]),
                destinatario=_cell(row, idx["destinatario"]),
                ocorrencia=ocorrencia,
                data_ocorrencia=data_txt,
                hora_ocorrencia=hora_txt,
                data_emissao=data_emi,
                hora_emissao=hora_emi,
                excluido=excluido,
                motivo_exclusao=motivo,
            )
        )
    return out


def agregar_romaneios(ctrcs: list[EntregaCtrc]) -> list[dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for c in ctrcs:
        if c.excluido or c.status_ace == "excluido":
            continue
        key = c.romaneio or "?"
        if key not in by:
            by[key] = {
                "romaneio": key,
                "placa": c.placa,
                "placa_carreta": c.placa_carreta,
                "motorista": c.motorista,
                "total": 0,
                "realizada": 0,
                "em_rota": 0,
                "pendencia": 0,
            }
        row = by[key]
        row["total"] += 1
        if c.status_ace in row:
            row[c.status_ace] += 1
        if not row["placa"] and c.placa:
            row["placa"] = c.placa
        if not row["motorista"] and c.motorista:
            row["motorista"] = c.motorista
        if not row["placa_carreta"] and c.placa_carreta:
            row["placa_carreta"] = c.placa_carreta
    result = []
    for row in by.values():
        total = int(row["total"] or 0)
        feitas = int(row["realizada"] or 0)
        row["pct"] = str(round((feitas / total) * 100) if total else 0)
        result.append(row)
    result.sort(key=lambda r: (-int(r["realizada"]), -int(r["total"]), r["romaneio"]))
    return result


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def analyze_report_36(
    path: Path | str,
    *,
    periodo: str = "",
    hoje: date | None = None,
) -> dict[str, Any]:
    ensure_dirs()
    file_path = Path(path)
    ref = hoje or date.today()
    all_rows = parse_ssw0146(file_path, hoje=ref)
    ativos = [c for c in all_rows if not c.excluido]
    excluidos = [c for c in all_rows if c.excluido]

    totais = {"realizada": 0, "em_rota": 0, "pendencia": 0, "excluido": len(excluidos)}
    for c in ativos:
        if c.status_ace in totais:
            totais[c.status_ace] += 1

    ctrc_rows = [
        {
            "ctrc_id": c.ctrc_id,
            "romaneio": c.romaneio,
            "situacao": c.situacao,
            "status_ace": c.status_ace,
            "placa": c.placa,
            "placa_carreta": c.placa_carreta,
            "motorista": c.motorista,
            "destinatario": c.destinatario,
            "ocorrencia": c.ocorrencia,
            "data_ocorrencia": c.data_ocorrencia,
            "hora_ocorrencia": c.hora_ocorrencia,
            "data_emissao": c.data_emissao,
            "hora_emissao": c.hora_emissao,
            "excluido": "1" if c.excluido else "0",
            "motivo_exclusao": c.motivo_exclusao,
        }
        for c in ativos
    ]
    # tambem grava excluidos no CSV com flag (gestao pode filtrar)
    for c in excluidos:
        ctrc_rows.append(
            {
                "ctrc_id": c.ctrc_id,
                "romaneio": c.romaneio,
                "situacao": c.situacao,
                "status_ace": "excluido",
                "placa": c.placa,
                "placa_carreta": c.placa_carreta,
                "motorista": c.motorista,
                "destinatario": c.destinatario,
                "ocorrencia": c.ocorrencia,
                "data_ocorrencia": c.data_ocorrencia,
                "hora_ocorrencia": c.hora_ocorrencia,
                "data_emissao": c.data_emissao,
                "hora_emissao": c.hora_emissao,
                "excluido": "1",
                "motivo_exclusao": c.motivo_exclusao,
            }
        )

    romaneios = agregar_romaneios(ativos)
    # Sempre rótulo do ciclo até a data de hoje
    from dates import format_period, periodo_36_ontem_hoje

    _ = periodo  # caller pode passar; label segue a data corrente
    periodo_txt = format_period(*periodo_36_ontem_hoje(ref))
    resumo = [
        {
            "periodo": periodo_txt,
            "total": str(len(ativos)),
            "realizada": str(totais["realizada"]),
            "em_rota": str(totais["em_rota"]),
            "pendencia": str(totais["pendencia"]),
            "excluido": str(totais["excluido"]),
        }
    ]

    _write_csv(ENTREGAS_36_CSV, ENTREGA_36_FIELDS, ctrc_rows)
    _write_csv(ROMANEIOS_36_CSV, ROMANEIO_36_FIELDS, romaneios)
    _write_csv(RESUMO_36_CSV, RESUMO_36_FIELDS, resumo)

    meta = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(file_path),
        "periodo": periodo_txt,
        "lote": len(ativos),
        "excluido": len(excluidos),
        "totais": totais,
        "romaneios": len(romaneios),
        "modelo": (
            "36 ssw0146: ciclo = emissão ≥19:00 do dia-base "
            "(sexta na segunda; ontem nos demais) até hoje (col. C data + D hora). "
            "blank/SAIDA PARA ENTREGA→em_rota, "
            "ENTREGA REALIZADA/PRE-ENTREGUE/CTE BAIXADO→realizada, "
            "qualquer outra col.AD→pendencia."
        ),
    }
    LAST_36_JSON.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta


if __name__ == "__main__":
    import sys

    sample = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not sample or not sample.exists():
        print("Uso: python parser_ssw0146.py <arquivo.sswweb>")
        raise SystemExit(1)
    # Exemplo do usuario era periodo 30-31/07; força hoje=31/07/2026 para teste
    ref = date(2026, 7, 31)
    meta = analyze_report_36(sample, periodo="30/07 a 31/07", hoje=ref)
    print(json.dumps(meta, indent=2, ensure_ascii=False))
