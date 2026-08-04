"""
Ocorrências SSW tratadas como entrega/CTRC concluído (realizado).

Códigos oficiais (tabela de ocorrências):
  18  MERCADORIA SERA DEVOLVIDA AO REMETENTE
  53  MERCADORIA REPASSADA PARA O OP. LOGISTIC
  58  MERCADORIA EM PROCESSO DE INDENIZACAO
  61  LJJR
  93  CTRC EMIT P/EFEITO FRETE/ICMS
  94  CONHECIMENTO SUBSTITUIDO
  99  CTE BAIXADO
"""
from __future__ import annotations

import re
import unicodedata

OCORR_REALIZADA_CODES: frozenset[str] = frozenset(
    {"18", "53", "58", "61", "93", "94", "99"}
)

OCORR_REALIZADA_MARKERS: tuple[str, ...] = (
    "DEVOLVIDA AO REMETENTE",
    "REPASSADA PARA O OP",
    "PROCESSO DE INDENIZACAO",
    "LJJR",
    "EMIT P/EFEITO FRETE",
    "EMIT P EFEITO FRETE",
    "CONHECIMENTO SUBSTITUIDO",
    "CTE BAIXADO",
)


def norm_ocorrencia(text: str) -> str:
    t = unicodedata.normalize("NFD", str(text or ""))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = t.upper().replace("Ç", "C")
    return re.sub(r"\s+", " ", t).strip()


def is_ocorrencia_realizada(ocorrencia: str) -> bool:
    """True se a ocorrência conta como realizada (36) / concluído (225)."""
    o = norm_ocorrencia(ocorrencia)
    if not o:
        return False
    for marker in OCORR_REALIZADA_MARKERS:
        if marker in o:
            return True
    # Prefixo típico SSW: "99 04/08/26 CTE ..." ou "18 MERCADORIA..."
    m = re.match(r"^(\d{1,3})\b", o)
    if m and m.group(1) in OCORR_REALIZADA_CODES:
        return True
    return False
