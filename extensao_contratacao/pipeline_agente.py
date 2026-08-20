"""Pipeline Contratação · Excel produtividade → cache/dashboard/Sheets.

Sem SSW 200 — o frete 200 fica no CRT principal.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

_EXT_DIR = Path(__file__).resolve().parent
_ACE_ROOT = _EXT_DIR.parent
if str(_ACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ACE_ROOT))

from config import AceSettings, ensure_dirs, load_settings  # noqa: E402
from dates import format_period, periodo_mes_ate_hoje  # noqa: E402
from parser_ssw073 import (  # noqa: E402
    CTRBS_073_CSV,
    CTRBS_FIELDS,
    DESTINO_FIELDS,
    DESTINOS_073_CSV,
    RESUMO_073_CSV,
    RESUMO_FIELDS,
    VEICULO_FIELDS,
    VEICULOS_073_CSV,
    _fmt_money,
    _fmt_peso,
    _publish_local,
    _write_csv,
)
from siglas_filiais import base_da_origem  # noqa: E402

try:
    from extensao_contratacao.parser_produtividade import (  # noqa: E402
        norm_placa,
        read_produtividade_xlsx,
        resolve_produtividade_xlsx,
    )
except ImportError:
    from parser_produtividade import (  # type: ignore  # noqa: E402
        norm_placa,
        read_produtividade_xlsx,
        resolve_produtividade_xlsx,
    )

StatusCallback = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _default_excel_path(cfg: AceSettings | None = None) -> Path:
    settings = cfg or load_settings()
    raw = str(getattr(settings, "ctr_agente_excel", "") or "").strip()
    return resolve_produtividade_xlsx(raw)


def _load_previous_veiculos() -> list[dict[str, Any]]:
    if not VEICULOS_073_CSV.exists():
        return []
    try:
        with VEICULOS_073_CSV.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def _merge_keep_custo_anterior(
    novos: list[dict[str, Any]],
    anteriores: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Custo sempre vem primeiro (Excel). Mantém placas/custo do mês que ainda
    não entraram no lote novo; preserva frete antigo até o CRT rodar o 200
    (mês referente, amarrado só pela placa — frete pode ser D+1 do custo).
    """
    by: dict[str, dict[str, Any]] = {}
    for v in anteriores:
        placa = norm_placa(v.get("placa"))
        if not placa:
            continue
        by[placa] = dict(v)
    for v in novos:
        placa = norm_placa(v.get("placa"))
        if not placa:
            continue
        prev = by.get(placa) or {}
        merged = dict(v)
        # custo novo do Excel manda; se zerado, mantém o anterior
        if float(merged.get("custo") or 0) <= 0 and float(prev.get("custo") or 0) > 0:
            merged["custo"] = prev.get("custo")
            merged["valor_pagar"] = prev.get("valor_pagar", prev.get("custo"))
        # frete só o CRT atualiza — não zera o que já tinha
        if float(merged.get("frete") or 0) <= 0 and float(prev.get("frete") or 0) > 0:
            merged["frete"] = prev.get("frete")
        by[placa] = merged
    out = list(by.values())
    out.sort(key=lambda r: float(r.get("custo") or 0), reverse=True)
    return out


