"""
ACE · Console CMD
Menu no topo + comando /e para editar qualquer config da automacao.

Exemplos:
  /e user m.aguir
  /e unit SPO,LEO,RIS
  /e enable_sheets true
  /e periodo_modo diario
  /automatica
  /e
  help
  50
  103
  sync
  sair

Modo automatico (sem menu):
  ace.bat /automatica
  ace.bat /automatica 5
"""
from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    CONFIG_PATH,
    AceSettings,
    SswCredentials,
    ensure_dirs,
    load_credentials,
    load_settings,
    save_all,
)
from dates import format_period, periodo_103_hoje, periodo_50_coleta_hoje, to_ssw_ddmmyy


# Campos editaveis: chave -> (grupo, tipo, mascara_senha)
EDITABLE: dict[str, tuple[str, str, bool]] = {
    # Login SSW
    "url": ("ssw", "str", False),
    "domain": ("ssw", "str", False),
    "document": ("ssw", "str", False),
    "user": ("ssw", "str", False),
    "password": ("ssw", "str", True),
    "unit": ("ssw", "str", False),
    # Automacao geral
    "coleta_option": ("auto", "str", False),
    "entrega_option": ("auto", "str", False),
    "periodo_modo": ("auto", "str", False),  # diario | sexta
    "auto_baixar_ao_abrir": ("auto", "bool", False),
    "loop_intervalo": ("auto", "str", False),  # fallback 30s | 5m | 1h | 2d
    "ciclo_paralelo": ("auto", "bool", False),
    "headless": ("auto", "bool", False),
    # Setores no /automatica (aba Automação do CRT)
    "dist_in_loop": ("automacao", "bool", False),
    "dist_intervalo": ("automacao", "str", False),
    "armazem_in_loop": ("automacao", "bool", False),
    "armazem_intervalo": ("automacao", "str", False),
    "pendencia_in_loop": ("automacao", "bool", False),
    "pendencia_intervalo": ("automacao", "str", False),
    "contratacao_in_loop": ("automacao", "bool", False),
    "contratacao_intervalo": ("automacao", "str", False),
    "emissao_in_loop": ("automacao", "bool", False),
    "emissao_intervalo": ("automacao", "str", False),
    "reciclagem_in_loop": ("automacao", "bool", False),
    "reciclagem_intervalo": ("automacao", "str", False),
    "mapa_in_loop": ("automacao", "bool", False),
    "mapa_intervalo": ("automacao", "str", False),
    "cybermap_path": ("automacao", "str", False),
    # Sheets / dashboard
    "enable_sheets": ("cloud", "bool", False),
    "apps_script_url": ("cloud", "str", False),
    "apps_script_token": ("cloud", "str", True),
    "google_sheet_id": ("cloud", "str", False),
    "enable_github_publish": ("cloud", "bool", False),
    "github_repo": ("cloud", "str", False),
    "github_branch": ("cloud", "str", False),
    "github_token_env": ("cloud", "str", False),
    "publish_target": ("cloud", "str", False),  # sites | github | local | auto
    "google_sites_url": ("cloud", "str", False),
    "modo_local": ("local", "bool", False),
    "dashboard_lan": ("local", "bool", False),
    "dashboard_port": ("local", "int", False),
    "crt_lock_password": ("crt", "str", True),
}

BOOL_TRUE = {"1", "true", "t", "yes", "y", "sim", "s", "on", "ligado"}
BOOL_FALSE = {"0", "false", "f", "no", "n", "nao", "não", "off", "desligado"}


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _mask(value: str, secret: bool = False) -> str:
    text = str(value or "")
    if not secret:
        return text if text else "—"
    if not text:
        return "(vazio)"
    if len(text) <= 2:
        return "*" * len(text)
    return text[:1] + ("*" * min(8, len(text) - 2)) + text[-1:]


def _parse_bool(raw: str) -> bool:
    v = raw.strip().lower()
    if v in BOOL_TRUE:
        return True
    if v in BOOL_FALSE:
        return False
    raise ValueError("Use true/false, sim/nao, 1/0")


def _load_payload() -> dict[str, Any]:
    creds = load_credentials()
    settings = load_settings()
    return {**asdict(creds), **asdict(settings)}


def _save_payload(payload: dict[str, Any]) -> None:
    creds = SswCredentials(
        url=str(payload.get("url") or ""),
        domain=str(payload.get("domain") or ""),
        document=str(payload.get("document") or ""),
        user=str(payload.get("user") or ""),
        password=str(payload.get("password") or ""),
        unit=str(payload.get("unit") or ""),
    )
    settings = AceSettings(
        coleta_option=str(payload.get("coleta_option") or "50"),
        entrega_option=str(payload.get("entrega_option") or ""),
        periodo_modo=str(payload.get("periodo_modo") or "diario"),
        auto_baixar_ao_abrir=bool(payload.get("auto_baixar_ao_abrir", True)),
        loop_intervalo=str(payload.get("loop_intervalo") or "5m"),
        dist_intervalo=str(payload.get("dist_intervalo") or ""),
        armazem_intervalo=str(payload.get("armazem_intervalo") or ""),
        pendencia_intervalo=str(payload.get("pendencia_intervalo") or ""),
        contratacao_intervalo=str(payload.get("contratacao_intervalo") or ""),
        emissao_intervalo=str(payload.get("emissao_intervalo") or ""),
        reciclagem_intervalo=str(payload.get("reciclagem_intervalo") or "30m"),
        mapa_intervalo=str(payload.get("mapa_intervalo") or "10m"),
        enable_sheets=bool(payload.get("enable_sheets", False)),
        apps_script_url=str(payload.get("apps_script_url") or ""),
        apps_script_token=str(payload.get("apps_script_token") or ""),
        google_sheet_id=str(payload.get("google_sheet_id") or ""),
        enable_github_publish=bool(payload.get("enable_github_publish", False)),
        github_repo=str(payload.get("github_repo") or ""),
        github_branch=str(payload.get("github_branch") or "main"),
        github_token_env=str(payload.get("github_token_env") or "GH_TOKEN"),
        publish_target=str(payload.get("publish_target") or "auto"),
        google_sites_url=str(payload.get("google_sites_url") or ""),
        dist_in_loop=bool(payload.get("dist_in_loop", True)),
        armazem_in_loop=bool(payload.get("armazem_in_loop", True)),
        pendencia_in_loop=bool(payload.get("pendencia_in_loop", True)),
        contratacao_in_loop=bool(payload.get("contratacao_in_loop", True)),
        emissao_in_loop=bool(payload.get("emissao_in_loop", False)),
        reciclagem_in_loop=bool(payload.get("reciclagem_in_loop", False)),
        mapa_in_loop=bool(payload.get("mapa_in_loop", True)),
        cybermap_path=str(payload.get("cybermap_path") or r"D:\MapaCustoRegiaoSP"),
        ciclo_paralelo=bool(payload.get("ciclo_paralelo", True)),
        modo_local=bool(payload.get("modo_local", False)),
        dashboard_lan=bool(payload.get("dashboard_lan", False)),
        dashboard_port=int(payload.get("dashboard_port") or 8787),
        headless=bool(payload.get("headless", True)),
        crt_theme=str(payload.get("crt_theme") or "gestao").strip() or "gestao",
        crt_frost_alpha=max(
            0, min(100, int(payload.get("crt_frost_alpha", 55) or 0))
        ),
        crt_frost_blur=max(
            0, min(100, int(payload.get("crt_frost_blur", 70) or 0))
        ),
        crt_lock_password=str(payload.get("crt_lock_password") or "binho"),
    )
    save_all(creds, settings)


def _periodo_hint(modo: str) -> str:
    try:
        ini50, fim50 = periodo_50_coleta_hoje()
        ini103, fim103 = periodo_103_hoje()
        return (
            f"50 coleta {to_ssw_ddmmyy(ini50)}-{to_ssw_ddmmyy(fim50)} "
            f"({format_period(ini50, fim50)}) | "
            f"103 limite {to_ssw_ddmmyy(ini103)} ({format_period(ini103, fim103)})"
        )
    except Exception:
        return "—"


def _on_status(msg: str) -> None:
    from term_brand import format_status, _enable_windows_ansi, classify_status_msg

    _enable_windows_ansi()
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"  {format_status(msg, hhmmss=stamp)}")
    try:
        from crt_bridge import append_log, publish

        kind = classify_status_msg(msg)
        online = kind != "err"
        label = "ONLINE" if online else "ERR"
        mode = {"ok": "OK", "err": "ERR", "work": "RUN"}.get(kind, "RUN")
        append_log(kind, msg, source="cmd")
        # Barrinhas: sobe % do(s) setor(es) em execução manual / tag [78]/[31]/…
        try:
            _manual_status_tick(msg, kind=kind)
        except Exception:
            publish(online=online, label=label, pct=0, detail=msg[:100], mode=mode)
    except Exception:
        pass


# Contexto de execução manual (1+ setores) → barrinhas no CRT
_MANUAL_RUNNING: list[str] = []
_MANUAL_PROGRESS: dict[str, dict[str, Any]] = {}
_MANUAL_LAST_RUN: dict[str, float] = {}

_CMD_TO_SECTOR: dict[str, str] = {
    "50": "dist",
    "103": "dist",
    "36": "dist",
    "225": "dist",
    "sync": "dist",
    "sync50": "dist",
    "sync103": "dist",
    "sync36": "dist",
    "sync225": "dist",
    "78": "78",
    "078": "78",
    "177": "78",
    "armazem": "78",
    "armazém": "78",
    "sync78": "78",
    "31": "31",
    "031": "31",
    "pendencia": "31",
    "pendência": "31",
    "sync31": "31",
    "73": "73",
    "073": "73",
    "076": "73",
    "200": "73",
    "contratacao": "73",
    "contratação": "73",
    "455": "455",
    "emissao": "455",
    "emissão": "455",
    "sync455": "455",
    "syncemissao": "455",
    "mapa": "mapa",
    "mapaop": "mapa",
    "maparotas": "mapa",
    "cybermap": "mapa",
    "reciclagem": "reciclagem",
    "recicla": "reciclagem",
    "019": "reciclagem",
    "19": "reciclagem",
    "081": "reciclagem",
    "81": "reciclagem",
}


