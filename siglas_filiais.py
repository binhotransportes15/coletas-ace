"""Cadastro de siglas de filiais BINHO → cidade (e UF).

Fonte: base operacional (print filiais). Usado no Armazém 078
para exibir cidade no lugar da sigla (ex.: GYN → GOIANIA).
"""
from __future__ import annotations

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
