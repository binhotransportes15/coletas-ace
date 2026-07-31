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
    Relatorio 50 · Periodo de CADASTRAMENTO (legado D-1).

    Mantido para referencia; o fluxo automatico atual usa
    periodo_50_coleta_hoje (Periodo de coleta = HOJE).
    """
    today = _as_date(hoje)
    weekday = today.weekday()  # 0=seg ... 6=dom
    if weekday == 0:  # segunda
        ini = today - timedelta(days=3)  # sexta
        fim = today - timedelta(days=2)  # sabado
    else:
        ini = fim = today - timedelta(days=1)
    return to_ddmm(ini), to_ddmm(fim)


def periodo_50_coleta_hoje(hoje: date | datetime | None = None) -> tuple[str, str]:
    """Relatorio 50 · Periodo de COLETA = HOJE (sempre o dia atual)."""
    today = _as_date(hoje)
    return to_ddmm(today), to_ddmm(today)


def periodo_103_hoje(hoje: date | datetime | None = None) -> tuple[str, str]:
    """Relatorio 103 · data LIMITE de HOJE (Por data de = L)."""
    today = _as_date(hoje)
    return to_ddmm(today), to_ddmm(today)


def periodo_36_ontem_hoje(hoje: date | datetime | None = None) -> tuple[str, str]:
    """
    Relatorio 36 · periodo de pesquisa: D-1 (inicio) ate HOJE (fim).
    Baixas com data de ocorrencia = ontem sao descartadas no parser.
    """
    today = _as_date(hoje)
    ontem = today - timedelta(days=1)
    return to_ddmm(ontem), to_ddmm(today)


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