def _norm_cmd_head(raw: str) -> str:
    """Normaliza cabeçalho do comando (acentos / maiúsculas) para mapear barrinha."""
    import unicodedata

    s = (raw or "").strip().lower().lstrip("/")
    # NFKD: emissão → emissao
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("M"))
    return s


def sectors_for_command(raw: str) -> list[str]:
    """Mapeia comando digitado → id(s) de barrinha."""
    parts = (raw or "").strip().split()
    if not parts:
        return []
    head = _norm_cmd_head(parts[0])
    sid = _CMD_TO_SECTOR.get(head) or _CMD_TO_SECTOR.get(parts[0].lower().lstrip("/"))
    if sid:
        return [sid]
    # atalhos compostos: "atualizar tudo" já resolvido; sync all
    if head in {"all", "tudo"}:
        return ["dist", "78", "31", "73", "455", "mapa"]
    return []


def begin_manual_sectors(sectors: list[str], *, detail: str = "iniciando…") -> None:
    """Marca setor(es) como run@3% e publica barrinhas."""
    global _MANUAL_RUNNING, _MANUAL_PROGRESS
    from ace_loop import publish_sector_bars
    from config import load_settings

    sectors = [s for s in sectors if s]
    if not sectors:
        _MANUAL_RUNNING = []
        return
    _MANUAL_RUNNING = list(dict.fromkeys(sectors))
    _MANUAL_PROGRESS = {
        sid: {"pct": 3.0, "state": "run", "detail": detail[:90]}
        for sid in _MANUAL_RUNNING
    }
    try:
        publish_sector_bars(
            load_settings(),
            _MANUAL_LAST_RUN,
            progress=_MANUAL_PROGRESS,
            running=list(_MANUAL_RUNNING),
            label="RUN",
            mode="RUN",
        )
    except Exception:
        pass


def end_manual_sectors(*, ok: bool = True, detail: str = "") -> None:
    """Fecha progresso manual (100% ou erro) e libera contexto."""
    global _MANUAL_RUNNING, _MANUAL_PROGRESS
    from ace_loop import publish_sector_bars
    from config import load_settings
    import time as _time

    if not _MANUAL_RUNNING and not _MANUAL_PROGRESS:
        return
    now = _time.time()
    for sid in list(_MANUAL_RUNNING) or list(_MANUAL_PROGRESS.keys()):
        slot = _MANUAL_PROGRESS.setdefault(sid, {})
        if ok:
            slot["pct"] = 100.0
            slot["state"] = "ok"
            slot["detail"] = (detail or "concluído")[:90]
            _MANUAL_LAST_RUN[sid] = now
        else:
            slot["state"] = "err"
            slot["pct"] = max(float(slot.get("pct") or 0), 1.0)
            slot["detail"] = (detail or "falhou")[:90]
    try:
        publish_sector_bars(
            load_settings(),
            _MANUAL_LAST_RUN,
            progress=_MANUAL_PROGRESS,
            running=[],
            label="OK" if ok else "ERR",
            mode="OK" if ok else "ERR",
        )
    except Exception:
        pass
    _MANUAL_RUNNING = []
    # mantém progress ok por um tick; próximo idle do CRT pode resetar


def _manual_status_tick(msg: str, *, kind: str = "work") -> None:
    """Atualiza % da(s) barrinha(s) a partir de uma linha de status."""
    from ace_loop import apply_status_to_progress, publish_sector_bars
    from config import load_settings
    from crt_bridge import publish

    if not _MANUAL_RUNNING and not _MANUAL_PROGRESS:
        # sem contexto: só status principal — NÃO limpa barrinhas (mode OK zerava sectors)
        online = kind != "err"
        label = "ONLINE" if online else "ERR"
        # Mantém RUN enquanto há trabalho; OK/ERR só no fim do comando (end_manual)
        mode = "ERR" if kind == "err" else "RUN"
        publish(online=online, label=label, pct=0, detail=msg[:100], mode=mode)
        return

    # garante slots
    for sid in _MANUAL_RUNNING:
        _MANUAL_PROGRESS.setdefault(
            sid, {"pct": 3.0, "state": "run", "detail": "executando…"}
        )
    apply_status_to_progress(_MANUAL_PROGRESS, msg, running=list(_MANUAL_RUNNING))
    publish_sector_bars(
        load_settings(),
        _MANUAL_LAST_RUN,
        progress=_MANUAL_PROGRESS,
        running=list(_MANUAL_RUNNING),
        label="RUN",
        mode="RUN",
    )


def _crt_boot(*, detail: str = "console pronta") -> None:
    try:
        from crt_bridge import spawn_crt, publish

        spawn_crt()
        publish(online=True, label="ONLINE", pct=0, detail=detail, mode="MENU")
    except Exception:
        pass


def draw_menu(payload: dict[str, Any], *, message: str = "") -> None:
    from term_brand import (
        _enable_windows_ansi,
        color_swatches,
        cubes_row,
        g,
        muted,
        print_header_banner,
        progress_bar,
        rule,
        status_idle,
        status_offline,
        status_online,
        status_work,
        w,
    )

    _clear()
    _enable_windows_ansi()
    modo = str(payload.get("periodo_modo") or "diario")
    sheets_on = bool(payload.get("enable_sheets"))
    viz_on = not bool(payload.get("headless", True))
    arm_on = bool(payload.get("armazem_in_loop", True))
    pend_on = bool(payload.get("pendencia_in_loop", True))
    ctr_on = bool(payload.get("contratacao_in_loop", True))
    emi_on = bool(payload.get("emissao_in_loop", False))
    mapa_on = bool(payload.get("mapa_in_loop", True))
    para_on = bool(payload.get("ciclo_paralelo", True))
    local_on = bool(payload.get("modo_local", False))

    print_header_banner(subtitle="OPERACIONAL · Console CMD", payload=payload)
    print(
        f"  {status_online('SHEETS') if sheets_on and not local_on else status_idle('SHEETS')}  "
        f"{status_work('SSW·VIZ') if viz_on else status_idle('SSW·HIDE')}  "
        f"{status_online('078') if arm_on else status_idle('078·OFF')}  "
        f"{status_online('031') if pend_on else status_idle('031·OFF')}  "
        f"{status_online('073') if ctr_on else status_idle('073·OFF')}  "
        f"{status_online('455') if emi_on else status_idle('455·OFF')}  "
        f"{status_online('MAPA') if mapa_on else status_idle('MAPA·OFF')}  "
        f"{status_online('PARA') if para_on else status_idle('SEQ')}  "
        f"{status_online('LOCAL') if local_on else status_idle('CLOUD')}"
    )
    print(f"  {rule()}")
    print(f"  {muted('config')}  {CONFIG_PATH}")
    print(f"  {muted('agora ')}  {datetime.now():%d/%m/%Y %H:%M:%S}")
    print(f"  {muted('ciclo ')}  {_periodo_hint(modo)}")
    print(f"  {rule()}")
    print(f"  {g('[SSW LOGIN]', bold=True)}")
    for key in ("url", "domain", "document", "user", "password", "unit"):
        _, _, secret = EDITABLE[key]
        print(f"    {muted(f'{key:<18}')} {_mask(str(payload.get(key, '')), secret)}")
    print(f"  {g('[AUTOMACAO]', bold=True)}")
    for key in (
        "coleta_option",
        "entrega_option",
        "periodo_modo",
        "auto_baixar_ao_abrir",
        "loop_intervalo",
    ):
        _, typ, secret = EDITABLE[key]
        val = payload.get(key, "")
        shown = _mask(str(val), secret) if typ == "str" else str(bool(val)).lower()
        print(f"    {muted(f'{key:<18}')} {shown}")
    try:
        from interval_parse import format_duration_long, parse_duration

        sec = parse_duration(str(payload.get("loop_intervalo") or "5m"))
        print(f"    {muted('intervalo'.ljust(18))} {format_duration_long(sec)}")
        mins = max(0.08, sec / 60.0)
        pct = min(100.0, (mins / 60.0) * 100.0)
        print(f"    {progress_bar(pct, width=22, label=format_duration_long(sec))}")
    except Exception:
        pass
    print(f"  {g('[SHEETS / DASH]', bold=True)}")
    for key in (
        "enable_sheets",
        "apps_script_url",
        "apps_script_token",
        "google_sheet_id",
        "publish_target",
        "google_sites_url",
        "enable_github_publish",
        "github_repo",
        "github_branch",
        "github_token_env",
    ):
        _, typ, secret = EDITABLE[key]
        val = payload.get(key, "")
        if typ == "bool":
            shown = str(bool(val)).lower()
        else:
            shown = _mask(str(val), secret)
            if key in {"apps_script_url", "google_sites_url"} and len(shown) > 48:
                shown = shown[:45] + "..."
        print(f"    {muted(key.ljust(18))} {shown}")
    try:
        from config import resolve_publish_target, load_settings

        print(
            f"    {muted('destino efetivo'.ljust(18))} "
            f"{resolve_publish_target(load_settings())}"
        )
    except Exception:
        pass
    print(f"  {g('[ARMAZEM 078]', bold=True)}")
    print(f"    {muted('armazem_in_loop'.ljust(18))} {str(arm_on).lower()}")
    print(f"  {g('[PENDENCIA 031]', bold=True)}")
    print(f"    {muted('pendencia_in_loop'.ljust(18))} {str(pend_on).lower()}")
    print(f"  {g('[CONTRATACAO 073]', bold=True)}")
    print(f"    {muted('contratacao_in_loop'.ljust(18))} {str(ctr_on).lower()}")
    print(f"  {g('[EMISSAO 455]', bold=True)}")
    print(f"    {muted('emissao_in_loop'.ljust(18))} {str(emi_on).lower()}")
    print(f"  {g('[MAPA OPERACIONAL]', bold=True)}")
    print(f"    {muted('mapa_in_loop'.ljust(18))} {str(mapa_on).lower()}")
    print(
        f"    {muted('mapa_intervalo'.ljust(18))} "
        f"{payload.get('mapa_intervalo') or '10m'}"
    )
    print(f"  {g('[PARALELO]', bold=True)}")
    print(f"    {muted('ciclo_paralelo'.ljust(18))} {str(para_on).lower()}")
    print(f"  {g('[MODO LOCAL]', bold=True)}")
    print(f"    {muted('modo_local'.ljust(18))} {str(local_on).lower()}  (sem Sheets; JSON em data/cache/local)")
    print(
        f"    {muted('dashboard_lan'.ljust(18))} "
        f"{str(bool(payload.get('dashboard_lan', False))).lower()}  "
        f"porta={payload.get('dashboard_port') or 8787}"
    )
    print(f"  {rule()}")
    print(f"  {w('CODIGOS', bold=True)}  {cubes_row()}")
    print(f"    Dist  {g('50')} coleta  {g('103')} torres  {g('36')} entrega  {g('225')} agenda")
    print(f"    Arm   {g('78')} patio   {g('177')} conf.   {g('607')} nomes   {g('sync78')}")
    print(f"    Pend  {g('31')} (10 cod.)  {g('sync31')}   Contr {g('73')}  {g('73 so73')}")
    print(f"    Emis  {g('455')} / {g('emissao')}  {g('sync455')}   Mapa {g('mapa')} (CyberMap)")
    print(f"    Sync  {g('sync')} dist   Loop {g('/automatica')}  {g('piloto_sites')}  {g('sites')}")
    print(f"    Local {g('local')}  {g('lan')}  {g('dash')}   Config {g('/e')}  {g('show')}  {g('help')}")
    print(f"    {g('/viz')} on|off   {g('brand')} ANSI   {g('crt')} CRT   {g('sair')}")
    print(f"    {muted('manual')}  docs/MANUAL.md  ·  {muted('sites')} docs/CONCEITO_SITES.md")
    print(f"  {rule('═')}")
    if message:
        low = message.lower()
        if "erro" in low or "falhou" in low:
            print(f"  {status_offline('MSG')}  {message}")
        elif any(x in low for x in ("ok", "pronto", "atualiz", "encerr", "ligado")):
            print(f"  {status_online('MSG')}  {message}")
        else:
            print(f"  {status_idle('MSG')}  {message}")
        print(f"  {rule()}")
    print(color_swatches())
    try:
        from crt_bridge import publish

        low = (message or "").lower()
        online = not ("erro" in low or "falhou" in low)
        publish(
            online=online,
            label="ONLINE" if online else "ERR",
            pct=0,
            detail=(message or "menu operacional")[:100],
            mode="MENU",
            title="BINHO · ACE",
        )
    except Exception:
        pass


