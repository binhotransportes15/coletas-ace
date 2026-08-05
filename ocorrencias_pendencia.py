"""Códigos de ocorrência · Pendência (SSW 31)."""
from __future__ import annotations

# Consultas no SSW 31 (Código de ocorrência) + rótulo TV
OCORR_PENDENCIA: dict[str, str] = {
    "13": "ENTREGA PREJUDICADA PELO HORARIO",
    "14": "PERDA DE AGENDAMENTO RESP. BINHO",
    "19": "MERCADORIA EM INDENIZACAO",
    "22": "EXTRAVIO DE MERCADORIA",
    "29": "SOBRA DE VOLUME/MERCADORIA",
    "32": "INVERSAO",
    "33": "MERCADORIA AVARIADA",
    "44": "PERDA DE AGENDAMENTO RESP. PARCEIRO",
    "57": "FALTA DE DOCUMENTACAO",
    "60": "FALTA COM BUSCA / RECONFERENCIA",
    "91": "VEICULO QUEBRADO - EM REPARO E /OU TRANS",
}

OCORR_PENDENCIA_CODES: tuple[str, ...] = tuple(OCORR_PENDENCIA.keys())


def label_ocorrencia(code: str) -> str:
    c = str(code or "").strip()
    return OCORR_PENDENCIA.get(c, c or "—")


def match_codigo_from_text(text: str) -> str:
    """Tenta achar o código a partir da última ocorrência / descrição."""
    raw = str(text or "").strip().upper()
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits in OCORR_PENDENCIA:
        return digits
    # "13 - ENTREGA…" / "13 ENTREGA…"
    head = raw.split("-", 1)[0].strip().split()[0] if raw else ""
    head_d = "".join(ch for ch in head if ch.isdigit())
    if head_d in OCORR_PENDENCIA:
        return head_d
    for code, lab in OCORR_PENDENCIA.items():
        if lab in raw or raw in lab:
            return code
    return ""
