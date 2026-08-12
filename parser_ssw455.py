"""Parser Excel SSW 455 — Fretes Expedidos/Recebidos (Emissão).

Agrega KPIs do painel com colunas fixas do Excel:
  H=hora · K=login · BD=peso · BE=cubagem · BF=volumes
  BI=cancelados · BN=valor mercadoria · BQ=frete
  DIA / NOITE (coluna H) · Expedidores (coluna K)
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

# Colunas fixas do Excel 455 (letras → métricas do painel)
# Ordem real SSW: BD=peso · BE=cubagem · BF=qtde volumes · BI=cancel · BN=merc · BQ=frete
COL_FIXED_455: dict[str, str] = {
    "hora": "H",
    "login": "K",
    "peso": "BD",
    "cubagem": "BE",
    "volumes": "BF",
    "cancelado": "BI",
    "valor_mercadoria": "BN",
    "frete": "BQ",
}

# Ranking do painel: só estes logins · exibição = nome completo (direita)
LOGIN_NOME_455: dict[str, str] = {
    "l.marque": "Lidiane Marques",
    "eloi": "Emerson Eloi",
    "g.fagund": "Gabriel Fagundes",
    "f.silva": "Flaviana Eneas",
    "e.beliza": "Edson Belizario",
    "m.cordei": "Maria Eduarda",
    "anderson": "Anderson Vieira",
    "s.silva": "Simone Silva",
    "j.rodrig": "Julia Rodrigues",
    "portal": "Portal",
    "g.nascim": "Gabriely Nascimento",
    "m.neres": "Mayara Neres",
}


def _col_letter_to_idx(letters: str) -> int:
    """Excel 'A'→0, 'H'→7, 'BD'→55, 'BQ'→68."""
    n = 0
    for ch in str(letters or "").strip().upper():
        if "A" <= ch <= "Z":
            n = n * 26 + (ord(ch) - ord("A") + 1)
    return max(0, n - 1)


def _cell_by_letter(row: tuple[Any, ...] | list[Any], letters: str) -> Any:
    idx = _col_letter_to_idx(letters)
    if idx >= len(row):
        return ""
    return row[idx]

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
    """Mapeia nomes canônicos → índice 0-based + letras fixas das métricas."""
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

    pick(("ctrc",), "serie numero", "serie/numero", "nr cte", "n cte", "ctrc", "conhecimento", "cte")
    pick(("data_emissao",), "data emissao", "dt emissao")
    pick(("hora",), "hora emissao", "hr emissao", "hora de emissao")
    pick(("unidade",), "unidade", "filial", "sigla")
    pick(("liquidacao",), "liquidacao", "liq ")
    # headers de métricas (se existirem, reforçam; letras fixas sobrescrevem abaixo)
    pick(("peso",), "peso real", "peso total", "peso kg", "peso")
    pick(("cubagem",), "cubagem", "m3", "metro cub")
    pick(("volumes",), "quantidade de volume", "qtde volume", "qtd volume", "volumes", "volume")
    pick(("valor_mercadoria",), "valor da mercadoria", "valor mercadoria", "vl mercadoria")
    pick(("frete",), "valor do frete", "valor frete", "frete total", "vl frete", "frete")
    pick(("cancelado",), "cancelado", "anulado")
    pick(("expedidor",), "login", "usuario", "usuário", "expedidor")

    # Letras fixas do Excel 455 — fonte da verdade das métricas
    out["hora"] = _col_letter_to_idx(COL_FIXED_455["hora"])
    out["hora_autorizacao"] = out["hora"]
    out["expedidor"] = _col_letter_to_idx(COL_FIXED_455["login"])
    out["peso"] = _col_letter_to_idx(COL_FIXED_455["peso"])
    out["cubagem"] = _col_letter_to_idx(COL_FIXED_455["cubagem"])
    out["volumes"] = _col_letter_to_idx(COL_FIXED_455["volumes"])
    out["cancelado"] = _col_letter_to_idx(COL_FIXED_455["cancelado"])
    out["valor_mercadoria"] = _col_letter_to_idx(COL_FIXED_455["valor_mercadoria"])
    out["frete"] = _col_letter_to_idx(COL_FIXED_455["frete"])
    return out


def _is_junk_row(rec: dict[str, Any]) -> bool:
    """Descarta cabeçalho residual / linhas sem movimento."""
    login = str(rec.get("expedidor") or "").strip().lower()
    ctrc = str(rec.get("ctrc") or "").strip().lower()
    if login in {"", "login", "usuario", "usuário", "expedidor"}:
        return True
    if any(x in ctrc for x in ("serie", "ct-e", "ctrc", "conhecimento", "numero")):
        return True
    if ctrc in {"0", "0.0", "-"}:
        # só aceita se tiver frete/peso real
        if float(rec.get("peso") or 0) <= 0 and float(rec.get("frete") or 0) <= 0:
            return True
    return False


def _parse_hora(row: dict[str, Any], raw_row: tuple[Any, ...] | list[Any], colmap: dict[str, int]) -> int | None:
    """Extrai hora 0–23 — prioridade coluna H (emissão)."""
    for key in ("hora", "hora_autorizacao", "data_emissao"):
        idx = colmap.get(key)
        if key == "hora" and idx is None:
            idx = _col_letter_to_idx(COL_FIXED_455["hora"])
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
        # Excel time as fraction of day
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            try:
                f = float(val)
                if 0 <= f < 1.5:  # serial time
                    return max(0, min(23, int(f * 24) % 24))
                if 0 <= f <= 23:
                    return int(f)
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
        if re.fullmatch(r"\d{1,2}", text):
            return max(0, min(23, int(text)))
    return None


def _is_cancelado(row: dict[str, str]) -> bool:
    """Coluna BI = cancelados (S/C/flag/texto)."""
    raw = (row.get("cancelado") or "").strip()
    if not raw:
        return False
    up = raw.upper()
    if up in {"S", "C", "1", "X", "SIM", "CANCELADO", "CANCELADA"}:
        return True
    if up in {"N", "0", "NAO", "NÃO", ""}:
        return False
    blob = " ".join(
        [
            raw,
            row.get("liquidacao") or "",
            row.get("ctrc") or "",
        ]
    ).lower()
    return bool(re.search(r"\bcancel|\banulad|\bsubstitu", blob))


def _norm_login(nome: str) -> str:
    return (nome or "").strip().rstrip("*＊").strip().lower()


def _is_expedidor(nome: str) -> bool:
    """Só logins da lista oficial do painel Emissão."""
    return _norm_login(nome) in LOGIN_NOME_455


def _display_expedidor(nome: str) -> str:
    """Nome completo no ranking (direita) — nunca o login."""
    key = _norm_login(nome)
    if key in LOGIN_NOME_455:
        return LOGIN_NOME_455[key]
    t = (nome or "").strip().rstrip("*＊").strip()
    return t.title() if t else "—"


def _login_from_row(row: tuple[Any, ...] | list[Any], colmap: dict[str, int]) -> str:
    """Sempre coluna K (login)."""
    idx_k = _col_letter_to_idx(COL_FIXED_455["login"])
    if idx_k < len(row):
        login_k = _clean(row[idx_k])
        if login_k:
            return login_k
    idx_h = colmap.get("expedidor")
    if idx_h is not None and idx_h < len(row):
        return _clean(row[idx_h])
    return ""


def _metrics_from_row(row: tuple[Any, ...] | list[Any]) -> dict[str, Any]:
    """Lê métricas pelas letras fixas do Excel 455."""
    return {
        "frete": _num(_cell_by_letter(row, COL_FIXED_455["frete"])),
        "valor_mercadoria": _num(_cell_by_letter(row, COL_FIXED_455["valor_mercadoria"])),
        "peso": _num(_cell_by_letter(row, COL_FIXED_455["peso"])),
        "volumes": _num(_cell_by_letter(row, COL_FIXED_455["volumes"])),
        "cubagem": _num(_cell_by_letter(row, COL_FIXED_455["cubagem"])),
        "cancelado": _clean(_cell_by_letter(row, COL_FIXED_455["cancelado"])),
    }


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
            metrics = _metrics_from_row(row)
            rec = {
                "ctrc": ctrc,
                "data_emissao": _clean(cell("data_emissao")),
                "hora_autorizacao": "",
                "expedidor": login,  # coluna K · login
                "frete": metrics["frete"],
                "valor_mercadoria": metrics["valor_mercadoria"],
                "peso": metrics["peso"],
                "volumes": metrics["volumes"],
                "cubagem": metrics["cubagem"],
                "cancelado": metrics["cancelado"],
                "unidade": _clean(cell("unidade")),
                "liquidacao": _clean(cell("liquidacao")),
            }
            hora = _parse_hora(rec, row, colmap)
            rec["hora_autorizacao"] = "" if hora is None else f"{hora:02d}:00"
            rec["_hora"] = hora
            rec["_cancelado"] = _is_cancelado(rec)
            if _is_junk_row(rec):
                continue
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
        metrics = _metrics_from_row(row)
        rec = {
            "ctrc": ctrc,
            "data_emissao": _clean(cell("data_emissao")),
            "hora_autorizacao": "",
            "expedidor": login,  # coluna K · login
            "frete": metrics["frete"],
            "valor_mercadoria": metrics["valor_mercadoria"],
            "peso": metrics["peso"],
            "volumes": metrics["volumes"],
            "cubagem": metrics["cubagem"],
            "cancelado": metrics["cancelado"],
            "unidade": _clean(cell("unidade")),
            "liquidacao": _clean(cell("liquidacao")),
        }
        hora = _parse_hora(rec, row, colmap)
        rec["hora_autorizacao"] = "" if hora is None else f"{hora:02d}:00"
        rec["_hora"] = hora
        rec["_cancelado"] = _is_cancelado(rec)
        if _is_junk_row(rec):
            continue
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

    # Expedidores: só logins oficiais · chave = login normalizado
    exp_count: Counter[str] = Counter()
    for r in rows:
        login = _norm_login(str(r.get("expedidor") or ""))
        if _is_expedidor(login):
            exp_count[login] += 1

    total_exp = sum(exp_count.values()) or 1
    expedidores = []
    for login, qtd in exp_count.most_common(20):
        expedidores.append(
            {
                "nome": login,
                "nome_exibicao": _display_expedidor(login),
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
