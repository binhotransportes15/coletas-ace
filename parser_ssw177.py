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
    "vol_total",
    "vol_total_fmt",
    "topo",
    "topo_peso",
    "topo_vol",
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


def _lidos_total_from_tail(raw: str) -> float:
    """
    TOTAL LIDOS no fim da linha: … LIDOS | % | SOBRA | FALTA.
    Sempre os 4 últimos números (não soma colunas do meio).
    """
    nums = re.findall(r"[\d.,]+", raw or "")
    if len(nums) >= 4:
        return _parse_br_num(nums[-4])
    if nums:
        return _parse_br_num(nums[0])
    return 0.0


def parse_totais_177(text: str) -> dict[str, float]:
    """
    Totais oficiais do rodapé:
      TOTAL SSWBAR VOL → volumes
      TOTAL SSWBAR KG  → peso
    """
    out = {"vol_sswbar": 0.0, "peso_sswbar": 0.0, "vol_unidade": 0.0, "peso_unidade": 0.0}
    # TOTAL SSWBAR · VOL na mesma linha; KG na linha seguinte (indentada)
    m = re.search(
        r"TOTAL\s+SSWBAR\s+VOL\s+([^\n]+)\n[^\n]*?\bKG\s+([^\n]+)",
        text or "",
        flags=re.I,
    )
    if m:
        out["vol_sswbar"] = _lidos_total_from_tail(m.group(1))
        out["peso_sswbar"] = _lidos_total_from_tail(m.group(2))
    else:
        mv = re.search(r"TOTAL\s+SSWBAR\s+VOL\s+([^\n]+)", text or "", flags=re.I)
        mk = re.search(r"TOTAL\s+SSWBAR\s+KG\s+([^\n]+)", text or "", flags=re.I)
        if mv:
            out["vol_sswbar"] = _lidos_total_from_tail(mv.group(1))
        if mk:
            out["peso_sswbar"] = _lidos_total_from_tail(mk.group(1))

    mu = re.search(
        r"TOTAL\s+UNIDADE\s+VOL\s+([^\n]+)\n[^\n]*?\bKG\s+([^\n]+)",
        text or "",
        flags=re.I,
    )
    if mu:
        # TOTAL UNIDADE: só colunas LIDOS (sem % SOBRA FALTA) — pega o último número grande
        vnums = re.findall(r"[\d.,]+", mu.group(1))
        knums = re.findall(r"[\d.,]+", mu.group(2))
        if vnums:
            out["vol_unidade"] = _parse_br_num(vnums[-1])
        if knums:
            out["peso_unidade"] = _parse_br_num(knums[-1])
    return out


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("cp1252", "latin-1", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _pct_from_tail(raw: str) -> str:
    """% na cauda TOTAL (LIDOS | % | SOBRA | FALTA)."""
    nums = re.findall(r"[\d.,]+", raw or "")
    if len(nums) >= 3:
        return _clean(nums[-3])
    return ""


def parse_ssw177(path: Path | str) -> list[dict[str, Any]]:
    """
    Cada conferente = 1 linha VOL + 1 linha KG em seguida.
    Usa só o TOTAL LIDOS do fim de cada linha (não soma as colunas do meio).
    Se o mesmo login aparecer de novo no relatório, soma os totais.
    """
    text = _read_text(Path(path))
    mes_m = re.search(r"MES:\s*(\d{2}/\d{4})", text, flags=re.I)
    mes = mes_m.group(1) if mes_m else ""

    current_unit = ""
    agg: dict[str, dict[str, Any]] = {}
    pending: dict[str, Any] | None = None  # VOL aguardando KG do mesmo login

    vol_re = re.compile(r"^(\d{3})\s+(\S+)\s+VOL\s+(.+)$")
    kg_re = re.compile(r"^\s+(\S+)\s+KG\s+(.+)$")

    def _bump(name: str, vol: float, peso: float, pct: str) -> None:
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
        if pct:
            slot["pct"] = pct
        if current_unit:
            prev = str(slot.get("unidade") or "")
            parts = [p for p in prev.split(",") if p]
            if current_unit not in parts:
                parts.append(current_unit)
                slot["unidade"] = ",".join(parts)

    for line in text.splitlines():
        um = re.match(r"^UNIDADE:\s*(\S+)", line, flags=re.I)
        if um:
            current_unit = um.group(1).upper()
            pending = None
            continue

        vm = vol_re.match(line)
        if vm:
            name = vm.group(2)
            pending = {
                "name": name,
                "vol": _lidos_total_from_tail(vm.group(3)),
                "pct_vol": _pct_from_tail(vm.group(3)),
            }
            continue

        km = kg_re.match(line)
        if not km:
            continue

        name = km.group(1)
        low = name.lower()
        if low in {"kg", "vol"} or "total" in low or "sswbar" in low or "calc" in low:
            pending = None
            continue
        if "%" in name:
            pending = None
            continue

        peso = _lidos_total_from_tail(km.group(2))
        pct = _pct_from_tail(km.group(2))
        vol = 0.0
        if pending and pending["name"].lower() == low:
            vol = float(pending["vol"] or 0.0)
            if not pct:
                pct = str(pending.get("pct_vol") or "")
        pending = None
        _bump(name, vol, peso, pct)

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

    # Dois logins distintos no 177 não se somam só porque o 0607 apontou o mesmo cadastro.
    # Soma de repetição já aconteceu em parse_ssw177 (mesmo login na coluna CONFERENTE).
    # Aqui só evita dois cards com o mesmo nome de exibição: fica o de maior score.
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

    ativos = [r for r in rows if float(r["peso_lidos"]) > 0 or float(r.get("vol_lidos") or 0) > 0]
    peso_soma = sum(float(r["peso_lidos"]) for r in rows)
    vol_soma = sum(float(r.get("vol_lidos") or 0) for r in rows)
    mes = (rows[0].get("mes") if rows else "") or ""
    topo = ativos[0]["conferente"] if ativos else ""
    topo_peso = ativos[0]["peso_lidos_fmt"] if ativos else "0"
    topo_vol = _fmt_peso(float(ativos[0].get("vol_lidos") or 0)) if ativos else "0"

    # Totais oficiais do rodapé TOTAL SSWBAR (fonte da verdade)
    text = _read_text(path)
    totais = parse_totais_177(text)
    peso_total = totais["peso_sswbar"] if totais["peso_sswbar"] > 0 else peso_soma
    vol_total = totais["vol_sswbar"] if totais["vol_sswbar"] > 0 else vol_soma
    if not mes:
        mes_m = re.search(r"MES:\s*(\d{2}/\d{4})", text, flags=re.I)
        mes = mes_m.group(1) if mes_m else ""

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
        "vol_total": f"{vol_total:.0f}",
        "vol_total_fmt": _fmt_peso(vol_total),
        "topo": topo,
        "topo_peso": topo_peso,
        "topo_vol": topo_vol,
    }]
    with RESUMO_177_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RESUMO_177_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(resumo)

    status(
        f"177 OK · {len(ativos)} conferente(s) | "
        f"peso={_fmt_peso(peso_total)} kg · vol={_fmt_peso(vol_total)} | "
        f"topo={topo} | nomes={resolvidos}/{len(rows)}"
    )
    return {
        "ok": True,
        "report": str(path),
        "mes": mes,
        "total_conferentes": len(ativos),
        "peso_total": peso_total,
        "vol_total": vol_total,
        "topo": topo,
        "nomes_resolvidos": resolvidos,
        "totais": totais,
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