def cmd_help() -> str:
    return "\n".join(
        [
            "=== AJUDA ACE · BINHO OPERACIONAL ===",
            "",
            "Digite o comando no prompt (ACE>) ou use os atalhos do painel.",
            "Menu (F2): Configuração · Automação · Local · TV · Gestão.",
            "",
            "────────────────────────────────────",
            "DISTRIBUIÇÃO (SSW)",
            "────────────────────────────────────",
            "  50            Baixa coletas do dia (SSW 0157) e atualiza o dashboard.",
            "  103           Torres / situação das coletas (limites e status).",
            "  36            Entregas / romaneios do dia.",
            "  225           Agendamentos (amanhã / agenda operacional).",
            "  sync          Só envia à planilha/site o que já está baixado",
            "                (50+103+36+225). Não abre o SSW de novo.",
            "",
            "────────────────────────────────────",
            "ARMAZÉM",
            "────────────────────────────────────",
            "  78            Pátio / veículos (SSW 078) — KPIs e torres do armazém.",
            "  177           Ranking de conferentes.",
            "  607           Atualiza nomes dos conferentes.",
            "  sync78        Envia só dados do armazém (078+177) sem baixar de novo.",
            "",
            "────────────────────────────────────",
            "PENDÊNCIA",
            "────────────────────────────────────",
            "  31            Puxa os 10 códigos de pendência (inclui SLA).",
            "  31 63 60      Só os códigos listados (ex.: 63=SLA+).",
            "  31 63,60      Mesmo efeito com vírgula.",
            "  sync31        Envia só a pendência à planilha/site.",
            "",
            "────────────────────────────────────",
            "CONTRATAÇÃO (hoje)",
            "────────────────────────────────────",
            "  73            073 → filiais 200 (frete). Monta visão por destino.",
            "  73 so73       Só a tela 073 (sem frete 200).",
            "  73 sem200     Sem manifesto 200.",
            "",
            "────────────────────────────────────",
            "EMISSÃO",
            "────────────────────────────────────",
            "  455           CTEs / frete / picos / expedidores do dia.",
            "  sync455       Envia só emissão à planilha/site.",
            "",
            "────────────────────────────────────",
            "RECICLAGEM",
            "────────────────────────────────────",
            "  reciclagem    Pipeline 019/081 (retorno / reciclagem).",
            "  019 / 081     Mesmo comando (aliases).",
            "",
            "────────────────────────────────────",
            "MAPA OPERACIONAL",
            "────────────────────────────────────",
            "  mapa          Puxa dados (36+) e monta rotas (CyberMap) para a TV.",
            "  /tempo mapa 50s",
            "                Define quanto tempo cada rota/placa fica na TV",
            "                (5s a 5m). Salva no layout e publica se Sheets ligado.",
            "  /tempo mapa   Mostra o tempo atual configurado.",
            "  Obs.: o tempo NÃO se altera no menu do dashboard — só no CRT.",
            "",
            "────────────────────────────────────",
            "AUTOMAÇÃO (loop contínuo)",
            "────────────────────────────────────",
            "  /automatica [intervalo]",
            "                Liga o ciclo contínuo (ex.: /automatica 5m).",
            "                Setores e tempos: Configurações → Automação.",
            "  parar         Interrompe comando em andamento e o loop.",
            "  Em Automação você marca: Distribuição, Armazém, Pendência,",
            "  Contratação, Emissão, Reciclagem, Mapa — e o intervalo de cada um.",
            "  Tema do CRT: Configurações (sidebar) → Aparência do painel",
            "  (Escuro, Verde BINHO, Azul painel, Verde ops, Claro, Escuro fosco).",
            "  A logo das dashboards muda em Configurações → Branding.",
            "",
            "────────────────────────────────────",
            "PUBLICAÇÃO (Sites / GitHub / local)",
            "────────────────────────────────────",
            "  /status       Situação da publicação (destino, Sheets, GitHub).",
            "  /push [msg]   Publica no GitHub Pages (se habilitado).",
            "  /pull         Traz atualizações do repositório.",
            "  dash          Gera/atualiza arquivos locais do dashboard.",
            "  piloto_sites  Liga Sheets, desliga GitHub, destino=sites.",
            "  sites         Abre a URL do Google Sites configurada.",
            "  Destino: /e publish_target sites|github|local|auto",
            "",
            "────────────────────────────────────",
            "LOCAL / REDE (LAN)",
            "────────────────────────────────────",
            "  local [tela]  Abre telas internas (coleta, entrega, armazem…).",
            "  lan           Lista URLs do dashboard na rede local.",
            "  /e modo_local true     Não envia Sheets (só cache local).",
            "  /e dashboard_lan true  Serve dashboard na LAN.",
            "  /e dashboard_port 8787 Porta fixa (0 = automática).",
            "",
            "────────────────────────────────────",
            "CONFIGURAÇÃO",
            "────────────────────────────────────",
            "  Configurações (sidebar) → Configuração",
            "    · Login SSW (URL, empresa, usuário, senha…)",
            "    · Planilha / Sites / GitHub",
            "    · Modo local e LAN",
            "    · Logo das dashboards (arquivo, URL, publicar, ocultar)",
            "  /e campo valor   Altera um campo (ex.: /e loop_intervalo 5m)",
            "  /e               Lista todos os campos editáveis e valores.",
            "  Bool: true/false | sim/nao | 1/0",
            "  Intervalos: 30s | 5m | 1h | 2d  (mín. 5s, máx. 30d)",
            "  periodo_modo: diario | sexta",
            "",
            "────────────────────────────────────",
            "TV / PAREDE",
            "────────────────────────────────────",
            "  Configurações → TV  Editor da parede (setores por TV, logo, margens).",
            "  /tempo mapa   Tempo de troca de rota no mapa (ver acima).",
            "  Na TV: Ctrl+F5 após mudar layout ou tempo.",
            "",
            "────────────────────────────────────",
            "UTILITÁRIOS DO PAINEL",
            "────────────────────────────────────",
            "  /log          Mostra o console detalhado (em vez das barrinhas).",
            "  /bars         Volta para as barrinhas por setor.",
            "  limpar / cls  Limpa o histórico do log na tela.",
            "  /viz on|off   Mostra ou oculta o navegador (headless).",
            "  show / config Lista a configuração atual no console.",
            "  gui           Abre o painel gráfico legado (se houver).",
            "  bloquear      Trava o CRT com cadeado (automação continua).",
            "                Senha: Configurações → Bloqueio do painel.",
            "  F2            Abre/fecha o Menu.",
            "  F11 / Esc     Entra/sai da tela cheia.",
            "  help / /help  Esta ajuda.",
            "",
            "Manual completo: docs/MANUAL.md",
            "Conceito Sites: docs/CONCEITO_SITES.md",
        ]
    )



