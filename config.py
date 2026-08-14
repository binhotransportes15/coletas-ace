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
SSW_019_PATH = "/bin/ssw0036"  # 019 - CTRCs disponíveis (reciclagem)
SSW_081_PATH = "/bin/ssw0052"  # 081 - CTRCs disponíveis para entrega

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
    # Intervalo padrão do /automatica (fallback se o setor não tiver tempo próprio)
    # texto: 30s, 5m, 1h, 2d
    loop_intervalo: str = "5m"
    # Tempos por setor (vazio = usa loop_intervalo)
    dist_intervalo: str = ""
    armazem_intervalo: str = ""
    pendencia_intervalo: str = ""
    contratacao_intervalo: str = ""
    emissao_intervalo: str = ""
    reciclagem_intervalo: str = "30m"
    enable_sheets: bool = False
    apps_script_url: str = ""
    apps_script_token: str = ""
    # legado (nao usado no fluxo Apps Script)
    google_sheet_id: str = ""
    enable_github_publish: bool = False
    github_repo: str = ""  # owner/repo
    github_branch: str = "main"
    github_token_env: str = "GH_TOKEN"
    # Parede TV: sites | github | local | auto (ver docs/CONCEITO_SITES.md)
    publish_target: str = "auto"
    # URL pública do Google Sites (comando `sites` / piloto)
    google_sites_url: str = ""
    # O que entra no /automatica
    dist_in_loop: bool = True  # 50+103(+36)+225
    armazem_in_loop: bool = True  # 078
    pendencia_in_loop: bool = True  # 031
    contratacao_in_loop: bool = True  # 073
    emissao_in_loop: bool = False  # 455
    reciclagem_in_loop: bool = False  # 019 + 081
    # /automatica: blocos em paralelo (1 browser cada)
    ciclo_paralelo: bool = True
    # Modo local: não envia Sheets/GitHub — só cache CSV + JSON em data/cache/local
    modo_local: bool = False
    # Servir dashboard na LAN (0.0.0.0) para outros aparelhos na mesma rede
    dashboard_lan: bool = False
    # Porta fixa na LAN (0 = automática). Padrão útil: 8787
    dashboard_port: int = 8787
    headless: bool = True
    # Tema visual do CRT (binho | painel | ops | claro | fosco)
    crt_theme: str = "binho"
    # Tema fosco: transparência 0–100 (ver fundo) e blur 0–100 (fosco Windows)
    crt_frost_alpha: int = 55
    crt_frost_blur: int = 70


def ensure_dirs() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data").mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data" / "armazem").mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data" / "pendencia").mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data" / "contratacao").mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data" / "emissao").mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data" / "reciclagem").mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "local").mkdir(parents=True, exist_ok=True)
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
        dist_intervalo=str(payload.get("dist_intervalo") or "").strip(),
        armazem_intervalo=str(payload.get("armazem_intervalo") or "").strip(),
        pendencia_intervalo=str(payload.get("pendencia_intervalo") or "").strip(),
        contratacao_intervalo=str(payload.get("contratacao_intervalo") or "").strip(),
        emissao_intervalo=str(payload.get("emissao_intervalo") or "").strip(),
        reciclagem_intervalo=str(
            payload.get("reciclagem_intervalo") or defaults.reciclagem_intervalo or "30m"
        ).strip()
        or "30m",
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
        publish_target=str(payload.get("publish_target") or defaults.publish_target)
        .strip()
        .lower()
        or defaults.publish_target,
        google_sites_url=str(payload.get("google_sites_url") or "").strip(),
        dist_in_loop=bool(payload.get("dist_in_loop", defaults.dist_in_loop)),
        armazem_in_loop=bool(payload.get("armazem_in_loop", defaults.armazem_in_loop)),
        pendencia_in_loop=bool(payload.get("pendencia_in_loop", defaults.pendencia_in_loop)),
        emissao_in_loop=bool(payload.get("emissao_in_loop", defaults.emissao_in_loop)),
        reciclagem_in_loop=bool(
            payload.get("reciclagem_in_loop", defaults.reciclagem_in_loop)
        ),
        contratacao_in_loop=bool(
            payload.get("contratacao_in_loop", defaults.contratacao_in_loop)
        ),
        ciclo_paralelo=bool(payload.get("ciclo_paralelo", defaults.ciclo_paralelo)),
        modo_local=bool(payload.get("modo_local", defaults.modo_local)),
        dashboard_lan=bool(payload.get("dashboard_lan", defaults.dashboard_lan)),
        dashboard_port=int(payload.get("dashboard_port") or defaults.dashboard_port or 8787),
        headless=bool(payload.get("headless", defaults.headless)),
        crt_theme=str(payload.get("crt_theme") or defaults.crt_theme).strip()
        or defaults.crt_theme,
        crt_frost_alpha=max(
            0,
            min(100, int(payload.get("crt_frost_alpha", defaults.crt_frost_alpha) or 0)),
        ),
        crt_frost_blur=max(
            0,
            min(100, int(payload.get("crt_frost_blur", defaults.crt_frost_blur) or 0)),
        ),
    )


def sheets_enabled(settings: AceSettings | None = None) -> bool:
    """True só se planilha ligada E não estiver em modo local."""
    cfg = settings or load_settings()
    if getattr(cfg, "modo_local", False):
        return False
    return bool(getattr(cfg, "enable_sheets", False))


def resolve_publish_target(settings: AceSettings | None = None) -> str:
    """sites | github | local — ver docs/CONCEITO_SITES.md."""
    cfg = settings or load_settings()
    raw = str(getattr(cfg, "publish_target", "") or "auto").strip().lower()
    if raw in {"sites", "site", "googlesites"}:
        return "sites"
    if raw in {"github", "gh", "pages"}:
        return "github"
    if raw in {"local", "lan", "offline"}:
        return "local"
    # auto
    if getattr(cfg, "modo_local", False):
        return "local"
    if getattr(cfg, "enable_github_publish", False):
        return "github"
    if getattr(cfg, "enable_sheets", False):
        return "sites"
    return "local"


def github_publish_allowed(settings: AceSettings | None = None) -> bool:
    """Push Pages só se destino for github e flags ok."""
    cfg = settings or load_settings()
    if getattr(cfg, "modo_local", False):
        return False
    if resolve_publish_target(cfg) != "github":
        return False
    return bool(getattr(cfg, "enable_github_publish", False))


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
