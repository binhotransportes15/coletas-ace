from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from config import BASE_DIR, ensure_dirs

CACHE_DIR = BASE_DIR / "data" / "cache"
COLETAS_CSV = CACHE_DIR / "coletas.csv"
HISTORICO_CSV = CACHE_DIR / "historico.csv"
RESUMO_CSV = CACHE_DIR / "resumo_diario.csv"
LAST_RUN_JSON = CACHE_DIR / "last_run.json"

_HEADER_RE = re.compile(
    r"^(?P<unidade>[A-Z]{3})\s+(?P<numero>\d+)\s*-\s*(?P<tipo>\S+)"
    r".*?DATA LIMITE INICIAL:\s*(?P<dt_limite>\d{2}/\d{2})",
    re.IGNORECASE,
)
_SITUACAO_ATUAL_RE = re.compile(
    r"SITUAC[AÃ]O\s+ATUAL:\s*(?P<data>\d{2}/\d{2})?\s*(?P<hora>\d{2}:\d{2})?\s*(?P<status>\w+)?",
    re.IGNORECASE,
)
_STATUS_SIDE_RE = re.compile(
    r"(?P<label>CADASTRADA|COMANDADA|COLETADA|CANCELADA):\s*"
    r"(?P<data>\d{2}/\d{2})?\s*(?P<hora>\d{2}:\d{2})?\s*(?P<usuario>\S+)?",
    re.IGNORECASE,
)
_HIST_RE = re.compile(
    r"^(?:SIT/INSTR:\s*)?(?P<dominio>[A-Z]{3})\s+(?P<unidade>[A-Z]{3})\s+"
    r"(?P<usuario>\S+)\s+(?P<data>\d{2}/\d{2})\s+(?P<hora>\d{2}:\d{2})\s+(?P<obs>.+)$",
    re.IGNORECASE,
)
_FIELD_PATTERNS = {
    "reme": re.compile(r"REME:\s*(.+?)(?:\s{2,}END:|\s+SITUAC|\s*$)", re.IGNORECASE),
    "dest": re.compile(r"DEST:\s*(.+?)(?:\s{2,}END:|\s*$)", re.IGNORECASE),
    "dest_cidade": re.compile(r"DEST:.*?END:\s*(.+?)\s*$", re.IGNORECASE),
    "data_hora_limite": re.compile(
        r"DATA/HORA LIMITE:\s*(\d{2}/\d{2}\s+\d{2}:\d{2})", re.IGNORECASE
    ),
    "solicitante": re.compile(r"SOLICITANTE:\s*(\S+(?:\s+\S+)?)", re.IGNORECASE),
    "motorista": re.compile(r"MOTORISTA:\s*(.+?)(?:\s{2,}|$)", re.IGNORECASE),
    "val_merc": re.compile(r"VAL MERC:\s*([\d.,]+)", re.IGNORECASE),
    "qtde_vol": re.compile(r"QTDE VOL:\s*([\d.,]+)", re.IGNORECASE),
    "peso_kg": re.compile(r"PESO\(KG\):\s*([\d.,]+)", re.IGNORECASE),
    "veiculo": re.compile(r"VEICULO:\s*(.+?)(?:\s{2,}|$)", re.IGNORECASE),
    "merc": re.compile(r"MERC:\s*(.+?)(?:\s{2,}TIPO FRETE:|\s*$)", re.IGNORECASE),
    "tipo_frete": re.compile(r"TIPO FRETE:\s*(\S+)", re.IGNORECASE),
    "nfiscais": re.compile(r"NFISCAIS:\s*(.+?)(?:\s{2,}COMANDADA:|\s{2,}CADASTRADA:|\s*$)", re.IGNORECASE),
    "obs1": re.compile(r"OBS1:\s*(.+?)(?:\s{2,}COLETADA:|\s{2,}CANCELADA:|\s*$)", re.IGNORECASE),
    "obs2": re.compile(r"OBS2:\s*(.+?)(?:\s{2,}CANCELADA:|\s*$)", re.IGNORECASE),
    "obs3": re.compile(r"OBS3:\s*(.+?)\s*$", re.IGNORECASE),
}


@dataclass
class SituacaoEvento:
    data: str = ""
    hora: str = ""
    usuario: str = ""


