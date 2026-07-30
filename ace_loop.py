"""
ACE · Loop CMD (tempo real)

Roda sem parar:
  - a cada N minutos (padrao 5) baixa 50 + 103 EM PARALELO
  - 50  = cadastramento D-1 (segunda = sexta–sabado)
  - 103 = inclusao HOJE
  - na virada do dia recalcula sozinho os periodos

Ctrl+C para parar.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime

from config import CONFIG_PATH, ensure_dirs, load_credentials, load_settings
from dates import format_period, periodo_103_hoje, periodo_50_cadastramento, to_ssw_ddmmyy
from pipeline import run_dual_cycle


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _banner(interval_min: int, headless: bool) -> None:
    ini50, fim50 = periodo_50_cadastramento()
    ini103, fim103 = periodo_103_hoje()
    hoje = date.today()
    print("=" * 72, flush=True)
    print("  ACE · MODO /AUTOMATICA", flush=True)
    print("=" * 72, flush=True)
    print(f"  Hoje:        {hoje:%d/%m/%Y} ({hoje.strftime('%A')})", flush=True)
    print(
        f"  50 cad:      {to_ssw_ddmmyy(ini50)} a {to_ssw_ddmmyy(fim50)} "
        f"({format_period(ini50, fim50)})",
        flush=True,
    )
    print(
        f"  103 inclusao:{to_ssw_ddmmyy(ini103)} ({format_period(ini103, fim103)})",
        flush=True,
    )
    print(f"  Intervalo:   {interval_min} min | headless={headless}", flush=True)
    print(f"  Config:      {CONFIG_PATH}", flush=True)
    print("  Parar:       Ctrl+C", flush=True)
    print("=" * 72, flush=True)


def run_loop(*, interval_min: int = 5, headless: bool = True, once: bool = False) -> int:
    ensure_dirs()
    creds = load_credentials()
    cfg = load_settings()
    if not (creds.user and creds.password):
        _log("ERRO: configure login no ace.bat (/e user ... /e password ...)")
        return 1

    day_marker = date.today()
    _banner(interval_min, headless)
    ciclo = 0

    while True:
        ciclo += 1
        today = date.today()
        if today != day_marker:
            _log(f"VIRADA DE DIA: {day_marker} → {today} | recalculando periodos")
            day_marker = today
            _banner(interval_min, headless)

        ini50, fim50 = periodo_50_cadastramento(today)
        ini103, fim103 = periodo_103_hoje(today)
        _log(
            f"=== CICLO {ciclo} | 50={format_period(ini50, fim50)} "
            f"| 103={format_period(ini103, fim103)} ==="
        )
        t0 = time.time()
        try:
            result = run_dual_cycle(
                credentials=creds,
                settings=cfg,
                headless=headless,
                on_status=_log,
                sync=True,
            )
            elapsed = time.time() - t0
            err = result.get("errors") or {}
            tot50 = ((result.get("50") or {}).get("analysis") or {}).get("totais_situacao") or {}
            tot103 = ((result.get("103") or {}).get("analysis") or {}).get("totais") or {}
            _log(
                f"CICLO {ciclo} OK em {elapsed:.0f}s | "
                f"50={tot50 or '—'} | 103={tot103 or '—'} | erros={err or '{}'}"
            )
        except Exception as err:  # noqa: BLE001
            elapsed = time.time() - t0
            _log(f"CICLO {ciclo} FALHOU em {elapsed:.0f}s: {err}")

        if once:
            return 0

        wait_s = max(5, int(interval_min * 60))
        _log(f"Aguardando {interval_min} min ate o proximo ciclo...")
        # dorme em fatias para reagir a Ctrl+C e virada de dia
        end_wait = time.time() + wait_s
        while time.time() < end_wait:
            time.sleep(min(15, end_wait - time.time()))
            if date.today() != day_marker:
                _log("Dia mudou durante a espera — iniciando ciclo agora.")
                break
        # reload config a cada ciclo (permite /e em outro terminal)
        creds = load_credentials()
        cfg = load_settings()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ACE loop 50+103 paralelo")
    parser.add_argument(
        "--interval",
        "-i",
        type=int,
        default=5,
        help="Minutos entre ciclos (padrao 5)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Mostra o navegador (mais lento). Padrao e headless/rapido.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Roda um unico ciclo e sai",
    )
    args = parser.parse_args(argv)
    try:
        return run_loop(
            interval_min=max(1, args.interval),
            headless=not args.headed,
            once=bool(args.once),
        )
    except KeyboardInterrupt:
        print("\nLoop interrompido.", flush=True)
        return 0


if __name__ == "__main__":
    os.chdir(str(CONFIG_PATH.parent.parent))
    if str(CONFIG_PATH.parent.parent) not in sys.path:
        sys.path.insert(0, str(CONFIG_PATH.parent.parent))
    raise SystemExit(main())
