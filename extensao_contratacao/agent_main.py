"""
ACE · Agente Contratação (mini-extensão)

Roda no PC que tem a planilha PRODUTIVIDADE CONTRATAÇÃO.xlsx.
Lê Excel → publica dashboard + Sheets (custo). Frete 200 fica no CRT.

Uso:
  python -m extensao_contratacao.agent_main
  run_agente.bat
  python -m extensao_contratacao.agent_main --once
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

_EXT_DIR = Path(__file__).resolve().parent
_ACE_ROOT = _EXT_DIR.parent
if str(_ACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ACE_ROOT))

CONFIG_PATH = _EXT_DIR / "config_agente.json"
FORCE_RUN = _EXT_DIR / "FORCE_RUN"
EXAMPLE_PATH = _EXT_DIR / "config_agente.example.json"


def _load_agent_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    if EXAMPLE_PATH.exists():
        try:
            return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "excel_path": "PRODUTIVIDADE CONTRATAÇÃO.xlsx",
        "intervalo": "15m",
        "sync_sheets": True,
    }


def _parse_interval(text: str) -> int:
    raw = (text or "15m").strip().lower()
    if raw.endswith("s"):
        return max(30, int(float(raw[:-1])))
    if raw.endswith("m"):
        return max(60, int(float(raw[:-1]) * 60))
    if raw.endswith("h"):
        return max(60, int(float(raw[:-1]) * 3600))
    try:
        return max(60, int(float(raw)))
    except ValueError:
        return 900


def _status(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _apply_agent_config_to_ace(cfg_agent: dict) -> None:
    """Espelha URL/token Sheets do agente no settings do ACE (se vierem preenchidos)."""
    from config import load_settings, save_settings

    settings = load_settings()
    changed = False
    excel = str(cfg_agent.get("excel_path") or "").strip()
    if excel and getattr(settings, "ctr_agente_excel", "") != excel:
        settings.ctr_agente_excel = excel
        changed = True
    for key_src, key_dst in (
        ("apps_script_url", "apps_script_url"),
        ("apps_script_token", "apps_script_token"),
    ):
        val = str(cfg_agent.get(key_src) or "").strip()
        if val and getattr(settings, key_dst, "") != val:
            setattr(settings, key_dst, val)
            changed = True
    if cfg_agent.get("enable_sheets") is True and not settings.enable_sheets:
        settings.enable_sheets = True
        changed = True
    if cfg_agent.get("sync_remoto") is True and not getattr(settings, "sync_remoto", True):
        settings.sync_remoto = True
        changed = True
    if changed:
        save_settings(settings)
        _status("Config ACE atualizada a partir do config_agente.json")


def run_once(*, excel: str = "", skip_200: bool | None = None) -> dict:
    try:
        from extensao_contratacao.pipeline_agente import run_pipeline_contratacao_excel
        from extensao_contratacao.parser_produtividade import resolve_produtividade_xlsx
    except ImportError:
        from pipeline_agente import run_pipeline_contratacao_excel  # type: ignore
        from parser_produtividade import resolve_produtividade_xlsx  # type: ignore

    agent_cfg = _load_agent_config()
    _apply_agent_config_to_ace(agent_cfg)
    path = resolve_produtividade_xlsx(excel or agent_cfg.get("excel_path") or "")
    return run_pipeline_contratacao_excel(
        excel_path=path,
        sync_sheets=bool(agent_cfg.get("sync_sheets", True)),
        on_status=_status,
    )


def loop_forever(*, excel: str = "") -> None:
    agent_cfg = _load_agent_config()
    interval = _parse_interval(str(agent_cfg.get("intervalo") or "15m"))
    _status(f"Agente Contratação iniciado · intervalo={interval}s · ctrl+c para sair")
    while True:
        forced = FORCE_RUN.exists()
        if forced:
            try:
                FORCE_RUN.unlink()
            except OSError:
                pass
            _status("FORCE_RUN detectado — ciclo imediato")
            agent_cfg = _load_agent_config()
            interval = _parse_interval(str(agent_cfg.get("intervalo") or "15m"))
        try:
            result = run_once(excel=excel)
            resumo = result.get("resumo") or {}
            _status(
                f"ciclo OK · veíc={resumo.get('total_veiculos')} "
                f"custo={resumo.get('custo_fmt')}"
            )
        except KeyboardInterrupt:
            raise
        except Exception as err:  # noqa: BLE001
            _status(f"ciclo FALHOU: {err}")
        _status(f"próximo em {interval}s…")
        # acorda cedo se FORCE_RUN aparecer
        slept = 0
        while slept < interval:
            if FORCE_RUN.exists():
                break
            time.sleep(min(5, interval - slept))
            slept += 5


def push_update_from_main(dest_dir: Path | str | None = None) -> str:
    """Copia arquivos da extensão + escreve config a partir do ACE principal."""
    try:
        from extensao_contratacao.push_agente import format_push_result, push_agente_update
    except ImportError:
        from push_agente import format_push_result, push_agente_update  # type: ignore

    try:
        result = push_agente_update(dest_dir)
    except Exception as err:  # noqa: BLE001
        return f"push agente FALHOU · {err}"
    msg = format_push_result(result)
    print(msg)
    return msg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ACE Agente Contratação (Excel → Sheets)")
    parser.add_argument("--once", action="store_true", help="Roda um ciclo e sai (sem janela)")
    parser.add_argument("--loop", action="store_true", help="Loop no console (sem janela)")
    parser.add_argument("--excel", default="", help="Nome/caminho da planilha .xlsx")
    parser.add_argument(
        "--update-self",
        action="store_true",
        help="Regrava config_agente.json a partir do ACE (uso interno)",
    )
    args = parser.parse_args(argv)

    if args.update_self:
        print(push_update_from_main())
        return 0
    if args.once:
        run_once(excel=args.excel)
        return 0
    if args.loop:
        try:
            loop_forever(excel=args.excel)
        except KeyboardInterrupt:
            _status("agente encerrado")
        return 0

    # Padrão: janela visual (estilo CRT)
    try:
        from extensao_contratacao.ui_agente import run_ui
    except ImportError:
        from ui_agente import run_ui  # type: ignore
    return run_ui()


if __name__ == "__main__":
    raise SystemExit(main())