def cmd_edit(payload: dict[str, Any], parts: list[str]) -> str:
    if len(parts) == 1:
        lines = ["Campos editaveis (/e campo valor):"]
        for key, (group, typ, secret) in EDITABLE.items():
            cur = payload.get(key, "")
            shown = _mask(str(cur), secret) if typ != "bool" else str(bool(cur)).lower()
            lines.append(f"  [{group}] {key} ({typ}) = {shown}")
        return "\n".join(lines)

    key = parts[1].strip().lower()
    # aliases curtos
    aliases = {
        "senha": "password",
        "usuario": "user",
        "unidade": "unit",
        "dominio": "domain",
        "cpf": "document",
        "modo": "periodo_modo",
        "sheets": "enable_sheets",
        "token": "apps_script_token",
        "script": "apps_script_url",
        "repo": "github_repo",
        "destino": "publish_target",
        "publish_target": "publish_target",
        "sites_url": "google_sites_url",
        "google_sites_url": "google_sites_url",
        "opcao": "coleta_option",
        "auto": "auto_baixar_ao_abrir",
        "intervalo": "loop_intervalo",
        "interval": "loop_intervalo",
        "tempo": "loop_intervalo",
        "loop": "loop_intervalo",
        "dist_loop": "dist_in_loop",
        "armazem_loop": "armazem_in_loop",
        "pendencia_loop": "pendencia_in_loop",
        "pend_loop": "pendencia_in_loop",
        "31_loop": "pendencia_in_loop",
        "contratacao_loop": "contratacao_in_loop",
        "ctr_loop": "contratacao_in_loop",
        "73_loop": "contratacao_in_loop",
        "emissao_loop": "emissao_in_loop",
        "455_loop": "emissao_in_loop",
        "mapa_loop": "mapa_in_loop",
        "map_loop": "mapa_in_loop",
        "reciclagem_loop": "reciclagem_in_loop",
        "recicla_loop": "reciclagem_in_loop",
        "019_loop": "reciclagem_in_loop",
        "081_loop": "reciclagem_in_loop",
        "dist_tempo": "dist_intervalo",
        "armazem_tempo": "armazem_intervalo",
        "pendencia_tempo": "pendencia_intervalo",
        "contratacao_tempo": "contratacao_intervalo",
        "emissao_tempo": "emissao_intervalo",
        "mapa_tempo": "mapa_intervalo",
        "paralelo": "ciclo_paralelo",
        "parallel": "ciclo_paralelo",
        "ciclo_paralelo": "ciclo_paralelo",
        "local_mode": "modo_local",
        "modo_local": "modo_local",
        "sem_planilha": "modo_local",
        "lan_rede": "dashboard_lan",
        "dashboard_lan": "dashboard_lan",
        "porta_dash": "dashboard_port",
        "dashboard_port": "dashboard_port",
        "visualizar": "visualizar",
        "viz": "visualizar",
        "mostrar": "visualizar",
        "mostrar_navegador": "visualizar",
        "janela": "visualizar",
    }
    key = aliases.get(key, key)

    # visualizar = inverso de headless (mais intuitivo)
    if key == "visualizar":
        if len(parts) >= 3:
            raw = " ".join(parts[2:]).strip()
        else:
            atual = not bool(payload.get("headless", True))
            print(f"Atual: visualizacao={'ON' if atual else 'OFF'} (headless={payload.get('headless')})")
            raw = input("Novo valor (on/off | sim/nao): ").strip()
            if raw == "":
                return "visualizar mantido."
        try:
            want_viz = _parse_bool(raw)
        except ValueError as err:
            return str(err)
        payload["headless"] = not want_viz
        _save_payload(payload)
        return (
            f"visualizar={'ON' if want_viz else 'OFF'} · "
            f"headless={str(payload['headless']).lower()} "
            f"({'janela oculta' if payload['headless'] else 'janela visivel'})"
        )

    if key not in EDITABLE:
        return f"Campo desconhecido: {key}. Digite /e para listar."

    _, typ, secret = EDITABLE[key]
    if len(parts) >= 3:
        raw = " ".join(parts[2:]).strip()
    else:
        atual = payload.get(key, "")
        prompt = f"Novo valor para {key}"
        if secret:
            # getpass evita eco da senha
            print(f"Atual: {_mask(str(atual), True)}")
            raw = getpass.getpass(f"{prompt} (oculto): ").strip()
            if raw == "":
                # permite limpar? pede confirmacao simples
                conf = input("Em branco = manter atual. Digite LIMPAR para apagar: ").strip()
                if conf.upper() == "LIMPAR":
                    raw = ""
                else:
                    return f"{key} mantido."
        else:
            print(f"Atual: {atual}")
            raw = input(f"{prompt}: ").strip()
            if raw == "" and typ != "bool":
                return f"{key} mantido (vazio)."

    if typ == "bool":
        try:
            payload[key] = _parse_bool(raw)
        except ValueError as err:
            return str(err)
    elif typ == "int":
        try:
            payload[key] = int(str(raw).strip())
        except ValueError:
            return f"{key} deve ser um número inteiro (ex.: 8787)"
    else:
        if key == "periodo_modo":
            v = raw.lower()
            if v not in {"diario", "sexta"}:
                return "periodo_modo deve ser: diario | sexta"
            payload[key] = v
        elif key == "loop_intervalo" or key.endswith("_intervalo"):
            from interval_parse import format_duration, parse_duration

            text = raw.strip()
            if not text and key != "loop_intervalo":
                payload[key] = ""
            else:
                try:
                    sec = parse_duration(text or "5m")
                except ValueError as err:
                    return str(err)
                payload[key] = format_duration(sec)
        elif key == "unit":
            # Aceita varias siglas: SPO,LEO,RIS | * = todas
            payload[key] = raw.strip().upper().replace(" ", "")
        elif key == "publish_target":
            v = raw.strip().lower()
            aliases_pt = {
                "site": "sites",
                "googlesites": "sites",
                "gh": "github",
                "pages": "github",
                "lan": "local",
                "offline": "local",
            }
            v = aliases_pt.get(v, v)
            if v not in {"sites", "github", "local", "auto"}:
                return "publish_target deve ser: sites | github | local | auto"
            payload[key] = v
        elif key in {"domain", "user"}:
            payload[key] = raw.strip()
        else:
            payload[key] = raw

    _save_payload(payload)
    shown = _mask(str(payload[key]), secret) if typ != "bool" else str(payload[key]).lower()
    if key == "loop_intervalo" or key.endswith("_intervalo"):
        from interval_parse import format_duration_long, parse_duration

        try:
            raw_iv = str(payload.get(key) or "").strip()
            if raw_iv:
                shown = f"{raw_iv} ({format_duration_long(parse_duration(raw_iv))})"
            else:
                shown = "(usa padrão)"
        except ValueError:
            pass
    return f"OK: {key} = {shown}"


def _cfg_headless() -> bool:
    return bool(load_settings().headless)


def cmd_viz(parts: list[str], payload: dict[str, Any]) -> str:
    """Ativa/desativa janela do Chromium na automacao SSW."""
    from config import CONFIG_PATH

    # Sem argumento = alterna ON/OFF
    if len(parts) == 1:
        want_viz = bool(payload.get("headless", True))  # se headless, liga viz
    else:
        raw = " ".join(parts[1:]).strip().lower()
        if raw in {"on", "1", "sim", "s", "true", "ligar", "ativa", "ativar", "mostrar", "ligado"}:
            want_viz = True
        elif raw in {
            "off", "0", "nao", "não", "n", "false", "desligar", "desativa",
            "desativar", "ocultar", "desligado",
        }:
            want_viz = False
        elif raw in {"toggle", "alt", "alternar", "trocar"}:
            want_viz = bool(payload.get("headless", True))
        else:
            return "Use: /viz   |  /viz on  |  /viz off"

    payload["headless"] = not want_viz
    _save_payload(payload)

    # Confere no disco (evita cache / outro config)
    disk = _load_payload()
    disk_headless = bool(disk.get("headless", True))
    on = not disk_headless
    return (
        f"OK: janela SSW={'LIGADA' if on else 'DESLIGADA'} "
        f"(headless={str(disk_headless).lower()}) · "
        f"salvo em {CONFIG_PATH}"
    )


def run_pipeline_50() -> str:
    from pipeline import run_full_pipeline

    print("\n=== Pipeline 50 ===")
    result = run_full_pipeline(on_status=_on_status, headless=_cfg_headless())
    tot = ((result.get("analysis") or {}).get("totais") or {})
    return f"50 OK · totais={tot}" if result else "50 concluido"


def run_pipeline_103() -> str:
    from pipeline import run_full_pipeline_103

    print("\n=== Pipeline 103 ===")
    result = run_full_pipeline_103(on_status=_on_status, headless=_cfg_headless())
    tot = ((result.get("analysis") or {}).get("totais") or {})
    return f"103 OK · totais={tot}"


def run_pipeline_36() -> str:
    from pipeline import run_full_pipeline_36

    print("\n=== Pipeline 36 (entregas) ===")
    result = run_full_pipeline_36(on_status=_on_status, headless=_cfg_headless())
    tot = ((result.get("analysis") or {}).get("totais") or {})
    return f"36 OK · totais={tot}"


def run_pipeline_225() -> str:
    from pipeline import run_full_pipeline_225

    print("\n=== Pipeline 225 (agendamentos mes corrente · arquivo R) ===")
    result = run_full_pipeline_225(on_status=_on_status, headless=_cfg_headless())
    tot = result.get("analysis") or {}
    return (
        f"225 OK · total={tot.get('total')} rota={tot.get('em_rota')} "
        f"parado={tot.get('parado')} concluido={tot.get('concluido')} alerta={tot.get('alerta')}"
    )


def run_pipeline_78_cmd() -> str:
    from pipeline import run_pipeline_78

    print("\n=== Pipeline 078 (Armazém) ===")
    result = run_pipeline_78(on_status=_on_status, headless=_cfg_headless())
    conf = result.get("177") or {}
    extra = ""
    if conf.get("ok"):
        extra = f" · 177 topo={conf.get('topo')} ({conf.get('total_conferentes')} conf.)"
    return (
        f"078 OK · linhas={result.get('total_linhas')} "
        f"veículos={result.get('total_veiculos')} "
        f"sheets={(result.get('sheets') or {}).get('ok')}"
        f"{extra}"
    )


