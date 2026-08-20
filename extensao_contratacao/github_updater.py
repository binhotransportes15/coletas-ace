"""Atualização do Agente Contratação via GitHub (sem rede local)."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[int], None]  # 0–100

# Repo padrão do ACE
DEFAULT_OWNER = "binhotransportes15"
DEFAULT_REPO = "coletas-ace"
DEFAULT_BRANCH = "main"
AGENT_SUBDIR = "extensao_contratacao"


def _noop(_: str) -> None:
    return None


def _noop_pct(_: int) -> None:
    return None


def agent_root() -> Path:
    """Pasta instalada do agente (dev ou .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def read_local_version(root: Path | None = None) -> str:
    base = root or agent_root()
    for name in ("VERSION", "version.txt"):
        p = base / name
        if p.exists():
            return (p.read_text(encoding="utf-8").strip().splitlines() or ["0.0.0"])[0].strip()
    # fallback version.json (push antigo)
    vj = base / "version.json"
    if vj.exists():
        try:
            data = json.loads(vj.read_text(encoding="utf-8"))
            return str(data.get("version") or data.get("pushed_at") or "0.0.0")
        except Exception:
            pass
    return "0.0.0"


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (text or "").strip())
    if not nums:
        return (0,)
    return tuple(int(x) for x in nums[:4])


def version_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ACE-AgenteContratacao",
    }
    token = (
        os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    ).strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_get(url: str, *, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=_github_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def resolve_repo(settings=None) -> tuple[str, str, str]:
    """owner, repo, branch."""
    owner, repo, branch = DEFAULT_OWNER, DEFAULT_REPO, DEFAULT_BRANCH
    try:
        if settings is None:
            from config import load_settings

            settings = load_settings()
        raw = str(getattr(settings, "github_repo", "") or "").strip()
        if "/" in raw:
            owner, repo = raw.split("/", 1)
            owner, repo = owner.strip(), repo.strip()
        branch = str(getattr(settings, "github_branch", "") or branch).strip() or branch
    except Exception:
        pass
    # env override
    owner = os.environ.get("ACE_CTR_GH_OWNER", owner)
    repo = os.environ.get("ACE_CTR_GH_REPO", repo)
    branch = os.environ.get("ACE_CTR_GH_BRANCH", branch)
    return owner, repo, branch


@dataclass
class UpdateInfo:
    local_version: str
    remote_version: str
    has_update: bool
    owner: str
    repo: str
    branch: str
    version_url: str
    zip_url: str
    error: str = ""


def check_for_update(
    *,
    settings=None,
    on_status: StatusCallback | None = None,
) -> UpdateInfo:
    status = on_status or _noop
    owner, repo, branch = resolve_repo(settings)
    local = read_local_version()
    version_url = (
        f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
        f"{AGENT_SUBDIR}/VERSION"
    )
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    try:
        status("Consultando versão no GitHub…")
        remote_raw = _http_get(version_url, timeout=30).decode("utf-8", errors="replace")
        remote = (remote_raw.strip().splitlines() or ["0.0.0"])[0].strip()
        return UpdateInfo(
            local_version=local,
            remote_version=remote,
            has_update=version_newer(remote, local),
            owner=owner,
            repo=repo,
            branch=branch,
            version_url=version_url,
            zip_url=zip_url,
        )
    except Exception as err:  # noqa: BLE001
        return UpdateInfo(
            local_version=local,
            remote_version="",
            has_update=False,
            owner=owner,
            repo=repo,
            branch=branch,
            version_url=version_url,
            zip_url=zip_url,
            error=str(err),
        )


def _extract_agent_from_repo_zip(zip_bytes: bytes, dest: Path) -> int:
    """Extrai apenas extensao_contratacao/ do zipball do repo → dest."""
    count = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # coletas-ace-main/extensao_contratacao/...
        prefix = None
        for name in zf.namelist():
            norm = name.replace("\\", "/")
            if f"/{AGENT_SUBDIR}/" in norm or norm.endswith(f"/{AGENT_SUBDIR}"):
                # raiz do zip: repo-branch/
                parts = norm.split("/")
                try:
                    idx = parts.index(AGENT_SUBDIR)
                except ValueError:
                    continue
                prefix = "/".join(parts[: idx + 1]) + "/"
                break
        if not prefix:
            raise RuntimeError(
                f"Pasta {AGENT_SUBDIR}/ não encontrada no ZIP do GitHub."
            )
        skip_names = {"config_agente.json", "FORCE_RUN", ".ace_push_probe"}
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            rel = name[len(prefix) :]
            if not rel or rel.split("/")[0] in skip_names:
                # preserve local config
                if rel in skip_names or (rel and Path(rel).name in skip_names):
                    continue
            if Path(rel).name in skip_names:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def apply_update(
    *,
    settings=None,
    on_status: StatusCallback | None = None,
    on_progress: ProgressCallback | None = None,
    force: bool = False,
) -> dict:
    """
    Baixa o ZIP do branch no GitHub e atualiza os arquivos do agente.
    Preserva config_agente.json local.
    """
    status = on_status or _noop
    progress = on_progress or _noop_pct
    info = check_for_update(settings=settings, on_status=status)
    if info.error:
        return {"ok": False, "error": info.error, "info": info}
    if not info.has_update and not force:
        status(f"Já está na versão {info.local_version}")
        return {
            "ok": True,
            "skipped": True,
            "reason": "up_to_date",
            "local": info.local_version,
            "remote": info.remote_version,
        }

    dest = agent_root()
    status(f"Baixando {info.owner}/{info.repo}@{info.branch}…")
    progress(5)
    try:
        zip_bytes = _http_get(info.zip_url, timeout=180)
    except urllib.error.HTTPError as err:
        return {"ok": False, "error": f"HTTP {err.code}: {err.reason}", "info": info}
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "error": str(err), "info": info}

    progress(55)
    status("Instalando arquivos…")
    # backup config
    cfg_path = dest / "config_agente.json"
    cfg_backup = None
    if cfg_path.exists():
        cfg_backup = cfg_path.read_bytes()

    try:
        n = _extract_agent_from_repo_zip(zip_bytes, dest)
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "error": str(err), "info": info}

    if cfg_backup is not None:
        cfg_path.write_bytes(cfg_backup)

    # grava VERSION se veio no zip; senão usa remote
    ver_path = dest / "VERSION"
    if not ver_path.exists() and info.remote_version:
        ver_path.write_text(info.remote_version + "\n", encoding="utf-8")

    progress(100)
    status(f"Atualizado para {read_local_version(dest)} · {n} arquivo(s)")
    return {
        "ok": True,
        "files": n,
        "local": info.local_version,
        "remote": read_local_version(dest),
        "restart_hint": True,
        "info": info,
    }


