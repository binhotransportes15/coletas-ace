"""Mapa Operacional — rotas dos veículos em rota (SSW 36) via CyberMap.

Usa geocode + OSRM de D:\\MapaCustoRegiaoSP (sem Qt).
Gera dashboard/data/mapa/mapa_distribuicao.json para o painel Leaflet.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import BASE_DIR, CACHE_DIR, DASHBOARD_DIR, ensure_dirs

StatusCallback = Callable[[str], None]

CYBERMAP_DEFAULT = Path(r"D:\MapaCustoRegiaoSP")
MAPA_CACHE_JSON = CACHE_DIR / "mapa_distribuicao.json"
MAPA_DASH_DIR = DASHBOARD_DIR / "data" / "mapa"
MAPA_DASH_JSON = MAPA_DASH_DIR / "mapa_distribuicao.json"
MAPA_LOCAL_JSON = DASHBOARD_DIR / "data" / "local" / "mapa_distribuicao.json"
COLETAS_103_CSV = CACHE_DIR / "coletas_103.csv"
ENTREGAS_36_CSV = CACHE_DIR / "entregas_36.csv"

# Situações = veículo na rua (entrega)
_ON_STREET_TOKENS = (
    "SAIDA PARA ENTREGA",
    "SAÍDA PARA ENTREGA",
    "EM ROTA",
    "EM TRANSITO",
    "EM TRÂNSITO",
    "SAIU PARA ENTREGA",
)

_MAX_VEICULOS = 40
_MAX_PARADAS = 25


def _noop(_: str) -> None:
    return None


def _cybermap_root() -> Path:
    try:
        from config import load_settings

        cfg = load_settings()
        raw = str(getattr(cfg, "cybermap_path", "") or "").strip()
        if raw:
            return Path(raw)
    except Exception:
        pass
    return CYBERMAP_DEFAULT


def _ensure_cybermap(on_status: StatusCallback) -> Path | None:
    root = _cybermap_root()
    if not root.is_dir():
        on_status(f"Mapa: CyberMap não encontrado em {root}")
        return None
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _norm_plate(p: str) -> str:
    return "".join(ch for ch in str(p or "").upper() if ch.isalnum())


def _num(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace("R$", "").replace(" ", "")
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s or 0)
    except Exception:
        return 0.0


def _status_ace_from_ocorrencia(status: str) -> str:
    """Normaliza status SSW/CyberMap → em_rota | realizada | pendencia | outro."""
    s = " ".join(str(status or "").upper().split())
    if not s or s in {"COLETA", "PENDENTE", "EM ROTA", "EM TRANSITO", "EM TRÂNSITO"}:
        return "em_rota"
    if any(
        tok in s
        for tok in (
            "SAIDA PARA ENTREGA",
            "SAÍDA PARA ENTREGA",
            "SAIU PARA ENTREGA",
            "EM ROTA",
            "EM TRANSITO",
            "EM TRÂNSITO",
        )
    ):
        return "em_rota"
    if any(
        tok in s
        for tok in (
            "REALIZAD",
            "BAIXAD",
            "ENTREGUE",
            "PRE-ENTREG",
            "PRE ENTREG",
            "COLETADA",
        )
    ):
        return "realizada"
    if "CANCEL" in s:
        return "cancelada"
    return "pendencia"


def _is_on_street_status(status: str) -> bool:
    return _status_ace_from_ocorrencia(status) == "em_rota"


def _is_mapa_stop_status(status: str) -> bool:
    """No mapa do dia: em rota (pisca) ou realizado (verde)."""
    return _status_ace_from_ocorrencia(status) in {"em_rota", "realizada", "pendencia"}


def _delivery_is_today(d: Any) -> bool:
    """Filtra entregas do dia atual (ocorrência / emissão)."""
    from datetime import date

    today = date.today()
    for attr in ("occurrence_date", "emission_date", "data_ocorrencia"):
        raw = getattr(d, attr, None)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        # ISO / YYYY-MM-DD
        if len(text) >= 10 and text[4] == "-":
            try:
                return date.fromisoformat(text[:10]) == today
            except Exception:
                pass
        # DD/MM/YYYY or DD/MM
        if "/" in text:
            parts = text.replace("-", "/").split("/")
            try:
                if len(parts) >= 3:
                    dd, mm, yy = int(parts[0]), int(parts[1]), int(parts[2])
                    if yy < 100:
                        yy += 2000
                    return date(yy, mm, dd) == today
                if len(parts) == 2:
                    dd, mm = int(parts[0]), int(parts[1])
                    return dd == today.day and mm == today.month
            except Exception:
                pass
    # Sem data explícita: mantém (relatório já é do ciclo do dia)
    return True


def _ace_em_rota_plates() -> set[str]:
    path = ENTREGAS_36_CSV
    if not path.is_file():
        return set()
    out: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("status_ace") or "").strip().lower() != "em_rota":
                continue
            if str(row.get("excluido") or "") in {"1", "true", "True"}:
                continue
            pl = _norm_plate(row.get("placa") or "")
            if pl:
                out.add(pl)
    return out


def _ace_coleta_em_rota_plates() -> set[str]:
    """Placas de coleta na rua (103 · comandada → em_rota)."""
    path = COLETAS_103_CSV
    if not path.is_file():
        return set()
    out: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("status_ace") or "").strip().lower() != "em_rota":
                continue
            pl = _norm_plate(row.get("placa") or "")
            if pl:
                out.add(pl)
    return out


def _find_report_36(explicit: Path | str | None = None) -> Path | None:
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
    try:
        from pipeline import find_latest_36

        found = find_latest_36()
        if found and found.is_file():
            return found
    except Exception:
        pass
    # CyberMap project path
    root = _cybermap_root()
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from map_renderer import load_project

        proj = load_project()
        rp = Path(str(getattr(proj, "report_36_path", "") or ""))
        if rp.is_file():
            return rp
    except Exception:
        pass
    for folder in (
        BASE_DIR / "data" / "downloads",
        root / "data" / "downloads",
    ):
        if not folder.is_dir():
            continue
        cands = sorted(
            list(folder.glob("*0146*")) + list(folder.glob("*ssw0146*")) + list(folder.glob("entrega_36*")),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        if cands:
            return cands[0]
    return None


def _newest_in_folders(patterns: tuple[str, ...], folders: list[Path]) -> Path | None:
    cands: list[Path] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for pat in patterns:
            cands.extend(folder.glob(pat))
    cands = [p for p in cands if p.is_file()]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def _download_folders() -> list[Path]:
    root = _cybermap_root()
    return [
        BASE_DIR / "data" / "downloads",
        root / "data" / "downloads",
        CACHE_DIR,
    ]


def _find_report_50() -> Path | None:
    try:
        from pipeline import find_latest_report

        found = find_latest_report()
        if found and found.is_file():
            return found
    except Exception:
        pass
    try:
        from map_renderer import load_project

        rp = Path(str(getattr(load_project(), "report_50_path", "") or ""))
        if rp.is_file():
            return rp
    except Exception:
        pass
    return _newest_in_folders(
        ("*ssw0157*", "*0157*.sswweb", "coleta_50*", "relatorio_50*"),
        _download_folders(),
    )


def _find_report_76_cyber() -> Path | None:
    """SSW 76 / ssw0216 (CyberMap) — frete/peso oficial das coletas. ≠ parser remuneração ACE."""
    try:
        from map_renderer import load_project

        rp = Path(str(getattr(load_project(), "report_76_path", "") or ""))
        if rp.is_file():
            return rp
    except Exception:
        pass
    return _newest_in_folders(
        (
            "relatorio_76*",
            "mapa_76*",
            "*ssw0216*",
            "*0216*.sswweb",
            "contratacao_076*",
        ),
        _download_folders(),
    )


def _find_report_200() -> Path | None:
    try:
        from map_renderer import load_project

        rp = Path(str(getattr(load_project(), "report_200_path", "") or ""))
        if rp.is_file():
            return rp
    except Exception:
        pass
    return _newest_in_folders(
        ("*ssw0644*", "*0644*.CSV", "*0644*.csv", "relatorio_200*", "*200*.CSV"),
        _download_folders(),
    )


def _build_project_for_frame(*, report_36: Path | None) -> Any:
    from map_renderer import ProjectConfig, load_project

    try:
        base_proj = load_project()
    except Exception:
        base_proj = ProjectConfig()

    p50 = _find_report_50()
    p76 = _find_report_76_cyber()
    p200 = _find_report_200()
    path36 = str(report_36) if report_36 and report_36.is_file() else (
        str(getattr(base_proj, "report_36_path", "") or "")
    )
    return ProjectConfig(
        app_title=getattr(base_proj, "app_title", "ACE"),
        base=getattr(base_proj, "base", None) or ProjectConfig().base,
        report_36_path=path36,
        report_50_path=str(p50) if p50 else str(getattr(base_proj, "report_50_path", "") or ""),
        report_76_path=str(p76) if p76 else str(getattr(base_proj, "report_76_path", "") or ""),
        report_200_path=str(p200) if p200 else str(getattr(base_proj, "report_200_path", "") or ""),
        operation_view_mode="TODOS",
    )


def _frete_peso_por_placa(
    *,
    report_36: Path | None,
    on_status: StatusCallback,
) -> dict[str, dict[str, float]]:
    """Totais por placa (coleta/entrega) via lógica CyberMap: 36 + 50(+76) + 200."""
    out: dict[str, dict[str, float]] = {}
    try:
        from report76_support import load_combined_operational_frame
    except Exception as err:
        on_status(f"Mapa: frete/peso CyberMap indisponível ({err})")
        return out

    try:
        proj = _build_project_for_frame(report_36=report_36)
    except Exception as err:
        on_status(f"Mapa: frete/peso projeto ({err})")
        return out

    fontes = []
    if proj.report_36_path:
        fontes.append("36")
    if proj.report_50_path:
        fontes.append("50")
    if proj.report_76_path:
        fontes.append("76")
    if proj.report_200_path:
        fontes.append("200")
    on_status(f"Mapa: frete/peso · fontes {','.join(fontes) or '—'}")

    try:
        df = load_combined_operational_frame(proj)
    except Exception as err:
        on_status(f"Mapa: frame operacional falhou ({err})")
        return out

    if df is None or getattr(df, "empty", True):
        return out

    for _, row in df.iterrows():
        pl = _norm_plate(row.get("PLACA_NORM") or row.get("PLACA") or "")
        if not pl:
            continue
        slot = out.setdefault(
            pl,
            {
                "peso_entrega": 0.0,
                "frete_entrega": 0.0,
                "peso_coleta": 0.0,
                "frete_coleta": 0.0,
            },
        )
        peso = _num(row.get("PESO_CALCULADO_NUM"))
        frete = _num(row.get("FRETE_NUM"))
        mov = str(row.get("MOVIMENTO_NORM") or "").upper()
        if mov == "COLETA":
            slot["peso_coleta"] += peso
            slot["frete_coleta"] += frete
        else:
            slot["peso_entrega"] += peso
            slot["frete_entrega"] += frete

    on_status(f"Mapa: frete/peso · {len(out)} placa(s) no operacional")
    return out


def _build_coleta_vehicles(
    *,
    report_36: Path | None,
    cargas: dict[str, dict[str, float]],
    on_status: StatusCallback,
) -> list[dict[str, Any]]:
    """Rotas das coletas na rua (103 em_rota + relatório 50/76 do CyberMap)."""
    try:
        from map_renderer import resolve_operational_point, save_delivery_geocode_cache
        from osrm_client import build_visual_route
        from report76_support import load_combined_operational_frame
    except Exception as err:
        on_status(f"Mapa: coleta geocode indisponível ({err})")
        return []

    plates = _ace_coleta_em_rota_plates()
    if not plates:
        # fallback: placas que só têm coleta no frame operacional
        plates = {
            pl
            for pl, c in cargas.items()
            if (c.get("peso_coleta") or c.get("frete_coleta"))
            and not (c.get("peso_entrega") or c.get("frete_entrega"))
        }
    if not plates:
        on_status("Mapa: nenhuma placa de coleta em rota (103)")
        return []

    try:
        proj = _build_project_for_frame(report_36=report_36)
        df = load_combined_operational_frame(proj)
    except Exception as err:
        on_status(f"Mapa: frame coleta falhou ({err})")
        return []

    if df is None or getattr(df, "empty", True):
        on_status("Mapa: sem linhas de coleta no operacional (rode `50`)")
        return []

    col = df[df["MOVIMENTO_NORM"].astype(str).str.upper() == "COLETA"].copy()
    if col.empty:
        on_status("Mapa: relatório 50 sem coletas — puxe `50` / `mapa`")
        return []

    base_name, base_lat, base_lon = _base_ll()
    base_pt = (base_lat, base_lon)
    district_points: dict[str, tuple[float, float]] = {}
    veiculos: list[dict[str, Any]] = []

    plates_sorted = sorted(plates)[:_MAX_VEICULOS]
    on_status(f"Mapa: {len(plates_sorted)} placa(s) coleta na rua…")

    for pl in plates_sorted:
        rows = col[col["PLACA_NORM"].astype(str) == pl]
        if rows.empty:
            rows = col[col["PLACA"].map(_norm_plate) == pl]
        if rows.empty:
            continue
        stops_out: list[dict[str, Any]] = []
        coords: list[tuple[float, float]] = []
        peso_rota = 0.0
        frete_rota = 0.0
        motorista = ""
        for i, (_, row) in enumerate(rows.head(_MAX_PARADAS).iterrows(), start=1):
            w = _num(row.get("PESO_CALCULADO_NUM"))
            f = _num(row.get("FRETE_NUM"))
            peso_rota += w
            frete_rota += f
            addr = str(row.get("ENDERECO") or "")
            cep = str(row.get("CEP") or "")
            city = str(
                row.get("CIDADE_ENTREGA_EXIBICAO")
                or row.get("CLIENTE_EXIBICAO")
                or ""
            )
            bairro = str(row.get("BAIRRO_MAPA") or "")
            pt = resolve_operational_point(
                address=addr,
                cep=cep,
                city=city,
                district_norm=bairro,
                district_points=district_points,
            )
            if not pt:
                continue
            lat, lon = float(pt[0]), float(pt[1])
            coords.append((lat, lon))
            stops_out.append(
                {
                    "seq": i,
                    "lat": lat,
                    "lon": lon,
                    "ctrc": str(row.get("CTRC") or row.get("DOCUMENTO_NUMERO") or ""),
                    "cliente": str(row.get("CLIENTE_EXIBICAO") or row.get("DESTINATARIO_EXIBICAO") or ""),
                    "cidade": city,
                    "bairro": bairro,
                    "status": "coleta",
                    "status_ace": "em_rota",
                    "cep": cep,
                    "peso": round(w, 2),
                    "frete": round(f, 2),
                    "tipo": "coleta",
                }
            )

        if not stops_out and not (peso_rota or frete_rota):
            op = cargas.get(pl) or {}
            if not (op.get("peso_coleta") or op.get("frete_coleta")):
                continue

        polyline: list[list[float]] = []
        dist_km = 0.0
        dur_min = 0.0
        if len(coords) >= 1:
            try:
                route = build_visual_route(base_pt, coords, include_return_to_base=False)
                polyline = [[float(a), float(b)] for a, b in (route.geometry or [])]
                dist_km = float(getattr(route, "distance_km", 0) or 0)
                dur_min = float(getattr(route, "duration_min", 0) or 0)
            except Exception as err:
                on_status(f"Mapa: OSRM coleta {pl}: {err}")
                polyline = [[base_lat, base_lon]] + [[a, b] for a, b in coords]

        op = cargas.get(pl) or {}
        peso_col = float(op.get("peso_coleta") or 0) or peso_rota
        frete_col = float(op.get("frete_coleta") or 0) or frete_rota

        veiculos.append(
            {
                "placa": pl,
                "motorista": motorista,
                "tipo": "coleta",
                "romaneio": "",
                "paradas_n": len(stops_out),
                "distance_km": round(dist_km, 1),
                "duration_min": round(dur_min, 0),
                "peso": round(peso_rota, 2),
                "frete": round(frete_rota, 2),
                "peso_rota": round(peso_rota, 2),
                "frete_rota": round(frete_rota, 2),
                "peso_entrega": 0.0,
                "frete_entrega": 0.0,
                "peso_coleta": round(peso_col, 2),
                "frete_coleta": round(frete_col, 2),
                "polyline": polyline,
                "paradas": stops_out,
            }
        )

    try:
        save_delivery_geocode_cache()
    except Exception:
        pass

    on_status(f"Mapa: {len(veiculos)} veículo(s) de coleta com rota")
    return veiculos


def _merge_coleta_into_veiculos(
    entrega: list[dict[str, Any]],
    coleta: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Une entrega+coleta na mesma placa; mantém coleta-only como tipo=coleta."""
    by_pl: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for v in entrega:
        pl = str(v.get("placa") or "")
        if not pl:
            continue
        by_pl[pl] = dict(v)
        order.append(pl)
    for cv in coleta:
        pl = str(cv.get("placa") or "")
        if not pl:
            continue
        if pl in by_pl:
            ev = by_pl[pl]
            ev["tipo"] = "ambos"
            ev["polyline_coleta"] = cv.get("polyline") or []
            ev["paradas_coleta"] = cv.get("paradas") or []
            ev["paradas_coleta_n"] = int(cv.get("paradas_n") or 0)
            if not (ev.get("peso_coleta") or ev.get("frete_coleta")):
                ev["peso_coleta"] = cv.get("peso_coleta") or 0
                ev["frete_coleta"] = cv.get("frete_coleta") or 0
        else:
            by_pl[pl] = dict(cv)
            order.append(pl)
    return [by_pl[pl] for pl in order if pl in by_pl]


