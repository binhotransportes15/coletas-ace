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
import sys
from dataclasses import asdict
from datetime import datetime
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
    # Automacao
    "coleta_option": ("auto", "str", False),
    "entrega_option": ("auto", "str", False),
    "periodo_modo": ("auto", "str", False),  # diario | sexta
    "auto_baixar_ao_abrir": ("auto", "bool", False),
    "loop_intervalo": ("auto", "str", False),  # 30s | 5m | 1h | 2d
    # Sheets / dashboard
    "enable_sheets": ("cloud", "bool", False),
    "apps_script_url": ("cloud", "str", False),
    "apps_script_token": ("cloud", "str", True),
    "google_sheet_id": ("cloud", "str", False),
    "enable_github_publish": ("cloud", "bool", False),
    "github_repo": ("cloud", "str", False),
    "github_branch": ("cloud", "str", False),
    "github_token_env": ("cloud", "str", False),
    # Armazém 078 (mesmo Sheets da distribuição)
    "armazem_in_loop": ("armazem", "bool", False),
    "headless": ("auto", "bool", False),
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
        enable_sheets=bool(payload.get("enable_sheets", False)),
        apps_script_url=str(payload.get("apps_script_url") or ""),
        apps_script_token=str(payload.get("apps_script_token") or ""),
        google_sheet_id=str(payload.get("google_sheet_id") or ""),
        enable_github_publish=bool(payload.get("enable_github_publish", False)),
        github_repo=str(payload.get("github_repo") or ""),
        github_branch=str(payload.get("github_branch") or "main"),
        github_token_env=str(payload.get("github_token_env") or "GH_TOKEN"),
        armazem_in_loop=bool(payload.get("armazem_in_loop", True)),
        headless=bool(payload.get("headless", True)),
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


