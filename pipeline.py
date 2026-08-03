from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, LOG_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import format_period, normalize_date, periodo_103_hoje, periodo_36_ontem_hoje, periodo_50_coleta_hoje, periodo_semana_seg_dom, sugestao_periodo
from parser_ssw0157 import analyze_report
from publish_dashboard import publish_dashboard
from sheets_sync import sync_google_sheets, sync_google_sheets_103, sync_google_sheets_36, sync_google_sheets_225
from ssw_client import cleanup_downloads, download_ace_103, download_ace_36, download_ace_reports
from parser_ssw103 import analyze_report_103
from parser_ssw0146 import analyze_report_36
from parser_ssw225 import analyze_report_225

StatusCallback = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _log_file(message: str) -> None:
    ensure_dirs()
    path = LOG_DIR / f"ace_{datetime.now():%Y%m%d}.log"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{datetime.now():%H:%M:%S}] {message}\n")


def find_latest_report(download_dir: Path | None = None) -> Path | None:
    folder = Path(download_dir or DOWNLOAD_DIR)
    if not folder.exists():
        return None
    files = sorted(
        list(folder.glob("*.sswweb")) + list(folder.glob("ssw0157*")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


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
        "*.xlsx",
        "*.xls",
        "*.csv",
    )
    for directory in search_dirs:
        if not directory.exists():
            continue
        for pattern in patterns:
            candidates.extend(directory.glob(pattern))

    # Prefer arquivos da 103
    def score(path: Path) -> tuple:
        name = path.name.lower()
        is_103 = ("ssw0166" in name) or name.startswith("coleta_103") or name.startswith("csvssw0166")
        return (1 if is_103 else 0, path.stat().st_mtime)

    files = sorted({p.resolve() for p in candidates if p.is_file()}, key=score, reverse=True)
    return files[0] if files else None


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
    folder = Path(download_dir or DOWNLOAD_DIR)
    if not folder.exists():
        return None
    candidates: list[Path] = []
    for pattern in ("*225*", "*agend*", "CSV*100432*", "*BIN*.csv", "*.csv"):
        candidates.extend(folder.glob(pattern))
    files = sorted(
        {p.resolve() for p in candidates if p.is_file()},
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Prefere CSV com cabecalho de agendamento
    for p in files:
        try:
            head = p.read_text(encoding="latin-1", errors="replace")[:200].upper()
        except OSError:
            continue
        if "AGENDADO" in head and "CTRC" in head:
            return p
    return files[0] if files else None


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


def run_dual_cycle(
    *,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    headless: bool = True,
    on_status: StatusCallback | None = None,
    sync: bool = True,
) -> dict[str, Any]:
    """
    Baixa 50 + 103 (+ 36 se entrega_option=36) EM PARALELO, analisa e sobe Sheets/dashboard.

    Periodos automaticos (recalculados a cada ciclo / virada de dia):
      50  → periodo de COLETA = HOJE
      103 → data LIMITE HOJE (Por data de = L)
      36  → seg: SEXTA..HOJE | demais: D-1..HOJE (romaneios/CTRCs entrega)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    status = on_status or _noop

    def emit(msg: str) -> None:
        status(msg)
        _log_file(msg)

    cfg = settings or load_settings()
    creds = credentials or load_credentials()
    ini50, fim50 = periodo_50_coleta_hoje()
    ini103, fim103 = periodo_103_hoje()
    ini36, fim36 = periodo_36_ontem_hoje()
    run_36 = str(getattr(cfg, "entrega_option", "") or "").strip() == "36"
    emit(
        f"CICLO dual | 50 coleta={format_period(ini50, fim50)} "
        f"| 103 limite={format_period(ini103, fim103)}"
        + (
            f" | 36 periodo={format_period(ini36, fim36)} (apos 50/103)"
            if run_36
            else ""
        )
        + " | paralelo 50+103"
    )

    # Limpa antigos UMA vez antes do paralelo (evita race)
    cleanup_downloads(DOWNLOAD_DIR, on_status=emit)

    result_50: dict[str, Any] = {}
    result_103: dict[str, Any] = {}
    result_36: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def job_50() -> dict[str, Any]:
        def st(m: str) -> None:
            emit(f"[50] {m}")

        download = download_ace_reports(
            ini50,
            fim50,
            keep_open=False,
            headless=headless,
            on_status=st,
            credentials=creds,
            settings=cfg,
            clean_downloads=False,
        )
        report = Path((download.get("paths") or {}).get("coleta") or "")
        if not report.exists():
            latest = find_latest_report()
            if not latest:
                raise RuntimeError("50 sem arquivo")
            report = latest
            st(f"Usando ultimo: {report.name}")
        analysis = run_analysis_only(report, settings=cfg, on_status=st, sync=False)
        return {"download": download, **analysis, "period": format_period(ini50, fim50)}

    def job_103() -> dict[str, Any]:
        def st(m: str) -> None:
            emit(f"[103] {m}")

        download = download_ace_103(
            ini103,
            fim103,
            keep_open=False,
            headless=headless,
            on_status=st,
            credentials=creds,
            settings=cfg,
            clean_downloads=False,
        )
        report = Path((download.get("paths") or {}).get("coleta_103") or "")
        if not report.exists():
            latest = find_latest_103()
            if not latest:
                raise RuntimeError("103 sem arquivo")
            report = latest
            st(f"Usando ultimo: {report.name}")
        analysis = run_analysis_103(
            report,
            periodo=format_period(ini103, fim103),
            settings=cfg,
            on_status=st,
            sync=False,
        )
        return {"download": download, **analysis, "period": format_period(ini103, fim103)}

    def job_36() -> dict[str, Any]:
        def st(m: str) -> None:
            emit(f"[36] {m}")

        download = download_ace_36(
            ini36,
            fim36,
            keep_open=False,
            headless=headless,
            on_status=st,
            credentials=creds,
            settings=cfg,
            clean_downloads=False,
        )
        report = Path((download.get("paths") or {}).get("entrega_36") or "")
        if not report.exists():
            latest = find_latest_36()
            if not latest:
                raise RuntimeError("36 sem arquivo")
            report = latest
            st(f"Usando ultimo: {report.name}")
        analysis = run_analysis_36(
            report,
            periodo=format_period(ini36, fim36),
            settings=cfg,
            on_status=st,
            sync=False,
        )
        return {"download": download, **analysis, "period": format_period(ini36, fim36)}

    # 50 + 103 em paralelo; 36 depois (sozinha) para nao disputar SSW/download
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ace") as pool:
        futures = {
            pool.submit(job_50): "50",
            pool.submit(job_103): "103",
        }
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                data = fut.result()
                if label == "50":
                    result_50 = data
                else:
                    result_103 = data
                emit(f"{label} concluido.")
            except Exception as err:  # noqa: BLE001
                errors[label] = str(err)
                emit(f"{label} FALHOU: {err}")

    if run_36:
        emit(f"36 sequencial | periodo={format_period(ini36, fim36)}")
        try:
            result_36 = job_36()
            emit("36 concluido.")
        except Exception as err:  # noqa: BLE001
            errors["36"] = str(err)
            emit(f"36 FALHOU: {err}")

    # Mantem so os relatorios finais deste ciclo
    keep: list[Path] = []
    for block in (result_50, result_103, result_36):
        paths = (block.get("download") or {}).get("paths") or {}
        for key in ("coleta", "coleta_103", "entrega_36"):
            p = Path(paths.get(key) or "")
            if p.exists():
                keep.append(p)
        report = Path(block.get("report") or "")
        if report.exists():
            keep.append(report)
    cleanup_downloads(DOWNLOAD_DIR, keep=keep, on_status=emit)

    sheets50 = sheets103 = sheets36 = dash = {"ok": False, "skipped": True}
    if sync and (result_50 or result_103 or result_36):
        if result_50:
            sheets50 = sync_google_sheets(cfg, on_status=emit)
        if result_103:
            sheets103 = sync_google_sheets_103(cfg, on_status=emit)
        if result_36:
            sheets36 = sync_google_sheets_36(cfg, on_status=emit)
        dash = publish_dashboard(cfg, on_status=emit)

    if errors and not result_50 and not result_103 and not result_36:
        raise RuntimeError("; ".join(f"{k}: {v}" for k, v in errors.items()))

    return {
        "ok": not errors or bool(result_50 or result_103 or result_36),
        "errors": errors,
        "period_50": format_period(ini50, fim50),
        "period_103": format_period(ini103, fim103),
        "period_36": format_period(ini36, fim36) if run_36 else "",
        "50": result_50,
        "103": result_103,
        "36": result_36,
        "sheets_50": sheets50,
        "sheets_103": sheets103,
        "sheets_36": sheets36,
        "dashboard": dash,
    }
