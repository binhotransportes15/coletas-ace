from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, LOG_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import format_period, normalize_date, periodo_103_hoje, periodo_36_ontem_hoje, periodo_50_coleta_hoje, periodo_mes_corrente, sugestao_periodo, titulo_agendamento_mes
from parser_ssw0157 import analyze_report
from publish_dashboard import publish_dashboard
from sheets_sync import sync_google_sheets, sync_google_sheets_103, sync_google_sheets_36, sync_google_sheets_225
from ssw_client import (
    cleanup_downloads,
    download_ace_103,
    download_ace_36,
    download_ace_225,
    download_ace_reports,
    download_ace_shared_cycle,
)
from parser_ssw103 import analyze_report_103
from parser_ssw0146 import analyze_report_36
from parser_ssw225 import analyze_report_225

StatusCallback = Callable[[str], None]

_LOG_LOCK = threading.Lock()
_STATUS_LOCK = threading.Lock()


def _noop(_: str) -> None:
    return None


def _persist_local_instead_of_sheets(
    sector: str,
    *,
    cfg: AceSettings,
    on_status: StatusCallback,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Grava JSON interno + dashboard local (sem Sheets/GitHub)."""
    from local_store import persist_sector

    snap = persist_sector(sector, extra=extra, on_status=on_status)
    dash = publish_dashboard(cfg, on_status=on_status, allow_push=False)
    return {"ok": True, "via": "local_json", "local": snap, "dashboard": dash}


def _should_use_local_store(cfg: AceSettings) -> bool:
    return bool(getattr(cfg, "modo_local", False))


def _sync_after_report(
    label: str,
    *,
    cfg: AceSettings,
    on_status: StatusCallback,
    sync_fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Sheets ou JSON local conforme modo_local."""
    if _should_use_local_store(cfg):
        on_status(f"[{label}] Modo local: salvando JSON (sem planilha)…")
        return _persist_local_instead_of_sheets(label, cfg=cfg, on_status=on_status, extra={"report": label})
    return sync_fn()


def _log_file(message: str) -> None:
    ensure_dirs()
    path = LOG_DIR / f"ace_{datetime.now():%Y%m%d}.log"
    with _LOG_LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%H:%M:%S}] {message}\n")


