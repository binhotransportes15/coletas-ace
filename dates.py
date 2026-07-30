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


def periodo_analise_diaria(hoje: date | datetime | None = None) -> tuple[str, str]:
    """
    Periodo de CADASTRAMENTO para a opcao 50 (campo 'Periodo de cadastramento').

    Hoje = D → puxa coletas cadastradas em D-2 (performance do dia anterior D-1).
    Ex.: hoje 30/07 → cadastramento 28/07 (performance de 29/07).

    Segunda: cobre sexta→sabado (cadastro sex/sab):
      ini = sexta (hoje-3), fim = sabado (hoje-2)
    """
    today = _as_date(hoje)
    weekday = today.weekday()  # 0=seg ... 6=dom
    if weekday == 0:  # segunda
        ini = today - timedelta(days=3)  # sexta
        fim = today - timedelta(days=2)  # sabado
    else:
        ini = fim = today - timedelta(days=2)
    return to_ddmm(ini), to_ddmm(fim)


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
