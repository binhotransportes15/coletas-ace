"""Parser do relatório SSW 177 — Produção dos conferentes no SSWBAR (mensal)."""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from config import CACHE_DIR, ensure_dirs

CONFERENTES_CSV = CACHE_DIR / "conferentes_177.csv"
RESUMO_177_CSV = CACHE_DIR / "resumo_177.csv"

CONFERENTE_FIELDS = [
    "rank",
    "login",
    "conferente",
    "nome",
    "unidade",
    "peso_lidos",
    "peso_lidos_fmt",
    "vol_lidos",
    "pct",
    "mes",
]

RESUMO_177_FIELDS = [
    "atualizado",
    "mes",
    "total_conferentes",
    "peso_total",
    "peso_total_fmt",
    "topo",
    "topo_peso",
]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _parse_br_num(raw: str) -> float:
    """'23.650' → 23650 | '31,5' → 31.5 | '779' → 779."""
    text = _clean(raw).replace(" ", "")
    if not text or text == "-":
        return 0.0
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return 0.0
    # só pontos = milhar brasileiro
    try:
        return float(text.replace(".", ""))
    except ValueError:
        return 0.0


def _fmt_peso(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("cp1252", "latin-1", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def parse_ssw177(path: Path | str) -> list[dict[str, Any]]:
    """
    Cada conferente tem 2 linhas (VOL / KG). Ranking usa TOTAL LIDOS KG.
    Agrega por nome se o arquivo tiver várias UNIDADE:.
    """
    text = _read_text(Path(path))
    mes_m = re.search(r"MES:\s*(\d{2}/\d{4})", text, flags=re.I)
    mes = mes_m.group(1) if mes_m else ""

    current_unit = ""
    # conferente -> agg
    agg: dict[str, dict[str, Any]] = {}

    vol_re = re.compile(
        r"^(\d{3})\s+(\S+)\s+VOL\s+(.+)$",
        flags=re.M,
    )
    kg_re = re.compile(
        r"^\s+(\S+)\s+KG\s+(.+)$",
        flags=re.M,
    )

    # mapa posição: linhas KG seguem a VOL do mesmo nome
    for block in re.split(r"(?=UNIDADE:\s*)", text):
        um = re.search(r"UNIDADE:\s*(\S+)", block)
        if um:
            current_unit = um.group(1).upper()
        # pareia VOL+KG pelo nome
        vols = {m.group(2).lower(): m for m in vol_re.finditer(block)}
        for m in kg_re.finditer(block):
            name = m.group(1)
            # ignora totais / títulos
            low = name.lower()
            if low in {"kg", "vol"} or "total" in low or "sswbar" in low or "calc" in low:
                continue
            if "%" in name:
                continue
            nums = re.findall(r"[\d.,]+", m.group(2))
            if len(nums) < 4:
                continue
            # ... TOTAL LIDOS | % | SOBRA | FALTA
            peso = _parse_br_num(nums[-4])
            pct = _clean(nums[-3]) if len(nums) >= 3 else ""
            vol = 0.0
            vm = vols.get(name.lower())
            if vm:
                vnums = re.findall(r"[\d.,]+", vm.group(3))
                if len(vnums) >= 4:
                    vol = _parse_br_num(vnums[-4])
            key = name.lower()
            slot = agg.setdefault(
                key,
                {
                    "conferente": name,
                    "unidade": current_unit,
                    "peso_lidos": 0.0,
                    "vol_lidos": 0.0,
                    "pct": pct,
                    "mes": mes,
                },
            )
            slot["peso_lidos"] += peso
            slot["vol_lidos"] += vol
            if current_unit and current_unit not in str(slot.get("unidade") or ""):
                # múltiplas unidades: junta siglas
                prev = str(slot.get("unidade") or "")
                parts = [p for p in prev.split(",") if p]
                if current_unit not in parts:
                    parts.append(current_unit)
                    slot["unidade"] = ",".join(parts)
            if pct:
                slot["pct"] = pct

    rows = list(agg.values())
    rows.sort(key=lambda r: (-float(r["peso_lidos"]), str(r["conferente"]).lower()))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
        r["peso_lidos_fmt"] = _fmt_peso(float(r["peso_lidos"]))
    return rows


def analyze_report_177(
    report_path: Path | str,
    *,
    on_status: Any = None,
    mapa_0607_path: Path | str | None = None,
) -> dict[str, Any]:
    status = on_status or (lambda m: None)
    ensure_dirs()
    path = Path(report_path)
    status(f"Analisando 177: {path.name}")
    rows = parse_ssw177(path)

    from parser_ssw0607 import (
        MAPA_CSV,
        analyze_report_0607,
        find_local_0607,
        load_mapa_0607,
        resolve_conferente_nome,
    )

    try:
        if mapa_0607_path:
            analyze_report_0607(mapa_0607_path, on_status=status)
        else:
            local = find_local_0607()
            if local and (
                not MAPA_CSV.exists() or local.stat().st_mtime > MAPA_CSV.stat().st_mtime
            ):
                analyze_report_0607(local, on_status=status)
    except Exception as err:  # noqa: BLE001
        status(f"0607 mapa: {err}")

    mapa = load_mapa_0607()
    resolvidos = 0
    hits: list[dict[str, Any]] = []
    for r in rows:
        login = str(r.get("conferente") or "")
        hit = resolve_conferente_nome(login, mapa)
        r["login"] = login.lower()
        r["_hit"] = hit
        hits.append(hit)

    # evita dois logins do 177 no mesmo nome cadastral (fica o de maior score)
    best_by_nome: dict[str, tuple[int, int]] = {}
    for i, hit in enumerate(hits):
        nome_key = _clean(hit.get("nome") or "").upper()
        if not nome_key:
            continue
        sc = int(hit.get("score") or 0)
        prev = best_by_nome.get(nome_key)
        if prev is None or sc > prev[0]:
            best_by_nome[nome_key] = (sc, i)

    winners = {idx for _, idx in best_by_nome.values()}
    for i, r in enumerate(rows):
        hit = r.pop("_hit", {}) or {}
        login = r["login"]
        nome_key = _clean(hit.get("nome") or "").upper()
        if hit.get("nome_exibicao") and (not nome_key or i in winners):
            r["nome"] = hit["nome"]
            r["conferente"] = hit["nome_exibicao"]
            resolvidos += 1
        else:
            r["nome"] = ""
            r["conferente"] = login

    ativos = [r for r in rows if float(r["peso_lidos"]) > 0]
    peso_total = sum(float(r["peso_lidos"]) for r in rows)
    mes = (rows[0].get("mes") if rows else "") or ""
    topo = ativos[0]["conferente"] if ativos else ""
    topo_peso = ativos[0]["peso_lidos_fmt"] if ativos else "0"

    out_rows = []
    for r in rows:
        out_rows.append({k: r.get(k, "") for k in CONFERENTE_FIELDS})

    with CONFERENTES_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CONFERENTE_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    resumo = [{
        "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "mes": mes,
        "total_conferentes": str(len(ativos)),
        "peso_total": f"{peso_total:.0f}",
        "peso_total_fmt": _fmt_peso(peso_total),
        "topo": topo,
        "topo_peso": topo_peso,
    }]
    with RESUMO_177_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RESUMO_177_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(resumo)

    status(
        f"177 OK · {len(ativos)} conferente(s) com peso | "
        f"total={_fmt_peso(peso_total)} kg | topo={topo} | "
        f"nomes={resolvidos}/{len(rows)}"
    )
    return {
        "ok": True,
        "report": str(path),
        "mes": mes,
        "total_conferentes": len(ativos),
        "peso_total": peso_total,
        "topo": topo,
        "nomes_resolvidos": resolvidos,
        "rows": out_rows,
        "cache": str(CONFERENTES_CSV),
    }


if __name__ == "__main__":
    sample = CACHE_DIR / "sample_177.sswweb"
    mapa = CACHE_DIR / "sample_0607.sswweb"
    if sample.exists():
        print(analyze_report_177(sample, mapa_0607_path=mapa if mapa.exists() else None))
    else:
        print("Coloque sample_177.sswweb em data/cache/")