def find_latest_report(download_dir: Path | None = None) -> Path | None:
    """Somente arquivos do 50 (0157) — nunca aceita 36/103/225 por engano."""
    folder = Path(download_dir or DOWNLOAD_DIR)
    if not folder.exists():
        return None
    candidates: list[Path] = []
    for pattern in ("coleta_50*", "*ssw0157*", "*0157*.sswweb"):
        candidates.extend(folder.glob(pattern))
    files = sorted(
        {p.resolve() for p in candidates if p.is_file()},
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files:
        name = p.name.lower()
        if any(
            bad in name
            for bad in ("0146", "0166", "2862", "coleta_103", "entrega_36", "agendamento_225")
        ):
            continue
        if name.startswith("coleta_50") or "ssw0157" in name or "0157" in name:
            return p
    return None


def run_analysis_only(
    report_path: Path | str,
    *,
    settings: AceSettings | None = None,
    on_status: StatusCallback | None = None,
    sync: bool = True,
) -> dict[str, Any]:
    status = on_status or _noop
    cfg = settings or load_settings()
    path = Path(report_path)
    status(f"Analisando relatorio: {path.name}")
    # Sempre substitui cache/planilha pelo periodo do relatorio (nao acumula)
    meta = analyze_report(path, merge=False)
    totais = meta.get("totais_situacao") or {}
    status(
        f"Analise: {meta.get('lote_atual')} coleta(s) pelo cabecalho SPO | "
        f"SITUACAO ATUAL → "
        f"COL {totais.get('coletada', 0)} / "
        f"COM {totais.get('comandada', 0)} / "
        f"CAD {totais.get('cadastrada', 0)} / "
        f"CAN {totais.get('cancelada', 0)} | "
        f"historico {meta.get('historico')} evento(s) (nao entra na soma)"
    )
    result: dict[str, Any] = {"analysis": meta, "report": str(path)}

    if sync:
        if _should_use_local_store(cfg):
            result["sheets"] = {"ok": False, "skipped": True, "reason": "modo_local"}
            result["local"] = _persist_local_instead_of_sheets(
                "distribuicao", cfg=cfg, on_status=status, extra={"report": "50"}
            )
            result["dashboard"] = (result["local"] or {}).get("dashboard") or {}
        else:
            result["sheets"] = sync_google_sheets(cfg, on_status=status)
            result["dashboard"] = publish_dashboard(cfg, on_status=status)
    return result


def run_full_pipeline(
    *,
    modo: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    keep_open: bool = False,
    headless: bool = False,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """
    Baixa opcao 50 → analisa → sheets → dashboard.
    Periodo: modo diario/sexta, ou datas explicitas.
    """
    status = on_status or _noop

    def emit(msg: str) -> None:
        status(msg)
        _log_file(msg)

    cfg = settings or load_settings()
    creds = credentials or load_credentials()
    modo_eff = (modo or cfg.periodo_modo or "diario").strip().lower()

    if start_date and end_date:
        ini, fim = normalize_date(start_date), normalize_date(end_date)
    elif modo_eff in {"sexta", "friday", "sex"}:
        ini, fim = sugestao_periodo("sexta")
    else:
        ini, fim = periodo_50_coleta_hoje()

    emit(f"Pipeline ACE | modo={modo_eff} | periodo de coleta {format_period(ini, fim)}")

    download = download_ace_reports(
        ini,
        fim,
        keep_open=keep_open,
        on_status=emit,
        credentials=creds,
        settings=cfg,
        headless=headless,
    )
    report = Path((download.get("paths") or {}).get("coleta") or "")
    if not report.exists():
        latest = find_latest_report()
        if latest:
            report = latest
            emit(f"Usando ultimo relatorio encontrado: {report.name}")
        else:
            raise RuntimeError("Download da coleta nao gerou arquivo .sswweb")

    analysis = run_analysis_only(report, settings=cfg, on_status=emit, sync=True)
    return {
        "download": download,
        **analysis,
        "period": format_period(ini, fim),
        "modo": modo_eff,
    }


def find_latest_103(download_dir: Path | None = None) -> Path | None:
    """Somente arquivos do 103 (0166) — nunca *.xlsx genérico da pasta Downloads."""
    folder = Path(download_dir or DOWNLOAD_DIR)
    candidates: list[Path] = []
    search_dirs = [folder]
    downloads_user = Path.home() / "Downloads"
    if downloads_user.exists() and downloads_user.resolve() != folder.resolve():
        search_dirs.append(downloads_user)

    patterns = (
        "coleta_103*",
        "CSV*ssw0166*",
        "*ssw0166*",
        "*0166*.sswweb",
        "*0166*.xlsx",
        "*0166*.xls",
    )
    for directory in search_dirs:
        if not directory.exists():
            continue
        for pattern in patterns:
            candidates.extend(directory.glob(pattern))

    def score(path: Path) -> tuple:
        name = path.name.lower()
        is_103 = (
            "ssw0166" in name
            or "0166" in name
            or name.startswith("coleta_103")
            or name.startswith("csvssw0166")
        )
        return (1 if is_103 else 0, path.stat().st_mtime)

    files = sorted({p.resolve() for p in candidates if p.is_file()}, key=score, reverse=True)
    for p in files:
        name = p.name.lower()
        if any(
            bad in name
            for bad in ("0146", "0157", "2862", "coleta_50", "entrega_36", "agendamento")
        ):
            continue
        if "0166" in name or name.startswith("coleta_103"):
            return p
    return None


def run_analysis_103(
    report_path: Path | str,
    *,
    periodo: str = "",
    settings: AceSettings | None = None,
    on_status: StatusCallback | None = None,
    sync: bool = True,
) -> dict[str, Any]:
    status = on_status or _noop
    cfg = settings or load_settings()
    path = Path(report_path)
    status(f"Analisando 103: {path.name}")
    meta = analyze_report_103(path, periodo=periodo)
    tot = meta.get("totais") or {}
    status(
        f"103: {meta.get('lote')} coleta(s) | "
        f"parado={tot.get('parado', 0)} em_rota={tot.get('em_rota', 0)} "
        f"realizada={tot.get('realizada', 0)} cancelada={tot.get('cancelada', 0)}"
    )
    sheets = {"ok": False, "skipped": True}
    dash = {"ok": False, "skipped": True}
    if sync:
        if _should_use_local_store(cfg):
            local = _persist_local_instead_of_sheets(
                "103", cfg=cfg, on_status=status, extra={"report": "103"}
            )
            sheets = {"ok": False, "skipped": True, "reason": "modo_local"}
            dash = (local or {}).get("dashboard") or {}
            return {
                "analysis": meta,
                "report": str(path),
                "sheets": sheets,
                "dashboard": dash,
                "local": local,
            }
        sheets = sync_google_sheets_103(cfg, on_status=status)
        dash = publish_dashboard(cfg, on_status=status)
    return {
        "analysis": meta,
        "report": str(path),
        "sheets": sheets,
        "dashboard": dash,
    }


def run_full_pipeline_103(
    *,
    modo: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    keep_open: bool = False,
    headless: bool = False,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Baixa opcao 103 (Excel / data limite L) → analisa torres tempo real."""
    status = on_status or _noop

    def emit(msg: str) -> None:
        status(msg)
        _log_file(msg)

    cfg = settings or load_settings()
    creds = credentials or load_credentials()
    if start_date and end_date:
        ini, fim = normalize_date(start_date), normalize_date(end_date)
    else:
        ini, fim = periodo_103_hoje()

    emit(f"Pipeline ACE 103 | data LIMITE HOJE {format_period(ini, fim)}")
    download = download_ace_103(
        ini,
        fim,
        keep_open=keep_open,
        on_status=emit,
        credentials=creds,
        settings=cfg,
        headless=headless,
    )
    report = Path((download.get("paths") or {}).get("coleta_103") or "")
    if not report.exists():
        latest = find_latest_103()
        if latest:
            report = latest
            emit(f"Usando ultimo Excel 103: {report.name}")
        else:
            raise RuntimeError("Download 103 nao gerou Excel")

    analysis = run_analysis_103(
        report,
        periodo=format_period(ini, fim),
        settings=cfg,
        on_status=emit,
        sync=True,
    )
    return {
        "download": download,
        **analysis,
        "period": format_period(ini, fim),
        "modo": "hoje",
    }


def find_latest_225(download_dir: Path | None = None) -> Path | None:
    """Somente arquivos do 225 — nunca aceita 36/0146 por engano."""
    folder = Path(download_dir or DOWNLOAD_DIR)
    if not folder.exists():
        return None
    candidates: list[Path] = []
    for pattern in (
        "agendamento_225*",
        "*ssw2862*",
        "*2862*.sswweb",
        "*agendamento*",
    ):
        candidates.extend(folder.glob(pattern))
    files = sorted(
        {p.resolve() for p in candidates if p.is_file()},
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files:
        name = p.name.lower()
        # exclui claramente 36 / 50 / 103
        if any(bad in name for bad in ("0146", "0157", "0166", "coleta_50", "coleta_103", "entrega_36")):
            continue
        try:
            head = p.read_text(encoding="latin-1", errors="replace")[:800].upper()
        except OSError:
            continue
        if "ROMANEIO" in head and "AGEND" not in head:
            continue
        if "AGEND PARA" in head or "AGENDADO PARA" in head:
            return p
        if "AGEND" in head and "CTRC" in head and "OCORRENCIA" in head:
            return p
        if name.startswith("agendamento_225"):
            return p
    return None


def run_analysis_225(
    report_path: Path | str,
    *,
    periodo: str = "",
    settings: AceSettings | None = None,
    on_status: StatusCallback | None = None,
    sync: bool = True,
) -> dict[str, Any]:
    status = on_status or _noop
    cfg = settings or load_settings()
    path = Path(report_path)
    status(f"Analisando 225: {path.name}")
    meta = analyze_report_225(path, periodo=periodo)
    status(
        f"225: {meta.get('total')} CTRC(s) | "
        f"ROTA {meta.get('em_rota', 0)} / PARADO {meta.get('parado', 0)} / "
        f"CONCLUIDO {meta.get('concluido', 0)} | ALERTA {meta.get('alerta', 0)}"
    )
    result: dict[str, Any] = {"analysis": meta, "report": str(path)}
    if sync:
        if _should_use_local_store(cfg):
            result["sheets"] = {"ok": False, "skipped": True, "reason": "modo_local"}
            result["local"] = _persist_local_instead_of_sheets(
                "225", cfg=cfg, on_status=status, extra={"report": "225"}
            )
            result["dashboard"] = (result["local"] or {}).get("dashboard") or {}
        else:
            result["sheets"] = sync_google_sheets_225(cfg, on_status=status)
            result["dashboard"] = publish_dashboard(cfg, on_status=status)
    return result


def find_latest_36(download_dir: Path | None = None) -> Path | None:
    folder = Path(download_dir or DOWNLOAD_DIR)
    if not folder.exists():
        return None
    candidates: list[Path] = []
    for pattern in ("entrega_36*", "CSV*ssw0146*", "*ssw0146*", "coleta_36*"):
        candidates.extend(folder.glob(pattern))
    files = sorted(
        {p.resolve() for p in candidates if p.is_file()},
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def run_analysis_36(
    report_path: Path | str,
    *,
    periodo: str = "",
    settings: AceSettings | None = None,
    on_status: StatusCallback | None = None,
    sync: bool = True,
) -> dict[str, Any]:
    status = on_status or _noop
    cfg = settings or load_settings()
    path = Path(report_path)
    status(f"Analisando 36: {path.name}")
    meta = analyze_report_36(path, periodo=periodo)
    tot = meta.get("totais") or {}
    status(
        f"36: {meta.get('lote')} CTRC(s) | "
        f"REAL {tot.get('realizada', 0)} / "
        f"ROTA {tot.get('em_rota', 0)} / "
        f"PEND {tot.get('pendencia', 0)} | "
        f"excluidos ontem {meta.get('excluido', 0)}"
    )
    result: dict[str, Any] = {"analysis": meta, "report": str(path)}
    if sync:
        if _should_use_local_store(cfg):
            result["sheets"] = {"ok": False, "skipped": True, "reason": "modo_local"}
            result["local"] = _persist_local_instead_of_sheets(
                "36", cfg=cfg, on_status=status, extra={"report": "36"}
            )
            result["dashboard"] = (result["local"] or {}).get("dashboard") or {}
        else:
            result["sheets"] = sync_google_sheets_36(cfg, on_status=status)
            result["dashboard"] = publish_dashboard(cfg, on_status=status)
    return result


def run_full_pipeline_36(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    keep_open: bool = False,
    headless: bool = False,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    status = on_status or _noop

    def emit(msg: str) -> None:
        status(msg)
        _log_file(msg)

    cfg = settings or load_settings()
    creds = credentials or load_credentials()
    ini, fim = periodo_36_ontem_hoje()
    emit(f"Pipeline ACE 36 | periodo {format_period(ini, fim)} (seg=sex..hoje / demais=D-1..hoje)")

    download = download_ace_36(
        ini,
        fim,
        keep_open=keep_open,
        headless=headless,
        on_status=emit,
        credentials=creds,
        settings=cfg,
    )
    report = Path((download.get("paths") or {}).get("entrega_36") or "")
    if not report.exists():
        latest = find_latest_36()
        if latest:
            report = latest
            emit(f"Usando ultimo relatorio 36: {report.name}")
        else:
            raise RuntimeError("Download da entrega 36 nao gerou arquivo")

    analysis = run_analysis_36(
        report,
        periodo=format_period(ini, fim),
        settings=cfg,
        on_status=emit,
        sync=True,
    )
    return {
        "download": download,
        **analysis,
        "period": format_period(ini, fim),
        "modo": "ontem_hoje",
    }


def run_full_pipeline_225(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    keep_open: bool = False,
    headless: bool = False,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    status = on_status or _noop

    def emit(msg: str) -> None:
        status(msg)
        _log_file(msg)

    cfg = settings or load_settings()
    creds = credentials or load_credentials()
    ini, fim = periodo_mes_corrente()
    titulo = titulo_agendamento_mes()
    emit(f"Pipeline ACE 225 | {titulo} | mes {format_period(ini, fim)} | arquivo R")

    download = download_ace_225(
        ini,
        fim,
        keep_open=keep_open,
        headless=headless,
        on_status=emit,
        credentials=creds,
        settings=cfg,
    )
    report = Path((download.get("paths") or {}).get("agendamento_225") or "")
    if not report.exists():
        latest = find_latest_225()
        if latest:
            report = latest
            emit(f"Usando ultimo relatorio 225: {report.name}")
        else:
            raise RuntimeError("Download do agendamento 225 nao gerou arquivo")

    analysis = run_analysis_225(
        report,
        periodo=titulo,
        settings=cfg,
        on_status=emit,
        sync=True,
    )
    return {
        "download": download,
        **analysis,
        "period": format_period(ini, fim),
        "titulo": titulo,
        "modo": "mes_corrente",
    }


def run_dual_cycle(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    sync: bool = True,
    run_extras: bool = True,
    skip_cleanup: bool = False,
) -> dict[str, Any]:
    """
    Baixa 50 + 103 (+ 36 se entrega_option=36) + 225 EM CICLO automatico.

    Um unico login SSW (sessao compartilhada) — mais rapido que logar por relatorio.
    Assim que cada arquivo baixa: analisa e envia ao Sheets na hora (não espera o fim).

    Com ciclo_paralelo=True (padrao), 078 / 031 / 073 rodam ao mesmo tempo que a
    distribuicao (browsers separados). Use run_extras=False para so a dist.

    Periodos automaticos (recalculados a cada ciclo / virada de dia):
      50  → periodo de COLETA = HOJE
      103 → data LIMITE HOJE (Por data de = L)
      36  → seg: SEXTA..HOJE | demais: D-1..HOJE
      225 → sempre mes corrente (dia 1 → ultimo dia), arquivo R
    """
    status = on_status or _noop

    def emit(msg: str) -> None:
        status(msg)
        _log_file(msg)

    cfg = settings or load_settings()
    creds = credentials or load_credentials()
    use_headless = cfg.headless if headless is None else bool(headless)

    wants_extras = bool(
        getattr(cfg, "armazem_in_loop", False)
        or getattr(cfg, "pendencia_in_loop", False)
        or getattr(cfg, "contratacao_in_loop", False)
        or getattr(cfg, "emissao_in_loop", False)
    )
    if (
        run_extras
        and wants_extras
        and getattr(cfg, "ciclo_paralelo", True)
    ):
        return run_parallel_cycle(
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=on_status,
            sync=sync,
        )

    ini50, fim50 = periodo_50_coleta_hoje()
    ini103, fim103 = periodo_103_hoje()
    ini36, fim36 = periodo_36_ontem_hoje()
    ini225, fim225 = periodo_mes_corrente()
    titulo225 = titulo_agendamento_mes()
    run_36 = str(getattr(cfg, "entrega_option", "") or "").strip() == "36"
    viz = "oculto" if use_headless else "visivel"
    emit(
        f"CICLO dual | login 1x · telas na mesma sessão | viz={viz} | "
        f"50 coleta={format_period(ini50, fim50)} "
        f"| 103 limite={format_period(ini103, fim103)}"
        + (
            f" | 36 periodo={format_period(ini36, fim36)}"
            if run_36
            else ""
        )
        + f" | 225 {titulo225} mes={format_period(ini225, fim225)}"
    )

    if not skip_cleanup:
        cleanup_downloads(DOWNLOAD_DIR, on_status=emit)

    result_50: dict[str, Any] = {}
    result_103: dict[str, Any] = {}
    result_36: dict[str, Any] = {}
    result_225: dict[str, Any] = {}
    sheets50: dict[str, Any] = {"ok": False, "skipped": True}
    sheets103: dict[str, Any] = {"ok": False, "skipped": True}
    sheets36: dict[str, Any] = {"ok": False, "skipped": True}
    sheets225: dict[str, Any] = {"ok": False, "skipped": True}
    errors: dict[str, str] = {}
    done: set[str] = set()

    def _analyze_and_push(
        label: str,
        report: Path,
        *,
        fresh: bool,
        path_key: str,
    ) -> None:
        """Analisa 1 relatório e, se sync=True, manda ao Sheets na hora."""
        nonlocal result_50, result_103, result_36, result_225
        nonlocal sheets50, sheets103, sheets36, sheets225
        if label in done:
            return
        if label == "50":
            if not fresh:
                emit(f"[50] Usando ultimo: {report.name}")
            analysis = run_analysis_only(
                report, settings=cfg, on_status=lambda m: emit(f"[50] {m}"), sync=False
            )
            lote = int((analysis.get("analysis") or analysis).get("lote_atual") or 0)
            if not fresh and lote <= 0:
                raise RuntimeError(
                    f"50 cache invalido ({report.name}) com 0 coleta(s) — planilha nao sera sobrescrita"
                )
            result_50 = {
                "download": {"paths": {"coleta": str(report)}},
                **analysis,
                "period": format_period(ini50, fim50),
                "fresh": fresh,
            }
            emit("50 concluido.")
            if sync:
                sheets50 = _sync_after_report(
                    "50",
                    cfg=cfg,
                    on_status=emit,
                    sync_fn=lambda: sync_google_sheets(cfg, on_status=emit),
                )
                result_50["sheets"] = sheets50
            done.add("50")
            return

        if label == "103":
            if not fresh:
                emit(f"[103] Usando ultimo: {report.name}")
            analysis = run_analysis_103(
                report,
                periodo=format_period(ini103, fim103),
                settings=cfg,
                on_status=lambda m: emit(f"[103] {m}"),
                sync=False,
            )
            lote = int((analysis.get("analysis") or {}).get("lote") or analysis.get("lote") or 0)
            if not fresh and lote <= 0:
                raise RuntimeError(
                    f"103 cache invalido ({report.name}) com 0 coleta(s) — planilha nao sera sobrescrita"
                )
            result_103 = {
                "download": {"paths": {"coleta_103": str(report)}},
                **analysis,
                "period": format_period(ini103, fim103),
                "fresh": fresh,
            }
            emit("103 concluido.")
            if sync:
                sheets103 = _sync_after_report(
                    "103",
                    cfg=cfg,
                    on_status=emit,
                    sync_fn=lambda: sync_google_sheets_103(cfg, on_status=emit),
                )
                result_103["sheets"] = sheets103
            done.add("103")
            return

        if label == "36":
            if not fresh:
                emit(f"[36] Usando ultimo: {report.name}")
            analysis = run_analysis_36(
                report,
                periodo=format_period(ini36, fim36),
                settings=cfg,
                on_status=lambda m: emit(f"[36] {m}"),
                sync=False,
            )
            result_36 = {
                "download": {"paths": {"entrega_36": str(report)}},
                **analysis,
                "period": format_period(ini36, fim36),
            }
            emit("36 concluido.")
            if sync:
                sheets36 = _sync_after_report(
                    "36",
                    cfg=cfg,
                    on_status=emit,
                    sync_fn=lambda: sync_google_sheets_36(cfg, on_status=emit),
                )
                result_36["sheets"] = sheets36
            done.add("36")
            return

        if label == "225":
            if not fresh:
                emit(f"[225] Usando ultimo: {report.name}")
            analysis = run_analysis_225(
                report,
                periodo=titulo225,
                settings=cfg,
                on_status=lambda m: emit(f"[225] {m}"),
                sync=False,
            )
            total_225 = int((analysis.get("total") or analysis.get("totais", {}).get("total") or 0))
            if not fresh and total_225 <= 0:
                raise RuntimeError(
                    f"225 cache invalido ({report.name}) com 0 CTRC — Sheets 225 nao sera sobrescrito"
                )
            result_225 = {
                "download": {"paths": {"agendamento_225": str(report)}},
                **analysis,
                "period": format_period(ini225, fim225),
                "titulo": titulo225,
            }
            emit("225 concluido.")
            if sync:
                sheets225 = _sync_after_report(
                    "225",
                    cfg=cfg,
                    on_status=emit,
                    sync_fn=lambda: sync_google_sheets_225(cfg, on_status=emit),
                )
                result_225["sheets"] = sheets225
            done.add("225")
            return

    def _on_report_ready(label: str, path_key: str, path_str: str) -> None:
        """Chamado pelo SSW assim que cada arquivo termina o download."""
        nonlocal result_50, result_103, result_225
        try:
            report = Path(path_str)
            if not report.is_file():
                return
            emit(f"[{label}] baixou — analisando e enviando Sheets…")
            _analyze_and_push(label, report, fresh=True, path_key=path_key)
        except Exception as err:  # noqa: BLE001
            errors[label] = str(err)
            emit(f"[{label}] FALHOU (pos-download): {err}")
            if label == "50":
                result_50 = {}
            elif label == "103":
                result_103 = {}
            elif label == "225":
                result_225 = {}

    download_bundle: dict[str, Any] = {"paths": {}, "errors": {}}
    try:
        download_bundle = download_ace_shared_cycle(
            period_50=(ini50, fim50),
            period_103=(ini103, fim103),
            period_225=(ini225, fim225),
            period_36=(ini36, fim36) if run_36 else None,
            run_36=run_36,
            headless=use_headless,
            on_status=emit,
            credentials=creds,
            settings=cfg,
            clean_downloads=False,
            on_report_ready=_on_report_ready if sync else None,
        )
        errors.update(download_bundle.get("errors") or {})
    except Exception as err:  # noqa: BLE001
        errors["ssw"] = str(err)
        emit(f"Sessao SSW FALHOU: {err}")

    paths = (download_bundle.get("paths") or {}) if download_bundle else {}
    sessao_ok = "ssw" not in errors

    def _resolve_report(path_key: str, finder) -> Path | None:
        report = Path(paths.get(path_key) or "")
        if report.is_file():
            return report
        latest = finder()
        if latest and Path(latest).is_file():
            return Path(latest)
        return None

    # Fallback: o que não foi processado no callback (sync off, falha parcial, cache)
    if "50" not in done and (sessao_ok or paths.get("coleta")):
        try:
            fresh = Path(paths.get("coleta") or "")
            report = fresh if fresh.is_file() else find_latest_report()
            if report is None:
                raise RuntimeError("50 sem arquivo")
            _analyze_and_push("50", report, fresh=fresh.is_file(), path_key="coleta")
        except Exception as err:  # noqa: BLE001
            errors["50"] = str(err)
            emit(f"50 FALHOU: {err}")
            result_50 = {}

    if "103" not in done and (sessao_ok or paths.get("coleta_103")):
        try:
            fresh = Path(paths.get("coleta_103") or "")
            report = fresh if fresh.is_file() else find_latest_103()
            if report is None:
                raise RuntimeError("103 sem arquivo")
            _analyze_and_push("103", report, fresh=fresh.is_file(), path_key="coleta_103")
        except Exception as err:  # noqa: BLE001
            errors["103"] = str(err)
            emit(f"103 FALHOU: {err}")
            result_103 = {}

    if run_36 and "36" not in done and (sessao_ok or paths.get("entrega_36")):
        try:
            report = _resolve_report("entrega_36", find_latest_36)
            if report is None:
                raise RuntimeError("36 sem arquivo")
            fresh = str(report) == str(paths.get("entrega_36") or "")
            _analyze_and_push("36", report, fresh=fresh, path_key="entrega_36")
        except Exception as err:  # noqa: BLE001
            errors["36"] = str(err)
            emit(f"36 FALHOU: {err}")

    if "225" not in done and (paths.get("agendamento_225") or sessao_ok or errors.get("225")):
        try:
            downloaded = Path(paths.get("agendamento_225") or "")
            report: Path | None = downloaded if downloaded.is_file() else None
            if report is None and (errors.get("225") or not paths.get("agendamento_225")):
                emit("[225] Baixando de novo em sessão dedicada…")
                try:
                    ded = download_ace_225(
                        ini225,
                        fim225,
                        headless=use_headless,
                        on_status=lambda m: emit(f"[225] {m}"),
                        credentials=creds,
                        settings=cfg,
                        clean_downloads=False,
                    )
                    candid = Path((ded.get("paths") or {}).get("agendamento_225") or "")
                    if candid.is_file():
                        downloaded = candid
                        report = candid
                        paths["agendamento_225"] = str(candid)
                        errors.pop("225", None)
                except Exception as retry_err:  # noqa: BLE001
                    emit(f"[225] sessão dedicada falhou: {retry_err}")
            if report is None:
                report = find_latest_225()
            if report is None:
                raise RuntimeError("225 sem arquivo (download falhou e nao ha cache 225 valido)")
            fresh = downloaded.is_file()
            _analyze_and_push("225", report, fresh=fresh, path_key="agendamento_225")
        except Exception as err:  # noqa: BLE001
            errors["225"] = str(err)
            emit(f"225 FALHOU: {err}")
            result_225 = {}

    dash = {"ok": False, "skipped": True}
    if sync and (result_50 or result_103 or result_36 or result_225):
        dash = publish_dashboard(cfg, on_status=emit)

    keep: list[Path] = []
    for block in (result_50, result_103, result_36, result_225):
        blk_paths = (block.get("download") or {}).get("paths") or {}
        for key in ("coleta", "coleta_103", "entrega_36", "agendamento_225"):
            p = Path(blk_paths.get(key) or "")
            if p.exists():
                keep.append(p)
        report = Path(block.get("report") or "")
        if report.exists():
            keep.append(report)
    cleanup_downloads(DOWNLOAD_DIR, keep=keep, on_status=emit)

    result_78: dict[str, Any] = {}
    result_31: dict[str, Any] = {}
    result_73: dict[str, Any] = {}
    result_455: dict[str, Any] = {}
    result_reciclagem: dict[str, Any] = {}
    if run_extras:
        if getattr(cfg, "armazem_in_loop", False):
            emit("078 / Armazem sequencial apos distribuicao...")
            try:
                result_78 = run_pipeline_78(
                    credentials=creds,
                    settings=cfg,
                    headless=use_headless,
                    on_status=lambda m: emit(f"[78] {m}"),
                )
                emit("078 concluido.")
            except Exception as err:  # noqa: BLE001
                errors["78"] = str(err)
                emit(f"078 FALHOU: {err}")

        if getattr(cfg, "pendencia_in_loop", False):
            emit("031 / Pendencia sequencial apos armazem...")
            try:
                result_31 = run_pipeline_31(
                    credentials=creds,
                    settings=cfg,
                    headless=use_headless,
                    on_status=lambda m: emit(f"[31] {m}"),
                )
                emit("031 concluido.")
            except Exception as err:  # noqa: BLE001
                errors["31"] = str(err)
                emit(f"031 FALHOU: {err}")

        if getattr(cfg, "contratacao_in_loop", False):
            emit("073 / Contratacao (filiais 200)...")
            try:
                result_73 = run_pipeline_contratacao(
                    credentials=creds,
                    settings=cfg,
                    headless=use_headless,
                    on_status=lambda m: emit(f"[73] {m}"),
                )
                emit("073 concluido.")
            except Exception as err:  # noqa: BLE001
                errors["73"] = str(err)
                emit(f"073 FALHOU: {err}")

        if getattr(cfg, "emissao_in_loop", False):
            emit("455 / Emissao sequencial...")
            try:
                result_455 = run_pipeline_455(
                    credentials=creds,
                    settings=cfg,
                    headless=use_headless,
                    on_status=lambda m: emit(f"[455] {m}"),
                    clean_downloads=False,
                )
                emit("455 concluido.")
            except Exception as err:  # noqa: BLE001
                errors["455"] = str(err)
                emit(f"455 FALHOU: {err}")

        if getattr(cfg, "reciclagem_in_loop", False):
            emit("Reciclagem / 019+081 sequencial...")
            try:
                result_reciclagem = run_pipeline_reciclagem(
                    credentials=creds,
                    settings=cfg,
                    headless=use_headless,
                    on_status=lambda m: emit(f"[reciclagem] {m}"),
                    clean_downloads=False,
                )
                emit("Reciclagem concluida.")
            except Exception as err:  # noqa: BLE001
                errors["reciclagem"] = str(err)
                emit(f"Reciclagem FALHOU: {err}")

    if errors and not result_50 and not result_103 and not result_36 and not result_225 and not result_78 and not result_31 and not result_73 and not result_455 and not result_reciclagem:
        raise RuntimeError("; ".join(f"{k}: {v}" for k, v in errors.items()))

    return {
        "ok": not errors
        or bool(
            result_50
            or result_103
            or result_36
            or result_225
            or result_78
            or result_31
            or result_73
            or result_455
            or result_reciclagem
        ),
        "errors": errors,
        "period_50": format_period(ini50, fim50),
        "period_103": format_period(ini103, fim103),
        "period_36": format_period(ini36, fim36) if run_36 else "",
        "period_225": format_period(ini225, fim225),
        "50": result_50,
        "103": result_103,
        "36": result_36,
        "225": result_225,
        "78": result_78,
        "31": result_31,
        "73": result_73,
        "455": result_455,
        "reciclagem": result_reciclagem,
        "sheets_50": sheets50,
        "sheets_103": sheets103,
        "sheets_36": sheets36,
        "sheets_225": sheets225,
        "dashboard": dash,
        "shared_session": True,
        "parallel_cycle": False,
        "headless": use_headless,
    }


def run_parallel_cycle(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    sync: bool = True,
    jobs: list[str] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Roda setores escolhidos ao mesmo tempo (1 Chromium por bloco).

    jobs: lista entre dist | 78 | 31 | 73 | 455 | reciclagem.
    Se None, monta a partir das flags *_in_loop.
    should_stop: se True, interrompe assim que um bloco terminar (e sinaliza LoopStopped).
    """
    status = on_status or _noop

    def _stopped() -> bool:
        try:
            from ace_stop import stop_requested

            if stop_requested():
                return True
        except Exception:
            pass
        return bool(should_stop and should_stop())

    def emit(msg: str) -> None:
        if _stopped():
            from ace_stop import LoopStopped

            raise LoopStopped("parado pelo usuário")
        with _STATUS_LOCK:
            status(msg)
            _log_file(msg)

    cfg = settings or load_settings()
    creds = credentials or load_credentials()
    use_headless = cfg.headless if headless is None else bool(headless)

    if jobs is None:
        # Sync / ciclo completo: sempre distribuição + extras ligados
        jobs = ["dist"]
        if getattr(cfg, "armazem_in_loop", False):
            jobs.append("78")
        if getattr(cfg, "pendencia_in_loop", False):
            jobs.append("31")
        if getattr(cfg, "contratacao_in_loop", False):
            jobs.append("73")
        if getattr(cfg, "emissao_in_loop", False):
            jobs.append("455")
        if getattr(cfg, "reciclagem_in_loop", False):
            jobs.append("reciclagem")
    else:
        jobs = [str(j).strip().lower() for j in jobs if str(j).strip()]
        # aliases
        alias = {
            "078": "78",
            "031": "31",
            "073": "73",
            "076": "73",
            "emissao": "455",
            "armazem": "78",
            "019": "reciclagem",
            "19": "reciclagem",
            "081": "reciclagem",
            "81": "reciclagem",
            "recicla": "reciclagem",
        }
        jobs = [alias.get(j, j) for j in jobs]

    if not jobs:
        emit("CICLO: nenhum setor habilitado no automático.")
        return {
            "ok": True,
            "errors": {},
            "skipped": True,
            "parallel_jobs": [],
            "headless": use_headless,
        }

    emit(
        f"CICLO paralelo | {len(jobs)} bloco(s) simultâneos: "
        + " · ".join(jobs)
        + f" | viz={'oculto' if use_headless else 'visivel'}"
    )
    cleanup_downloads(DOWNLOAD_DIR, on_status=emit)

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def _run_dist() -> dict[str, Any]:
        return run_dual_cycle(
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=lambda m: emit(f"[dist] {m}"),
            sync=sync,
            run_extras=False,
            skip_cleanup=True,
        )

    def _run_78() -> dict[str, Any]:
        return run_pipeline_78(
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=lambda m: emit(f"[78] {m}"),
        )

    def _run_31() -> dict[str, Any]:
        return run_pipeline_31(
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=lambda m: emit(f"[31] {m}"),
            clean_downloads=False,
        )

    def _run_73() -> dict[str, Any]:
        return run_pipeline_contratacao(
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=lambda m: emit(f"[73] {m}"),
            clean_downloads=False,
        )

    def _run_455() -> dict[str, Any]:
        return run_pipeline_455(
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=lambda m: emit(f"[455] {m}"),
            clean_downloads=False,
        )

    def _run_reciclagem() -> dict[str, Any]:
        return run_pipeline_reciclagem(
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=lambda m: emit(f"[reciclagem] {m}"),
            clean_downloads=False,
        )

    workers = {
        "dist": _run_dist,
        "78": _run_78,
        "31": _run_31,
        "73": _run_73,
        "455": _run_455,
        "reciclagem": _run_reciclagem,
    }
    unknown = [j for j in jobs if j not in workers]
    if unknown:
        raise ValueError(f"setor(es) inválido(s): {', '.join(unknown)}")

    with ThreadPoolExecutor(max_workers=max(1, len(jobs)), thread_name_prefix="ace") as pool:
        futures = {pool.submit(workers[name]): name for name in jobs}
        stopped = False
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                results[name] = fut.result()
                emit(f"[{name}] bloco OK")
            except Exception as err:  # noqa: BLE001
                from ace_stop import LoopStopped

                if isinstance(err, LoopStopped) or _stopped():
                    stopped = True
                    errors[name] = "parado"
                    results[name] = {}
                else:
                    errors[name] = str(err)
                    results[name] = {}
                    try:
                        emit(f"[{name}] bloco FALHOU: {err}")
                    except Exception:
                        pass
            if _stopped() or stopped:
                stopped = True
                # cancela pendentes e derruba Chromium dos workers ainda vivos
                for other in futures:
                    other.cancel()
                try:
                    from ace_stop import kill_child_browsers, close_registered_browsers

                    close_registered_browsers()
                    kill_child_browsers()
                except Exception:
                    pass
                break

    if stopped or _stopped():
        from ace_stop import LoopStopped

        raise LoopStopped("parado pelo usuário")

    dist = results.get("dist") or {}
    dist_errors = dict(dist.get("errors") or {})
    merged_errors = {**dist_errors, **{k: v for k, v in errors.items() if k != "dist"}}
    if "dist" in errors:
        merged_errors["dist"] = errors["dist"]

    result_78 = results.get("78") or {}
    result_31 = results.get("31") or {}
    result_73 = results.get("73") or {}
    result_455 = results.get("455") or {}
    result_reciclagem = results.get("reciclagem") or {}
    result_50 = dist.get("50") or {}
    result_103 = dist.get("103") or {}
    result_36 = dist.get("36") or {}
    result_225 = dist.get("225") or {}

    any_ok = bool(
        result_50
        or result_103
        or result_36
        or result_225
        or result_78
        or result_31
        or result_73
        or result_455
        or result_reciclagem
    )
    if merged_errors and not any_ok:
        raise RuntimeError("; ".join(f"{k}: {v}" for k, v in merged_errors.items()))

    emit(
        "CICLO paralelo concluído | "
        f"ok={any_ok} | erros={merged_errors or '{}'}"
    )

    return {
        "ok": not merged_errors or any_ok,
        "errors": merged_errors,
        "period_50": dist.get("period_50") or "",
        "period_103": dist.get("period_103") or "",
        "period_36": dist.get("period_36") or "",
        "period_225": dist.get("period_225") or "",
        "50": result_50,
        "103": result_103,
        "36": result_36,
        "225": result_225,
        "78": result_78,
        "31": result_31,
        "73": result_73,
        "455": result_455,
        "reciclagem": result_reciclagem,
        "sheets_50": dist.get("sheets_50") or {"ok": False, "skipped": True},
        "sheets_103": dist.get("sheets_103") or {"ok": False, "skipped": True},
        "sheets_36": dist.get("sheets_36") or {"ok": False, "skipped": True},
        "sheets_225": dist.get("sheets_225") or {"ok": False, "skipped": True},
        "dashboard": dist.get("dashboard") or {"ok": False, "skipped": True},
        "shared_session": True,
        "parallel_cycle": True,
        "parallel_jobs": jobs,
        "headless": use_headless,
    }


def run_pipeline_78(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """Captura SSW 078 (+ 177 conferentes) + CSV local + Sheets. Sem push GitHub."""
    status = on_status or _noop
    ensure_dirs()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    from parser_ssw78 import analyze_78
    from parser_ssw177 import analyze_report_177
    from publish_dashboard import publish_armazem_local
    from sheets_sync_78 import sync_sheets_78
    from ssw_177 import download_report_177
    from ssw_78 import capture_ssw78

    status(f"ACE ARMAZÉM · 78 | {datetime.now():%d/%m %H:%M:%S}")
    use_headless = cfg.headless if headless is None else headless
    capture = capture_ssw78(
        credentials=creds,
        headless=use_headless,
        on_status=status,
    )
    analysis = analyze_78(
        capture.get("table_rows") or None,
        body_text=str(capture.get("body_text") or ""),
        html=str(capture.get("html") or ""),
    )
    if _should_use_local_store(cfg):
        status("078 capturado — modo local (JSON, sem Sheets)…")
        sheets78 = _persist_local_instead_of_sheets("78", cfg=cfg, on_status=status)
    else:
        status("078 capturado — enviando Sheets (pátio) agora…")
        sheets78 = sync_sheets_78(cfg, on_status=status, include_78=True, include_177=False)

    conf177: dict[str, Any] = {"ok": False}
    sheets177: dict[str, Any] = {"ok": False, "skipped": True}
    try:
        status("ACE ARMAZÉM · 177 conferentes (mensal)...")
        dl177 = download_report_177(headless=use_headless, on_status=status)
        conf177 = analyze_report_177(dl177["path"], on_status=status)
        conf177["download"] = dl177
        if _should_use_local_store(cfg):
            status("177 analisado — modo local (JSON)…")
            sheets177 = _persist_local_instead_of_sheets("177", cfg=cfg, on_status=status)
        else:
            status("177 analisado — enviando Sheets (conferentes) agora…")
            sheets177 = sync_sheets_78(cfg, on_status=status, include_78=False, include_177=True)
    except Exception as err:  # noqa: BLE001
        status(f"177 falhou (pátio 078 segue): {err}")
        conf177 = {"ok": False, "error": str(err)}

    pub = publish_armazem_local(on_status=status)
    sheets = {
        "ok": bool(sheets78.get("ok") or sheets177.get("ok")),
        "78": sheets78,
        "177": sheets177,
        "via": "apps_script",
        "mode": "per_report",
    }
    status(
        f"OK · linhas={analysis.get('total_linhas')} "
        f"veículos={analysis.get('total_veiculos')} "
        f"peso={analysis.get('peso_total'):,.0f}".replace(",", ".")
        + (
            f" · 177 topo={conf177.get('topo')} ({conf177.get('total_conferentes')} conf.)"
            if conf177.get("ok")
            else ""
        )
    )
    return {
        "capture": capture,
        "publish": pub,
        "sheets": sheets,
        "177": conf177,
        **analysis,
    }


def run_pipeline_31(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    headless: bool | None = None,
    codes: list[str] | tuple[str, ...] | None = None,
    on_status: StatusCallback | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """SSW 031: download Excel por ocorrência → análise → Sheets → dashboard local."""
    status = on_status or _noop
    ensure_dirs()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    from parser_ssw31 import analyze_reports_31
    from publish_dashboard import publish_pendencia_local
    from sheets_sync_31 import sync_sheets_31
    from ssw_31 import download_reports_31

    status(f"ACE PENDENCIA · 31 | {datetime.now():%d/%m %H:%M:%S}")
    use_headless = cfg.headless if headless is None else headless
    try:
        dl = download_reports_31(
            codes=codes,
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=status,
            clean_downloads=clean_downloads,
        )
    except Exception as err:
        from ace_stop import LoopStopped, stop_requested

        if isinstance(err, LoopStopped) or stop_requested() or "parado pelo usuário" in str(err).lower():
            status("031 parado pelo usuário")
            raise LoopStopped("031 parado pelo usuário") from err
        raise
    analysis = analyze_reports_31(
        dl.get("paths") or {},
        periodo=str(dl.get("period") or ""),
        on_status=status,
    )
    if _should_use_local_store(cfg):
        status("031 analisado — modo local (JSON, sem Sheets)…")
        sheets = _persist_local_instead_of_sheets("31", cfg=cfg, on_status=status)
    else:
        status("031 analisado — enviando Sheets agora…")
        sheets = sync_sheets_31(cfg, on_status=status)
    pub = publish_pendencia_local(on_status=status)
    status(
        f"OK · CTRCs={analysis.get('total')} "
        f"ofensores={len(analysis.get('ofensores') or [])} "
        f"topo={(analysis.get('resumo') or {}).get('topo_codigo')}"
    )
    return {
        "download": dl,
        "analysis": analysis,
        "sheets": sheets,
        "publish": pub,
        **analysis,
    }


def run_pipeline_455(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    headless: bool | None = None,
    unidade: str = "SPO",
    on_status: StatusCallback | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """SSW 455: Fretes Expedidos (SPO/E) · dia emissão → painel Emissão."""
    status = on_status or _noop
    ensure_dirs()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    from parser_ssw455 import analyze_reports_455
    from publish_dashboard import publish_emissao_local
    from ssw_455 import download_reports_455

    status(f"ACE EMISSAO · 455 | {datetime.now():%d/%m %H:%M:%S}")
    use_headless = cfg.headless if headless is None else headless
    dl = download_reports_455(
        unidade=unidade or "SPO",
        tipo_unidade="E",
        arquivo="E",
        credentials=creds,
        settings=cfg,
        headless=use_headless,
        on_status=status,
        clean_downloads=clean_downloads,
    )
    analysis = analyze_reports_455(
        dl.get("files") or [],
        periodo=str(dl.get("periodo_fmt") or dl.get("period") or ""),
        on_status=status,
    )
    if _should_use_local_store(cfg):
        status("455 analisado — modo local (JSON/CSV, sem Sheets)…")
        sheets: dict[str, Any] = {"ok": True, "local": True}
    else:
        from sheets_sync_455 import sync_sheets_455

        status("455 analisado — sync Sheets (Sites/TV)…")
        sheets = sync_sheets_455(settings=cfg, on_status=status)
    pub = publish_emissao_local(on_status=status)
    resumo = analysis.get("resumo") or {}
    status(
        f"OK · CTEs={resumo.get('ctes')} frete={resumo.get('frete_fmt')} "
        f"dia={resumo.get('dia')} noite={resumo.get('noite')}"
    )
    return {
        "download": dl,
        "analysis": analysis,
        "sheets": sheets,
        "publish": pub,
        **analysis,
    }


def run_pipeline_reciclagem(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """SSW 019 + 081 → parsers → JSON local → painel Reciclagem."""
    status = on_status or _noop
    ensure_dirs()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    from parser_ssw019 import analyze_reports_019
    from parser_ssw081 import analyze_reports_081
    from publish_dashboard import publish_reciclagem_local
    from ssw_019 import download_reports_019
    from ssw_081 import download_reports_081

    status(f"ACE RECICLAGEM · 019+081 | {datetime.now():%d/%m %H:%M:%S}")
    use_headless = cfg.headless if headless is None else headless

    try:
        dl19 = download_reports_019(
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=lambda m: status(f"[019] {m}" if not str(m).startswith("[019]") else m),
            clean_downloads=clean_downloads,
        )
    except Exception as err:
        from ace_stop import LoopStopped, stop_requested

        if isinstance(err, LoopStopped) or stop_requested() or "parado pelo usuário" in str(err).lower():
            status("019/reciclagem parado pelo usuário")
            raise LoopStopped("reciclagem parado pelo usuário") from err
        raise

    analysis19 = analyze_reports_019(
        (dl19.get("paths") or {}).get("019") or (dl19.get("files") or [None])[0],
        periodo=str(dl19.get("periodo_fmt") or dl19.get("period") or ""),
        on_status=status,
    )

    try:
        dl81 = download_reports_081(
            credentials=creds,
            settings=cfg,
            headless=use_headless,
            on_status=lambda m: status(f"[081] {m}" if not str(m).startswith("[081]") else m),
            clean_downloads=False,
        )
    except Exception as err:
        from ace_stop import LoopStopped, stop_requested

        if isinstance(err, LoopStopped) or stop_requested() or "parado pelo usuário" in str(err).lower():
            status("081/reciclagem parado pelo usuário")
            raise LoopStopped("reciclagem parado pelo usuário") from err
        raise

    analysis81 = analyze_reports_081(
        (dl81.get("paths") or {}).get("081") or (dl81.get("files") or [None])[0],
        periodo=str(dl81.get("periodo_fmt") or dl81.get("period") or ""),
        on_status=status,
    )

    status("Reciclagem analisada — gravando JSON local…")
    sheets = _persist_local_instead_of_sheets("reciclagem", cfg=cfg, on_status=status)
    pub = publish_reciclagem_local(on_status=status)
    r19 = analysis19.get("resumo") or {}
    r81 = analysis81.get("resumo") or {}
    status(
        f"OK · 019={r19.get('qtd')} CTRCs · 081={r81.get('qtd')} CTRCs "
        f"· frete 019={r19.get('frete_fmt')} · frete 081={r81.get('frete_fmt')}"
    )
    return {
        "download_019": dl19,
        "download_081": dl81,
        "analysis_019": analysis19,
        "analysis_081": analysis81,
        "sheets": sheets,
        "publish": pub,
        "ok": True,
        "total_019": analysis19.get("total"),
        "total_081": analysis81.get("total"),
    }


def run_pipeline_contratacao(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    headless: bool | None = None,
    skip_076: bool = True,
    skip_200: bool = False,
    local_073: list[str] | Path | str | None = None,
    local_200: list[str] | Path | str | None = None,
    on_status: StatusCallback | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """
    Contratação: 073 (tela) → por destino 200/ssw0644 (frete). Sem 076.
    """
    status = on_status or _noop
    ensure_dirs()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    from dates import format_period, periodo_hoje
    from parser_ssw073 import analyze_reports_073
    from parser_ssw076 import analyze_reports_076
    from parser_ssw0644 import analyze_reports_200
    from publish_dashboard import publish_contratacao_local
    from ssw_076 import download_reports_076
    from ssw_200 import download_reports_200

    status(f"ACE CONTRATACAO · 73→filiais(200) | {datetime.now():%d/%m %H:%M:%S}")
    use_headless = cfg.headless if headless is None else headless
    ini, fim = periodo_hoje()
    periodo_fmt = format_period(ini, fim)

    dl73: dict[str, Any] = {}
    dl76: dict[str, Any] = {}
    dl200: dict[str, Any] = {}

    # Só CSV 200 local: aplica frete no 073 já em cache
    if local_200 and not local_073:
        from parser_ssw073 import VEICULOS_073_CSV

        if not VEICULOS_073_CSV.exists():
            raise RuntimeError("200 local: rode o 073 antes (sem veiculos_073.csv)")
        files200 = local_200 if isinstance(local_200, (list, tuple)) else [local_200]
        status(f"200 local (merge no 073 em cache): {len(files200)} arquivo(s)")
        analysis200 = analyze_reports_200(list(files200), on_status=status)
        pub = publish_contratacao_local(on_status=status)
        resumo = analysis200.get("resumo") or {}
        status(
            f"OK · veículos={(resumo or {}).get('total_veiculos')} "
            f"custo={(resumo or {}).get('custo_fmt')} "
            f"frete={(resumo or {}).get('frete_fmt')}"
        )
        return {
            "ok": True,
            "073": {},
            "076": {},
            "200": {"download": {}, **analysis200},
            "publish": pub,
            "resumo": resumo,
            "placas": [],
        }

    if local_073:
        files = local_073 if isinstance(local_073, (list, tuple)) else [local_073]
        status(f"073 local: {len(files)} arquivo(s)")
        analysis73 = analyze_reports_073(
            list(files), periodo=periodo_fmt, unidade="SPO", on_status=status
        )
        analysis76: dict[str, Any] = {"ok": False, "skipped": True}
        analysis200: dict[str, Any] = {"ok": False, "skipped": True}
        placas = list(analysis73.get("placas") or [])
        if not skip_076:
            try:
                dl76 = download_reports_076(
                    placas=placas,
                    period=(ini, fim),
                    arquivo="E",
                    unidade="SPO",
                    credentials=creds,
                    settings=cfg,
                    headless=use_headless,
                    on_status=status,
                )
                analysis76 = analyze_reports_076(
                    dl76.get("files") or [],
                    placas=placas,
                    on_status=status,
                )
            except Exception as err:  # noqa: BLE001
                status(f"076 avisou: {err} (mantendo frete do 073)")
                analysis76 = {"ok": False, "error": str(err)}
        if local_200 and not skip_200:
            files200 = local_200 if isinstance(local_200, (list, tuple)) else [local_200]
            try:
                analysis200 = analyze_reports_200(
                    list(files200), placas=placas, on_status=status
                )
            except Exception as err:  # noqa: BLE001
                status(f"200 local avisou: {err}")
                analysis200 = {"ok": False, "error": str(err)}
        elif not skip_200:
            try:
                dl200 = download_reports_200(
                    period=(ini, fim),
                    unidade_origem="SPO",
                    tipo_arquivo="E",
                    tag="SPO",
                    credentials=creds,
                    settings=cfg,
                    headless=use_headless,
                    on_status=status,
                )
                analysis200 = analyze_reports_200(
                    dl200.get("files") or [],
                    placas=placas,
                    on_status=status,
                )
            except Exception as err:  # noqa: BLE001
                status(f"200 avisou: {err} (mantendo frete anterior)")
                analysis200 = {"ok": False, "error": str(err)}
        pub = publish_contratacao_local(on_status=status)
        resumo = (
            analysis200.get("resumo")
            or analysis76.get("resumo")
            or analysis73.get("resumo")
        )
        status(
            f"OK · veículos={(resumo or {}).get('total_veiculos')} "
            f"custo={(resumo or {}).get('custo_fmt')} "
            f"frete={(resumo or {}).get('frete_fmt')}"
        )
        return {
            "ok": True,
            "073": {"download": dl73, **analysis73},
            "076": {"download": dl76, **analysis76},
            "200": {"download": dl200, **analysis200},
            "publish": pub,
            "resumo": resumo,
            "placas": placas,
        }

    # Live SSW: 1 login · 073 + 076 + 200
    from ssw_073 import download_contratacao_ssw

    bundle = download_contratacao_ssw(
        period=(ini, fim),
        skip_076=skip_076,
        skip_200=skip_200,
        unidade_emissora="SPO",
        credentials=creds,
        settings=cfg,
        headless=use_headless,
        on_status=status,
        clean_downloads=clean_downloads,
    )
    pub = publish_contratacao_local(on_status=status)
    local_snap: dict[str, Any] = {}
    if _should_use_local_store(cfg):
        status("073 concluído — modo local (JSON, sem Sheets)…")
        local_snap = _persist_local_instead_of_sheets("73", cfg=cfg, on_status=status)
    resumo = bundle.get("resumo") or {}
    status(
        f"OK · veículos={(resumo or {}).get('total_veiculos')} "
        f"custo={(resumo or {}).get('custo_fmt')} "
        f"frete={(resumo or {}).get('frete_fmt')}"
    )
    return {
        "ok": True,
        "073": bundle.get("073") or {},
        "076": bundle.get("076") or {},
        "200": bundle.get("200") or {},
        "filiais": list(bundle.get("filiais") or []),
        "filial_errors": bundle.get("filial_errors") or {},
        "publish": pub,
        "local": local_snap,
        "resumo": resumo,
        "placas": list(bundle.get("placas") or []),
    }