@dataclass
class HistoricoEvento:
    coleta_id: str
    dominio: str = ""
    unidade: str = ""
    usuario: str = ""
    data: str = ""
    hora: str = ""
    observacao: str = ""

    @property
    def event_key(self) -> str:
        raw = "|".join(
            [
                self.coleta_id,
                self.data,
                self.hora,
                self.usuario,
                self.observacao.strip(),
            ]
        )
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class ColetaRecord:
    coleta_id: str
    unidade: str = ""
    numero: str = ""
    tipo: str = ""
    data_limite_inicial: str = ""
    situacao_atual: str = ""
    situacao_atual_data: str = ""
    situacao_atual_hora: str = ""
    cadastrada_data: str = ""
    cadastrada_hora: str = ""
    cadastrada_usuario: str = ""
    comandada_data: str = ""
    comandada_hora: str = ""
    comandada_usuario: str = ""
    coletada_data: str = ""
    coletada_hora: str = ""
    coletada_usuario: str = ""
    cancelada_data: str = ""
    cancelada_hora: str = ""
    cancelada_usuario: str = ""
    reme: str = ""
    dest: str = ""
    dest_cidade: str = ""
    data_hora_limite: str = ""
    solicitante: str = ""
    motorista: str = ""
    val_merc: str = ""
    qtde_vol: str = ""
    peso_kg: str = ""
    veiculo: str = ""
    merc: str = ""
    tipo_frete: str = ""
    nfiscais: str = ""
    obs1: str = ""
    obs2: str = ""
    obs3: str = ""
    historico: list[HistoricoEvento] = field(default_factory=list)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    # Remove form-feed and nulls; prefer latin-1 (SSW)
    text = raw.decode("latin-1", errors="replace")
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


def _split_blocks(text: str) -> list[str]:
    lines = text.split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _HEADER_RE.search(line.strip()) or _HEADER_RE.search(line):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            # Separador entre coletas
            if line.startswith("---") and len(line.strip("-")) == 0:
                blocks.append(current)
                current = []
            else:
                current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(b) for b in blocks]


def _apply_status(coleta: ColetaRecord, label: str, data: str, hora: str, usuario: str) -> None:
    key = label.upper()
    if key == "CADASTRADA":
        coleta.cadastrada_data = data
        coleta.cadastrada_hora = hora
        coleta.cadastrada_usuario = usuario
    elif key == "COMANDADA":
        coleta.comandada_data = data
        coleta.comandada_hora = hora
        coleta.comandada_usuario = usuario
    elif key == "COLETADA":
        coleta.coletada_data = data
        coleta.coletada_hora = hora
        coleta.coletada_usuario = usuario
    elif key == "CANCELADA":
        coleta.cancelada_data = data
        coleta.cancelada_hora = hora
        coleta.cancelada_usuario = usuario


def _parse_block(block: str) -> ColetaRecord | None:
    lines = block.split("\n")
    if not lines:
        return None
    header_line = lines[0]
    match = _HEADER_RE.search(header_line)
    if not match:
        return None

    unidade = match.group("unidade").upper()
    numero = match.group("numero")
    coleta_id = f"{unidade}{numero}"
    coleta = ColetaRecord(
        coleta_id=coleta_id,
        unidade=unidade,
        numero=numero,
        tipo=_clean(match.group("tipo")),
        data_limite_inicial=match.group("dt_limite") or "",
    )

    # Campos em qualquer linha do bloco (parte esquerda)
    joined = "\n".join(lines)
    for field_name, pattern in _FIELD_PATTERNS.items():
        found = pattern.search(joined)
        if found:
            setattr(coleta, field_name, _clean(found.group(1)))

    # Situacao atual e statuses laterais
    for line in lines:
        atual = _SITUACAO_ATUAL_RE.search(line)
        if atual:
            coleta.situacao_atual = _clean(atual.group("status") or "")
            coleta.situacao_atual_data = atual.group("data") or ""
            coleta.situacao_atual_hora = atual.group("hora") or ""
        for st in _STATUS_SIDE_RE.finditer(line):
            _apply_status(
                coleta,
                st.group("label"),
                st.group("data") or "",
                st.group("hora") or "",
                _clean(st.group("usuario") or ""),
            )

    # Historico SIT/INSTR
    in_hist = False
    current: HistoricoEvento | None = None
    for line in lines:
        stripped = line.strip()
        if "SIT/INSTR:" in line.upper() or stripped.upper().startswith("SIT/INSTR"):
            in_hist = True
        if not in_hist:
            continue
        # Remove prefixo SIT/INSTR:
        work = re.sub(r"^\s*SIT/INSTR:\s*", "", line, flags=re.IGNORECASE).rstrip()
        hist_match = _HIST_RE.match(work.strip())
        if hist_match:
            if current:
                coleta.historico.append(current)
            current = HistoricoEvento(
                coleta_id=coleta_id,
                dominio=hist_match.group("dominio").upper(),
                unidade=hist_match.group("unidade").upper(),
                usuario=_clean(hist_match.group("usuario")),
                data=hist_match.group("data"),
                hora=hist_match.group("hora"),
                observacao=_clean(hist_match.group("obs")),
            )
            continue
        # Continuacao indentada do texto
        if current and work.strip() and not work.strip().startswith("---"):
            # Evita novo cabecalho de coleta
            if _HEADER_RE.search(work.strip()):
                break
            if re.match(r"^[A-Z]{3}\s+[A-Z]{3}\s+\S+\s+\d{2}/\d{2}", work.strip()):
                # outra linha de hist sem passar no regex — tenta de novo
                alt = _HIST_RE.match(work.strip())
                if alt:
                    coleta.historico.append(current)
                    current = HistoricoEvento(
                        coleta_id=coleta_id,
                        dominio=alt.group("dominio").upper(),
                        unidade=alt.group("unidade").upper(),
                        usuario=_clean(alt.group("usuario")),
                        data=alt.group("data"),
                        hora=alt.group("hora"),
                        observacao=_clean(alt.group("obs")),
                    )
                    continue
            current.observacao = _clean(f"{current.observacao} {work.strip()}")
    if current:
        coleta.historico.append(current)

    if not coleta.situacao_atual:
        if coleta.cancelada_data:
            coleta.situacao_atual = "CANCELADA"
        elif coleta.coletada_data:
            coleta.situacao_atual = "COLETADA"
        elif coleta.comandada_data:
            coleta.situacao_atual = "COMANDADA"
        elif coleta.cadastrada_data:
            coleta.situacao_atual = "CADASTRADA"

    return coleta


