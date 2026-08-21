"""
ACE · GitHub sync (CMD)

Sobe alteracoes do projeto para o GitHub sem expor segredos.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import BASE_DIR, SECRETS_DIR

StatusCallback = Callable[[str], None]

GH_TOKEN_FILE = SECRETS_DIR / "gh_token.txt"


def load_github_token() -> str:
    """Token do arquivo local do CRT, senão GH_TOKEN / GITHUB_TOKEN do Windows."""
    try:
        if GH_TOKEN_FILE.is_file():
            for line in GH_TOKEN_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    os.environ["GH_TOKEN"] = line
                    return line
    except Exception:
        pass
    return str(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def save_github_token(token: str) -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    tok = str(token or "").strip()
    GH_TOKEN_FILE.write_text((tok + "\n") if tok else "", encoding="utf-8")
    if tok:
        os.environ["GH_TOKEN"] = tok
    elif "GH_TOKEN" in os.environ:
        os.environ.pop("GH_TOKEN", None)


def github_token_hint() -> str:
    tok = load_github_token()
    if not tok:
        return "Nenhum token neste PC. Cole um token classic com 'repo' e Salvar."
    return f"Token salvo neste PC ({tok[:4]}…, {len(tok)} caracteres). Cole outro para trocar."


def preflight_github_write(repo: str, token: str) -> str | None:
    """None = pode tentar push. Texto = motivo em português (cabe no log do CRT)."""
    import json
    import ssl
    import urllib.error
    import urllib.request

    repo = str(repo or "").strip().strip("/")
    tok = str(token or "").strip()
    if not tok:
        return (
            "Sem token de escrita. Config → Token GitHub (push) → cole token classic "
            "com a caixa repo → Salvar → feche e abra o CRT → /push."
        )
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {tok}",
            "User-Agent": "ACE-git-sync",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as resp:
            scopes = str(resp.headers.get("X-OAuth-Scopes") or "")
            body = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as err:
        if err.code in {401, 403}:
            return (
                "GitHub recusou o token (403). O token atual NAO escreve no repo. "
                "Gere outro classic com repo, cole no Config do CRT, Salvar, reinicie o CRT."
            )
        if err.code == 404:
            return f"Repo {repo} nao encontrado para este token."
        return f"GitHub API HTTP {err.code}."
    except Exception:
        return None
    perms = body.get("permissions") if isinstance(body, dict) else None
    if isinstance(perms, dict) and not (perms.get("push") or perms.get("admin")):
        return (
            "Token sem escrita neste repo. Token classic precisa da caixa 'repo' "
            "(nao so public_repo). Cole no Config do CRT e Salvar."
        )
    bits = [s.strip().lower() for s in scopes.split(",") if s.strip()]
    # Token classic ghp_ sem nenhuma caixa marcada: API mostra dono do repo,
    # mas git push dá 403. Escopos vazios = não pode escrever.
    if tok.startswith("ghp_") and not bits:
        return (
            "Token classic SEM ESCOPO (nenhuma caixa marcada). "
            "GitHub > Settings > Developer settings > Personal access tokens > "
            "Generate classic > marque repo > cole em Config Token GitHub (push) > Salvar."
        )
    if bits and "repo" not in bits and "public_repo" in bits:
        return "Token so tem public_repo. Marque 'repo' no token novo."
    if bits and "repo" not in bits and "public_repo" not in bits:
        return "Token sem escopo repo. No GitHub, gere o token e marque a caixa repo."
    return None


def redact_git_text(raw: str) -> str:
    text = str(raw or "")
    text = re.sub(r"ghp_[A-Za-z0-9]+", "ghp_***", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]+", "github_pat_***", text)
    text = re.sub(r"x-access-token:[^@\s]+", "x-access-token:***", text)
    return text


def github_origin_url(repo: str) -> str:
    clean = str(repo or "").strip().strip("/")
    return f"https://github.com/{clean}.git"


def _push_head(repo: str, branch: str, token: str) -> subprocess.CompletedProcess[str]:
    """Push sem Credential Manager e sem gravar token no origin."""
    tok = str(token or "").strip()
    url = f"https://x-access-token:{tok}@github.com/{str(repo).strip()}.git"
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    return subprocess.run(
        [
            "git",
            "-c",
            "credential.helper=",
            "push",
            "-u",
            url,
            f"HEAD:{branch or 'main'}",
        ],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def git_env_with_token(token: str | None = None) -> dict[str, str]:
    """Auth só no processo do git — nunca grava o token no remote."""
    env = os.environ.copy()
    tok = str(token or load_github_token()).strip()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    if tok:
        basic = base64.b64encode(f"x-access-token:{tok}".encode("ascii")).decode("ascii")
        env["GIT_CONFIG_COUNT"] = "2"
        env["GIT_CONFIG_KEY_0"] = "credential.helper"
        env["GIT_CONFIG_VALUE_0"] = ""
        env["GIT_CONFIG_KEY_1"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_1"] = f"AUTHORIZATION: basic {basic}"
    return env


def ensure_clean_github_origin(repo: str, *, cwd: Path | None = None) -> None:
    """Tira token embutido no origin (publish antigo gravava x-access-token na URL)."""
    want = github_origin_url(repo)
    got = _run(["git", "remote", "get-url", "origin"], cwd=cwd)
    current = (got.stdout or "").strip()
    if not current:
        _run(["git", "remote", "add", "origin", want], cwd=cwd)
        return
    if current != want:
        _run(["git", "remote", "set-url", "origin", want], cwd=cwd)

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


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd or BASE_DIR),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        env=env,
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
    url = redact_git_text((remote.stdout or "").strip())
    if "@" in url and "github.com" in url:
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

    cfg_repo = "binhotransportes15/coletas-ace"
    try:
        from config import load_settings

        cfg_repo = str(getattr(load_settings(), "github_repo", "") or cfg_repo)
    except Exception:
        pass
    ensure_clean_github_origin(cfg_repo)
    token = load_github_token()
    blocked = preflight_github_write(cfg_repo, token)
    if blocked:
        status(blocked)
        return blocked
    branch = (_run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout or "main").strip()
    status(f"Enviando para origin/{branch}...")
    push = _push_head(cfg_repo, branch, token)
    pout = redact_git_text(((push.stdout or "") + (push.stderr or "")).strip())
    print(pout or "(sem saida)")
    if push.returncode != 0:
        return f"Push falhou: {pout[:400]}\nCole token classic com repo no Config do CRT, Salvar, reinicie o CRT."

    log1 = _run(["git", "log", "-1", "--oneline"])
    head = (log1.stdout or "").strip()
    pages = "https://binhotransportes15.github.io/coletas-ace/dashboard/"
    status(f"OK · {head}")
    status(f"Pages: {pages}")
    return f"GitHub atualizado · {head}"