def draw_menu(payload: dict[str, Any], *, message: str = "") -> None:
    _clear()
    modo = str(payload.get("periodo_modo") or "diario")
    print("=" * 72)
    print("  ACE · Console CMD   |   digite /e campo valor   |   help   |   sair")
    print("=" * 72)
    print(f"  Arquivo: {CONFIG_PATH}")
    print(f"  Agora:   {datetime.now():%d/%m/%Y %H:%M:%S}")
    print(f"  Periodos auto: {_periodo_hint(modo)}")
    print("-" * 72)
    print("  [SSW LOGIN]")
    for key in ("url", "domain", "document", "user", "password", "unit"):
        _, _, secret = EDITABLE[key]
        print(f"    {key:<22} {_mask(str(payload.get(key, '')), secret)}")
    print("  [AUTOMACAO]")
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
        print(f"    {key:<22} {shown}")
    try:
        from interval_parse import format_duration_long, parse_duration

        sec = parse_duration(str(payload.get("loop_intervalo") or "5m"))
        print(f"    {'(intervalo)':<22} {format_duration_long(sec)}")
    except Exception:
        pass
    print("  [SHEETS / DASHBOARD · DISTRIBUIÇÃO]")
    for key in (
        "enable_sheets",
        "apps_script_url",
        "apps_script_token",
        "google_sheet_id",
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
            if key == "apps_script_url" and len(shown) > 48:
                shown = shown[:45] + "..."
        print(f"    {key:<22} {shown}")
    print("  [ARMAZÉM · 078]")
    for key in ("armazem_in_loop", "headless"):
        _, typ, secret = EDITABLE[key]
        val = payload.get(key, "")
        shown = str(bool(val)).lower() if typ == "bool" else _mask(str(val), secret)
        print(f"    {key:<28} {shown}")
    print("    (078 grava Veiculos78/Resumo78 na mesma planilha/Apps Script)")
    print("-" * 72)
    print("  ACOES RAPIDAS")
    print("    1 / 50      Baixar+analisar relatorio 50 (situacoes)")
    print("    2 / 103     Baixar+analisar relatorio 103 (tempo real)")
    print("    3 / sync    Sobe 50+103+36+225 para Sheets Distribuição")
    print("    4 / dash    Atualiza arquivos do dashboard local")
    print("    5 / gui     Abre o ACE grafico (app.py)")
    print("    6 / show    Mostra config completa (senha mascarada)")
    print("    7 /automatica  Loop auto (50+103+36+225 + 078 se armazem_in_loop)")
    print("    8 /status      Mostra alteracoes locais (git)")
    print("    9 /push        Commit + sobe TUDO pro GitHub (Pages)")
    print("    78 /armazem    Captura tela 078 (Armazém) agora")
    print("    sync78         Sobe cache 078 → Sheets Armazém")
    print("    /pull          Baixa alteracoes do GitHub")
    print("    /e             Lista campos editaveis")
    print("    /e intervalo 5m   Define tempo do /automatica (30s|5m|1h|2d)")
    print("    /e chave v     Edita direto: /e unit SPO,LEO,RIS")
    print("=" * 72)
    if message:
        print(f"  >> {message}")
        print("-" * 72)


def cmd_help() -> str:
    keys = ", ".join(sorted(EDITABLE.keys()))
    return (
        "Comandos: /e | 50 | 103 | 36 | 225 | 78 | sync | sync78 | dash | gui | "
        "/automatica | /status | /push | /pull | show | help | sair\n"
        f"  Campos: {keys}\n"
        "  Bool: true/false | sim/nao | 1/0\n"
        "  periodo_modo: diario | sexta\n"
        "  loop_intervalo: 30s | 5m | 1h | 2d  (min 5s, max 30d)\n"
        "    /e intervalo 30s\n"
        "  Armazém: /e armazem_in_loop true|false (Sheets = enable_sheets)\n"
        "  /automatica [intervalo] | /status | /push [msg] | /pull"
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
        "opcao": "coleta_option",
        "auto": "auto_baixar_ao_abrir",
        "intervalo": "loop_intervalo",
        "interval": "loop_intervalo",
        "tempo": "loop_intervalo",
        "loop": "loop_intervalo",
        "armazem_loop": "armazem_in_loop",
    }
    key = aliases.get(key, key)
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
    else:
        if key == "periodo_modo":
            v = raw.lower()
            if v not in {"diario", "sexta"}:
                return "periodo_modo deve ser: diario | sexta"
            payload[key] = v
        elif key == "loop_intervalo":
            from interval_parse import format_duration, parse_duration

            try:
                sec = parse_duration(raw)
            except ValueError as err:
                return str(err)
            payload[key] = format_duration(sec)
        elif key == "unit":
            # Aceita varias siglas: SPO,LEO,RIS | * = todas
            payload[key] = raw.strip().upper().replace(" ", "")
        elif key in {"domain", "user"}:
            payload[key] = raw.strip()
        else:
            payload[key] = raw

    _save_payload(payload)
    shown = _mask(str(payload[key]), secret) if typ != "bool" else str(payload[key]).lower()
    if key == "loop_intervalo":
        from interval_parse import format_duration_long, parse_duration

        try:
            shown = f"{payload[key]} ({format_duration_long(parse_duration(str(payload[key])))})"
        except ValueError:
            pass
    return f"OK: {key} = {shown}"


def run_pipeline_50() -> str:
    from pipeline import run_full_pipeline

    logs: list[str] = []

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")
        logs.append(msg)

    print("\n=== Pipeline 50 ===")
    result = run_full_pipeline(on_status=on_status, headless=False)
    tot = ((result.get("analysis") or {}).get("totais") or {})
    return f"50 OK · totais={tot}" if result else "50 concluido"


def run_pipeline_103() -> str:
    from pipeline import run_full_pipeline_103

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    print("\n=== Pipeline 103 ===")
    result = run_full_pipeline_103(on_status=on_status, headless=False)
    tot = ((result.get("analysis") or {}).get("totais") or {})
    return f"103 OK · totais={tot}"


def run_pipeline_36() -> str:
    from pipeline import run_full_pipeline_36

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    print("\n=== Pipeline 36 (entregas) ===")
    result = run_full_pipeline_36(on_status=on_status, headless=False)
    tot = ((result.get("analysis") or {}).get("totais") or {})
    return f"36 OK · totais={tot}"


def run_pipeline_225() -> str:
    from pipeline import run_full_pipeline_225

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    print("\n=== Pipeline 225 (agendamentos mes corrente · arquivo R) ===")
    result = run_full_pipeline_225(on_status=on_status, headless=False)
    tot = result.get("analysis") or {}
    return (
        f"225 OK · total={tot.get('total')} rota={tot.get('em_rota')} "
        f"parado={tot.get('parado')} concluido={tot.get('concluido')} alerta={tot.get('alerta')}"
    )


def run_pipeline_78_cmd() -> str:
    from pipeline import run_pipeline_78

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    print("\n=== Pipeline 078 (Armazém) ===")
    result = run_pipeline_78(on_status=on_status, headless=False)
    return (
        f"078 OK · linhas={result.get('total_linhas')} "
        f"veículos={result.get('total_veiculos')} "
        f"sheets={(result.get('sheets') or {}).get('ok')}"
    )


def run_sync_78() -> str:
    from sheets_sync_78 import sync_sheets_78

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    print("\n=== Sync Sheets Armazém 078 ===")
    r = sync_sheets_78(on_status=on_status)
    if r.get("ok"):
        return f"sync78 OK · veiculos={r.get('veiculos')} resumo={r.get('resumo')}"
    return f"sync78: {r.get('error') or r.get('reason') or r}"


def run_sync() -> str:
    from sheets_sync import (
        sync_google_sheets,
        sync_google_sheets_103,
        sync_google_sheets_36,
        sync_google_sheets_225,
    )

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    print("\n=== Sync Sheets 50 ===")
    r50 = sync_google_sheets(on_status=on_status)
    print("\n=== Sync Sheets 103 ===")
    r103 = sync_google_sheets_103(on_status=on_status)
    print("\n=== Sync Sheets 36 ===")
    r36 = sync_google_sheets_36(on_status=on_status)
    print("\n=== Sync Sheets 225 ===")
    r225 = sync_google_sheets_225(on_status=on_status)
    return (
        f"sync 50={r50.get('ok')} 103={r103.get('ok')} "
        f"36={r36.get('ok')} 225={r225.get('ok')}"
    )


def run_dash() -> str:
    from publish_dashboard import publish_dashboard

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    r = publish_dashboard(on_status=on_status)
    return f"dashboard ok={r.get('ok')} pushed={r.get('pushed')}"


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

    cfg = load_settings()
    try:
        sec = resolve_interval_sec(interval_arg, settings_intervalo=cfg.loop_intervalo)
    except ValueError as err:
        return f"ERRO: {err}"

    print("\n" + "=" * 72)
    print("  MODO /AUTOMATICA")
    print("  50  = periodo de COLETA HOJE")
    print("  103 = data LIMITE HOJE (L)")
    print("  36  = entregas (se habilitado)")
    print("  225 = agendamentos do MES (1→ultimo dia) · arquivo R")
    print("  078 = Armazém (se armazem_in_loop=true)")
    print(f"  Ciclo a cada {format_duration_long(sec)}: baixar + analisar + Sheets/dashboard")
    print("  50+103 em paralelo; 36, 225 e 078 em sequencia.")
    print("  Virada de dia/mes recalcula sozinho (225 segue o mes corrente).")
    print("  Altere com: /e intervalo 30s | 5m | 1h | 2d")
    print("  Ligar/desligar 078 no loop: /e armazem_in_loop true|false")
    if return_to_menu:
        print("  Ctrl+C volta ao menu. Fechar a janela encerra.")
    else:
        print("  Ctrl+C ou fechar a janela encerra.")
    print("=" * 72 + "\n")
    try:
        run_loop(interval_sec=sec, headless=True, once=False)
    except KeyboardInterrupt:
        print("\nModo automatica interrompido.")
    return "Modo /automatica encerrado."


def _parse_interval_arg(parts: list[str]) -> str | None:
    """Pega override apos o comando: /automatica 90s | /automatica 5m."""
    if len(parts) >= 2:
        return " ".join(parts[1:]).strip() or None
    return None


def run_git_status() -> str:
    from git_sync import git_status

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    return git_status(on_status=on_status)


def run_git_push(parts: list[str] | None = None) -> str:
    from git_sync import git_push

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    msg = " ".join((parts or [])[1:]).strip()
    return git_push(msg, on_status=on_status)


def run_git_pull() -> str:
    from git_sync import git_pull

    def on_status(msg: str) -> None:
        print(f"  [{datetime.now():%H:%M:%S}] {msg}")

    return git_pull(on_status=on_status)


def show_config(payload: dict[str, Any]) -> str:
    lines = ["Config atual:"]
    for key, (_, typ, secret) in EDITABLE.items():
        val = payload.get(key, "")
        if typ == "bool":
            lines.append(f"  {key}={str(bool(val)).lower()}")
        else:
            lines.append(f"  {key}={_mask(str(val), secret)}")
    return "\n".join(lines)


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
    if args and args[0].lstrip("/").lower() in {"sync78", "sheets78"}:
        print(run_sync_78())
        return 0

    payload = _load_payload()
    message = "Pronto. /push sobe Pages | /automatica | 78=Armazém | /e edita."
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
            elif cmd in {"sync78", "/sync78", "sheets78"}:
                message = run_sync_78()
            elif cmd in {"3", "sync", "/sync"}:
                message = run_sync()
            elif cmd in {"4", "dash", "/dash", "dashboard"}:
                message = run_dash()
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
            elif cmd in {"6", "show", "/show", "config"}:
                message = show_config(payload)
            elif cmd == "cls" or cmd == "clear":
                message = ""
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