def parse_ssw0157(path: Path | str) -> list[ColetaRecord]:
    text = _read_text(Path(path))
    blocks = _split_blocks(text)
    coletas: list[ColetaRecord] = []
    seen: set[str] = set()
    for block in blocks:
        rec = _parse_block(block)
        if not rec:
            continue
        # Dedup por coleta_id (relatorio pode repetir em paginas)
        if rec.coleta_id in seen:
            # Preferir bloco com mais historico
            idx = next(i for i, c in enumerate(coletas) if c.coleta_id == rec.coleta_id)
            if len(rec.historico) > len(coletas[idx].historico):
                coletas[idx] = rec
            continue
        seen.add(rec.coleta_id)
        coletas.append(rec)
    return coletas


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_resumo(coletas: list[ColetaRecord]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for c in coletas:
        dia = c.cadastrada_data or c.situacao_atual_data or ""
        if not dia:
            dia = "SEM_DATA"
        bucket = buckets.setdefault(
            dia,
            {
                "data_cadastro": dia,
                "total": 0,
                "cadastrada": 0,
                "comandada": 0,
                "coletada": 0,
                "cancelada": 0,
            },
        )
        bucket["total"] += 1
        status = (c.situacao_atual or "").upper()
        if status == "CANCELADA" or c.cancelada_data:
            bucket["cancelada"] += 1
        elif status == "COLETADA" or c.coletada_data:
            bucket["coletada"] += 1
        elif status == "COMANDADA" or c.comandada_data:
            bucket["comandada"] += 1
        else:
            bucket["cadastrada"] += 1
    return sorted(buckets.values(), key=lambda r: r["data_cadastro"])


def coleta_to_row(c: ColetaRecord) -> dict[str, Any]:
    return {
        "coleta_id": c.coleta_id,
        "unidade": c.unidade,
        "numero": c.numero,
        "tipo": c.tipo,
        "data_limite_inicial": c.data_limite_inicial,
        "situacao_atual": c.situacao_atual,
        "situacao_atual_data": c.situacao_atual_data,
        "situacao_atual_hora": c.situacao_atual_hora,
        "cadastrada_data": c.cadastrada_data,
        "cadastrada_hora": c.cadastrada_hora,
        "cadastrada_usuario": c.cadastrada_usuario,
        "comandada_data": c.comandada_data,
        "comandada_hora": c.comandada_hora,
        "comandada_usuario": c.comandada_usuario,
        "coletada_data": c.coletada_data,
        "coletada_hora": c.coletada_hora,
        "coletada_usuario": c.coletada_usuario,
        "cancelada_data": c.cancelada_data,
        "cancelada_hora": c.cancelada_hora,
        "cancelada_usuario": c.cancelada_usuario,
        "reme": c.reme,
        "dest": c.dest,
        "dest_cidade": c.dest_cidade,
        "data_hora_limite": c.data_hora_limite,
        "solicitante": c.solicitante,
        "motorista": c.motorista,
        "val_merc": c.val_merc,
        "qtde_vol": c.qtde_vol,
        "peso_kg": c.peso_kg,
        "veiculo": c.veiculo,
        "merc": c.merc,
        "tipo_frete": c.tipo_frete,
        "nfiscais": c.nfiscais,
        "obs1": c.obs1,
        "obs2": c.obs2,
        "obs3": c.obs3,
        "qtd_historico": len(c.historico),
    }


def historico_to_rows(coletas: list[ColetaRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in coletas:
        for h in c.historico:
            rows.append(
                {
                    "event_key": h.event_key,
                    "coleta_id": h.coleta_id,
                    "dominio": h.dominio,
                    "unidade": h.unidade,
                    "usuario": h.usuario,
                    "data": h.data,
                    "hora": h.hora,
                    "observacao": h.observacao,
                }
            )
    return rows


COLETA_FIELDS = list(coleta_to_row(ColetaRecord(coleta_id="X")).keys())
HIST_FIELDS = [
    "event_key",
    "coleta_id",
    "dominio",
    "unidade",
    "usuario",
    "data",
    "hora",
    "observacao",
]
RESUMO_FIELDS = [
    "data_cadastro",
    "total",
    "cadastrada",
    "comandada",
    "coletada",
    "cancelada",
]


def save_cache(
    coletas: list[ColetaRecord],
    *,
    source_file: str = "",
    merge: bool = True,
) -> dict[str, Any]:
    """Grava CSVs locais. Com merge=True, preserva dados antigos e faz upsert."""
    ensure_dirs()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    new_coleta_rows = {r["coleta_id"]: r for r in (coleta_to_row(c) for c in coletas)}
    new_hist_rows = {r["event_key"]: r for r in historico_to_rows(coletas)}

    if merge and COLETAS_CSV.exists():
        with COLETAS_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                cid = row.get("coleta_id") or ""
                if cid and cid not in new_coleta_rows:
                    new_coleta_rows[cid] = row
    if merge and HISTORICO_CSV.exists():
        with HISTORICO_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                key = row.get("event_key") or ""
                if key and key not in new_hist_rows:
                    new_hist_rows[key] = row

    coleta_list = sorted(new_coleta_rows.values(), key=lambda r: r.get("coleta_id") or "")
    hist_list = sorted(
        new_hist_rows.values(),
        key=lambda r: (r.get("coleta_id") or "", r.get("data") or "", r.get("hora") or ""),
    )

    # Rebuild coletas objects-ish for resumo from coleta_list
    resumo_source = coletas
    if merge and len(coleta_list) > len(coletas):
        # Resumo so do lote atual + merge simples por situacao_atual do CSV
        pass
    resumo = build_resumo(coletas)
    if merge and RESUMO_CSV.exists():
        merged_resumo: dict[str, dict[str, Any]] = {
            r["data_cadastro"]: r for r in resumo
        }
        with RESUMO_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                dia = row.get("data_cadastro") or ""
                if not dia:
                    continue
                if dia not in merged_resumo:
                    merged_resumo[dia] = {
                        "data_cadastro": dia,
                        "total": int(row.get("total") or 0),
                        "cadastrada": int(row.get("cadastrada") or 0),
                        "comandada": int(row.get("comandada") or 0),
                        "coletada": int(row.get("coletada") or 0),
                        "cancelada": int(row.get("cancelada") or 0),
                    }
                else:
                    # Atualiza com numeros do lote novo (substitui dia)
                    pass
        resumo = sorted(merged_resumo.values(), key=lambda r: r["data_cadastro"])

    _write_csv(COLETAS_CSV, coleta_list, COLETA_FIELDS)
    _write_csv(HISTORICO_CSV, hist_list, HIST_FIELDS)
    _write_csv(RESUMO_CSV, resumo, RESUMO_FIELDS)

    meta = {
        "ok": True,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source_file": source_file,
        "coletas": len(coleta_list),
        "historico": len(hist_list),
        "lote_atual": len(coletas),
        "paths": {
            "coletas": str(COLETAS_CSV),
            "historico": str(HISTORICO_CSV),
            "resumo": str(RESUMO_CSV),
        },
    }
    LAST_RUN_JSON.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def analyze_report(path: Path | str, *, merge: bool = True) -> dict[str, Any]:
    file_path = Path(path)
    coletas = parse_ssw0157(file_path)
    meta = save_cache(coletas, source_file=str(file_path), merge=merge)
    meta["records"] = [asdict(c) for c in coletas]
    return meta
