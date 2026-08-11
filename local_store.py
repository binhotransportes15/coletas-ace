"""
Armazenamento local rápido (JSON) — substitui Sheets quando modo_local=True.

Pasta: data/cache/local/
  stamp.json           — índice geral
  distribuicao.json    — resumos 50/103/36/225
  armazem.json
  pendencia.json
  contratacao.json
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import CACHE_DIR, DASHBOARD_DIR, ensure_dirs

StatusCallback = Callable[[str], None]

LOCAL_DIR = CACHE_DIR / "local"
STAMP_PATH = LOCAL_DIR / "stamp.json"


def _noop(_: str) -> None:
    return None


def _read_csv_rows(path: Path, *, limit: int | None = None) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:  # noqa: BLE001
        return []
    if limit is not None:
        return rows[: max(0, int(limit))]
    return rows


def _read_csv_first(path: Path) -> dict[str, str]:
    rows = _read_csv_rows(path, limit=1)
    return dict(rows[0]) if rows else {}


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    return path


def local_dir() -> Path:
    ensure_dirs()
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_DIR


def build_distribuicao_snapshot() -> dict[str, Any]:
    from parser_ssw0157 import RESUMO_CSV as R50
    from parser_ssw103 import RESUMO_103_CSV as R103
    from parser_ssw0146 import RESUMO_36_CSV as R36
    from parser_ssw225 import RESUMO_225_CSV as R225

    return {
        "setor": "distribuicao",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "resumo_50": _read_csv_first(R50),
        "resumo_103": _read_csv_first(R103),
        "resumo_36": _read_csv_first(R36),
        "resumo_225": _read_csv_first(R225),
    }


def build_armazem_snapshot() -> dict[str, Any]:
    from parser_ssw78 import RESUMO_CSV as R78, VEICULOS_CSV as V78
    from parser_ssw177 import RESUMO_177_CSV as R177

    return {
        "setor": "armazem",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "resumo_78": _read_csv_first(R78),
        "resumo_177": _read_csv_first(R177),
        "veiculos_amostra": _read_csv_rows(V78, limit=40),
        "veiculos_total": len(_read_csv_rows(V78)),
    }


def build_pendencia_snapshot() -> dict[str, Any]:
    from parser_ssw31 import OFENSORES_31_CSV, RESUMO_31_CSV

    return {
        "setor": "pendencia",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "resumo_31": _read_csv_first(RESUMO_31_CSV),
        "ofensores": _read_csv_rows(OFENSORES_31_CSV, limit=30),
    }


def build_contratacao_snapshot() -> dict[str, Any]:
    from parser_ssw073 import RESUMO_073_CSV, VEICULOS_073_CSV

    return {
        "setor": "contratacao",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "resumo_073": _read_csv_first(RESUMO_073_CSV),
        "veiculos_amostra": _read_csv_rows(VEICULOS_073_CSV, limit=40),
        "veiculos_total": len(_read_csv_rows(VEICULOS_073_CSV)),
    }


_BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {
    "distribuicao": build_distribuicao_snapshot,
    "50": build_distribuicao_snapshot,
    "103": build_distribuicao_snapshot,
    "36": build_distribuicao_snapshot,
    "225": build_distribuicao_snapshot,
    "armazem": build_armazem_snapshot,
    "78": build_armazem_snapshot,
    "177": build_armazem_snapshot,
    "pendencia": build_pendencia_snapshot,
    "31": build_pendencia_snapshot,
    "contratacao": build_contratacao_snapshot,
    "73": build_contratacao_snapshot,
}

_SECTOR_FILE: dict[str, str] = {
    "distribuicao": "distribuicao.json",
    "armazem": "armazem.json",
    "pendencia": "pendencia.json",
    "contratacao": "contratacao.json",
}


def _canon_sector(key: str) -> str:
    k = (key or "").strip().lower()
    if k in {"50", "103", "36", "225", "dist", "distribuicao"}:
        return "distribuicao"
    if k in {"78", "177", "armazem", "arm"}:
        return "armazem"
    if k in {"31", "pendencia"}:
        return "pendencia"
    if k in {"73", "076", "200", "contratacao", "ctr"}:
        return "contratacao"
    return k if k in _SECTOR_FILE else "distribuicao"


def persist_sector(
    sector: str,
    *,
    extra: dict[str, Any] | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Gera/atualiza JSON de um setor a partir dos CSVs de cache."""
    status = on_status or _noop
    local_dir()
    sid = _canon_sector(sector)
    builder = _BUILDERS.get(sid) or build_distribuicao_snapshot
    payload = builder()
    if extra:
        payload["extra"] = extra
    fname = _SECTOR_FILE.get(sid, f"{sid}.json")
    path = _write_json(LOCAL_DIR / fname, payload)
    _update_stamp(sid, path)
    # espelho leve no dashboard (TV local)
    dash_copy = DASHBOARD_DIR / "data" / "local" / fname
    try:
        _write_json(dash_copy, payload)
    except Exception:  # noqa: BLE001
        pass
    status(f"Local JSON · {sid} -> {path.name}")
    return {"ok": True, "via": "local_json", "sector": sid, "path": str(path), "payload": payload}


def persist_all(*, on_status: StatusCallback | None = None) -> dict[str, Any]:
    """Atualiza todos os JSONs locais + stamp (rápido, sem rede)."""
    status = on_status or _noop
    status("Local JSON: gravando snapshot interno…")
    out: dict[str, Any] = {"ok": True, "via": "local_json", "sectors": {}}
    for sid in ("distribuicao", "armazem", "pendencia", "contratacao"):
        try:
            out["sectors"][sid] = persist_sector(sid, on_status=status)
        except Exception as err:  # noqa: BLE001
            out["sectors"][sid] = {"ok": False, "error": str(err)}
            status(f"Local JSON · {sid} falhou: {err}")
    out["stamp"] = str(STAMP_PATH)
    return out


def _update_stamp(sector: str, path: Path) -> None:
    stamp: dict[str, Any] = {}
    if STAMP_PATH.is_file():
        try:
            stamp = json.loads(STAMP_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            stamp = {}
    sectors = dict(stamp.get("sectors") or {})
    sectors[sector] = {
        "file": path.name,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    stamp = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "sectors": sectors,
        "mode": "local_json",
    }
    _write_json(STAMP_PATH, stamp)
    try:
        _write_json(DASHBOARD_DIR / "data" / "local" / "stamp.json", stamp)
    except Exception:  # noqa: BLE001
        pass


def read_stamp() -> dict[str, Any]:
    if not STAMP_PATH.is_file():
        return {}
    try:
        return json.loads(STAMP_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