def _base_ll() -> tuple[str, float, float]:
    """Base BINHO — Av. Amâncio Gaioli, 1197 · Água Chata, Guarulhos-SP.

    Não usa o `cybermap_project.json` (lá a base aponta p/ Ministro Marcos Freire).
    Coords Nominatim da Av. Amâncio Gaiolli / Água Chata (CEP 07251).
    """
    return ("BINHO TRANSPORTES", -23.4265324, -46.3946075)


def _base_payload() -> dict[str, Any]:
    name, lat, lon = _base_ll()
    return {
        "nome": name or "BINHO TRANSPORTES",
        "lat": lat,
        "lon": lon,
        "endereco": "Av. Amâncio Gaioli, 1197 - Água Chata, Guarulhos - SP",
    }


def _servico_por_placa() -> dict[str, dict[str, float | int]]:
    """% de serviço feito por placa (103 coleta + 36 entrega)."""
    out: dict[str, dict[str, float | int]] = {}

    def _bump(pl: str, feitas: bool) -> None:
        if not pl:
            return
        slot = out.setdefault(pl, {"feitas": 0, "total": 0, "pct": 0.0})
        slot["total"] = int(slot["total"]) + 1
        if feitas:
            slot["feitas"] = int(slot["feitas"]) + 1

    if COLETAS_103_CSV.is_file():
        with COLETAS_103_CSV.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                pl = _norm_plate(row.get("placa") or "")
                st = str(row.get("status_ace") or "").strip().lower()
                if st in {"cancelada", "outro"}:
                    continue
                _bump(pl, st == "realizada")

    if ENTREGAS_36_CSV.is_file():
        with ENTREGAS_36_CSV.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("excluido") or "") in {"1", "true", "True"}:
                    continue
                pl = _norm_plate(row.get("placa") or "")
                st = str(row.get("status_ace") or "").strip().lower()
                if st in {"cancelada", "excluido", "excluída"}:
                    continue
                _bump(pl, st == "realizada")

    for pl, slot in out.items():
        tot = int(slot["total"]) or 0
        feitas = int(slot["feitas"]) or 0
        slot["pct"] = round((feitas / tot) * 100.0, 1) if tot else 0.0
    return out