def build_caches_from_excel_rows(
    rows: list[dict[str, Any]],
    *,
    periodo: str,
    unidade: str = "SPO",
) -> dict[str, Any]:
    """Monta veiculos/ctrbs/destinos/resumo no formato do painel Contratação."""
    ensure_dirs()
    prev_veiculos = _load_previous_veiculos()
    by_placa: dict[str, dict[str, Any]] = {}
    ctrbs: list[dict[str, Any]] = []
    dest_agg: dict[str, dict[str, float | int | str]] = {}

    for r in rows:
        placa = norm_placa(r.get("placa"))
        if not placa:
            continue
        custo = float(r.get("custo") or 0)
        destino_cid = str(r.get("destino") or "").strip()
        carreta = str(r.get("carreta") or "").strip().upper()
        prop = str(r.get("propriedade") or "").strip()
        manifesto = str(r.get("manifesto") or "").strip()
        origem = str(r.get("origem") or "").strip()
        base, base_lbl = base_da_origem(origem)

        slot = by_placa.setdefault(
            placa,
            {
                "placa": placa,
                "carreta": carreta,
                "propriedade": prop,
                "grupo": "contratados",
                "qtd_ctrb": 0,
                "custo": 0.0,
                "custo_av": 0.0,
                "valor_pagar": 0.0,
                "peso": 0.0,
                "frete": 0.0,
                "ctrbs": [],
                "base": base,
                "origem": origem,
            },
        )
        slot["qtd_ctrb"] = int(slot["qtd_ctrb"]) + 1
        slot["custo"] = float(slot["custo"]) + custo
        slot["valor_pagar"] = float(slot["valor_pagar"]) + custo
        if carreta and not slot["carreta"]:
            slot["carreta"] = carreta
        if prop and not slot["propriedade"]:
            slot["propriedade"] = prop
        if origem and not slot.get("origem"):
            slot["origem"] = origem
        if base and (not slot.get("base") or slot.get("base") == "OUT"):
            slot["base"] = base
        if manifesto:
            slot["ctrbs"].append(manifesto)

        ctrbs.append(
            {
                "ctrb": manifesto or placa,
                "tipo": "TRANSFERÊNCIA",
                "operacao": "T",
                "situacao": str(r.get("status") or "CONTRATADO"),
                "placa": placa,
                "carreta": carreta,
                "propriedade": prop,
                "grupo": "contratados",
                "custo": round(custo, 2),
                "custo_av": 0.0,
                "valor_pagar": round(custo, 2),
                "total_ctrb": round(custo, 2),
                "peso": 0.0,
                "frete": 0.0,
                "origem": origem,
                "destino": base,  # torres = base pela origem
                "cidade_destino": destino_cid,
                "fonte": str(r.get("fonte") or "excel"),
            }
        )

        d = dest_agg.setdefault(
            base,
            {"destino": base_lbl, "qtd": 0, "custo": 0.0, "frete": 0.0, "peso": 0.0},
        )
        d["qtd"] = int(d["qtd"]) + 1
        d["custo"] = float(d["custo"]) + custo

    veiculos: list[dict[str, Any]] = []
    planilha: list[dict[str, Any]] = []
    for slot in by_placa.values():
        ctrb_list = slot.pop("ctrbs")
        base = str(slot.pop("base", "OUT") or "OUT")
        origem = str(slot.pop("origem", "") or "")
        row_v = {
            "placa": slot["placa"],
            "carreta": slot["carreta"],
            "propriedade": slot["propriedade"] or base,
            "grupo": "contratados",
            "qtd_ctrb": int(slot["qtd_ctrb"]),
            "custo": round(float(slot["custo"]), 2),
            "custo_av": 0.0,
            "valor_pagar": round(float(slot["valor_pagar"]), 2),
            "peso": 0.0,
            "frete": 0.0,
            "ctrbs": " | ".join(str(x) for x in ctrb_list[:12]),
        }
        veiculos.append(row_v)
        planilha.append(
            {
                "placa": row_v["placa"],
                "carreta": row_v["carreta"],
                "origem": origem,
                "base": base,
                "valor": row_v["custo"],
                "frete": row_v["frete"],
                "qtd": row_v["qtd_ctrb"],
            }
        )
    veiculos = _merge_keep_custo_anterior(veiculos, prev_veiculos)
    # realinha frete/custo preservados na planilha da UI
    frete_by = {norm_placa(v.get("placa")): v for v in veiculos}
    for p in planilha:
        hit = frete_by.get(norm_placa(p.get("placa"))) or {}
        p["valor"] = float(hit.get("custo") or p["valor"] or 0)
        p["frete"] = float(hit.get("frete") or 0)

    destinos = [
        {
            "destino": d["destino"],
            "qtd": int(d["qtd"]),
            "custo": round(float(d["custo"]), 2),
            "frete": round(float(d["frete"]), 2),
            "peso": round(float(d["peso"]), 3),
        }
        for d in dest_agg.values()
    ]
    destinos.sort(key=lambda d: float(d.get("custo") or 0), reverse=True)

    total_custo = sum(float(v.get("custo") or 0) for v in veiculos)
    total_frete = sum(float(v.get("frete") or 0) for v in veiculos)
    resumo = {
        "periodo": periodo,
        "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "unidade": unidade,
        "total_veiculos": len(veiculos),
        "total_ctrbs": len(ctrbs),
        "custo": round(total_custo, 2),
        "custo_fmt": _fmt_money(total_custo),
        "frete": round(total_frete, 2),
        "frete_fmt": _fmt_money(total_frete),
        "peso": 0.0,
        "peso_fmt": _fmt_peso(0.0),
        "agregado": 0,
        "frota": 0,
        "contratados": len(veiculos),
        "terceiro": len(veiculos),
    }

    _write_csv(VEICULOS_073_CSV, VEICULO_FIELDS, veiculos)
    _write_csv(CTRBS_073_CSV, CTRBS_FIELDS, ctrbs)
    _write_csv(DESTINOS_073_CSV, DESTINO_FIELDS, destinos)
    _write_csv(RESUMO_073_CSV, RESUMO_FIELDS, [resumo])
    _publish_local()

    return {
        "ok": True,
        "veiculos": veiculos,
        "ctrbs": ctrbs,
        "destinos": destinos,
        "resumo": resumo,
        "placas": [norm_placa(v.get("placa")) for v in veiculos if v.get("placa")],
        "planilha": planilha,
    }


