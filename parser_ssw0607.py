"""Parser SSW 0607 — Relação de conferentes (login/apelido → nome real)."""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from config import CACHE_DIR, DOWNLOAD_DIR, ensure_dirs

MAPA_CSV = CACHE_DIR / "conferentes_mapa_0607.csv"
MAPA_FIELDS = [
    "unidade",
    "numero",
    "apelido",
    "nome",
    "nome_exibicao",
    "ativo",
    "login",
]

_PARTICLES = {"DE", "DA", "DO", "DOS", "DAS", "E", "DI", "DU", "DEL", "D"}
_PARTICLES_L = {p.lower() for p in _PARTICLES}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _norm(value: Any) -> str:
    return _clean(value).lower()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("cp1252", "latin-1", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def nome_e_sobrenome(nome_completo: str) -> str:
    """ALEJANDRO DAVID BRANDAO → Alejandro Brandao | GERSON ... LULA → Gerson Lula."""
    parts = [p for p in _clean(nome_completo).split() if p]
    if not parts:
        return ""
    significant = [p for p in parts if p.upper() not in _PARTICLES]
    if not significant:
        return parts[0].title()
    if len(significant) == 1:
        return significant[0].title()
    return f"{significant[0].title()} {significant[-1].title()}"


def parse_ssw0607(path: Path | str) -> list[dict[str, Any]]:
    """
    Colunas fixas do RELACAO DE CONFERENTES:
    UNIDADE NUME APELIDO  NOME                           ATIVO  LOGIN
    """
    text = _read_text(Path(path))
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if len(line) < 50:
            continue
        if not re.match(r"^[A-Z]{2,4}\s+\d{3,4}\s+", line):
            continue
        unidade = _clean(line[0:7])
        numero = _clean(line[7:12])
        apelido = _clean(line[12:21])
        nome = _clean(line[21:52])
        ativo = _clean(line[52:57])
        login = _clean(line[57:68]) if len(line) > 57 else ""
        if not nome and not apelido:
            continue
        rows.append(
            {
                "unidade": unidade.upper(),
                "numero": numero,
                "apelido": apelido,
                "nome": nome,
                "nome_exibicao": nome_e_sobrenome(nome),
                "ativo": ativo.upper()[:1] or "S",
                "login": login.lower(),
            }
        )
    return rows


def find_local_0607() -> Path | None:
    ensure_dirs()
    roots = [
        CACHE_DIR,
        DOWNLOAD_DIR,
        Path.home() / "Downloads",
    ]
    candidates: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        candidates.extend(root.glob("ssw0607*.sswweb"))
        candidates.extend(root.glob("*0607*.sswweb"))
        try:
            for p in root.iterdir():
                if p.is_file() and "0607" in p.name.lower() and p.suffix.lower() == ".sswweb":
                    candidates.append(p)
        except OSError:
            pass
    alive = [p for p in candidates if p.is_file() and p.stat().st_size > 200]
    if not alive:
        return None
    alive.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return alive[0]


def analyze_report_0607(
    report_path: Path | str | None = None,
    *,
    on_status: Any = None,
) -> dict[str, Any]:
    status = on_status or (lambda m: None)
    ensure_dirs()
    path = Path(report_path) if report_path else find_local_0607()
    if path is None or not path.is_file():
        raise FileNotFoundError("Relatório 0607 não encontrado (Downloads/cache).")
    status(f"Analisando 0607: {path.name}")
    rows = parse_ssw0607(path)
    with MAPA_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MAPA_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    status(f"0607 OK · {len(rows)} conferente(s) no mapa login/nome")
    return {
        "ok": True,
        "report": str(path),
        "total": len(rows),
        "cache": str(MAPA_CSV),
        "rows": rows,
        "atualizado": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }


def load_mapa_0607() -> list[dict[str, str]]:
    if not MAPA_CSV.exists():
        local = find_local_0607()
        if local:
            analyze_report_0607(local)
    if not MAPA_CSV.exists():
        return []
    with MAPA_CSV.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _split_login_parts(key: str) -> tuple[str, str]:
    """m.silva → (m, silva) | msantos → (m, santos) | rosa → ('', rosa)."""
    key = _norm(key)
    if "." in key:
        a, _, b = key.partition(".")
        return a, re.sub(r"[^a-z0-9]", "", b)
    # login colado tipo msantos (não aplica a apelidos curtos sem ponto)
    if len(key) >= 5 and key[0].isalpha() and key[1:].isalpha():
        return key[0], key[1:]
    return "", key


def resolve_conferente_nome(login_177: str, mapa: list[dict[str, str]] | None = None) -> dict[str, str]:
    """
    Casa o login/apelido do 177 com o cadastro 0607.
    Ordem: LOGIN exato → APELIDO exato → prefixo → inicial+sobrenome → palavra no nome.
    """
    key = _norm(login_177)
    empty = {
        "login": key,
        "nome": "",
        "nome_exibicao": "",
        "apelido": "",
        "match": "",
    }
    if not key:
        return empty
    rows = mapa if mapa is not None else load_mapa_0607()
    if not rows:
        return empty

    scored: list[tuple[int, dict[str, str]]] = []
    initial, tail = _split_login_parts(key)

    for r in rows:
        login = _norm(r.get("login"))
        apelido = _norm(r.get("apelido"))
        nome = _clean(r.get("nome"))
        nome_l = nome.lower()
        words = [w for w in re.split(r"[^a-z0-9]+", nome_l) if w]
        prenome = words[0] if words else ""
        score = 0
        how = ""

        if login and login == key:
            score, how = 100, "login"
        elif apelido and apelido == key:
            score, how = 90, "apelido"
        elif login and len(key) >= 3 and (login.startswith(key) or key.startswith(login)):
            score, how = 80, "login_prefix"
        elif apelido and len(key) >= 3 and (apelido.startswith(key) or key.startswith(apelido)):
            score, how = 78, "apelido_prefix"
        elif initial and len(tail) >= 3:
            # inicial do PRENOME + sobrenome (evita falso positivo por apelido)
            init_ok = prenome[:1] == initial
            pos = -1
            for i, w in enumerate(words[1:], start=1):
                if len(w) < 3 or w in _PARTICLES_L:
                    continue
                if w.startswith(tail) or tail.startswith(w):
                    pos = i
                    break
            if init_ok and pos > 0:
                # prioriza 1º sobrenome (msantos→Moiseis Santos > m.silva)
                score = 72 + max(0, 6 - pos)
                how = "inicial_sobrenome"
        if score < 60 and key in words:
            score, how = 60, "nome_palavra"
        if score < 55 and apelido and (
            key == apelido or (len(key) >= 4 and key.startswith(apelido))
        ):
            score, how = 55, "apelido_contem"

        if score > 0:
            scored.append(
                (
                    score,
                    {
                        **r,
                        "_how": how,
                        "_score": str(score),
                    },
                )
            )

    if not scored:
        return empty
    scored.sort(
        key=lambda t: (
            -t[0],
            t[1].get("ativo") != "S",
            t[1].get("nome", ""),
        )
    )
    best = scored[0][1]
    return {
        "login": key,
        "nome": _clean(best.get("nome")),
        "nome_exibicao": _clean(best.get("nome_exibicao"))
        or nome_e_sobrenome(best.get("nome") or ""),
        "apelido": _clean(best.get("apelido")),
        "match": str(best.get("_how") or ""),
        "score": int(best.get("_score") or 0),
    }


if __name__ == "__main__":
    sample = CACHE_DIR / "sample_0607.sswweb"
    r = analyze_report_0607(sample if sample.exists() else None)
    print(r["total"], r["cache"])
    for test in ("lula", "m.silva", "msantos", "alejand", "rosa", "b.mendes", "b.leme", "t.silva"):
        hit = resolve_conferente_nome(test, r["rows"])
        print(f"{test} -> {hit.get('nome_exibicao') or '-'} [{hit.get('match')}]")