def _apply_servico(veic: dict[str, Any], stats: dict[str, dict[str, float | int]]) -> None:
    pl = _norm_plate(veic.get("placa") or "")
    s = stats.get(pl) or {}
    veic["servico_feitas"] = int(s.get("feitas") or 0)
    veic["servico_total"] = int(s.get("total") or 0)
    veic["servico_pct"] = float(s.get("pct") or 0.0)


def _build_from_cybermap_report(
    report: Path,
    *,
    on_status: StatusCallback,
) -> dict[str, Any]:
    from map_renderer import parse_report_36, resolve_delivery_point, save_delivery_geocode_cache
    from osrm_client import build_visual_route

    on_status(f"Mapa: parse CyberMap {report.name}")
    stats = parse_report_36(str(report))
    ace_plates = _ace_em_rota_plates()
    # placas com atividade hoje (em rota ou já realizadas no CSV ACE)
    ace_today: set[str] = set(ace_plates)
    if ENTREGAS_36_CSV.is_file():
        with ENTREGAS_36_CSV.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("excluido") or "") in {"1", "true", "True"}:
                    continue
                st = str(row.get("status_ace") or "").strip().lower()
                if st in {"em_rota", "realizada", "pendencia"}:
                    pl = _norm_plate(row.get("placa") or "")
                    if pl:
                        ace_today.add(pl)

    by_plate: dict[str, list[Any]] = defaultdict(list)

    for d in stats.deliveries:
        if not _delivery_is_today(d):
            continue
        st_ace = _status_ace_from_ocorrencia(getattr(d, "status", "") or "")
        if st_ace not in {"em_rota", "realizada", "pendencia"}:
            continue
        pl = _norm_plate(getattr(d, "plate", "") or "")
        if not pl:
            continue
        # Prioriza placas do dia no ACE; se vazio, usa todas do relatório de hoje
        if ace_today and pl not in ace_today:
            # ainda inclui se estiver em rota no relatório
            if st_ace != "em_rota":
                continue
        by_plate[pl].append(d)

    if not by_plate and ace_today:
        for d in stats.deliveries:
            if not _delivery_is_today(d):
                continue
            pl = _norm_plate(getattr(d, "plate", "") or "")
            if pl in ace_today and _is_mapa_stop_status(getattr(d, "status", "") or ""):
                by_plate[pl].append(d)

    # Prefere placas ainda na rua; completa com realizadas do dia
    if ace_plates:
        preferred = {pl: rows for pl, rows in by_plate.items() if pl in ace_plates}
        if preferred:
            by_plate = defaultdict(list, preferred)

    base_name, base_lat, base_lon = _base_ll()
    base_pt = (base_lat, base_lon)
    veiculos: list[dict[str, Any]] = []
    district_points: dict[str, tuple[float, float]] = {}
    cargas = _frete_peso_por_placa(report_36=report, on_status=on_status)
    servico = _servico_por_placa()

    plates_sorted = sorted(by_plate.keys())[:_MAX_VEICULOS]
    on_status(f"Mapa: {len(plates_sorted)} placa(s) na rua — geocode/OSRM…")

    for pl in plates_sorted:
        deliveries = by_plate[pl][:_MAX_PARADAS]
        stops_out: list[dict[str, Any]] = []
        coords: list[tuple[float, float]] = []
        motorista = ""
        romaneio = ""
        peso_rota = 0.0
        frete_rota = 0.0
        for i, d in enumerate(deliveries, start=1):
            motorista = motorista or str(getattr(d, "driver", "") or "")
            romaneio = romaneio or str(getattr(d, "romaneio", "") or "")
            w = _num(getattr(d, "calculated_weight", 0))
            f = _num(getattr(d, "freight_amount", 0))
            peso_rota += w
            frete_rota += f
            pt = resolve_delivery_point(d, district_points)
            if not pt:
                continue
            lat, lon = float(pt[0]), float(pt[1])
            coords.append((lat, lon))
            st_raw = str(getattr(d, "status", "") or "")
            st_ace = _status_ace_from_ocorrencia(st_raw)
            stops_out.append(
                {
                    "seq": i,
                    "lat": lat,
                    "lon": lon,
                    "ctrc": str(getattr(d, "ctrc", "") or ""),
                    "cliente": str(getattr(d, "destinatario", "") or ""),
                    "cidade": str(getattr(d, "city", "") or ""),
                    "bairro": str(getattr(d, "bairro", "") or ""),
                    "status": st_raw,
                    "status_ace": st_ace,
                    "cep": str(getattr(d, "cep", "") or ""),
                    "peso": round(w, 2),
                    "frete": round(f, 2),
                }
            )
        polyline: list[list[float]] = []
        dist_km = 0.0
        dur_min = 0.0
        if len(coords) >= 1:
            try:
                route = build_visual_route(base_pt, coords, include_return_to_base=False)
                polyline = [[float(a), float(b)] for a, b in (route.geometry or [])]
                dist_km = float(getattr(route, "distance_km", 0) or 0)
                dur_min = float(getattr(route, "duration_min", 0) or 0)
            except Exception as err:
                on_status(f"Mapa: OSRM falhou {pl}: {err}")
                polyline = [[base_lat, base_lon]] + [[a, b] for a, b in coords]

        op = cargas.get(pl) or {}
        peso_ent = float(op.get("peso_entrega") or 0) or peso_rota
        frete_ent = float(op.get("frete_entrega") or 0) or frete_rota
        peso_col = float(op.get("peso_coleta") or 0)
        frete_col = float(op.get("frete_coleta") or 0)

        veiculos.append(
            {
                "placa": pl,
                "motorista": motorista,
                "tipo": "entrega",
                "romaneio": romaneio,
                "paradas_n": len(stops_out),
                "distance_km": round(dist_km, 1),
                "duration_min": round(dur_min, 0),
                "peso": round(peso_rota, 2),
                "frete": round(frete_rota, 2),
                "peso_rota": round(peso_rota, 2),
                "frete_rota": round(frete_rota, 2),
                "peso_entrega": round(peso_ent, 2),
                "frete_entrega": round(frete_ent, 2),
                "peso_coleta": round(peso_col, 2),
                "frete_coleta": round(frete_col, 2),
                "polyline": polyline,
                "paradas": stops_out,
            }
        )
        _apply_servico(veiculos[-1], servico)

    try:
        save_delivery_geocode_cache()
    except Exception:
        pass

    coleta_veics = _build_coleta_vehicles(
        report_36=report, cargas=cargas, on_status=on_status
    )
    veiculos = _merge_coleta_into_veiculos(veiculos, coleta_veics)
    for v in veiculos:
        _apply_servico(v, servico)

    tot_peso_rota = sum(float(v.get("peso_rota") or 0) for v in veiculos)
    tot_frete_rota = sum(float(v.get("frete_rota") or 0) for v in veiculos)
    tot_peso_ent = sum(float(v.get("peso_entrega") or 0) for v in veiculos)
    tot_frete_ent = sum(float(v.get("frete_entrega") or 0) for v in veiculos)
    tot_peso_col = sum(float(v.get("peso_coleta") or 0) for v in veiculos)
    tot_frete_col = sum(float(v.get("frete_coleta") or 0) for v in veiculos)
    n_coleta = sum(1 for v in veiculos if str(v.get("tipo") or "") in {"coleta", "ambos"})
    n_entrega = sum(1 for v in veiculos if str(v.get("tipo") or "") in {"entrega", "ambos"})

    return {
        "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "periodo": "hoje",
        "fonte": str(report),
        "base": _base_payload(),
        "veiculos": veiculos,
        "totais": {
            "veiculos": len(veiculos),
            "paradas": sum(
                int(v.get("paradas_n") or 0) + int(v.get("paradas_coleta_n") or 0)
                for v in veiculos
            ),
            "veiculos_entrega": n_entrega,
            "veiculos_coleta": n_coleta,
            "peso": round(tot_peso_rota, 2),
            "frete": round(tot_frete_rota, 2),
            "peso_rota": round(tot_peso_rota, 2),
            "frete_rota": round(tot_frete_rota, 2),
            "peso_entrega": round(tot_peso_ent, 2),
            "frete_entrega": round(tot_frete_ent, 2),
            "peso_coleta": round(tot_peso_col, 2),
            "frete_coleta": round(tot_frete_col, 2),
            "peso_total": round(tot_peso_ent + tot_peso_col, 2),
            "frete_total": round(tot_frete_ent + tot_frete_col, 2),
        },
    }


