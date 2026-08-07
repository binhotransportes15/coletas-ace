"""Códigos de ocorrência · Pendência (SSW 31) · SLA do setor."""
from __future__ import annotations

# Consultas no SSW 31 (Código de ocorrência) + rótulo TV
OCORR_PENDENCIA: dict[str, str] = {
    "19": "MERCADORIA EM INDENIZACAO",
    "22": "EXTRAVIO DE MERCADORIA",
    "29": "SOBRA DE VOLUME/MERCADORIA",
    "32": "INVERSAO",
    "33": "MERCADORIA AVARIADA",
    "50": "NOTA FISCAL DE DEVOLUCAO",
    "60": "FALTA COM BUSCA / RECONFERENCIA",
    "61": "LJJR",
    "63": "PENDENCIA SOLUCIONADA",
    "80": "PENDENCIA EM TRATATIVA",
}

# 63 conta como positivo no SLA; demais códigos = negativo (abre/mantém pendência)
CODIGO_SLA_POSITIVO = "63"

OCORR_PENDENCIA_CODES: tuple[str, ...] = tuple(OCORR_PENDENCIA.keys())


def label_ocorrencia(code: str) -> str:
    c = str(code or "").strip()
    return OCORR_PENDENCIA.get(c, c or "—")


def is_positivo(code: str) -> bool:
    return str(code or "").strip() == CODIGO_SLA_POSITIVO


def polaridade(code: str) -> str:
    """pos = contribui pro SLA · neg = ofensor."""
    return "pos" if is_positivo(code) else "neg"


def match_codigo_from_text(text: str) -> str:
    """Tenta achar o código a partir da última ocorrência / descrição."""
    raw = str(text or "").strip().upper()
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits in OCORR_PENDENCIA:
        return digits
    # "13 - ENTREGA…" / "63 PENDENCIA…"
    head = raw.split("-", 1)[0].strip().split()[0] if raw else ""
    head_d = "".join(ch for ch in head if ch.isdigit())
    if head_d in OCORR_PENDENCIA:
        return head_d
    for code, lab in OCORR_PENDENCIA.items():
        if lab in raw or raw in lab:
            return code
    return ""
