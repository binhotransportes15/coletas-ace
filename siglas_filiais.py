"""Cadastro de siglas de filiais BINHO → cidade (e UF).

Fonte: base operacional (print filiais). Usado no Armazém 078
para exibir cidade no lugar da sigla (ex.: GYN → GOIANIA).
Também resolve ORIGEM da planilha de contratação → base (SPO, GYN…).
"""
from __future__ import annotations

import re
import unicodedata

# sigla → (cidade, UF)
SIGLAS_FILIAIS: dict[str, tuple[str, str]] = {
    "DCX": ("RIO DE JANEIRO", "RJ"),
    "PPY": ("POUSO ALEGRE", "MG"),
    "GNN": ("GOIANIA", "GO"),
    "ATM": ("ALTAMIRA", "PA"),
    "GIR": ("GOIANIA", "GO"),
    "ATR": ("ALTAMIRA", "PA"),
    "GYN": ("GOIANIA", "GO"),
    "BSB": ("BRASILIA", "DF"),
    "GYL": ("GOIANIA", "GO"),
    "APL": ("ANAPOLIS", "GO"),
    "SPO": ("GUARULHOS", "SP"),
    "APS": ("ANAPOLIS", "GO"),
    "VIX": ("SERRA", "ES"),
    "SNN": ("GUARULHOS", "SP"),
    "STM": ("SANTAREM", "PA"),
    "STR": ("SANTAREM", "PA"),
    "BNU": ("ITAJAI", "SC"),
    "SPL": ("GUARULHOS", "SP"),
    "VIT": ("SERRA", "ES"),
}

_BASE_CANONICA: dict[str, str] = {
    "GUARULHOS": "SPO",
    "GOIANIA": "GYN",
    "SERRA": "VIX",
    "RIO DE JANEIRO": "DCX",
    "BRASILIA": "BSB",
    "ANAPOLIS": "APL",
    "ALTAMIRA": "ATM",
    "SANTAREM": "STM",
    "POUSO ALEGRE": "PPY",
    "ITAJAI": "BNU",
}

# Cidades da região que operam pela base (origem na planilha)
_ALIASES_CIDADE_BASE: dict[str, str] = {
    "GUARULHOS": "SPO",
    "SAO PAULO": "SPO",
    "BRAGANCA PAULISTA": "SPO",
    "MOGI DAS CRUZES": "SPO",
    "FERRAZ DE VASCONCELOS": "SPO",
    "ARUJA": "SPO",
    "MAUA": "SPO",
    "ITUPEVA": "SPO",
    "NOVA ODESSA": "SPO",
    "CAJAMAR": "SPO",
    "OSASCO": "SPO",
    "SUZANO": "SPO",
    "GOIANIA": "GYN",
    "APARECIDA DE GOIANIA": "GYN",
    "ANAPOLIS": "APL",
    "SERRA": "VIX",
    "VITORIA": "VIX",
    "COLATINA": "VIX",
    "RIO DE JANEIRO": "DCX",
    "BRASILIA": "BSB",
    "BETIM": "PPY",
}


def _fold(text: str) -> str:
    raw = unicodedata.normalize("NFKD", str(text or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^A-Z0-9\s/+]", " ", raw.upper())
    return re.sub(r"\s+", " ", raw).strip()


def normalizar_sigla(raw: str | None) -> str:
    return str(raw or "").strip().upper()


def cidade_da_sigla(sigla: str | None) -> str:
    """Retorna o nome da cidade da filial; se desconhecida, devolve a própria sigla."""
    key = normalizar_sigla(sigla)
    if not key:
        return ""
    hit = SIGLAS_FILIAIS.get(key)
    return hit[0] if hit else key


def uf_da_sigla(sigla: str | None) -> str:
    key = normalizar_sigla(sigla)
    hit = SIGLAS_FILIAIS.get(key)
    return hit[1] if hit else ""


def label_origem(sigla: str | None, *, com_uf: bool = False) -> str:
    """Rotulo para TV/planilha: cidade (ex. GOIANIA) ou CIDADE/UF."""
    key = normalizar_sigla(sigla)
    if not key:
        return ""
    hit = SIGLAS_FILIAIS.get(key)
    if not hit:
        return key
    cidade, uf = hit
    if com_uf and uf:
        return f"{cidade}/{uf}"
    return cidade


def _cidades_do_texto(origem: str) -> list[str]:
    """Extrai pedaços CIDADE de um texto de origem (pode ter '+')."""
    folded = _fold(origem)
    if not folded:
        return []
    parts: list[str] = []
    for chunk in re.split(r"\+", folded):
        c = chunk.strip()
        if not c:
            continue
        # GUARULHOS/SP → GUARULHOS
        if "/" in c:
            c = c.split("/", 1)[0].strip()
        toks = c.split()
        if len(toks) >= 2 and len(toks[-1]) == 2 and toks[-1].isalpha():
            c = " ".join(toks[:-1])
        if c and c not in parts:
            parts.append(c)
    return parts


def base_da_origem(origem: str | None) -> tuple[str, str]:
    """
    Resolve texto de ORIGEM da planilha → (sigla_base, rotulo_exibicao).

    Exemplos:
      GUARULHOS/SP → (SPO, SPO · GUARULHOS)
      GOIANIA/GO → (GYN, GYN · GOIANIA)
    """
    text = str(origem or "").strip()
    if not text:
        return ("OUT", "OUT · OUTROS")

    folded = _fold(text)
    if folded in SIGLAS_FILIAIS:
        return folded, f"{folded} · {SIGLAS_FILIAIS[folded][0]}"

    for city in _cidades_do_texto(text):
        if city in _ALIASES_CIDADE_BASE:
            base = _ALIASES_CIDADE_BASE[city]
            nome = cidade_da_sigla(base) if base != "OUT" else city
            return base, f"{base} · {nome}"
        if city in _BASE_CANONICA:
            base = _BASE_CANONICA[city]
            return base, f"{base} · {cidade_da_sigla(base)}"
        for alias, base in _ALIASES_CIDADE_BASE.items():
            if alias in city or city in alias:
                nome = cidade_da_sigla(base) if base != "OUT" else city
                return base, f"{base} · {nome}"

    uf_match = re.search(r"/([A-Z]{2})\b", folded)
    if uf_match:
        uf = uf_match.group(1)
        uf_hub = {
            "SP": "SPO",
            "GO": "GYN",
            "ES": "VIX",
            "RJ": "DCX",
            "DF": "BSB",
            "MG": "PPY",
        }
        if uf in uf_hub:
            base = uf_hub[uf]
            return base, f"{base} · {cidade_da_sigla(base)}"

    return "OUT", "OUT · OUTROS"
