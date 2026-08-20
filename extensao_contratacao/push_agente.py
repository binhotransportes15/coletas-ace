"""Push / deploy do Agente Contratação para outra máquina (via CRT)."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

_EXT_DIR = Path(__file__).resolve().parent
_ACE_ROOT = _EXT_DIR.parent

# Arquivos da mini-extensão (sempre)
AGENT_FILES = (
    "agent_main.py",
    "pipeline_agente.py",
    "parser_produtividade.py",
    "push_agente.py",
    "config_agente.example.json",
    "run_agente.bat",
    "README.md",
    "__init__.py",
)

# Módulos do ACE que o agente usa — sincronizados na raiz do ACE remoto
ACE_RUNTIME_FILES = (
    "sheets_sync_073.py",
    "parser_ssw0644.py",
)


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def resolve_agent_dest(dest_dir: Path | str | None = None) -> Path:
    """
    Destino do push:
    - pasta extensao_contratacao (com agent_main.py), ou
    - raiz do ACE remoto (tem config.py) → usa …/extensao_contratacao
    """
    from config import load_settings

    cfg = load_settings()
    raw = str(dest_dir or getattr(cfg, "ctr_agente_dir", "") or "").strip()
    if not raw:
        raise RuntimeError(
            "Configure ctr_agente_dir com a pasta do agente no outro PC "
            "(ex.: \\\\PC-CTR\\ACE\\extensao_contratacao ou \\\\PC-CTR\\ACE)."
        )
    dest = Path(raw)
    # UNC / rede: não dá para create local se não montou — só tenta
    if dest.is_file():
        raise RuntimeError(f"ctr_agente_dir aponta para arquivo, não pasta: {dest}")

    # Se é raiz do ACE, empurra para a subpasta da extensão
    if (dest / "config.py").exists() and (dest / "ace_cmd.py").exists():
        dest = dest / "extensao_contratacao"
    elif dest.name.lower() != "extensao_contratacao" and (dest / "extensao_contratacao").is_dir():
        dest = dest / "extensao_contratacao"

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise RuntimeError(
            f"Não consegui acessar/criar a pasta do agente:\n  {dest}\n"
            f"({err})\nConfira compartilhamento de rede e permissão."
        ) from err

    # smoke: precisa gravar
    probe = dest / ".ace_push_probe"
    try:
        probe.write_text(datetime.now().isoformat(), encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as err:
        raise RuntimeError(
            f"Pasta sem permissão de escrita:\n  {dest}\n({err})"
        ) from err
    return dest


def _load_existing_config(dest: Path) -> dict[str, Any]:
    path = dest / "config_agente.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _copy_file(src: Path, target: Path) -> str:
    """Copia e devolve status: copied | same | skipped."""
    if not src.exists():
        return "missing"
    try:
        if target.exists() and src.resolve() == target.resolve():
            return "same"
    except OSError:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    return "copied"


def push_agente_update(
    dest_dir: Path | str | None = None,
    *,
    sync_runtime: bool = True,
    force_run: bool = True,
) -> dict[str, Any]:
    """
    Envia a versão atual do agente (desta máquina) para ctr_agente_dir.
    Preserva ace_root e preferências locais do PC remoto.
    """
    from config import load_settings

    try:
        from extensao_contratacao.parser_produtividade import resolve_produtividade_xlsx
    except ImportError:
        from parser_produtividade import resolve_produtividade_xlsx  # type: ignore

    cfg = load_settings()
    dest = resolve_agent_dest(dest_dir)
    ace_remote = dest.parent if dest.name.lower() == "extensao_contratacao" else dest

    copied: list[str] = []
    same: list[str] = []
    missing: list[str] = []

    for name in AGENT_FILES:
        status = _copy_file(_EXT_DIR / name, dest / name)
        if status == "copied":
            copied.append(name)
        elif status == "same":
            same.append(name)
        else:
            missing.append(name)

    runtime_copied: list[str] = []
    if sync_runtime and (ace_remote / "config.py").exists():
        for name in ACE_RUNTIME_FILES:
            src = _ACE_ROOT / name
            if not src.exists():
                missing.append(f"runtime:{name}")
                continue
            st = _copy_file(src, ace_remote / name)
            if st == "copied":
                runtime_copied.append(name)
                copied.append(f"runtime:{name}")
            elif st == "same":
                same.append(f"runtime:{name}")

    existing = _load_existing_config(dest)
    # ace_root do remoto: preservar; senão pasta pai do agente
    ace_root = str(existing.get("ace_root") or "").strip()
    if not ace_root or Path(ace_root) == _ACE_ROOT:
        # não grava o caminho desta máquina no PC remoto
        if (ace_remote / "config.py").exists():
            ace_root = str(ace_remote)
        else:
            ace_root = str(existing.get("ace_root") or "")

    excel_name = resolve_produtividade_xlsx(
        getattr(cfg, "ctr_agente_excel", "") or "PRODUTIVIDADE CONTRATAÇÃO.xlsx"
    ).name

    files_meta = {}
    for name in AGENT_FILES:
        p = dest / name
        if p.exists():
            files_meta[name] = _file_sha(p)

    version = {
        "pushed_at": datetime.now().isoformat(timespec="seconds"),
        "pushed_from": str(_ACE_ROOT),
        "dest": str(dest),
        "files": files_meta,
        "runtime": runtime_copied,
    }
    (dest / "version.json").write_text(
        json.dumps(version, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    payload = {
        **existing,
        "excel_path": excel_name,
        "intervalo": str(
            existing.get("intervalo")
            or getattr(cfg, "ctr_agente_intervalo", "")
            or "15m"
        ),
        "skip_200": bool(existing.get("skip_200", False)),
        "sync_sheets": bool(existing.get("sync_sheets", True)),
        "enable_sheets": bool(
            existing.get("enable_sheets", getattr(cfg, "enable_sheets", False))
        ),
        "sync_remoto": bool(
            existing.get("sync_remoto", getattr(cfg, "sync_remoto", True))
        ),
        # credenciais Sheets: preferem as do ACE desta máquina (fonte da verdade)
        "apps_script_url": str(
            getattr(cfg, "apps_script_url", "") or existing.get("apps_script_url") or ""
        ),
        "apps_script_token": str(
            getattr(cfg, "apps_script_token", "")
            or existing.get("apps_script_token")
            or ""
        ),
        "ace_root": ace_root,
        "updated_at": version["pushed_at"],
        "last_push_from": str(_ACE_ROOT),
    }
    (dest / "config_agente.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if force_run:
        (dest / "FORCE_RUN").write_text(version["pushed_at"], encoding="utf-8")

    return {
        "ok": True,
        "dest": str(dest),
        "ace_remote": str(ace_remote),
        "copied": copied,
        "same": same,
        "missing": missing,
        "version": version,
        "force_run": force_run,
    }


def format_push_result(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"push agente FALHOU · {result.get('error')}"
    n = len(result.get("copied") or [])
    return (
        f"push agente OK -> {result.get('dest')}\n"
        f"  arquivos novos/atualizados: {n}\n"
        f"  FORCE_RUN={'sim' if result.get('force_run') else 'nao'} "
        f"- o loop no outro PC pega na proxima checagem (<=5s)"
    )