def publish_agent_to_github(
    *,
    bump: bool = False,
    message: str = "",
    on_status: StatusCallback | None = None,
) -> str:
    """
    CRT: sobe a pasta do agente para o GitHub (git add/commit/push).
    O .exe no outro PC usa o botão Atualizar para baixar.
    """
    status = on_status or _noop
    from config import BASE_DIR

    root = Path(BASE_DIR)
    agent = root / AGENT_SUBDIR
    if not agent.is_dir():
        return f"Pasta {AGENT_SUBDIR} não encontrada."

    if bump:
        cur = read_local_version(agent)
        parts = list(parse_version(cur))
        while len(parts) < 3:
            parts.append(0)
        parts[2] += 1
        new_v = ".".join(str(x) for x in parts[:3])
        (agent / "VERSION").write_text(new_v + "\n", encoding="utf-8")
        status(f"VERSION {cur} → {new_v}")

    ver = read_local_version(agent)
    msg = (message or "").strip() or f"chore(ctr-agente): publica agente v{ver}"

    def run(cmd: list[str]) -> tuple[int, str]:
        import subprocess

        r = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode, out

    status("Git: adicionando extensao_contratacao…")
    paths = [
        f"{AGENT_SUBDIR}/",
        "sheets_sync_073.py",
        "parser_ssw0644.py",
    ]
    code, out = run(["git", "add", "--", *paths])
    if code != 0:
        return f"git add falhou: {out[:300]}"

    code, porcelain = run(["git", "status", "--porcelain", "--", *paths])
    if not (porcelain or "").strip():
        return f"Nada novo para publicar (agente já em v{ver} no Git)."

    status("Git: commit…")
    code, out = run(["git", "commit", "-m", msg])
    if code != 0:
        return f"git commit falhou: {out[:400]}"

    status("Git: push origin…")
    code, out = run(["git", "push", "origin", "HEAD"])
    if code != 0:
        return f"git push falhou: {out[:400]}"

    return (
        f"Agente v{ver} publicado no GitHub.\n"
        f"No PC da planilha: abra o agente e clique em Atualizar."
    )
