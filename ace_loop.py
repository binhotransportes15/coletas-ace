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
            from crt_bridge import append_log

            # Só espelha no log — NÃO publish(pct=0) (zera a barra principal)
            kind = classify_status_msg(msg)
            append_log(kind, msg, source="cmd")
        except Exception:
            pass
    except Exception:
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# Alias de tag no log → id do setor
_JOB_TAG_ALIAS: dict[str, str] = {
    "dist": "dist",
    "distribuicao": "dist",
    "distribuição": "dist",
    "50": "dist",
    "103": "dist",
    "36": "dist",
    "225": "dist",
    "78": "78",
    "078": "78",
    "armazem": "78",
    "armazém": "78",
    "31": "31",
    "031": "31",
    "pendencia": "31",
    "pendência": "31",
    "73": "73",
    "073": "73",
    "076": "73",
    "200": "73",
    "contratacao": "73",
    "contratação": "73",
    "455": "455",
    "emissao": "455",
    "emissão": "455",
}

# Degraus de progresso (só sobe). Keywords curtas/ambíguas causavam 100% cedo.
_PROGRESS_LADDER: tuple[tuple[float, tuple[str, ...]], ...] = (
    (5.0, ("iniciando", "ciclo paralelo", "cleanup", "limpeza downloads")),
    (12.0, ("efetuando login", "login no ssw", "credenc", "abrindo navegador", "sessão", "sessao")),
    (18.0, ("login concluido", "login concluído", "já conectada")),
    (28.0, ("download", "baixando", "relatório", "relatorio", "fila 156", "aguardando arquivo", "gerando", "abrindo opcao", "abrindo programa")),
    (45.0, ("analisando", "processando", "lendo", "parse", "conferent", "ofensor", "sla")),
    (58.0, ("cache", "dashboard", "json local", "modo local", "persist")),
    (70.0, ("sheets", "planilha", "apps script", "preparando envio")),
    (82.0, ("enviando", "lote", "atualizando aba", "batch")),
    (92.0, ("sheets:", "atualizada", "sem mudança", "pulou sync")),
    (100.0, ("bloco ok", "pipeline ok", "sync ok", "automação + sheets")),
)

_DONE_MARKERS = ("bloco ok", "pipeline ok", "sync ok", "automação + sheets")


def parse_job_tag(msg: str) -> tuple[str | None, str]:
    """Extrai [dist]/[78]/… do início da mensagem."""
    text = str(msg or "").strip()
    if text.startswith("[") and "]" in text:
        tag, rest = text[1:].split("]", 1)
        key = tag.strip().lower()
        sid = _JOB_TAG_ALIAS.get(key)
        if sid is None and "/" in key:
            sid = _JOB_TAG_ALIAS.get(key.split("/", 1)[0].strip())
        return sid, rest.strip()
    return None, text


def estimate_job_pct(prev: float, text: str) -> float:
    """Estima % do job (automação + Sheets) a partir da mensagem de status."""
    low = (text or "").lower()
    best = max(0.0, float(prev or 0.0))
    if any(x in low for x in ("falhou", "erro:", "traceback", "exception")):
        return best
    for pct, keys in _PROGRESS_LADDER:
        if any(k in low for k in keys):
            if pct > best:
                best = pct
    if best < 95 and low and best > 0:
        best = min(95.0, best + 0.25)
    if best >= 100.0 and not any(m in low for m in _DONE_MARKERS):
        best = min(95.0, best)
    return min(100.0, best)


