"""
ACE · GitHub sync (CMD)

Sobe alteracoes do projeto para o GitHub sem expor segredos.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import BASE_DIR

StatusCallback = Callable[[str], None]

# Nunca forcar add destes (mesmo se alguem tentar)
BLOCKED_PATTERNS = (
    "data/config.json",
    "data/secrets/",
    ".env",
    "credentials",
    "password",
    "__pycache__/",
    ".venv/",
    "venv/",
)


def _noop(_: str) -> None:
    return None


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd or BASE_DIR),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )


def _is_blocked(path: str) -> bool:
    norm = path.replace("\\", "/").lower()
    return any(b.lower() in norm for b in BLOCKED_PATTERNS)


def git_status(*, on_status: StatusCallback | None = None) -> str:
    status = on_status or _noop
    print("\n=== Git status ===")
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    remote = _run(["git", "remote", "get-url", "origin"])
    ahead = _run(["git", "status", "-sb"])
    status(f"Branch: {(branch.stdout or '').strip() or '?'}")
    url = (remote.stdout or "").strip()
    if "@" in url and "github.com" in url:
        # mascara token se existir na URL
        url = "https://github.com/" + url.split("github.com/")[-1]
    status(f"Remote: {url or '(sem origin)'}")
    print(ahead.stdout or ahead.stderr or "(vazio)")
    porcelain = _run(["git", "status", "--porcelain"])
    lines = [ln for ln in (porcelain.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return "Nenhuma alteracao pendente."
    return f"{len(lines)} arquivo(s) com alteracao. Use /push para subir."


def git_pull(*, on_status: StatusCallback | None = None) -> str:
    status = on_status or _noop
    print("\n=== Git pull ===")
    status("Baixando alteracoes do GitHub...")
    r = _run(["git", "pull", "--ff-only", "origin", "HEAD"])
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    print(out or "(sem saida)")
    if r.returncode != 0:
        return f"Pull falhou: {out[:300]}"
    return "Pull OK."


def git_push(
    message: str = "",
    *,
    on_status: StatusCallback | None = None,
) -> str:
    """
    Adiciona alteracoes versionaveis, commit e push para origin.
    Nao inclui data/config.json nem secrets.
    """
    status = on_status or _noop
    print("\n=== Git push (atualizar GitHub) ===")

    if not (BASE_DIR / ".git").exists():
        return "ERRO: pasta nao e um repositorio git."

    status("Verificando alteracoes...")
    porcelain = _run(["git", "status", "--porcelain"])
    changed = [ln for ln in (porcelain.stdout or "").splitlines() if ln.strip()]
    if not changed:
        # ainda tenta push se houver commits locais nao enviados
        status("Nada novo para commit. Tentando push de commits pendentes...")
    else:
        blocked = []
        for ln in changed:
            path = ln[3:].strip() if len(ln) > 3 else ln
            if " -> " in path:
                path = path.split(" -> ", 1)[-1]
            if _is_blocked(path):
                blocked.append(path)
        if blocked:
            status("Ignorando segredos/bloqueados:")
            for p in blocked:
                print(f"    ! {p}")

        status("Adicionando arquivos (respeitando .gitignore)...")
        add = _run(["git", "add", "-A"])
        if add.returncode != 0:
            return f"git add falhou: {(add.stderr or add.stdout)[:300]}"

        # remove do stage qualquer blocked que tenha entrado
        staged = _run(["git", "diff", "--cached", "--name-only"])
        for path in (staged.stdout or "").splitlines():
            if _is_blocked(path):
                _run(["git", "reset", "HEAD", "--", path])
                status(f"Removido do commit (segredo): {path}")

        staged2 = _run(["git", "diff", "--cached", "--name-only"])
        staged_files = [p for p in (staged2.stdout or "").splitlines() if p.strip()]
        if staged_files:
            msg = (message or "").strip() or (
                f"chore(ace): atualiza projeto {datetime.now():%Y-%m-%d %H:%M}"
            )
            status(f"Commit: {msg}")
            for p in staged_files:
                print(f"    + {p}")
            # mensagem via -m (Windows-safe sem heredoc)
            commit = _run(["git", "commit", "-m", msg])
            cout = ((commit.stdout or "") + (commit.stderr or "")).strip()
            print(cout or "(sem saida)")
            if commit.returncode != 0 and "nothing to commit" not in cout.lower():
                return f"Commit falhou: {cout[:400]}"
        else:
            status("Nada seguro para commit apos filtrar segredos.")

    branch = (_run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout or "main").strip()
    status(f"Enviando para origin/{branch}...")
    push = _run(["git", "push", "-u", "origin", "HEAD"])
    pout = ((push.stdout or "") + (push.stderr or "")).strip()
    print(pout or "(sem saida)")
    if push.returncode != 0:
        tip = (
            "Dica: confira login do git (gh auth login) ou "
            "/e enable_github_publish true + GH_TOKEN."
        )
        return f"Push falhou: {pout[:400]}\n{tip}"

    log1 = _run(["git", "log", "-1", "--oneline"])
    head = (log1.stdout or "").strip()
    pages = "https://binhotransportes15.github.io/coletas-ace/dashboard/"
    status(f"OK · {head}")
    status(f"Pages: {pages}")
    return f"GitHub atualizado · {head}"
