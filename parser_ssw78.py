"""Parser da tela 078 · Descarga de Veículos (captura estilo Ctrl+A / tabela)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from config import CACHE_DIR, ensure_dirs
from siglas_filiais import label_origem, normalizar_sigla

VEICULOS_CSV = CACHE_DIR / "veiculos_78.csv"
RESUMO_CSV = CACHE_DIR / "resumo_78.csv"
RAW_TXT = CACHE_DIR / "raw_78.txt"

VEICULO_FIELDS = [
    "origem",
    "origem_sigla",
    "cavalo",
    "carreta",
    "manifesto",
    "peso",
    "peso_num",
    "saida",
    "prev_chegada",
    "chegada",
    "inicio_descarga",
    "final_descarga",
    "status",
    "atrasado",
    "tempo_descarga_min",
    "tempo_descarga",
]

# Descarga em andamento há mais de 4h (sem fim) → status atrasado (vermelho na TV)
LIMITE_DESCARGA_ATRASO_MIN = 4 * 60

RESUMO_FIELDS = [
    "atualizado",
    "total_linhas",
    "total_veiculos",
    "peso_total",
    "finalizado",
    "descarregando",
    "atrasado",
    "aguardando",
    "chegou",
]


def _clean(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ").replace("\u25ba", "").strip()
    return re.sub(r"\s+", " ", text).strip()


def _parse_peso(value: str) -> float:
    raw = _clean(value).replace(".", "").replace(",", ".")
    try:
        return float(raw) if raw else 0.0
    except ValueError:
        return 0.0


def _fmt_peso(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def _parse_dt(value: str, *, hoje: date | None = None) -> datetime | None:
    """
    Aceita:
      HOJE 18:00
      01/08 17:07
      01/08/26 17:07
      31/07 22:20 m.silva  (ignora usuário)
    """
    raw = _clean(value)
    if not raw:
        return None
    ref = hoje or date.today()
    # remove operador no fim
    raw = re.sub(r"\s+[a-zA-Z_.]{2,}$", "", raw).strip()

    m = re.match(r"^HOJE\s+(\d{1,2}):(\d{2})$", raw, flags=re.I)
    if m:
        return datetime(ref.year, ref.month, ref.day, int(m.group(1)), int(m.group(2)))

    m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s+(\d{1,2}):(\d{2})$", raw)
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    y_raw = m.group(3)
    if y_raw:
        y = int(y_raw)
        if y < 100:
            y += 2000
    else:
        y = ref.year
        # virada de ano: se mês > mês atual+1, assume ano anterior
        if mo > ref.month + 1:
            y -= 1
    try:
        return datetime(y, mo, d, int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None


def _fmt_duracao(minutes: int | None) -> str:
    if minutes is None or minutes < 0:
        return ""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h {m:02d}min"
    if h:
        return f"{h}h"
    return f"{m}min"


def mapear_status(
    chegada: str,
    prev: str,
    inicio: str,
    final: str,
    *,
    agora: datetime | None = None,
) -> tuple[str, bool]:
    """
    final_descarga → finalizado
    inicio_descarga + >4h sem fim → atrasado (vermelho)
    inicio_descarga → descarregando
    sem chegada + prev passada → atrasado
    com chegada → chegou
    senão → aguardando (mostra previsão)
    """
    now = agora or datetime.now()
    if _clean(final):
        return "finalizado", False
    if _clean(inicio):
        ini_dt = _parse_dt(inicio, hoje=now.date())
        if ini_dt is not None:
            mins = int((now - ini_dt).total_seconds() // 60)
            if mins > LIMITE_DESCARGA_ATRASO_MIN:
                return "atrasado", True
        return "descarregando", False
    if not _clean(chegada):
        prev_dt = _parse_dt(prev, hoje=now.date())
        if prev_dt and prev_dt < now:
            return "atrasado", True
        return "aguardando", False
    return "chegou", False


@dataclass
class Linha78:
    origem: str = ""
    origem_sigla: str = ""
    cavalo: str = ""
    carreta: str = ""
    manifesto: str = ""
    peso: str = ""
    peso_num: float = 0.0
    saida: str = ""
    prev_chegada: str = ""
    chegada: str = ""
    inicio_descarga: str = ""
    final_descarga: str = ""
    status: str = "aguardando"
    atrasado: bool = False
    tempo_descarga_min: int | None = None
    tempo_descarga: str = ""


def parse_table_rows(rows: list[list[str]], *, agora: datetime | None = None) -> list[Linha78]:
    now = agora or datetime.now()
    out: list[Linha78] = []
    for row in rows:
        cells = [_clean(c) for c in row]
        if not cells:
            continue
        # pula cabeçalho
        head = " ".join(cells[:3]).upper()
        if "ORIGEM" in head and "CAVALO" in head:
            continue
        if len(cells) < 8:
            continue
        # completa até 10 colunas
        while len(cells) < 10:
            cells.append("")
        origem, cavalo, carreta, manifesto, peso = cells[:5]
        if not origem or not cavalo:
            continue
        if origem.upper() in {"ORIGEM", "DOMÍNIO", "DOMINIO"}:
            continue
        saida, prev, chegada, inicio, final = cells[5:10]
        peso_num = _parse_peso(peso)
        status, atrasado = mapear_status(chegada, prev, inicio, final, agora=now)
        ini_dt = _parse_dt(inicio, hoje=now.date())
        fim_dt = _parse_dt(final, hoje=now.date())
        mins = None
        em_andamento = False
        if ini_dt and fim_dt and fim_dt >= ini_dt:
            mins = int((fim_dt - ini_dt).total_seconds() // 60)
        elif ini_dt and not fim_dt:
            # descarregando: quanto já levou até agora
            mins = max(0, int((now - ini_dt).total_seconds() // 60))
            em_andamento = True
        tempo_txt = _fmt_duracao(mins)
        if em_andamento and tempo_txt:
            tempo_txt = f"{tempo_txt} (andamento)"
        sigla = normalizar_sigla(origem)
        out.append(
            Linha78(
                origem=label_origem(sigla),
                origem_sigla=sigla,
                cavalo=cavalo.upper(),
                carreta=carreta.upper(),
                manifesto=manifesto,
                peso=peso,
                peso_num=peso_num,
                saida=saida,
                prev_chegada=prev,
                chegada=chegada,
                inicio_descarga=inicio,
                final_descarga=final,
                status=status,
                atrasado=atrasado,
                tempo_descarga_min=mins,
                tempo_descarga=tempo_txt,
            )
        )
    return out


def _strip_html(fragment: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", fragment or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"(?i)&nbsp;", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return _clean(text)


def parse_html_78(html: str, *, agora: datetime | None = None) -> list[Linha78]:
    """Extrai linhas da tabela #tblsr no HTML da tela 078."""
    raw = html or ""
    # isola a tabela principal
    m = re.search(r'(?is)<table[^>]*id=["\']tblsr["\'][^>]*>(.*?)</table>', raw)
    chunk = m.group(1) if m else raw
    rows_html = re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", chunk)
    table: list[list[str]] = []
    for row_html in rows_html:
        cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row_html)
        if not cells:
            continue
        values = [_strip_html(c) for c in cells]
        # ignora rodapé ×
        if len(values) < 5:
            continue
        table.append(values[:10])
    return parse_table_rows(table, agora=agora)