def run_pipeline_contratacao_excel(
    *,
    excel_path: Path | str | None = None,
    settings: AceSettings | None = None,
    on_status: StatusCallback | None = None,
    sync_sheets: bool = True,
    **_ignored: Any,
) -> dict[str, Any]:
    """
    Excel (produtividade) → CSVs contratação → publish + Sheets.
    Frete SSW 200 fica no CRT (não nesta extensão).
    """
    status = on_status or _noop
    ensure_dirs()
    cfg = settings or load_settings()
    path = resolve_produtividade_xlsx(excel_path or _default_excel_path(cfg))

    status(f"ACE CTR Excel · {path} | {datetime.now():%d/%m %H:%M:%S}")
    parsed = read_produtividade_xlsx(path)
    rows = parsed.get("rows") or []
    if not rows:
        raise RuntimeError(
            f"Planilha sem linhas válidas (aba={parsed.get('sheet')}, "
            f"cancelados={parsed.get('skipped_cancel')}, fora do mês={parsed.get('skipped_date')})"
        )

    ini, fim = periodo_mes_ate_hoje()
    periodo_fmt = format_period(ini, fim)
    status(
        f"Excel OK · aba={parsed.get('sheet')} · {len(rows)} linha(s) · "
        f"mês={periodo_fmt} · "
        f"cancel={parsed.get('skipped_cancel')} · fora={parsed.get('skipped_date')}"
    )
    built = build_caches_from_excel_rows(rows, periodo=periodo_fmt, unidade="SPO")
    placas = list(built.get("placas") or [])

    from publish_dashboard import publish_contratacao_local

    pub = publish_contratacao_local(on_status=status)

    sheets_result: dict[str, Any] = {"ok": False, "skipped": True}
    if sync_sheets:
        try:
            from sheets_sync_073 import sync_sheets_073

            sheets_result = sync_sheets_073(settings=cfg, on_status=status, force=False)
        except Exception as err:  # noqa: BLE001
            status(f"Sheets CTR avisou: {err}")
            sheets_result = {"ok": False, "error": str(err)}

    resumo = built.get("resumo") or {}
    if RESUMO_073_CSV.exists():
        with RESUMO_073_CSV.open(encoding="utf-8-sig", newline="") as fh:
            r0 = next(csv.DictReader(fh), None)
            if r0:
                resumo = r0

    try:
        from config import DASHBOARD_DIR

        stamp_path = DASHBOARD_DIR / "data" / "contratacao" / "stamp.json"
        stamp = {}
        if stamp_path.exists():
            stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        stamp.update(
            {
                "fonte": "excel_produtividade",
                "excel": str(path),
                "aba": parsed.get("sheet"),
                "atualizado": resumo.get("atualizado")
                or datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            }
        )
        stamp_path.write_text(json.dumps(stamp, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    status(
        f"OK CTR Excel · veículos={resumo.get('total_veiculos')} "
        f"custo={resumo.get('custo_fmt')} (frete 200 = CRT)"
    )
    return {
        "ok": True,
        "excel": parsed,
        "073": built,
        "200": {"ok": False, "skipped": True, "reason": "200_no_crt"},
        "publish": pub,
        "sheets": sheets_result,
        "resumo": resumo,
        "placas": placas,
        "planilha": built.get("planilha") or [],
        "destinos": built.get("destinos") or [],
    }