def apply_status_to_progress(
    progress: dict[str, dict[str, Any]],
    msg: str,
    *,
    running: list[str] | None = None,
) -> None:
    """Atualiza o mapa live de progresso por setor com uma linha de status."""
    sid, body = parse_job_tag(msg)
    if sid:
        targets = [sid]
    elif running and len(running) == 1:
        targets = list(running)
    else:
        return
    if not targets:
        return
    low = (body or msg or "").lower()
    for tid in targets:
        if tid not in progress:
            continue
        cur = progress[tid]
        if cur.get("state") in {"off"}:
            continue
        detail = (body or msg or "")[:90]
        if any(m in low for m in _DONE_MARKERS):
            cur["pct"] = 100.0
            cur["state"] = "ok"
            cur["detail"] = detail or "concluído · Sheets ok"
            continue
        if "falhou" in low or "erro:" in low or low.startswith("erro "):
            cur["state"] = "err"
            cur["pct"] = max(float(cur.get("pct") or 0), 1.0)
            cur["detail"] = detail or "falhou"
            continue
        # Já concluiu neste ciclo: ignora mensagens posteriores (evita run@100 fantasma)
        if cur.get("state") == "ok":
            continue
        if "sheets" in low or "planilha" in low or "apps script" in low:
            cur["pct"] = max(float(cur.get("pct") or 0), 70.0)
        cur["state"] = "run"
        cur["pct"] = estimate_job_pct(float(cur.get("pct") or 0), detail)
        cur["detail"] = detail or "executando…"