def parse_body_text(text: str, *, agora: datetime | None = None) -> list[Linha78]:
    """Fallback: transforma o texto Ctrl+A em linhas (blocos separados por linha em branco)."""
    raw = (text or "").replace("\xa0", " ").replace("\u00a0", " ")
    if "Final Descarga" in raw:
        raw = raw.split("Final Descarga", 1)[1]
    for stop in ("Domínio:", "Dominio:", "ssw1257", "Imprimir"):
        i = raw.find(stop)
        if i > 0:
            raw = raw[:i]
            break

    rows: list[list[str]] = []
    # cada veículo/manifesto vem como bloco com campos separados por tab/newline
    for block in re.split(r"\n\s*\n+", raw.strip()):
        parts: list[str] = []
        for line in block.replace("\t", "\n").splitlines():
            p = _clean(line)
            if p and p != "►":
                parts.append(p)
        if len(parts) >= 5 and re.match(r"^[A-Z]{3}$", parts[0], flags=re.I):
            # completa colunas vazias no fim (início/fim descarga)
            while len(parts) < 10:
                parts.append("")
            rows.append(parts[:10])
    return parse_table_rows(rows, agora=agora)


def analyze_78(
    rows: list[list[str]] | None = None,
    *,
    body_text: str = "",
    html: str = "",
    agora: datetime | None = None,
) -> dict[str, Any]:
    ensure_dirs()
    now = agora or datetime.now()
    items = parse_table_rows(rows or [], agora=now) if rows else []
    if not items and html:
        items = parse_html_78(html, agora=now)
    if not items and body_text:
        items = parse_body_text(body_text, agora=now)

    if body_text:
        RAW_TXT.write_text(body_text, encoding="utf-8", errors="replace")

    # totais
    peso_total = sum(i.peso_num for i in items)
    rank = {"atrasado": 0, "descarregando": 1, "chegou": 2, "aguardando": 3, "finalizado": 4}
    veiculos: dict[str, dict[str, Any]] = {}
    for i in items:
        key = f"{i.cavalo}|{i.carreta}"
        slot = veiculos.setdefault(
            key,
            {
                "cavalo": i.cavalo,
                "carreta": i.carreta,
                "origem": i.origem,
                "origem_sigla": i.origem_sigla,
                "peso": 0.0,
                "manifestos": 0,
                "status": i.status,
                "chegada": i.chegada,
                "prev_chegada": i.prev_chegada,
                "tempo_descarga": i.tempo_descarga,
            },
        )
        slot["peso"] += i.peso_num
        slot["manifestos"] += 1
        # prioridade operacional na TV (atrasado > descarregando > …)
        if rank.get(i.status, 9) < rank.get(slot["status"], 9):
            slot["status"] = i.status
            slot["origem"] = i.origem
            slot["origem_sigla"] = i.origem_sigla
            slot["chegada"] = i.chegada
            slot["prev_chegada"] = i.prev_chegada
            slot["tempo_descarga"] = i.tempo_descarga
        elif i.tempo_descarga and not slot.get("tempo_descarga"):
            slot["tempo_descarga"] = i.tempo_descarga

    counts = {"finalizado": 0, "descarregando": 0, "atrasado": 0, "aguardando": 0, "chegou": 0}
    for slot in veiculos.values():
        counts[slot["status"]] = counts.get(slot["status"], 0) + 1

    items.sort(key=lambda x: (rank.get(x.status, 9), x.prev_chegada or "", x.cavalo))

    linha_rows = []
    for i in items:
        linha_rows.append({
            "origem": i.origem,
            "origem_sigla": i.origem_sigla,
            "cavalo": i.cavalo,
            "carreta": i.carreta,
            "manifesto": i.manifesto,
            "peso": i.peso,
            "peso_num": f"{i.peso_num:.0f}",
            "saida": i.saida,
            "prev_chegada": i.prev_chegada,
            "chegada": i.chegada,
            "inicio_descarga": i.inicio_descarga,
            "final_descarga": i.final_descarga,
            "status": i.status,
            "atrasado": "1" if i.atrasado else "0",
            "tempo_descarga_min": "" if i.tempo_descarga_min is None else str(i.tempo_descarga_min),
            "tempo_descarga": i.tempo_descarga,
        })

    # anexa peso por veículo em cada linha
    for row in linha_rows:
        key = f"{row['cavalo']}|{row['carreta']}"
        row["peso_veiculo"] = _fmt_peso(veiculos[key]["peso"])

    VEICULO_FIELDS_OUT = VEICULO_FIELDS + ["peso_veiculo"]
    _write_csv(VEICULOS_CSV, VEICULO_FIELDS_OUT, linha_rows)

    resumo = [{
        "atualizado": now.strftime("%d/%m/%Y %H:%M:%S"),
        "total_linhas": str(len(items)),
        "total_veiculos": str(len(veiculos)),
        "peso_total": _fmt_peso(peso_total),
        "finalizado": str(counts.get("finalizado", 0)),
        "descarregando": str(counts.get("descarregando", 0)),
        "atrasado": str(counts.get("atrasado", 0)),
        "aguardando": str(counts.get("aguardando", 0)),
        "chegou": str(counts.get("chegou", 0)),
    }]
    _write_csv(RESUMO_CSV, RESUMO_FIELDS, resumo)

    return {
        "ok": True,
        "total_linhas": len(items),
        "total_veiculos": len(veiculos),
        "peso_total": peso_total,
        "counts": counts,
        "veiculos": list(veiculos.values()),
        "cache": str(VEICULOS_CSV),
    }


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


if __name__ == "__main__":
    sample = Path(__file__).resolve().parent / "data" / "cache" / "dump_78_full.txt"
    body = sample.read_text(encoding="utf-8") if sample.exists() else ""
    html_path = Path(__file__).resolve().parent / "data" / "cache" / "dump_78.html"
    html = html_path.read_text(encoding="utf-8", errors="replace") if html_path.exists() else ""
    meta = analyze_78(None, body_text=body, html=html, agora=datetime(2026, 8, 3, 11, 50))
    print(meta)
