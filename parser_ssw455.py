"""Parser Excel SSW 455 — Fretes Expedidos/Recebidos (Emissão).

Agrega KPIs do painel:
  CTEs · Peso · Valor mercadoria · Volumes · Cubagem · Frete
  DIA / NOITE (hora de autorização) · Expedidores (coluna K · login) · Cancelados · Picos 0–23h
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from config import BASE_DIR, ensure_dirs
from dates import MESES_PT

CACHE_DIR = BASE_DIR / "data" / "cache"
EMISSOES_455_CSV = CACHE_DIR / "emissoes_455.csv"
RESUMO_455_CSV = CACHE_DIR / "resumo_455.csv"
EXPEDIDORES_455_CSV = CACHE_DIR / "expedidores_455.csv"
HORAS_455_CSV = CACHE_DIR / "horas_455.csv"
LAST_455_JSON = CACHE_DIR / "last_run_455.json"

# DIA = 06:00–17:59 · NOITE = 18:00–05:59 (hora de autorização)
DIA_START = 6
DIA_END = 18  # exclusive

# Excel 455: coluna K = Login (1-based) — base dos expedidores
COL_K_LOGIN = 11

EMISSAO_FIELDS = [
    "ctrc",
    "data_emissao",
    "hora_autorizacao",
    "expedidor",
    "frete",
    "valor_mercadoria",
    "peso",
    "volumes",
    "cubagem",
    "cancelado",
    "unidade",
    "liquidacao",
]

RESUMO_FIELDS = [
    "periodo",
    "mes",
    "atualizado",
    "ctes",
    "peso",
    "peso_fmt",
    "valor_mercadoria",
    "valor_mercadoria_fmt",
    "volumes",
    "cubagem",
    "cubagem_fmt",
    "frete",
    "frete_fmt",
    "dia",
    "noite",
    "cancelados",
]

EXPEDIDOR_FIELDS = ["nome", "nome_exibicao", "qtd", "pct"]
HORA_FIELDS = ["hora", "label", "qtd"]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M:%S")
    if hasattr(value, "strftime") and not isinstance(value, str):
        try:
            return value.strftime("%d/%m/%Y")
        except Exception:
            pass
    text = str(value).strip()
    if text.lower() in {"none", "nan", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _num(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    neg = text.startswith("(") and text.endswith(")")
    text = text.replace("R$", "").replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    text = re.sub(r"[^\d.\-]", "", text)
    try:
        n = float(text or 0)
        return -n if neg else n
    except Exception:
        return 0.0


def _fmt_int(n: float | int) -> str:
    return f"{int(round(float(n) or 0)):,}".replace(",", ".")


def _fmt_dec(n: float, places: int = 2) -> str:
    s = f"{float(n or 0):,.{places}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_money(n: float) -> str:
    return f"R$ {_fmt_dec(n, 2)}"


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _norm_header(h: Any) -> str:
    t = _clean(h).lower()
    t = t.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("é", "e")
    t = t.replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ê", "e")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _map_headers(headers: list[Any]) -> dict[str, int]:
    """Mapeia nomes canônicos → índice 0-based."""
    norms = [_norm_header(h) for h in headers]
    out: dict[str, int] = {}

    def pick(keys: tuple[str, ...], *aliases: str) -> None:
        for i, n in enumerate(norms):
            if not n:
                continue
            for a in aliases:
                if a in n:
                    for k in keys:
                        out.setdefault(k, i)
                    return

    pick(("ctrc",), "ctrc", "conhecimento", "cte", "nr cte", "n cte")
    pick(("data_emissao",), "data emissao", "emissao", "dt emissao")
    pick(("hora_autorizacao",), "hora autoriz", "hr autoriz", "autorizacao", "data autoriz")
    pick(("data_autorizacao",), "data autoriz", "dt autoriz")
    # Coluna K costuma ser Login — prioriza "login"
    pick(("expedidor",), "login", "usuario", "usuário", "expedidor", "emitente")
    pick(("frete",), "valor frete", "frete total", "vl frete", "frete")
    pick(("valor_mercadoria",), "valor mercadoria", "vl mercadoria", "mercadoria")
    pick(("peso",), "peso real", "peso total", "peso kg", "peso")
    pick(("volumes",), "volumes", "qtde volume", "volume")
    pick(("cubagem",), "cubagem", "m3", "metro cub")
    pick(("cancelado",), "cancelado", "anulado", "situacao cte")
    pick(("liquidacao",), "liquidacao", "liq ")
    pick(("unidade",), "unidade", "filial", "sigla")
    return out


def _parse_hora(row: dict[str, Any], raw_row: tuple[Any, ...] | list[Any], colmap: dict[str, int]) -> int | None:
    """Extrai hora 0–23 da autorização."""
    # datetime cell
    for key in ("hora_autorizacao", "data_autorizacao"):
        idx = colmap.get(key)
        if idx is None or idx >= len(raw_row):
            continue
        val = raw_row[idx]
        if isinstance(val, datetime):
            return val.hour
        if hasattr(val, "hour") and not isinstance(val, str):
            try:
                return int(val.hour)
            except Exception:
                pass
        text = _clean(val)
        if not text:
            continue
        m = re.search(r"(\d{1,2}):(\d{2})", text)
        if m:
            return max(0, min(23, int(m.group(1))))
        m = re.search(r"\b(\d{1,2})h", text, re.I)
        if m:
            return max(0, min(23, int(m.group(1))))
        # só data sem hora → None
    # fallback: data_emissao se tiver hora
    idx = colmap.get("data_emissao")
    if idx is not None and idx < len(raw_row):
        val = raw_row[idx]
        if isinstance(val, datetime):
            return val.hour
        text = _clean(val)
        m = re.search(r"(\d{1,2}):(\d{2})", text)
        if m:
            return max(0, min(23, int(m.group(1))))
    return None


def _is_cancelado(row: dict[str, str]) -> bool:
    blob = " ".join(
        [
            row.get("cancelado") or "",
            row.get("liquidacao") or "",
            row.get("ctrc") or "",
        ]
    ).lower()
    if re.search(r"\bcancel|\banulad|\bsubstitu", blob):
        return True
    liq = (row.get("liquidacao") or "").strip().upper()[:1]
    return liq == "C"


def _is_expedidor(nome: str) -> bool:
    """Qualquer login não vazio da coluna K conta."""
    return bool((nome or "").strip())


def _display_expedidor(nome: str) -> str:
    """Exibe o login (coluna K), sem sufixo * se houver."""
    t = (nome or "").strip().rstrip("*＊").strip()
    if not t:
        return "—"
    # Preferência visual: 1º token em maiúsculas (ex.: bia.regina → BIA.REGINA / BIA)
    parts = re.split(r"[\s._\-]+", t)
    if parts and len(parts[0]) >= 2:
        return parts[0].upper()
    return t.upper()


def _login_from_row(row: tuple[Any, ...] | list[Any], colmap: dict[str, int]) -> str:
    """Sempre coluna K (login). Header 'login' só reforça se bater na K."""
    idx_k = COL_K_LOGIN - 1  # 0-based
    login_k = ""
    if idx_k < len(row):
        login_k = _clean(row[idx_k])
    idx_h = colmap.get("expedidor")
    if idx_h is not None and idx_h == idx_k and login_k:
        return login_k
    # Se o header mapeou outra coluna chamada login, ainda prioriza K
    if login_k:
        return login_k
    if idx_h is not None and idx_h < len(row):
        return _clean(row[idx_h])
    return ""


def parse_excel_455(path: Path | str) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"455: arquivo inexistente: {p}")

    # .sswweb / csv fallback
    if p.suffix.lower() in {".csv", ".sswweb", ".txt"}:
        return _parse_text_455(p)

    wb = load_workbook(p, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header_row = None
        colmap: dict[str, int] = {}
        out: list[dict[str, Any]] = []
        for i, row in enumerate(rows_iter, start=1):
            if not row:
                continue
            if header_row is None:
                # detect header
                joined = " ".join(_clean(c).lower() for c in row if c is not None)
                if any(
                    k in joined
                    for k in ("ctrc", "cte", "frete", "emiss", "conhecimento", "peso")
                ):
                    header_row = row
                    colmap = _map_headers(list(row))
                    continue
                if i <= 5:
                    continue
                # sem header claro — aborta
                if i == 6:
                    raise RuntimeError("455: cabeçalho do Excel não reconhecido")
                continue

            def cell(key: str) -> Any:
                idx = colmap.get(key)
                if idx is None or idx >= len(row):
                    return ""
                return row[idx]

            ctrc = _clean(cell("ctrc"))
            if not ctrc:
                continue
            up = ctrc.upper()
            if "CTRC" in up and len(ctrc) < 8:
                continue

            login = _login_from_row(row, colmap)
            rec = {
                "ctrc": ctrc,
                "data_emissao": _clean(cell("data_emissao")),
                "hora_autorizacao": "",
                "expedidor": login,  # coluna K · login
                "frete": _num(cell("frete")),
                "valor_mercadoria": _num(cell("valor_mercadoria")),
                "peso": _num(cell("peso")),
                "volumes": _num(cell("volumes")),
                "cubagem": _num(cell("cubagem")),
                "cancelado": _clean(cell("cancelado")),
                "unidade": _clean(cell("unidade")),
                "liquidacao": _clean(cell("liquidacao")),
            }
            hora = _parse_hora(rec, row, colmap)
            rec["hora_autorizacao"] = "" if hora is None else f"{hora:02d}:00"
            rec["_hora"] = hora
            rec["_cancelado"] = _is_cancelado(rec)
            out.append(rec)
        return out
    finally:
        wb.close()


def _parse_text_455(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        raise RuntimeError("455: encoding inválido")
    # detect sep
    sample = text.splitlines()[:5]
    sep = ";" if sum(l.count(";") for l in sample) >= sum(l.count(",") for l in sample) else ","
    reader = csv.reader(text.splitlines(), delimiter=sep)
    rows = list(reader)
    if not rows:
        return []
    colmap = _map_headers(rows[0])
    out: list[dict[str, Any]] = []
    for row in rows[1:]:
        if not row:
            continue

        def cell(key: str) -> str:
            idx = colmap.get(key)
            if idx is None or idx >= len(row):
                return ""
            return row[idx]

        ctrc = _clean(cell("ctrc"))
        if not ctrc:
            continue
        login = _login_from_row(row, colmap)
        rec = {
            "ctrc": ctrc,
            "data_emissao": _clean(cell("data_emissao")),
            "hora_autorizacao": _clean(cell("hora_autorizacao")),
            "expedidor": login,  # coluna K · login
            "frete": _num(cell("frete")),
            "valor_mercadoria": _num(cell("valor_mercadoria")),
            "peso": _num(cell("peso")),
            "volumes": _num(cell("volumes")),
            "cubagem": _num(cell("cubagem")),
            "cancelado": _clean(cell("cancelado")),
            "unidade": _clean(cell("unidade")),
            "liquidacao": _clean(cell("liquidacao")),
        }
        hora = None
        m = re.search(r"(\d{1,2}):", rec["hora_autorizacao"] or rec["data_emissao"])
        if m:
            hora = max(0, min(23, int(m.group(1))))
        rec["_hora"] = hora
        rec["_cancelado"] = _is_cancelado(rec)
        if hora is not None:
            rec["hora_autorizacao"] = f"{hora:02d}:00"
        out.append(rec)
    return out


def analyze_reports_455(
    files: list[str] | list[Path] | str | Path | None,
    *,
    periodo: str = "",
    on_status=None,
) -> dict[str, Any]:
    status = on_status or (lambda _m: None)
    paths: list[Path] = []
    if isinstance(files, (str, Path)):
        paths = [Path(files)]
    elif files:
        paths = [Path(f) for f in files]

    rows: list[dict[str, Any]] = []
    for p in paths:
        status(f"[455] parse {p.name}")
        rows.extend(parse_excel_455(p))

    # KPIs
    ctes = len(rows)
    peso = sum(float(r.get("peso") or 0) for r in rows)
    valor = sum(float(r.get("valor_mercadoria") or 0) for r in rows)
    volumes = sum(float(r.get("volumes") or 0) for r in rows)
    cubagem = sum(float(r.get("cubagem") or 0) for r in rows)
    frete = sum(float(r.get("frete") or 0) for r in rows)
    cancelados = sum(1 for r in rows if r.get("_cancelado"))

    dia = 0
    noite = 0
    horas = Counter({h: 0 for h in range(24)})
    for r in rows:
        h = r.get("_hora")
        if h is None:
            continue
        horas[int(h)] += 1
        if DIA_START <= int(h) < DIA_END:
            dia += 1
        else:
            noite += 1

    # Expedidores: TODOS os logins da coluna K (sem filtro *)
    exp_count: Counter[str] = Counter()
    for r in rows:
        nome = str(r.get("expedidor") or "").strip()
        if _is_expedidor(nome):
            exp_count[nome] += 1

    total_exp = sum(exp_count.values()) or 1
    expedidores = []
    for nome, qtd in exp_count.most_common(20):
        expedidores.append(
            {
                "nome": nome,
                "nome_exibicao": _display_expedidor(nome),
                "qtd": qtd,
                "pct": round(100.0 * qtd / total_exp, 1),
            }
        )

    horas_rows = [
        {"hora": h, "label": f"{h}:00", "qtd": int(horas.get(h, 0))} for h in range(24)
    ]

    mes_nome = ""
    try:
        mes_nome = MESES_PT[datetime.now().month - 1]
    except Exception:
        mes_nome = ""

    atualizado = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    resumo = {
        "periodo": periodo or "",
        "mes": mes_nome,
        "atualizado": atualizado,
        "ctes": ctes,
        "peso": round(peso, 2),
        "peso_fmt": _fmt_dec(peso, 2),
        "valor_mercadoria": round(valor, 2),
        "valor_mercadoria_fmt": _fmt_money(valor),
        "volumes": int(round(volumes)),
        "cubagem": round(cubagem, 2),
        "cubagem_fmt": f"{_fmt_dec(cubagem, 2)} m3",
        "frete": round(frete, 2),
        "frete_fmt": _fmt_money(frete),
        "dia": dia,
        "noite": noite,
        "cancelados": cancelados,
    }

    # CSV detalhe (sem campos internos)
    detail_rows = []
    for r in rows:
        detail_rows.append(
            {
                "ctrc": r.get("ctrc"),
                "data_emissao": r.get("data_emissao"),
                "hora_autorizacao": r.get("hora_autorizacao"),
                "expedidor": r.get("expedidor"),
                "frete": r.get("frete"),
                "valor_mercadoria": r.get("valor_mercadoria"),
                "peso": r.get("peso"),
                "volumes": r.get("volumes"),
                "cubagem": r.get("cubagem"),
                "cancelado": "S" if r.get("_cancelado") else "N",
                "unidade": r.get("unidade"),
                "liquidacao": r.get("liquidacao"),
            }
        )

    _write_csv(EMISSOES_455_CSV, EMISSAO_FIELDS, detail_rows)
    _write_csv(RESUMO_455_CSV, RESUMO_FIELDS, [resumo])
    _write_csv(EXPEDIDORES_455_CSV, EXPEDIDOR_FIELDS, expedidores)
    _write_csv(HORAS_455_CSV, HORA_FIELDS, horas_rows)
    LAST_455_JSON.write_text(
        json.dumps(
            {
                "resumo": resumo,
                "expedidores": expedidores,
                "horas": horas_rows,
                "total": ctes,
                "files": [str(p) for p in paths],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    status(
        f"[455] OK · CTEs={ctes} frete={resumo['frete_fmt']} "
        f"dia={dia} noite={noite} cancel={cancelados} exp={len(expedidores)}"
    )
    return {
        "ok": True,
        "total": ctes,
        "resumo": resumo,
        "expedidores": expedidores,
        "horas": horas_rows,
        "rows": detail_rows,
        "files": {
            "emissoes": str(EMISSOES_455_CSV),
            "resumo": str(RESUMO_455_CSV),
            "expedidores": str(EXPEDIDORES_455_CSV),
            "horas": str(HORAS_455_CSV),
        },
    }