def _build_from_ace_csv(*, on_status: StatusCallback) -> dict[str, Any]:
    """Fallback sem relatório bruto: lista placas em_rota (sem polyline completa)."""
    on_status("Mapa: sem relatório 36 bruto — lista em_rota do CSV ACE")
    base_name, base_lat, base_lon = "BINHO TRANSPORTES", -23.4265324, -46.3946075
    try:
        root = _ensure_cybermap(on_status)
        if root:
            base_name, base_lat, base_lon = _base_ll()
    except Exception:
        pass

    by_plate: dict[str, list[dict[str, str]]] = defaultdict(list)
    if ENTREGAS_36_CSV.is_file():
        with ENTREGAS_36_CSV.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                st = str(row.get("status_ace") or "").strip().lower()
                if st not in {"em_rota", "realizada", "pendencia"}:
                    continue
                if str(row.get("excluido") or "") in {"1", "true", "True"}:
                    continue
                pl = _norm_plate(row.get("placa") or "")
                if not pl:
                    continue
                by_plate[pl].append(row)

    veiculos = []
    cargas = _frete_peso_por_placa(report_36=_find_report_36(), on_status=on_status)
    servico = _servico_por_placa()
    for i, pl in enumerate(sorted(by_plate.keys())[:_MAX_VEICULOS]):
        rows = by_plate[pl]
        # espalha levemente em torno da base para aparecer no mapa
        jitter = (i % 7) * 0.004
        lat = base_lat + jitter
        lon = base_lon - jitter * 0.6
        op = cargas.get(pl) or {}
        peso_ent = float(op.get("peso_entrega") or 0)
        frete_ent = float(op.get("frete_entrega") or 0)
        peso_col = float(op.get("peso_coleta") or 0)
        frete_col = float(op.get("frete_coleta") or 0)
        veiculos.append(
            {
                "placa": pl,
                "motorista": rows[0].get("motorista") or "",
                "tipo": "entrega",
                "romaneio": rows[0].get("romaneio") or "",
                "paradas_n": len(rows),
                "distance_km": 0,
                "duration_min": 0,
                "peso": round(peso_ent, 2),
                "frete": round(frete_ent, 2),
                "peso_rota": round(peso_ent, 2),
                "frete_rota": round(frete_ent, 2),
                "peso_entrega": round(peso_ent, 2),
                "frete_entrega": round(frete_ent, 2),
                "peso_coleta": round(peso_col, 2),
                "frete_coleta": round(frete_col, 2),
                "polyline": [[base_lat, base_lon], [lat, lon]],
                "paradas": [
                    {
                        "seq": j + 1,
                        "lat": lat,
                        "lon": lon,
                        "ctrc": r.get("ctrc_id") or "",
                        "cliente": r.get("destinatario") or "",
                        "cidade": r.get("cidade") or "",
                        "bairro": r.get("bairro") or "",
                        "status": r.get("ocorrencia") or r.get("status_ace") or "em_rota",
                        "status_ace": str(r.get("status_ace") or "em_rota").strip().lower()
                        or "em_rota",
                        "cep": r.get("cep") or "",
                    }
                    for j, r in enumerate(rows[:_MAX_PARADAS])
                ],
                "aprox": True,
            }
        )
        _apply_servico(veiculos[-1], servico)

    tot_peso_ent = sum(float(v.get("peso_entrega") or 0) for v in veiculos)
    tot_frete_ent = sum(float(v.get("frete_entrega") or 0) for v in veiculos)
    tot_peso_col = sum(float(v.get("peso_coleta") or 0) for v in veiculos)
    tot_frete_col = sum(float(v.get("frete_coleta") or 0) for v in veiculos)

    coleta_veics = _build_coleta_vehicles(
        report_36=_find_report_36(), cargas=cargas, on_status=on_status
    )
    veiculos = _merge_coleta_into_veiculos(veiculos, coleta_veics)
    tot_peso_col = sum(float(v.get("peso_coleta") or 0) for v in veiculos)
    tot_frete_col = sum(float(v.get("frete_coleta") or 0) for v in veiculos)
    tot_peso_ent = sum(float(v.get("peso_entrega") or 0) for v in veiculos)
    tot_frete_ent = sum(float(v.get("frete_entrega") or 0) for v in veiculos)

    return {
        "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "periodo": "hoje",
        "fonte": "entregas_36.csv",
        "base": _base_payload(),
        "aviso": "Rotas aproximadas — rode `mapa` para puxar 50+36 e geocode real.",
        "veiculos": veiculos,
        "totais": {
            "veiculos": len(veiculos),
            "paradas": sum(
                int(v.get("paradas_n") or 0) + int(v.get("paradas_coleta_n") or 0)
                for v in veiculos
            ),
            "peso": round(tot_peso_ent, 2),
            "frete": round(tot_frete_ent, 2),
            "peso_rota": round(tot_peso_ent, 2),
            "frete_rota": round(tot_frete_ent, 2),
            "peso_entrega": round(tot_peso_ent, 2),
            "frete_entrega": round(tot_frete_ent, 2),
            "peso_coleta": round(tot_peso_col, 2),
            "frete_coleta": round(tot_frete_col, 2),
            "peso_total": round(tot_peso_ent + tot_peso_col, 2),
            "frete_total": round(tot_frete_ent + tot_frete_col, 2),
        },
    }


