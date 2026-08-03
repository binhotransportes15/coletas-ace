from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import re

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "data" / "downloads"
CACHE_DIR = BASE_DIR / "data" / "cache"
LOG_DIR = BASE_DIR / "data" / "logs"
SECRETS_DIR = BASE_DIR / "data" / "secrets"
DASHBOARD_DIR = BASE_DIR / "dashboard"
CONFIG_PATH = BASE_DIR / "data" / "config.json"
GOOGLE_SA_PATH = SECRETS_DIR / "google_service_account.json"

SSW_LOGIN_URL = "https://sistema.ssw.inf.br/bin/ssw0422"
SSW_78_PATH = "/bin/ssw1257"  # 078 - Descarga de Veículos

DEFAULT_COLETA_OPTION = "50"
DEFAULT_ENTREGA_OPTION = "36"


@dataclass(slots=True)
class SswCredentials:
    url: str = SSW_LOGIN_URL
    domain: str = "bin"
    document: str = "11491465832"
    user: str = "m.aguir"
    password: str = "114@mig"
    # Uma ou varias siglas: "SPO" | "SPO,LEO,RIS" | "*" (todas, sem filtro)
    unit: str = "SPO,LEO,RIS"


def parse_coleta_units(raw: str | None) -> list[str]:
    """
    Interpreta credencial/config `unit`.
    - "SPO,LEO,RIS" → ["SPO", "LEO", "RIS"]
    - "*" / "todas" / "all" / "" → []  (sem filtro de unidade no relatorio)
    """
    text = str(raw or "").strip()
    if not text:
        return []
    low = text.lower().replace(" ", "")
    if low in {"*", "todas", "all", "tudo"}:
        return []
    units: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;|/+\s]+", text):
        u = part.strip().upper()
        if not u or u in seen:
            continue
        if u in {"*", "TODAS", "ALL", "TUDO"}:
            return []
        seen.add(u)
        units.append(u)
    return units


def login_unit(raw: str | None) -> str:
    """Unidade usada no menu apos login (primeira da lista, se houver)."""
    units = parse_coleta_units(raw)
    return units[0] if units else ""


@dataclass(slots=True)
class AceSettings:
    coleta_option: str = DEFAULT_COLETA_OPTION
    entrega_option: str = DEFAULT_ENTREGA_OPTION
    periodo_modo: str = "diario"  # diario | sexta
    auto_baixar_ao_abrir: bool = True
    # Intervalo do modo /automatica (texto: 30s, 5m, 1h, 2d)
    loop_intervalo: str = "5m"
    enable_sheets: bool = False
    apps_script_url: str = ""
    apps_script_token: str = ""
    # legado (nao usado no fluxo Apps Script)
    google_sheet_id: str = ""
    enable_github_publish: bool = False
    github_repo: str = ""  # owner/repo
    github_branch: str = "main"
    github_token_env: str = "GH_TOKEN"
    # Se true, /automatica também captura a tela 078 no fim do ciclo
    # (Sheets 078 usa a mesma enable_sheets / apps_script_url / token)
    armazem_in_loop: bool = True
    headless: bool = True


def ensure_dirs() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data").mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data" / "armazem").mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def default_credentials() -> SswCredentials:
    return SswCredentials()


def default_settings() -> AceSettings:
    return AceSettings()


def _payload_settings(payload: dict, defaults: AceSettings) -> AceSettings:
    return AceSettings(
        coleta_option=str(payload.get("coleta_option") or defaults.coleta_option).strip()
        or defaults.coleta_option,
        entrega_option=str(payload.get("entrega_option") or "").strip(),
        periodo_modo=str(payload.get("periodo_modo") or defaults.periodo_modo).strip()
        or defaults.periodo_modo,
        auto_baixar_ao_abrir=bool(
            payload.get("auto_baixar_ao_abrir", defaults.auto_baixar_ao_abrir)
        ),
        loop_intervalo=str(
            payload.get("loop_intervalo") or defaults.loop_intervalo
        ).strip()
        or defaults.loop_intervalo,
        enable_sheets=bool(payload.get("enable_sheets", defaults.enable_sheets)),
        apps_script_url=str(payload.get("apps_script_url") or "").strip(),
        apps_script_token=str(payload.get("apps_script_token") or "").strip(),
        google_sheet_id=str(payload.get("google_sheet_id") or "").strip(),
        enable_github_publish=bool(
            payload.get("enable_github_publish", defaults.enable_github_publish)
        ),
        github_repo=str(payload.get("github_repo") or "").strip(),
        github_branch=str(payload.get("github_branch") or defaults.github_branch).strip()
        or defaults.github_branch,
        github_token_env=str(
            payload.get("github_token_env") or defaults.github_token_env
        ).strip()
        or defaults.github_token_env,
        armazem_in_loop=bool(payload.get("armazem_in_loop", defaults.armazem_in_loop)),
        headless=bool(payload.get("headless", defaults.headless)),
    )


def load_credentials() -> SswCredentials:
    ensure_dirs()
    defaults = default_credentials()
    if not CONFIG_PATH.exists():
        save_all(defaults, default_settings())
        return defaults
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return SswCredentials(
            url=str(payload.get("url") or defaults.url),
            domain=str(payload.get("domain") or defaults.domain),
            document=str(payload.get("document") or defaults.document),
            user=str(payload.get("user") or defaults.user),
            password=str(payload.get("password") or defaults.password),
            unit=str(payload.get("unit") or defaults.unit),
        )
    except Exception:
        return defaults


def load_settings() -> AceSettings:
    ensure_dirs()
    defaults = default_settings()
    if not CONFIG_PATH.exists():
        return defaults
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return _payload_settings(payload, defaults)
    except Exception:
        return defaults


def save_all(credentials: SswCredentials, settings: AceSettings) -> None:
    ensure_dirs()
    payload = {
        **asdict(credentials),
        **asdict(settings),
    }
    CONFIG_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def save_credentials(credentials: SswCredentials) -> None:
    save_all(credentials, load_settings())


def save_settings(settings: AceSettings) -> None:
    save_all(load_credentials(), settings)