def build_sector_rows(
    cfg: AceSettings,
    last_run: dict[str, float],
    *,
    progress: dict[str, dict[str, Any]] | None = None,
    running: list[str] | None = None,
    errors: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Barrinhas = % real da automação/Sheets (não countdown)."""
    running_set = set(running or [])
    errs = errors or {}
    live = progress or {}
    rows: list[dict[str, Any]] = []
    for sid, flag, _iv, label in SECTOR_SPECS:
        enabled = bool(getattr(cfg, flag, sid == "dist"))
        iv = sector_interval_sec(cfg, sid) if enabled else 0
        row: dict[str, Any] = {
            "id": sid,
            "label": label,
            "enabled": enabled,
            "interval": format_duration(iv) if enabled else "",
            "state": "off",
            "pct": 0.0,
            "detail": "fora do automático",
        }
        if not enabled:
            rows.append(row)
            continue

        live_row = live.get(sid) or {}
        live_state = str(live_row.get("state") or "")
        if sid in errs or live_state == "err":
            row["state"] = "err"
            row["pct"] = float(live_row.get("pct") or 0.0)
            row["detail"] = str(errs.get(sid) or live_row.get("detail") or "erro")[:90]
        elif live_state == "ok":
            # Preferir ok sobre running_set (job já terminou no paralelo)
            row["state"] = "ok"
            row["pct"] = 100.0 if sid in running_set else 0.0
            rem_hint = ""
            prev = last_run.get(sid)
            if prev is not None and iv > 0 and sid not in running_set:
                rem = max(0.0, float(iv) - (time.time() - prev))
                if rem > 1:
                    rem_hint = f" · próximo em {format_duration_long(int(rem))}"
            row["detail"] = str(
                live_row.get("detail")
                or ("concluído" if sid in running_set else f"último ciclo OK{rem_hint}")
            )[:90]
        elif sid in running_set or live_state == "run":
            row["state"] = "run"
            pct = float(live_row.get("pct") or 0.0)
            if pct <= 0:
                pct = 3.0
            row["pct"] = min(95.0, pct) if pct < 100 else 100.0
            if live_state != "ok" and pct >= 100:
                row["pct"] = 95.0
            row["detail"] = str(live_row.get("detail") or "executando…")[:90]
        elif sid in last_run:
            row["state"] = "ok"
            row["pct"] = 0.0
            prev = last_run[sid]
            rem = max(0.0, float(iv) - (time.time() - prev))
            row["detail"] = (
                f"último OK · próximo em {format_duration_long(int(rem))}"
                if rem > 1
                else "último OK · na fila"
            )
        else:
            row["state"] = "due"
            row["pct"] = 0.0
            row["detail"] = "na fila · aguardando 1º ciclo"
        rows.append(row)
    return rows


def publish_sector_bars(
    cfg: AceSettings,
    last_run: dict[str, float],
    *,
    progress: dict[str, dict[str, Any]] | None = None,
    running: list[str] | None = None,
    errors: dict[str, Any] | None = None,
    label: str = "LOOP",
    mode: str = "RUN",
) -> None:
    try:
        from crt_bridge import publish_sectors

        rows = build_sector_rows(
            cfg,
            last_run,
            progress=progress,
            running=running,
            errors=errors,
        )
        ons = [r for r in rows if r.get("enabled")]
        running_rows = [r for r in ons if r.get("state") == "run"]
        if running_rows:
            pct = sum(float(r.get("pct") or 0) for r in running_rows) / len(running_rows)
            detail = " · ".join(
                f"{r['label'][:4]} {float(r.get('pct') or 0):.0f}%" for r in running_rows[:4]
            )
        elif ons:
            pct = sum(float(r.get("pct") or 0) for r in ons) / max(1, len(ons))
            detail = " · ".join(
                f"{r['label'][:4]} {float(r.get('pct') or 0):.0f}%" for r in ons[:4]
            )
        else:
            pct = 0.0
            detail = "nenhum setor no automático"
        publish_sectors(
            rows,
            online=True,
            label=label,
            pct=pct,
            detail=detail[:100],
            mode=mode,
        )
    except Exception:
        pass


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
    on_tick: Callable[[], None] | None = None,
) -> tuple[bool, bool]:
    """Espera até `until`. Retorna (stop, day_changed)."""
    last_tick = 0.0
    while time.time() < until:
        if stop_requested() or (should_stop and should_stop()):
            return True, False
        if date.today() != day_marker:
            return False, True
        now = time.time()
        if on_tick and (now - last_tick) >= 0.8:
            last_tick = now
            try:
                on_tick()
            except Exception:
                pass
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
    # progresso live por setor: {sid: {pct, detail, state}}
    progress: dict[str, dict[str, Any]] = {}

    def _tick_bars(running: list[str] | None = None, errors: dict | None = None) -> None:
        publish_sector_bars(
            cfg,
            last_run,
            progress=progress,
            running=running,
            errors=errors,
            label="LOOP" if running else "WAIT",
            mode="RUN" if running else "STANDBY",
        )

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
                _tick_bars()
                stop, day_chg = _sleep_until(
                    time.time() + 30,
                    should_stop=should_stop,
                    day_marker=day_marker,
                    on_tick=_tick_bars,
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
                _tick_bars()
                stop, day_chg = _sleep_until(
                    time.time() + wait_s,
                    should_stop=should_stop,
                    day_marker=day_marker,
                    on_tick=_tick_bars,
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
            for sid in due:
                progress[sid] = {
                    "pct": 3.0,
                    "detail": f"ciclo {ciclo} · iniciando…",
                    "state": "run",
                }
            _tick_bars(running=due)

            def _status_guard(msg: str) -> None:
                if _want_stop(should_stop):
                    raise LoopStopped("parado pelo usuário")
                apply_status_to_progress(progress, msg, running=due)
                # Só jobs ainda em "run" (os que já deram bloco OK saem da lista)
                active = [
                    s
                    for s in due
                    if str((progress.get(s) or {}).get("state") or "") == "run"
                ]
                _tick_bars(running=active)
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
                        progress[job] = {
                            "pct": 3.0,
                            "detail": f"setor {job} · iniciando…",
                            "state": "run",
                        }
                        _tick_bars(running=[job])
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
                        else:
                            progress[job] = {
                                "pct": 100.0,
                                "detail": "concluído · automação + Sheets",
                                "state": "ok",
                            }
                elapsed = time.time() - t0
                err = result.get("errors") or {}
                _log(
                    f"CICLO {ciclo} OK em {elapsed:.0f}s | "
                    f"jobs={due} | erros={err or '{}'}"
                )
                stamp = time.time()
                for sid in due:
                    last_run[sid] = stamp
                    if sid in err:
                        progress[sid] = {
                            "pct": float((progress.get(sid) or {}).get("pct") or 1.0),
                            "detail": str(err.get(sid) or "erro")[:90],
                            "state": "err",
                        }
                    else:
                        progress[sid] = {
                            "pct": 100.0,
                            "detail": "concluído · automação + Sheets",
                            "state": "ok",
                        }
                _tick_bars(errors=err if err else None)
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
                    progress[sid] = {
                        "pct": float((progress.get(sid) or {}).get("pct") or 1.0),
                        "detail": str(err)[:90],
                        "state": "err",
                    }
                _tick_bars(errors={sid: str(err) for sid in due})

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