def _parse_codes_31(extra: list[str] | None) -> list[str] | None:
    """Aceita `13,14`, `13 14` ou `13;14`."""
    if not extra:
        return None
    raw = " ".join(extra).replace(";", ",").replace("|", ",")
    codes = [c.strip() for c in re.split(r"[\s,]+", raw) if c.strip()]
    return codes or None


def run_pipeline_31_cmd(extra: list[str] | None = None) -> str:
    """`31` = todos os códigos; `31 13,14` ou `31 13 14` = só esses."""
    from ocorrencias_pendencia import OCORR_PENDENCIA_CODES
    from pipeline import run_pipeline_31

    codes = _parse_codes_31(extra)
    show = codes or list(OCORR_PENDENCIA_CODES)
    print("\n=== Pipeline 031 (Pendência) ===")
    print(f"  códigos ({len(show)}): {', '.join(show)}")
    result = run_pipeline_31(on_status=_on_status, headless=_cfg_headless(), codes=codes)
    resumo = result.get("resumo") or {}
    return (
        f"031 OK · CTRCs={result.get('total')} "
        f"SLA={resumo.get('sla_pct')}% "
        f"(+{resumo.get('solucionadas')} / −{resumo.get('abertas')}) "
        f"ofensor={resumo.get('topo_codigo')} ({resumo.get('topo_qtd')}) "
        f"sheets={(result.get('sheets') or {}).get('ok')}"
    )


def run_pipeline_455_cmd(extra: list[str] | None = None) -> str:
    """`455` / `emissao` — Fretes Expedidos/Recebidos → painel Emissão."""
    from pipeline import run_pipeline_455

    extra = extra or []
    unidade = "SPO"
    for x in extra:
        t = str(x).strip().upper()
        if t in {"SPO", "LEO", "RIS", "GRU"}:
            unidade = t
            break
    print("\n=== Pipeline 455 (Emissão) ===")
    print(f"  unidade={unidade} · tipo=E(expedidora) · arquivo=E · período=emissão hoje")
    result = run_pipeline_455(
        on_status=_on_status, headless=_cfg_headless(), unidade=unidade
    )
    resumo = result.get("resumo") or {}
    return (
        f"455 OK · CTEs={resumo.get('ctes')} "
        f"frete={resumo.get('frete_fmt')} "
        f"dia={resumo.get('dia')} noite={resumo.get('noite')} "
        f"cancel={resumo.get('cancelados')}"
    )


def run_pipeline_reciclagem_cmd(extra: list[str] | None = None) -> str:
    """`reciclagem` / `019` / `081` — Sem transferência + Sem saída."""
    from pipeline import run_pipeline_reciclagem

    print("\n=== Pipeline Reciclagem (019 + 081) ===")
    result = run_pipeline_reciclagem(on_status=_on_status, headless=_cfg_headless())
    return (
        f"reciclagem OK · 019={result.get('total_019')} CTRCs · "
        f"081={result.get('total_081')} CTRCs"
    )


def run_pipeline_contratacao_cmd(extra: list[str] | None = None) -> str:
    """`73` / `contratacao` · 073 + 200 por destino (sem 076). `73 so73` só 073."""
    from pipeline import run_pipeline_contratacao

    extra = extra or []
    # 076 desligado por padrão; `com76` reativa se precisar
    skip_076 = not any(str(x).lower() in {"com76", "com076", "com_76"} for x in extra)
    if any(str(x).lower() in {"so73", "só73", "skip76", "sem76"} for x in extra):
        skip_076 = True
    skip_200 = any(str(x).lower() in {"sem200", "skip200", "so73", "só73"} for x in extra)
    local_073: list[str] = []
    local_200: list[str] = []
    for x in extra:
        xl = str(x).lower()
        if xl in {
            "local",
            "so73",
            "só73",
            "skip76",
            "sem76",
            "sem200",
            "skip200",
            "com76",
            "com076",
            "com_76",
        }:
            continue
        p = Path(x)
        if not p.exists():
            continue
        name = p.name.lower()
        if "0644" in name or "manifesto" in name or "contratacao_200" in name:
            local_200.append(str(p))
        else:
            local_073.append(str(p))
    print("\n=== Pipeline Contratação (073 → filiais: 200) ===")
    if local_073 or local_200:
        if local_073:
            print(f"  073 local: {', '.join(local_073)}")
        if local_200:
            print(f"  200 local: {', '.join(local_200)}")
    else:
        print("  073: hoje · tela MOS · prop=T op=T · filtro carreteiro+transf no parser")
    if skip_076:
        print("  modo: sem 076 (só 200)")
    if skip_200:
        print("  modo: sem 200")
    result = run_pipeline_contratacao(
        on_status=_on_status,
        headless=_cfg_headless(),
        skip_076=skip_076,
        skip_200=skip_200,
        local_073=local_073 or None,
        local_200=local_200 or None,
    )
    resumo = result.get("resumo") or {}
    filiais = result.get("filiais") or []
    extra_msg = f" · filiais={','.join(filiais)}" if filiais else ""
    return (
        f"073/200 OK · veículos={resumo.get('total_veiculos')} "
        f"custo=R${resumo.get('custo_fmt')} frete=R${resumo.get('frete_fmt')} "
        f"· placas={len(result.get('placas') or [])}{extra_msg}"
    )


def run_sync_455() -> str:
    from publish_dashboard import publish_emissao_local
    from sheets_sync_455 import sync_sheets_455

    print("\n=== Sync Sheets Emissão 455 ===")
    r = sync_sheets_455(on_status=_on_status)
    publish_emissao_local(on_status=_on_status)
    if r.get("ok"):
        return (
            f"sync455 OK · expedidores={r.get('expedidores')} "
            f"horas={r.get('horas')}"
        )
    return f"sync455: {r.get('error') or r.get('reason') or r}"


def run_mapa_cmd() -> str:
    """Puxa coleta 50 + 103 + entrega 36 e monta o Mapa Operacional."""
    from pipeline import run_pipeline_mapa

    print("\n=== Mapa Operacional (50 + 103 + 36 + CyberMap) ===")
    result = run_pipeline_mapa(
        headless=_cfg_headless(),
        on_status=_on_status,
    )
    mapa = result.get("mapa") or {}
    tot = (mapa.get("payload") or {}).get("totais") or {}
    errs = result.get("errors") or {}
    extra = f" · erros={errs}" if errs else ""
    return (
        f"mapa OK · veiculos={tot.get('veiculos')} "
        f"E={tot.get('veiculos_entrega')} C={tot.get('veiculos_coleta')} "
        f"paradas={tot.get('paradas')} · "
        f"frete E R${tot.get('frete_entrega') or 0} · "
        f"frete C R${tot.get('frete_coleta') or 0}"
        f"{extra}"
    )


def run_sync_31() -> str:
    from publish_dashboard import publish_pendencia_local
    from sheets_sync_31 import sync_sheets_31

    print("\n=== Sync Sheets Pendência 031 ===")
    r = sync_sheets_31(on_status=_on_status)
    publish_pendencia_local(on_status=_on_status)
    if r.get("ok"):
        return f"sync31 OK · pendencias={r.get('pendencias')} ofensores={r.get('ofensores')}"
    return f"sync31: {r.get('error') or r.get('reason') or r}"


def run_pipeline_177_cmd() -> str:
    from parser_ssw177 import analyze_report_177
    from publish_dashboard import publish_dashboard
    from sheets_sync_78 import sync_sheets_78
    from ssw_177 import download_report_177

    print("\n=== Pipeline 177 (Conferentes · mensal) ===")
    dl = download_report_177(headless=_cfg_headless(), on_status=_on_status)
    result = analyze_report_177(dl["path"], on_status=_on_status)
    publish_dashboard(on_status=_on_status)
    sheets = sync_sheets_78(on_status=_on_status)
    return (
        f"177 OK · conferentes={result.get('total_conferentes')} "
        f"topo={result.get('topo')} · nomes={result.get('nomes_resolvidos')} · "
        f"sheets={sheets.get('ok')}"
    )


def run_pipeline_0607_cmd() -> str:
    from parser_ssw0607 import analyze_report_0607
    from parser_ssw177 import analyze_report_177
    from publish_dashboard import publish_armazem_local
    from ssw_177 import _find_local_177

    print("\n=== Relação 0607 (login → nome) ===")
    r = analyze_report_0607(on_status=_on_status)
    extra = ""
    local177 = _find_local_177()
    if local177:
        a = analyze_report_177(local177, on_status=_on_status)
        publish_armazem_local(on_status=_on_status)
        extra = f" · 177 reaplicado topo={a.get('topo')} nomes={a.get('nomes_resolvidos')}"
    else:
        publish_armazem_local(on_status=_on_status)
    return f"0607 OK · {r.get('total')} cadastro(s){extra}"


def run_sync_78() -> str:
    from sheets_sync_78 import sync_sheets_78

    print("\n=== Sync Sheets Armazém 078 ===")
    r = sync_sheets_78(on_status=_on_status)
    if r.get("ok"):
        conf = r.get("conferentes")
        extra = f" · conferentes={conf}" if conf is not None else ""
        return f"sync78 OK · veiculos={r.get('veiculos')} resumo={r.get('resumo')}{extra}"
    return f"sync78: {r.get('error') or r.get('reason') or r}"


def run_sync() -> str:
    from sheets_sync import (
        sync_google_sheets,
        sync_google_sheets_103,
        sync_google_sheets_36,
        sync_google_sheets_225,
    )

    print("\n=== Sync Sheets 50 ===")
    r50 = sync_google_sheets(on_status=_on_status)
    print("\n=== Sync Sheets 103 ===")
    r103 = sync_google_sheets_103(on_status=_on_status)
    print("\n=== Sync Sheets 36 ===")
    r36 = sync_google_sheets_36(on_status=_on_status)
    print("\n=== Sync Sheets 225 ===")
    r225 = sync_google_sheets_225(on_status=_on_status)
    return (
        f"sync 50={r50.get('ok')} 103={r103.get('ok')} "
        f"36={r36.get('ok')} 225={r225.get('ok')}"
    )


