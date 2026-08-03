"""
ACE · Loop CMD (tempo real)

Roda sem parar:
  - a cada N segundos/minutos/horas/dias baixa 50 + 103 EM PARALELO
  - depois 36 (entregas) e 225 (agendamentos do MES corrente)
  - 50  = periodo de COLETA HOJE
  - 103 = data LIMITE HOJE (Por data de = L)
  - 225 = previsao entrega do mes (dia 1 → ultimo), arquivo R
  - na virada do dia/mes recalcula sozinho os periodos
  - intervalo vem de config loop_intervalo (ex.: 5m, 30s, 1h)

Ctrl+C para parar.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime

from config import CONFIG_PATH, ensure_dirs, load_credentials, load_settings
from dates import format_period, periodo_103_hoje, periodo_50_coleta_hoje, to_ssw_ddmmyy
from interval_parse import format_duration, format_duration_long, parse_duration
from pipeline import run_dual_cycle


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def resolve_interval_sec(
    override: str | int | float | None = None,
    *,
    settings_intervalo: str | None = None,
) -> int:
    """Prioridade: override CLI > settings.loop_intervalo > 5m."""
    if override is not None and str(override).strip() != "":
        if isinstance(override, (int, float)) and not isinstance(override, bool):
            # numero puro no CLI antigo = minutos (compat)
            return parse_duration(f"{override}m")
        return parse_duration(str(override))
    raw = (settings_intervalo or "").strip() or "5m"
    try:
        return parse_duration(raw)
    except ValueError:
        return parse_duration("5m")


def _banner(interval_sec: int, headless: bool) -> None:
    ini50, fim50 = periodo_50_coleta_hoje()
    ini103, fim103 = periodo_103_hoje()
    hoje = date.today()
    print("=" * 72, flush=True)
    print("  ACE · MODO /AUTOMATICA", flush=True)
    print("=" * 72, flush=True)
    print(f"  Hoje:        {hoje:%d/%m/%Y} ({hoje.strftime('%A')})", flush=True)
    print(
        f"  50 coleta:   {to_ssw_ddmmyy(ini50)} a {to_ssw_ddmmyy(fim50)} "
        f"({format_period(ini50, fim50)})",
        flush=True,
    )
    print(
        f"  103 limite:  {to_ssw_ddmmyy(ini103)} ({format_period(ini103, fim103)})",
        flush=True,
    )
    print(
        f"  Intervalo:   {format_duration(interval_sec)} "
        f"({format_duration_long(interval_sec)}) | headless={headless}",
        flush=True,
    )
    print(
        f"  Armazém 078: {'ON no ciclo' if load_settings().armazem_in_loop else 'OFF'} "
        f"| sheets={'ON' if load_settings().armazem_enable_sheets else 'OFF'}",
        flush=True,
    )
    print(f"  Config:      {CONFIG_PATH}", flush=True)
    print("  Parar:       Ctrl+C", flush=True)
    print("=" * 72, flush=True)


def run_loop(
    *,
    interval_sec: int | None = None,
    interval_min: int | None = None,  # legado
    headless: bool = True,
    once: bool = False,
) -> int:
    ensure_dirs()
    creds = load_credentials()
    cfg = load_settings()
    if not (creds.user and creds.password):
        _log("ERRO: configure login no ace.bat (/e user ... /e password ...)")
        return 1

    if interval_sec is None and interval_min is not None:
        interval_sec = resolve_interval_sec(f"{interval_min}m")
    if interval_sec is None:
        interval_sec = resolve_interval_sec(settings_intervalo=cfg.loop_intervalo)

    day_marker = date.today()
    _banner(interval_sec, headless)
    ciclo = 0

    while True:
        ciclo += 1
        today = date.today()
        if today != day_marker:
            _log(f"VIRADA DE DIA: {day_marker} → {today} | recalculando periodos")
            day_marker = today
            _banner(interval_sec, headless)

        ini50, fim50 = periodo_50_coleta_hoje(today)
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

        # recarrega intervalo a cada ciclo (permite /e intervalo em outro terminal)
        creds = load_credentials()
        cfg = load_settings()
        try:
            interval_sec = resolve_interval_sec(settings_intervalo=cfg.loop_intervalo)
        except ValueError:
            pass

        wait_s = max(5, int(interval_sec))
        _log(f"Aguardando {format_duration_long(wait_s)} ate o proximo ciclo...")
        # dorme em fatias curtas para reagir a Ctrl+C e virada de dia
        slice_s = 1.0 if wait_s <= 30 else 5.0 if wait_s <= 120 else 15.0
        end_wait = time.time() + wait_s
        while time.time() < end_wait:
            time.sleep(min(slice_s, max(0.2, end_wait - time.time())))
            if date.today() != day_marker:
                _log("Dia mudou durante a espera — iniciando ciclo agora.")
                break


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ACE loop 50+103 paralelo")
    parser.add_argument(
        "--interval",
        "-i",
        default=None,
        help="Intervalo: 30s | 5m | 1h | 2d (padrao = config loop_intervalo)",
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
        cfg = load_settings()
        sec = resolve_interval_sec(args.interval, settings_intervalo=cfg.loop_intervalo)
        return run_loop(
            interval_sec=sec,
            headless=not args.headed,
            once=bool(args.once),
        )
    except KeyboardInterrupt:
        print("\nLoop interrompido.", flush=True)
        return 0
    except ValueError as err:
        print(f"ERRO: {err}", flush=True)
        return 1


if __name__ == "__main__":
    os.chdir(str(CONFIG_PATH.parent.parent))
    if str(CONFIG_PATH.parent.parent) not in sys.path:
        sys.path.insert(0, str(CONFIG_PATH.parent.parent))
    raise SystemExit(main())
