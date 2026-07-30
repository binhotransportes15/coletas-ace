from __future__ import annotations

from datetime import date, datetime, timedelta


def _digits_only(value: str) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def normalize_date(value: str) -> str:
    """Normaliza para DDMM (4 digitos) para exibicao na UI e opcao 50."""
    digits = _digits_only(value)
    if len(digits) == 4:
        return digits
    if len(digits) == 6:
        return digits[:4]
    if len(digits) == 8:
        if digits[:4].isdigit() and digits[:2] in {"19", "20"}:
            raise ValueError("Informe a data como DDMM ou DD/MM, nao como AAAAMMDD.")
        return digits[:4]
    raise ValueError("Informe a data no formato DDMM ou DD/MM.")


def to_ssw_ddmmyy(value: str) -> str:
    """Converte para DDMMYY (6 digitos), quando a tela SSW exigir."""
    digits = _digits_only(value)
    year = datetime.now().strftime("%y")
    if len(digits) == 4:
        return digits + year
    if len(digits) == 6:
        return digits
    if len(digits) == 8:
        if digits[:4].isdigit() and digits[:2] in {"19", "20"}:
            raise ValueError("Informe a data como DDMM, DDMMYY ou DD/MM.")
        return digits[:4] + digits[6:8]
    raise ValueError("Informe a data no formato DDMM, DDMMYY ou DD/MM.")


def format_date(value: str) -> str:
    compact = normalize_date(value)
    return f"{compact[:2]}/{compact[2:]}"


def format_period(start_value: str, end_value: str) -> str:
    start = normalize_date(start_value)
    end = normalize_date(end_value)
    if start == end:
        return format_date(start)
    return f"{format_date(start)} a {format_date(end)}"


def _as_date(value: date | datetime | None = None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    return value


def to_ddmm(value: date) -> str:
    return value.strftime("%d%m")


def periodo_50_cadastramento(hoje: date | datetime | None = None) -> tuple[str, str]:
    """
    Relatorio 50 · Periodo de CADASTRAMENTO.

    Regra ACE (tempo real CMD):
      - Dias uteis: cadastradas no dia anterior (D-1)
      - Segunda: conforme sexta → sexta a sabado (cadastros do fim de semana)

    Ex.: terça 30/07 → 29/07
         segunda 03/08 → 31/07 a 01/08 (sex–sab)
    """
    today = _as_date(hoje)
    weekday = today.weekday()  # 0=seg ... 6=dom
    if weekday == 0:  # segunda
        ini = today - timedelta(days=3)  # sexta
        fim = today - timedelta(days=2)  # sabado
    else:
        ini = fim = today - timedelta(days=1)
    return to_ddmm(ini), to_ddmm(fim)


def periodo_103_hoje(hoje: date | datetime | None = None) -> tuple[str, str]:
    """Relatorio 103 · sempre a data de inclusao de HOJE."""
    today = _as_date(hoje)
    return to_ddmm(today), to_ddmm(today)


def periodo_analise_diaria(hoje: date | datetime | None = None) -> tuple[str, str]:
    """Alias historico → agora D-1 (ver periodo_50_cadastramento)."""
    return periodo_50_cadastramento(hoje)


def periodo_sexta(hoje: date | datetime | None = None) -> tuple[str, str]:
    """
    Relatorio da sexta cadastrada (sai na segunda).

    Se hoje for sexta → sexta de hoje.
    Caso contrario → ultima sexta passada (ou a sexta da semana atual se ainda
    nao passou; usa a sexta anterior concluida quando hoje < sexta).
    """
    today = _as_date(hoje)
    weekday = today.weekday()
    if weekday == 4:  # sexta
        target = today
    elif weekday > 4:  # sab/dom → sexta desta semana
        target = today - timedelta(days=weekday - 4)
    else:  # seg–qui → sexta da semana passada
        target = today - timedelta(days=weekday + 3)
    return to_ddmm(target), to_ddmm(target)


def sugestao_periodo(
    modo: str = "diario",
    hoje: date | datetime | None = None,
) -> tuple[str, str]:
    """modo: 'diario' | 'sexta'."""
    if str(modo or "").strip().lower() in {"sexta", "friday", "sex"}:
        return periodo_sexta(hoje)
    return periodo_analise_diaria(hoje)