def run_dash() -> str:
    from publish_dashboard import publish_dashboard

    r = publish_dashboard(on_status=_on_status)
    target = r.get("publish_target") or ""
    extra = f" destino={target}" if target else ""
    return (
        f"dashboard ok={r.get('ok')} pushed={r.get('pushed')}"
        f" skipped_push={r.get('skipped_push')}{extra}"
    )


def apply_piloto_sites(payload: dict[str, Any] | None = None) -> str:
    """Liga fluxo Sheets→Sites e desliga GitHub Pages (piloto TV)."""
    payload = dict(payload or _load_payload())
    payload["modo_local"] = False
    payload["enable_sheets"] = True
    payload["enable_github_publish"] = False
    payload["publish_target"] = "sites"
    _save_payload(payload)
    url = str(payload.get("google_sites_url") or "").strip()
    lines = [
        "Piloto Google Sites aplicado:",
        "  modo_local=false",
        "  enable_sheets=true",
        "  enable_github_publish=false",
        "  publish_target=sites",
        "",
        "Proximos passos:",
        "  1) Sites embutindo Resumo103 / ResumoDiario (docs/CONCEITO_SITES.md secao 6)",
        "  2) /e google_sites_url <URL publica>",
        "  3) 103  ->  sync  ->  sites  (medir latencia vs /push)",
    ]
    if url:
        lines.append(f"  URL ja salva: {url}")
    else:
        lines.append("  (google_sites_url ainda vazio)")
    return "\n".join(lines)


def run_sites(open_browser: bool = True) -> str:
    """Mostra destino de publicação e abre o Google Sites se houver URL."""
    from config import load_settings, resolve_publish_target

    cfg = load_settings()
    target = resolve_publish_target(cfg)
    url = str(getattr(cfg, "google_sites_url", "") or "").strip()
    lines = [
        f"publish_target (config) = {getattr(cfg, 'publish_target', 'auto')}",
        f"destino efetivo         = {target}",
        f"enable_sheets           = {str(bool(cfg.enable_sheets)).lower()}",
        f"enable_github_publish   = {str(bool(cfg.enable_github_publish)).lower()}",
        f"modo_local              = {str(bool(cfg.modo_local)).lower()}",
        f"google_sites_url        = {url or '(vazio)'}",
        "Conceito: docs/CONCEITO_SITES.md",
    ]
    if open_browser and url:
        try:
            import webbrowser

            webbrowser.open(url)
            lines.append("Abrindo Google Sites no navegador…")
        except Exception as err:  # noqa: BLE001
            lines.append(f"Não abriu o navegador: {err}")
    elif open_browser and not url:
        lines.append("Defina a URL: /e google_sites_url https://sites.google.com/...")
    return "\n".join(lines)


def run_local(tokens: list[str] | None = None) -> str:
    """Abre telas do dashboard em modo local (sem GitHub)."""
    from ace_local_view import open_local_screens, screen_label

    result = open_local_screens(
        tokens,
        parent=None,
        refresh=True,
        prefer_embed=True,
        on_status=_on_status,
    )
    labels = ", ".join(screen_label(s) for s in (result.get("screens") or []))
    mode = "janelas Qt" if result.get("embed") else "navegador"
    return (
        f"local ok · {mode} · porta={result.get('port')} · {labels}"
    )


def run_lan(tokens: list[str] | None = None) -> str:
    """Liga servidor na LAN e lista URLs por setor (mesma rede Wi‑Fi)."""
    from ace_local_view import screen_label
    from dashboard_server import ensure_dashboard_server, get_lan_ip, lan_urls_by_screen, server_info
    from publish_dashboard import publish_dashboard

    payload = _load_payload()
    payload["dashboard_lan"] = True
    if not payload.get("dashboard_port"):
        payload["dashboard_port"] = 8787
    _save_payload(payload)
    publish_dashboard(on_status=_on_status, allow_push=False)
    port = ensure_dashboard_server(lan=True, restart_if_needed=True)
    urls = lan_urls_by_screen(port)
    info = server_info()
    lines = [
        f"LAN ligada · PC={get_lan_ip()} · porta={port} · bind={info.get('bind')}",
        f"Base: {info.get('lan_url')}/index.html",
        "IMPORTANTE: use IP + PORTA (não só o IP).",
        f"Exemplo coleta: {info.get('lan_url')}/index.html#tv/distribuicao/coleta",
        "No celular/TV (mesma Wi‑Fi), abra uma URL:",
    ]
    wanted = {t.strip().lower() for t in (tokens or []) if str(t).strip()}
    for sid, url in urls.items():
        if wanted and sid not in wanted and not any(w in sid for w in wanted):
            continue
        lines.append(f"  {screen_label(sid)}: {url}")
    lines.append("Firewall do Windows pode pedir permissão na 1ª vez.")
    return "\n".join(lines)


def run_gui() -> str:
    import subprocess

    print("Abrindo ACE grafico...")
    subprocess.Popen([sys.executable, "app.py"], cwd=str(CONFIG_PATH.parent.parent))
    return "GUI iniciada em processo separado."


def run_automatica_cmd(interval_arg: str | None = None, *, return_to_menu: bool = True) -> str:
    """Modo /automatica: ciclo dual 50+103 ate fechar (Ctrl+C)."""
    from ace_loop import resolve_interval_sec, run_loop
    from interval_parse import format_duration_long
    from config import load_settings
    from term_brand import (
        _enable_windows_ansi,
        g,
        loading_screen,
        muted,
        print_header_banner,
        rule,
        status_idle,
        status_online,
        status_work,
    )

    cfg = load_settings()
    try:
        sec = resolve_interval_sec(interval_arg, settings_intervalo=cfg.loop_intervalo)
    except ValueError as err:
        return f"ERRO: {err}"

    _enable_windows_ansi()
    loading_screen(
        "ACE · boot /automatica",
        steps=["kernel", "periodos", "sheets bridge", "loop"],
        seconds=1.4,
    )
    print_header_banner(
        subtitle="/AUTOMATICA · ciclo contínuo",
        payload={
            "enable_sheets": cfg.enable_sheets,
            "headless": cfg.headless,
            "armazem_in_loop": cfg.armazem_in_loop,
            "pendencia_in_loop": getattr(cfg, "pendencia_in_loop", True),
            "contratacao_in_loop": getattr(cfg, "contratacao_in_loop", True),
            "emissao_in_loop": getattr(cfg, "emissao_in_loop", False),
            "mapa_in_loop": getattr(cfg, "mapa_in_loop", True),
            "ciclo_paralelo": getattr(cfg, "ciclo_paralelo", True),
            "loop_intervalo": cfg.loop_intervalo,
            "unit": getattr(load_credentials(), "unit", ""),
            "user": getattr(load_credentials(), "user", ""),
        },
    )
    print(f"  {status_work('LOOP')}  a cada {g(format_duration_long(sec), bold=True)}")
    print(f"  {status_idle('50')} coleta  ·  {status_idle('103')} limite  ·  {status_idle('36')} entrega")
    print(f"  {status_idle('225')} agendamentos do mês · arquivo R")
    if getattr(cfg, "armazem_in_loop", False):
        print(f"  {status_online('078')} Armazém no loop")
    else:
        print(f"  {status_idle('078')} fora do loop")
    if getattr(cfg, "pendencia_in_loop", False):
        print(f"  {status_online('031')} Pendência no loop")
    else:
        print(f"  {status_idle('031')} fora do loop (rode `31` sob demanda)")
    if getattr(cfg, "contratacao_in_loop", False):
        print(f"  {status_online('073')} Contratação no loop (filiais 200)")
    else:
        print(f"  {status_idle('073')} fora do loop (rode `73` sob demanda)")
    if getattr(cfg, "emissao_in_loop", False):
        print(f"  {status_online('455')} Emissão no loop")
    else:
        print(f"  {status_idle('455')} fora do loop (rode `455` sob demanda)")
    if getattr(cfg, "mapa_in_loop", True):
        iv = getattr(cfg, "mapa_intervalo", None) or "10m"
        print(f"  {status_online('MAPA')} Mapa Operacional no loop (36 · {iv})")
    else:
        print(f"  {status_idle('MAPA')} fora do loop (rode `mapa` sob demanda)")
    if getattr(cfg, "ciclo_paralelo", True):
        print(f"  {status_online('PARA')} ciclo paralelo (setores juntos)")
    else:
        print(f"  {status_idle('SEQ')} ciclo sequencial (/e ciclo_paralelo true)")
    print(f"  {rule()}")
    print(f"  {muted('Ctrl+C → menu · /viz on|off · /e intervalo 30s|5m')}")
    print(f"  {rule()}\n")
    try:
        run_loop(interval_sec=sec, headless=_cfg_headless(), once=False)
    except KeyboardInterrupt:
        print(f"\n  {status_idle('STOP')}  Modo automatica interrompido.")
    return "Modo /automatica encerrado."


