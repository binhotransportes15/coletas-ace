"""
ACE · Loop CMD (tempo real)

Agenda por setor:
  - cada setor ligado no automático tem seu intervalo (ex.: dist 5m, 455 30m)
  - vazio no setor → usa loop_intervalo (fallback)
  - ciclo_paralelo: setores due rodam juntos

Ctrl+C / Parar no CRT para encerrar.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from config import AceSettings, CONFIG_PATH, ensure_dirs, load_credentials, load_settings
from dates import format_period, periodo_103_hoje, periodo_50_coleta_hoje, to_ssw_ddmmyy
from interval_parse import format_duration, format_duration_long, parse_duration
from pipeline import run_parallel_cycle
from ace_stop import (
    LoopStopped,
    clear_loop_pid,
    clear_stop,
    stop_requested,
    write_loop_pid,
)

# id interno → (flag in_loop, campo intervalo, rótulo)
SECTOR_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("dist", "dist_in_loop", "dist_intervalo", "Distribuição"),
    ("78", "armazem_in_loop", "armazem_intervalo", "Armazém"),
    ("31", "pendencia_in_loop", "pendencia_intervalo", "Pendência"),
    ("73", "contratacao_in_loop", "contratacao_intervalo", "Contratação"),
    ("455", "emissao_in_loop", "emissao_intervalo", "Emissão"),
)


def _log(msg: str) -> None:
    try:
        from term_brand import format_status, _enable_windows_ansi, classify_status_msg

        _enable_windows_ansi()
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"  {format_status(msg, hhmmss=stamp)}", flush=True)
        try:
            from crt_bridge import append_log, publish

            kind = classify_status_msg(msg)
            append_log(kind, msg, source="cmd")
            publish(
                online=kind != "err",
                label="ONLINE" if kind != "err" else "ERR",
                pct=0,
                detail=str(msg)[:100],
                mode={"ok": "OK", "err": "ERR", "work": "RUN"}.get(kind, "RUN"),
            )
        except Exception:
            pass
    except Exception:
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


def sector_interval_sec(cfg: AceSettings, sector_id: str) -> int:
    """Intervalo do setor; vazio → loop_intervalo."""
    field = next((f for sid, _fl, f, _lb in SECTOR_SPECS if sid == sector_id), "")
    raw = ""
    if field:
        raw = str(getattr(cfg, field, "") or "").strip()
    if not raw:
        raw = str(getattr(cfg, "loop_intervalo", "") or "5m").strip() or "5m"
    try:
        return max(5, parse_duration(raw))
    except ValueError:
        return max(5, parse_duration("5m"))


def enabled_sectors(cfg: AceSettings) -> list[str]:
    out: list[str] = []
    for sid, flag, _iv, _lb in SECTOR_SPECS:
        if bool(getattr(cfg, flag, False if sid != "dist" else True)):
            out.append(sid)
    return out


def _banner(cfg: AceSettings, headless: bool) -> None:
    ini50, fim50 = periodo_50_coleta_hoje()
    ini103, fim103 = periodo_103_hoje()
    hoje = date.today()
    try:
        from term_brand import (
            _enable_windows_ansi,
            cubes_row,
            print_header_banner,
            progress_bar,
            rule,
            status_idle,
            status_online,
            status_work,
            muted,
        )

        _enable_windows_ansi()
        print_header_banner(subtitle="/AUTOMATICA · em execução")
        print(f"  {status_work('HOJE')}  {hoje:%d/%m/%Y} ({hoje.strftime('%A')})")
        print(
            f"  {status_idle('50')}  {to_ssw_ddmmyy(ini50)}→{to_ssw_ddmmyy(fim50)} "
            f"({format_period(ini50, fim50)})"
        )
        print(
            f"  {status_idle('103')} {to_ssw_ddmmyy(ini103)} "
            f"({format_period(ini103, fim103)})"
        )
        print(
            f"  {status_work('TICK')} fallback {format_duration_long(resolve_interval_sec(settings_intervalo=cfg.loop_intervalo))}  "
            f"headless={headless}"
        )
        for sid, flag, _iv, label in SECTOR_SPECS:
            on = bool(getattr(cfg, flag, sid == "dist"))
            mark = status_online(sid.upper()[:4]) if on else status_idle(sid.upper()[:4])
            if on:
                print(
                    f"  {mark}  {label} · a cada {format_duration(sector_interval_sec(cfg, sid))}"
                )
            else:
                print(f"  {mark}  {label} · fora do automático")
        print(
            f"  {status_online('PARA') if getattr(cfg, 'ciclo_paralelo', True) else status_idle('SEQ')}  "
            f"{status_online('SHEETS') if cfg.enable_sheets else status_idle('SHEETS')}"
        )
        print(f"  {cubes_row()}")
        print(f"  {progress_bar(40.0, width=24, label='agenda setores')}")
        print(f"  {muted(str(CONFIG_PATH))}")
        print(f"  {rule()}")
    except Exception:
        print("=" * 72, flush=True)
        print("  ACE · MODO /AUTOMATICA", flush=True)
        print("=" * 72, flush=True)
        print(f"  Hoje: {hoje:%d/%m/%Y}", flush=True)
        for sid, flag, _iv, label in SECTOR_SPECS:
            on = bool(getattr(cfg, flag, sid == "dist"))
            if on:
                print(f"  {label}: {format_duration(sector_interval_sec(cfg, sid))}", flush=True)
        print("=" * 72, flush=True)


def _sleep_until(
    until: float,
    *,
    should_stop: Callable[[], bool] | None,
    day_marker: date,
) -> tuple[bool, bool]:
    """Espera até `until`. Retorna (stop, day_changed)."""
    while time.time() < until:
        if stop_requested() or (should_stop and should_stop()):
            return True, False
        if date.today() != day_marker:
            return False, True
        rem = until - time.time()
        # fatias curtas = Parar responde em ~0,4s
        slice_s = 0.4 if rem <= 30 else 1.0 if rem <= 120 else 2.0
        time.sleep(min(slice_s, max(0.15, rem)))
    return False, False


def _want_stop(should_stop: Callable[[], bool] | None) -> bool:
    return stop_requested() or bool(should_stop and should_stop())


def run_loop(
    *,
    interval_sec: int | None = None,
    interval_min: int | None = None,  # legado
    headless: bool | None = None,
    once: bool = False,
    should_stop: Callable[[], bool] | None = None,
    quiet_banner: bool = False,
) -> int:
    ensure_dirs()
    clear_stop()
    write_loop_pid()
    creds = load_credentials()
    cfg = load_settings()
    if not (creds.user and creds.password):
        _log("ERRO: configure login no painel (aba Configuração)")
        clear_loop_pid()
        return 1

    # override CLI → aplica como fallback global (não apaga tempos por setor)
    if interval_sec is None and interval_min is not None:
        interval_sec = resolve_interval_sec(f"{interval_min}m")
    if interval_sec is not None:
        # força fallback temporário só nesta sessão (não grava config)
        try:
            cfg.loop_intervalo = format_duration(int(interval_sec))
        except Exception:
            cfg.loop_intervalo = f"{int(interval_sec)}s"

    use_headless = cfg.headless if headless is None else bool(headless)
    day_marker = date.today()
    if not quiet_banner:
        _banner(cfg, use_headless)
    else:
        ons = enabled_sectors(cfg)
        _log(
            "Atualização contínua · setores: "
            + (
                ", ".join(
                    f"{s}={format_duration(sector_interval_sec(cfg, s))}" for s in ons
                )
                or "(nenhum)"
            )
        )

    last_run: dict[str, float] = {}
    ciclo = 0

    try:
        while True:
            if _want_stop(should_stop):
                _log("Atualização contínua interrompida.")
                return 0

            today = date.today()
            if today != day_marker:
                _log(f"VIRADA DE DIA: {day_marker} → {today} | recalculando periodos")
                day_marker = today
                if not quiet_banner:
                    _banner(cfg, use_headless)

            cfg = load_settings()
            if interval_sec is not None:
                try:
                    cfg.loop_intervalo = format_duration(int(interval_sec))
                except Exception:
                    pass
            if headless is None:
                use_headless = bool(cfg.headless)
            creds = load_credentials()

            ons = enabled_sectors(cfg)
            if not ons:
                _log("Nenhum setor no automático — aguardando 30s (configure na aba Automação)…")
                stop, day_chg = _sleep_until(
                    time.time() + 30, should_stop=should_stop, day_marker=day_marker
                )
                if stop:
                    _log("Atualização contínua interrompida.")
                    return 0
                if day_chg:
                    continue
                continue

            now = time.time()
            due: list[str] = []
            for sid in ons:
                iv = sector_interval_sec(cfg, sid)
                prev = last_run.get(sid)
                if prev is None or (now - prev) >= iv:
                    due.append(sid)

            if not due:
                waits = []
                for sid in ons:
                    iv = sector_interval_sec(cfg, sid)
                    prev = last_run.get(sid, now)
                    waits.append(max(1.0, iv - (now - prev)))
                wait_s = min(waits) if waits else 30.0
                _log(f"Aguardando {format_duration_long(int(wait_s))} até o próximo setor…")
                stop, day_chg = _sleep_until(
                    time.time() + wait_s, should_stop=should_stop, day_marker=day_marker
                )
                if stop:
                    _log("Atualização contínua interrompida.")
                    return 0
                continue

            if _want_stop(should_stop):
                _log("Atualização contínua interrompida.")
                return 0

            ciclo += 1
            ini50, fim50 = periodo_50_coleta_hoje(today)
            ini103, fim103 = periodo_103_hoje(today)
            _log(
                f"=== CICLO {ciclo} | setores={','.join(due)} | "
                f"50={format_period(ini50, fim50)} | 103={format_period(ini103, fim103)} ==="
            )
            t0 = time.time()

            def _status_guard(msg: str) -> None:
                if _want_stop(should_stop):
                    raise LoopStopped("parado pelo usuário")
                _log(msg)

            try:
                if getattr(cfg, "ciclo_paralelo", True) or len(due) == 1:
                    result: dict[str, Any] = run_parallel_cycle(
                        credentials=creds,
                        settings=cfg,
                        headless=use_headless,
                        on_status=_status_guard,
                        sync=True,
                        jobs=due,
                        should_stop=lambda: _want_stop(should_stop),
                    )
                else:
                    result = {"errors": {}, "ok": True}
                    for job in due:
                        if _want_stop(should_stop):
                            raise LoopStopped("parado pelo usuário")
                        part = run_parallel_cycle(
                            credentials=creds,
                            settings=cfg,
                            headless=use_headless,
                            on_status=_status_guard,
                            sync=True,
                            jobs=[job],
                            should_stop=lambda: _want_stop(should_stop),
                        )
                        errs = part.get("errors") or {}
                        if errs:
                            result["errors"].update(errs)
                            result["ok"] = False
                elapsed = time.time() - t0
                err = result.get("errors") or {}
                _log(
                    f"CICLO {ciclo} OK em {elapsed:.0f}s | "
                    f"jobs={due} | erros={err or '{}'}"
                )
            except LoopStopped:
                elapsed = time.time() - t0
                _log(f"CICLO {ciclo} interrompido em {elapsed:.0f}s (Parar).")
                return 0
            except Exception as err:  # noqa: BLE001
                if _want_stop(should_stop):
                    _log(f"CICLO {ciclo} interrompido (Parar).")
                    return 0
                elapsed = time.time() - t0
                _log(f"CICLO {ciclo} FALHOU em {elapsed:.0f}s: {err}")

            stamp = time.time()
            for sid in due:
                last_run[sid] = stamp

            if once:
                return 0
    finally:
        clear_loop_pid()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ACE loop automático por setor")
    parser.add_argument(
        "--interval",
        "-i",
        default=None,
        help="Override do intervalo fallback: 30s | 5m | 1h (padrao = config)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Mostra o navegador (mais lento). Padrao e headless/rapido.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Roda um unico ciclo (setores due) e sai",
    )
    args = parser.parse_args(argv)
    try:
        cfg = load_settings()
        sec = None
        if args.interval:
            sec = resolve_interval_sec(args.interval, settings_intervalo=cfg.loop_intervalo)
        return run_loop(
            interval_sec=sec,
            headless=False if args.headed else None,
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