def _write_outputs(payload: dict[str, Any], *, on_status: StatusCallback) -> dict[str, str]:
    ensure_dirs()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    paths: dict[str, str] = {}
    for dest in (MAPA_CACHE_JSON, MAPA_DASH_JSON, MAPA_LOCAL_JSON):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        paths[str(dest)] = str(dest)
    stamp = {
        "ts": time.time(),
        "atualizado": payload.get("atualizado"),
        "veiculos": (payload.get("totais") or {}).get("veiculos"),
    }
    stamp_path = MAPA_DASH_DIR / "stamp.json"
    stamp_path.write_text(json.dumps(stamp, ensure_ascii=False), encoding="utf-8")
    paths["stamp"] = str(stamp_path)
    on_status(
        f"Mapa: OK · {stamp.get('veiculos') or 0} veículo(s) · "
        f"{(payload.get('totais') or {}).get('paradas') or 0} parada(s) · "
        f"frete R$ {(payload.get('totais') or {}).get('frete_total') or (payload.get('totais') or {}).get('frete') or 0}"
    )
    return paths


def build_mapa_distribuicao(
    *,
    report_path: Path | str | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Gera o JSON do Mapa Operacional e publica no dashboard."""
    status = on_status or _noop
    status("Mapa Operacional: gerando…")
    root = _ensure_cybermap(status)
    report = _find_report_36(report_path)

    payload: dict[str, Any]
    if root and report:
        try:
            payload = _build_from_cybermap_report(report, on_status=status)
        except Exception as err:
            status(f"Mapa: falha CyberMap ({err}) — fallback CSV")
            payload = _build_from_ace_csv(on_status=status)
    else:
        if not report:
            status("Mapa: relatório 36 não encontrado no disco")
        payload = _build_from_ace_csv(on_status=status)

    paths = _write_outputs(payload, on_status=status)
    return {"ok": True, "payload": payload, "paths": paths}


def publish_mapa_local(*, on_status: StatusCallback | None = None) -> dict[str, Any]:
    """Re-copia JSON já em cache para o dashboard (se existir)."""
    status = on_status or _noop
    if MAPA_CACHE_JSON.is_file():
        data = json.loads(MAPA_CACHE_JSON.read_text(encoding="utf-8"))
        paths = _write_outputs(data, on_status=status)
        return {"ok": True, "paths": paths}
    return build_mapa_distribuicao(on_status=status)