def execute_line(raw: str, payload: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """
    Executa um comando ACE (mesmo motor do menu CMD).
    Retorna (mensagem, payload atualizado). Usado pelo painel CRT.
    """
    payload = dict(payload or _load_payload())
    text = (raw or "").strip()
    if not text:
        return ("", payload)

    parts = text.split()
    cmd = parts[0].lower()

    # Parar também funciona se digitado no motor CMD (além do CRT)
    if cmd in {"parar", "stop", "halt"}:
        try:
            from ace_stop import close_registered_browsers, kill_child_browsers, request_stop, stop_external_loop_process

            request_stop(force_browsers=True)
            close_registered_browsers()
            kill_child_browsers()
            stop_external_loop_process()
        except Exception:
            pass
        return ("Parado: sinal enviado a qualquer comando/processo ACE.", payload)

    try:
        from ace_stop import check_stop, clear_stop, LoopStopped

        # Novo comando: limpa flag antiga (exceto se o usuário acabou de pedir parar
        # no mesmo instante — clear_stop no CmdWorker já cuida disso).
        clear_stop()
        check_stop()
    except Exception:
        LoopStopped = Exception  # type: ignore

    sectors = sectors_for_command(text)
    if sectors:
        begin_manual_sectors(sectors, detail=f"exec · {text[:60]}")
    try:
        msg, payload = _execute_line_body(raw, payload, parts, cmd)
        low = (msg or "").lower()
        ok = "erro" not in low and "falhou" not in low and msg != ""
        if sectors:
            if msg == "__CLEAR__":
                end_manual_sectors(ok=True, detail="log limpo")
            else:
                end_manual_sectors(ok=ok, detail=(msg or "ok")[:90])
        return (msg, payload)
    except Exception as err:
        if sectors:
            end_manual_sectors(ok=False, detail=str(err)[:90])
        try:
            from ace_stop import LoopStopped as _LS, stop_requested

            if isinstance(err, _LS) or stop_requested() or "parado pelo usuário" in str(err).lower():
                return ("Parado pelo usuário.", _load_payload())
        except Exception:
            pass
        raise


def cmd_tempo_mapa(parts: list[str]) -> str:
    """`/tempo mapa 50s` — tempo de troca de rota/placa no Mapa Operacional (TV)."""
    from interval_parse import format_duration_long, parse_duration
    from tv_layout import load_layout, push_layout_to_sheets, save_layout

    toks = [str(p).strip().lower().lstrip("/") for p in (parts or []) if str(p).strip()]
    if len(toks) < 2 or toks[1] not in {"mapa", "map", "rota", "rotas", "placa", "placas"}:
        return (
            "Use: /tempo mapa 50s\n"
            "     /tempo mapa        (ver atual)\n"
            "Aliases: /tempo rota 30s · /tempo placa 1m"
        )

    lay = load_layout()
    cur_ms = int(lay.get("mapaRouteMs") or 15000)
    if len(toks) == 2:
        sec = max(1, cur_ms // 1000)
        return (
            f"Mapa · tempo de rota atual: {sec}s "
            f"({format_duration_long(sec)}).\n"
            f"Ex.: /tempo mapa 50s"
        )

    raw_iv = " ".join(toks[2:]).strip()
    try:
        sec = parse_duration(raw_iv, default_unit="s")
    except ValueError as err:
        return str(err)

    # Carrossel de rota: 5s–5min (evita travar TV com troca rápida demais)
    sec = max(5, min(300, int(sec)))
    ms = sec * 1000
    lay["mapaRouteMs"] = ms
    save_layout(lay)

    push_note = ""
    try:
        ok, msg = push_layout_to_sheets(lay)
        push_note = f" · planilha {'ok' if ok else 'falhou: ' + str(msg)}"
    except Exception as err:  # noqa: BLE001
        push_note = f" · planilha: {err}"

    return (
        f"Mapa · tempo de rota = {sec}s ({format_duration_long(sec)}). "
        f"Salvo no layout TV{push_note}. "
        f"Na TV: Ctrl+F5 (ou aguarde o layout recarregar)."
    )


def _execute_line_body(
    raw: str,
    payload: dict[str, Any],
    parts: list[str],
    cmd: str,
) -> tuple[str, dict[str, Any]]:
    if cmd in {"sair", "exit", "quit", "q"}:
        return ("Feche a janela CRT para sair do painel.", payload)
    if cmd in {"help", "/help", "?", "h"}:
        return (cmd_help(), payload)
    if cmd in {"/tempo", "tempo"}:
        return (cmd_tempo_mapa(parts), payload)
    if cmd in {"/viz", "viz", "/visualizar", "visualizar"}:
        msg = cmd_viz(parts, payload)
        return (msg, _load_payload())
    if cmd in {"/e", "/edit", "e"}:
        if cmd == "e":
            parts = ["/e"] + parts[1:]
        msg = cmd_edit(payload, parts)
        return (msg, _load_payload())
    if cmd in {"1", "50", "/50"}:
        return (run_pipeline_50(), _load_payload())
    if cmd in {"2", "103", "/103"}:
        return (run_pipeline_103(), _load_payload())
    if cmd in {"36", "/36", "entrega", "/entrega"}:
        return (run_pipeline_36(), _load_payload())
    if cmd in {"225", "/225", "agenda", "/agenda", "agendamento"}:
        return (run_pipeline_225(), _load_payload())
    if cmd in {"78", "/78", "armazem", "/armazem"}:
        return (run_pipeline_78_cmd(), _load_payload())
    if cmd in {"31", "/31", "pendencia", "/pendencia", "pendencias"}:
        return (run_pipeline_31_cmd(parts[1:] if len(parts) > 1 else None), _load_payload())
    if cmd in {"455", "/455", "emissao", "/emissao", "emissão", "/emissão"}:
        return (run_pipeline_455_cmd(parts[1:] if len(parts) > 1 else None), _load_payload())
    if cmd in {
        "reciclagem",
        "/reciclagem",
        "recicla",
        "019",
        "/019",
        "19",
        "/19",
        "081",
        "/081",
        "81",
        "/81",
    }:
        return (run_pipeline_reciclagem_cmd(parts[1:] if len(parts) > 1 else None), _load_payload())
    if cmd in {"73", "/73", "76", "/76", "contratacao", "/contratacao", "contratação"}:
        return (run_pipeline_contratacao_cmd(parts[1:] if len(parts) > 1 else None), _load_payload())
    if cmd in {"sync31", "/sync31", "sheets31"}:
        return (run_sync_31(), payload)
    if cmd in {"sync455", "/sync455", "sheets455", "syncemissao"}:
        return (run_sync_455(), payload)
    if cmd in {"177", "/177", "conferentes", "/conferentes"}:
        return (run_pipeline_177_cmd(), _load_payload())
    if cmd in {"607", "/607", "0607", "/0607", "nomes", "conferentes_nomes"}:
        return (run_pipeline_0607_cmd(), _load_payload())
    if cmd in {"mapa", "/mapa", "mapaop", "maparotas", "cybermap"}:
        return (run_mapa_cmd(), payload)
    if cmd in {"sync78", "/sync78", "sheets78"}:
        return (run_sync_78(), payload)
    if cmd in {"3", "sync", "/sync"}:
        return (run_sync(), payload)
    if cmd in {"4", "dash", "/dash", "dashboard"}:
        return (run_dash(), payload)
    if cmd in {"sites", "/sites", "googlesites", "site"}:
        return (run_sites(open_browser=True), payload)
    if cmd in {"piloto_sites", "/piloto_sites", "piloto", "pilotosites"}:
        return (apply_piloto_sites(payload), _load_payload())
    if cmd in {"local", "/local", "tvlocal", "dashlocal", "telas"}:
        return (run_local(parts[1:] if len(parts) > 1 else None), payload)
    if cmd in {"lan", "/lan", "rede", "wifi"}:
        return (run_lan(parts[1:] if len(parts) > 1 else None), payload)
    if cmd in {"5", "gui", "/gui", "app"}:
        return (run_gui(), payload)
    if cmd in {
        "7",
        "loop",
        "/loop",
        "watch",
        "automatica",
        "/automatica",
        "automática",
        "/automática",
        "auto",
        "/auto",
    }:
        # Sem janela extra: loop em processo oculto; status vai para o histórico do CRT
        iv = _parse_interval_arg(parts)
        args = [sys.executable, "-u", str(Path(__file__).resolve().parent / "ace_cmd.py"), "automatica"]
        if iv:
            args.append(iv)
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            from ace_stop import clear_stop, write_loop_pid

            clear_stop()
        except Exception:
            pass
        proc = subprocess.Popen(
            args,
            cwd=str(Path(__file__).resolve().parent),
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            from ace_stop import write_loop_pid

            write_loop_pid(proc.pid)
        except Exception:
            pass
        return (
            f"Atualização contínua iniciada em segundo plano (pid={proc.pid}, {iv or 'intervalo config'}). "
            "No CRT use Parar / digite parar para encerrar.",
            payload,
        )
    if cmd in {"8", "status", "/status", "git", "/git"}:
        return (run_git_status(), payload)
    if cmd in {
        "9",
        "push",
        "/push",
        "atualizar",
        "/atualizar",
        "update",
        "/update",
        "github",
        "/github",
        "subir",
        "/subir",
    }:
        return (run_git_push(parts), payload)
    if cmd in {"pull", "/pull", "baixar", "/baixar"}:
        return (run_git_pull(), payload)
    if cmd in {"brand", "/brand", "logo", "/logo", "cubos", "/cubos"}:
        return ("Use o CMD (`brand`) para demo ANSI — CRT já mostra a logo.", payload)
    if cmd in {"crt", "/crt", "crtpanel", "painel"}:
        return ("Painel CRT já está aberto.", payload)
    if cmd in {"6", "show", "/show", "config"}:
        return (show_config(payload), payload)
    if cmd in {"cls", "clear", "limpar", "/limpar", "/cls", "/clear"}:
        try:
            from crt_bridge import clear_log

            clear_log()
        except Exception:
            pass
        return ("__CLEAR__", payload)
    return (f"Comando desconhecido: {raw}. Digite help", payload)


def run_git_status() -> str:
    from git_sync import git_status

    return git_status(on_status=_on_status)


def run_git_push(parts: list[str] | None = None) -> str:
    from git_sync import git_push

    msg = " ".join((parts or [])[1:]).strip()
    return git_push(msg, on_status=_on_status)


def run_git_pull() -> str:
    from git_sync import git_pull

    return git_pull(on_status=_on_status)


def show_config(payload: dict[str, Any]) -> str:
    lines = ["Config atual:"]
    for key, (_, typ, secret) in EDITABLE.items():
        val = payload.get(key, "")
        if typ == "bool":
            lines.append(f"  {key}={str(bool(val)).lower()}")
        else:
            lines.append(f"  {key}={_mask(str(val), secret)}")
    return "\n".join(lines)


def _parse_interval_arg(parts: list[str] | None) -> str | None:
    """Intervalo opcional após o comando. Ex.: ['/automatica','5m'] → '5m'; ['auto','5'] → '5m'."""
    if not parts or len(parts) < 2:
        return None
    raw = " ".join(str(p) for p in parts[1:]).strip()
    if not raw:
        return None
    # Compat CLI antigo: só número = minutos (ace.bat /automatica 5)
    if raw.isdigit():
        return f"{raw}m"
    return raw


def _is_automatica_token(token: str) -> bool:
    t = token.strip().lower().lstrip("/")
    return t in {"automatica", "automática", "auto", "loop", "watch"}


def _is_push_token(token: str) -> bool:
    t = token.strip().lower().lstrip("/")
    return t in {"push", "atualizar", "update", "github", "subir"}


def _is_status_token(token: str) -> bool:
    t = token.strip().lower().lstrip("/")
    return t in {"status", "git"}


def _is_pull_token(token: str) -> bool:
    t = token.strip().lower().lstrip("/")
    return t in {"pull", "baixar"}


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":
        try:
            os.system("chcp 65001 >nul")
        except Exception:
            pass
    ensure_dirs()

    args = list(argv if argv is not None else sys.argv[1:])
    # ace.bat /automatica  [minutos]
    if args and _is_automatica_token(args[0]):
        run_automatica_cmd(_parse_interval_arg(args), return_to_menu=False)
        return 0
    # ace.bat /push [mensagem]
    if args and _is_push_token(args[0]):
        print(run_git_push(args))
        return 0
    if args and _is_status_token(args[0]):
        print(run_git_status())
        return 0
    if args and _is_pull_token(args[0]):
        print(run_git_pull())
        return 0
    if args and args[0].lstrip("/").lower() in {"78", "armazem", "once78"}:
        print(run_pipeline_78_cmd())
        return 0
    if args and args[0].lstrip("/").lower() in {"31", "pendencia", "pendencias", "once31"}:
        print(run_pipeline_31_cmd(args[1:] if len(args) > 1 else None))
        return 0
    if args and args[0].lstrip("/").lower() in {
        "reciclagem",
        "recicla",
        "019",
        "081",
        "19",
        "81",
        "oncerec",
    }:
        print(run_pipeline_reciclagem_cmd(args[1:] if len(args) > 1 else None))
        return 0
    if args and args[0].lstrip("/").lower() in {"177", "conferentes", "once177"}:
        print(run_pipeline_177_cmd())
        return 0
    if args and args[0].lstrip("/").lower() in {"607", "0607", "nomes"}:
        print(run_pipeline_0607_cmd())
        return 0
    if args and args[0].lstrip("/").lower() in {"mapa", "mapaop", "maparotas", "cybermap"}:
        print(run_mapa_cmd())
        return 0
    if args and args[0].lstrip("/").lower() in {"viz", "visualizar"}:
        payload = _load_payload()
        print(cmd_viz(args, payload))
        return 0
    if args and args[0].lstrip("/").lower() in {"sync78", "sheets78"}:
        print(run_sync_78())
        return 0
    if args and args[0].lstrip("/").lower() in {"sync31", "sheets31"}:
        print(run_sync_31())
        return 0
    if args and args[0].lstrip("/").lower() in {"sync455", "sheets455", "syncemissao"}:
        print(run_sync_455())
        return 0
    if args and args[0].lstrip("/").lower() in {"piloto_sites", "piloto", "pilotosites"}:
        print(apply_piloto_sites())
        return 0
    if args and args[0].lstrip("/").lower() in {"sites", "site", "googlesites"}:
        print(run_sites(open_browser=True))
        return 0
    if args and args[0].lstrip("/").lower() in {"brand", "logo", "cubos", "neofetch"}:
        from term_brand import main as brand_main

        sub = args[1:] if len(args) > 1 else ["demo"]
        return int(brand_main(sub) or 0)
    if args and args[0].lstrip("/").lower() in {"crt", "crtpanel", "painel"}:
        from ace_crt import main as crt_main

        return int(crt_main() or 0)

    payload = _load_payload()
    message = "Pronto. /push sobe Pages | /automatica | 78=Armazém | crt=painel"
    _crt_boot(detail=message)
    draw_menu(payload, message=message)

    while True:
        try:
            raw = input("ACE> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaindo...")
            return 0
        if not raw:
            draw_menu(payload, message=message)
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        try:
            if cmd in {"sair", "exit", "quit", "q"}:
                print("Ate logo.")
                return 0
            if cmd in {"help", "/help", "?", "h"}:
                message = cmd_help()
            elif cmd in {"/tempo", "tempo"}:
                message = cmd_tempo_mapa(parts)
            elif cmd in {"/viz", "viz", "/visualizar", "visualizar"}:
                message = cmd_viz(parts, payload)
                payload = _load_payload()
            elif cmd == "/e" or cmd == "/edit" or cmd == "e":
                # permite "e campo valor" tambem
                if cmd == "e":
                    parts = ["/e"] + parts[1:]
                message = cmd_edit(payload, parts)
                payload = _load_payload()
            elif cmd in {"1", "50", "/50"}:
                message = run_pipeline_50()
                payload = _load_payload()
            elif cmd in {"2", "103", "/103"}:
                message = run_pipeline_103()
                payload = _load_payload()
            elif cmd in {"36", "/36", "entrega", "/entrega"}:
                message = run_pipeline_36()
                payload = _load_payload()
            elif cmd in {"225", "/225", "agenda", "/agenda", "agendamento"}:
                message = run_pipeline_225()
                payload = _load_payload()
            elif cmd in {"78", "/78", "armazem", "/armazem"}:
                message = run_pipeline_78_cmd()
                payload = _load_payload()
            elif cmd in {"31", "/31", "pendencia", "/pendencia", "pendencias"}:
                message = run_pipeline_31_cmd(parts[1:] if len(parts) > 1 else None)
            elif cmd in {"455", "/455", "emissao", "/emissao", "emissão", "/emissão"}:
                message = run_pipeline_455_cmd(parts[1:] if len(parts) > 1 else None)
                payload = _load_payload()
            elif cmd in {
                "reciclagem",
                "/reciclagem",
                "recicla",
                "019",
                "/019",
                "19",
                "081",
                "/081",
                "81",
            }:
                message = run_pipeline_reciclagem_cmd(parts[1:] if len(parts) > 1 else None)
                payload = _load_payload()
            elif cmd in {"73", "/73", "76", "/76", "contratacao", "/contratacao", "contratação"}:
                message = run_pipeline_contratacao_cmd(parts[1:] if len(parts) > 1 else None)
                payload = _load_payload()
            elif cmd in {"177", "/177", "conferentes", "/conferentes"}:
                message = run_pipeline_177_cmd()
                payload = _load_payload()
            elif cmd in {"607", "/607", "0607", "/0607", "nomes", "conferentes_nomes"}:
                message = run_pipeline_0607_cmd()
                payload = _load_payload()
            elif cmd in {"mapa", "/mapa", "mapaop", "maparotas", "cybermap"}:
                message = run_mapa_cmd()
            elif cmd in {"sync78", "/sync78", "sheets78"}:
                message = run_sync_78()
            elif cmd in {"sync31", "/sync31", "sheets31"}:
                message = run_sync_31()
            elif cmd in {"sync455", "/sync455", "sheets455", "syncemissao"}:
                message = run_sync_455()
            elif cmd in {"3", "sync", "/sync"}:
                message = run_sync()
            elif cmd in {"4", "dash", "/dash", "dashboard"}:
                message = run_dash()
            elif cmd in {"sites", "/sites", "googlesites", "site"}:
                message = run_sites(open_browser=True)
            elif cmd in {"piloto_sites", "/piloto_sites", "piloto", "pilotosites"}:
                message = apply_piloto_sites()
            elif cmd in {"local", "/local", "tvlocal", "dashlocal", "telas"}:
                message = run_local(parts[1:] if len(parts) > 1 else None)
            elif cmd in {"lan", "/lan", "rede", "wifi"}:
                message = run_lan(parts[1:] if len(parts) > 1 else None)
            elif cmd in {"5", "gui", "/gui", "app"}:
                message = run_gui()
            elif cmd in {
                "7",
                "loop",
                "/loop",
                "watch",
                "automatica",
                "/automatica",
                "automática",
                "/automática",
                "auto",
                "/auto",
            }:
                message = run_automatica_cmd(_parse_interval_arg(parts))
                payload = _load_payload()
            elif cmd in {"8", "status", "/status", "git", "/git"}:
                message = run_git_status()
            elif cmd in {
                "9",
                "push",
                "/push",
                "atualizar",
                "/atualizar",
                "update",
                "/update",
                "github",
                "/github",
                "subir",
                "/subir",
            }:
                message = run_git_push(parts)
            elif cmd in {"pull", "/pull", "baixar", "/baixar"}:
                message = run_git_pull()
            elif cmd in {"brand", "/brand", "logo", "/logo", "cubos", "/cubos"}:
                from term_brand import demo as brand_demo

                brand_demo()
                message = "Visual ANSI (PNG→terminal)."
            elif cmd in {"crt", "/crt", "crtpanel", "painel"}:
                _crt_boot(detail="painel CRT reaberto")
                message = "Painel CRT de gestão aberto."
            elif cmd in {"6", "show", "/show", "config"}:
                message = show_config(payload)
            elif cmd in {"cls", "clear", "limpar", "/limpar", "/cls", "/clear"}:
                try:
                    from crt_bridge import clear_log

                    clear_log()
                except Exception:
                    pass
                message = ""
                _clear()
                draw_menu(payload, message="Log limpo.")
                continue
            else:
                message = f"Comando desconhecido: {raw}. Digite help"
        except Exception as err:  # noqa: BLE001
            message = f"ERRO: {err}"

        draw_menu(payload, message=message)


if __name__ == "__main__":
    # Garante imports relativos ao projeto
    os.chdir(str(CONFIG_PATH.parent.parent))
    if str(CONFIG_PATH.parent.parent) not in sys.path:
        sys.path.insert(0, str(CONFIG_PATH.parent.parent))
    raise SystemExit(main())
