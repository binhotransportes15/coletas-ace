from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import (
    DOWNLOAD_DIR,
    AceSettings,
    SswCredentials,
    default_credentials,
    default_settings,
    ensure_dirs,
    login_unit,
    parse_coleta_units,
)
from dates import format_period, normalize_date, to_ssw_ddmmyy

StatusCallback = Callable[[str], None]

SSW_ORIGIN = "https://sistema.ssw.inf.br"

# Extensoes / padroes de relatorios SSW na pasta de downloads
_DOWNLOAD_CLEAN_PATTERNS = (
    "*.sswweb",
    "*.csv",
    "*.xlsx",
    "*.xls",
    "ssw0157*",
    "CSV*",
    "coleta_*",
)


def cleanup_downloads(
    download_dir: Path | None = None,
    *,
    keep: list[Path] | None = None,
    on_status: StatusCallback | None = None,
) -> int:
    """
    Remove relatorios antigos de data/downloads.
    Mantem apenas os caminhos em `keep` (ex.: arquivos recem-baixados).
    """
    folder = Path(download_dir or DOWNLOAD_DIR)
    if not folder.exists():
        return 0
    keep_set = {Path(p).resolve() for p in (keep or []) if p}
    seen: set[Path] = set()
    removed = 0
    for pattern in _DOWNLOAD_CLEAN_PATTERNS:
        for path in folder.glob(pattern):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved in keep_set:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    if on_status and removed:
        on_status(f"Limpeza downloads: {removed} arquivo(s) antigo(s) removido(s)")
    return removed


# Codigo menu → programa SSW
MENU_PROGRAM = {
    "50": "/bin/ssw0157",  # Relacao das Coletas
    "103": "/bin/ssw0166",  # 103 - Situacao de Coletas (coletas normais / Excel)
    "36": "/bin/ssw0146",  # 36 - Relacao de romaneios e CTRCs de entrega
}

_PATCH_CREATE_NEW_DOC = """
() => {
  if (window.__sswCreateNewDocPatched) return true;
  if (typeof window.createNewDoc !== 'function') return false;
  window.__sswCreateNewDocPatched = true;
  const original = window.createNewDoc;
  window.createNewDoc = function(pathname) {
    if (typeof newPage === 'undefined' || Number(newPage) !== 1) {
      return original.apply(this, arguments);
    }
    const html = (typeof valSep !== 'undefined' && valSep != null) ? String(valSep) : '';
    if (!html) {
      return original.apply(this, arguments);
    }
    let janela = null;
    try {
      janela = window.open('about:blank', '_blank');
    } catch (e) {
      janela = null;
    }
    if (!janela) {
      return original.apply(this, arguments);
    }
    const escrever = () => {
      try {
        janela.document.open('text/html', 'replace');
        janela.document.write(html);
        janela.document.close();
        if (pathname) {
          try { janela.history.pushState({}, '', pathname); } catch (e) {}
        }
        try { janela.focus(); } catch (e) {}
        window.janela = janela;
        return true;
      } catch (e) {
        return false;
      }
    };
    if (!escrever()) {
      try {
        janela.addEventListener('load', function() { escrever(); }, { once: true });
      } catch (e) {}
      setTimeout(escrever, 50);
      setTimeout(escrever, 250);
    }
  };
  return true;
}
"""


def _noop(_: str) -> None:
    return None


class AceSswClient:
    """
    ACE - Analisador Coleta Entrega.

    Automacao SSW:
    1) login
    2) opcao de coleta (padrao 50 / ssw0157) → baixa relatorio
    3) opcao de entrega (ainda em aberto) → so roda se estiver configurada
    """

    def __init__(
        self,
        start_date: str,
        end_date: str,
        *,
        credentials: SswCredentials | None = None,
        settings: AceSettings | None = None,
        download_dir: Path | None = None,
        keep_open: bool = False,
        headless: bool = False,
        on_status: StatusCallback | None = None,
        clean_downloads: bool = True,
    ) -> None:
        self.start_date_ui = normalize_date(start_date)
        self.end_date_ui = normalize_date(end_date)
        self.start_date = normalize_date(start_date)  # opcao 50 (CyberMap) usa DDMM
        self.end_date = normalize_date(end_date)
        self.start_date_yy = to_ssw_ddmmyy(start_date)
        self.end_date_yy = to_ssw_ddmmyy(end_date)
        self.credentials = credentials or default_credentials()
        self.settings = settings or default_settings()
        self.download_dir = Path(download_dir or DOWNLOAD_DIR)
        self.keep_open = bool(keep_open)
        self.headless = bool(headless)
        self.on_status = on_status or _noop
        self.clean_downloads = bool(clean_downloads)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.paths: dict[str, str] = {}

    def _cleanup_before_download(self) -> None:
        if not self.clean_downloads:
            return
        cleanup_downloads(self.download_dir, on_status=self.on_status)

    def run(self) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright nao esta instalado. Rode: pip install playwright && playwright install chromium"
            ) from error

        ensure_dirs()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_before_download()
        period = format_period(self.start_date_ui, self.end_date_ui)
        coleta = (self.settings.coleta_option or "50").strip()
        entrega = (self.settings.entrega_option or "").strip()
        self.on_status(f"ACE | Coleta={coleta} Entrega={entrega or '(a definir)'} | {period}")

        browser = None
        context = None
        with sync_playwright() as playwright:
            try:
                launch_kwargs: dict[str, Any] = {
                    "headless": self.headless,
                    "slow_mo": 0,
                }
                if self.headless:
                    launch_kwargs["args"] = ["--disable-dev-shm-usage"]
                browser = playwright.chromium.launch(**launch_kwargs)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(30000)

                self._login(page)
                self._ensure_unit(page)
                self._patch_blank_popup_fix(page)

                errors: dict[str, str] = {}

                try:
                    path = self._download_coleta(page, coleta)
                    self.paths["coleta"] = str(path)
                    self.on_status(f"Coleta ({coleta}) salva: {path.name}")
                except Exception as error:  # noqa: BLE001
                    errors["coleta"] = str(error)
                    self.on_status(f"Coleta ({coleta}) falhou: {error}")

                # Entrega 36 NAO roda aqui — so via run_36 / job dedicado do ciclo
                # (evita periodo errado da 50 e race com o job [36]).
                if entrega:
                    self.on_status(
                        f"Entrega={entrega}: pulando neste browser (use job 36 dedicado)."
                    )

                if not self.paths:
                    details = "; ".join(f"{k}: {v}" for k, v in errors.items())
                    raise RuntimeError(f"Nenhum arquivo baixado. {details}")

                if self.keep_open:
                    self.on_status("Navegador mantido aberto. Feche as janelas do SSW quando terminar.")
                    while True:
                        alive = []
                        for open_page in list(context.pages):
                            try:
                                if not open_page.is_closed():
                                    alive.append(open_page)
                            except Exception:
                                continue
                        if not alive:
                            break
                        try:
                            alive[0].wait_for_timeout(1000)
                        except Exception:
                            page.wait_for_timeout(1000)

                return {
                    "paths": dict(self.paths),
                    "errors": errors,
                    "period": period,
                    "coleta_option": coleta,
                    "entrega_option": entrega,
                    "download_dir": str(self.download_dir),
                }
            finally:
                if not self.keep_open:
                    if context is not None:
                        context.close()
                    if browser is not None:
                        browser.close()

    def _login(self, page) -> None:
        creds = self.credentials
        self.on_status("Efetuando login no SSW...")
        page.goto(creds.url, wait_until="domcontentloaded")
        page.locator('[id="1"]').wait_for()
        page.locator('[id="1"]').fill(creds.domain)
        page.locator('[id="2"]').fill(creds.document)
        page.locator('[id="3"]').fill(creds.user)
        page.locator('[id="3"]').press("Tab")
        page.locator('[id="4"]').fill(creds.password)
        page.locator("a").first.click()
        page.wait_for_timeout(6000)
        body_text = page.locator("body").inner_text(timeout=5000)
        if "Menu Principal" not in body_text and "menu01" not in page.url:
            raise RuntimeError("Falha no login: menu principal do SSW nao foi carregado.")
        self.on_status("Login concluido.")

    def _coleta_units(self) -> list[str]:
        return parse_coleta_units(getattr(self.credentials, "unit", "") or "")

    def _ensure_unit(self, page) -> None:
        # Menu pos-login: usa so a 1ª sigla (contexto do operador).
        # Relatorios 50/103 iteram SPO/LEO/RIS conforme config.
        unit = login_unit(getattr(self.credentials, "unit", "") or "")
        if not unit:
            return
        campo = page.locator('input[name="f2"][id="2"]')
        if campo.count() <= 0:
            return
        atual = (campo.first.input_value() or "").strip().upper()
        if atual == unit:
            return
        self.on_status(f"Ajustando unidade do menu para {unit}...")
        campo.first.fill(unit)
        campo.first.press("Tab")
        page.wait_for_timeout(500)

    def _merge_downloaded_files(self, paths: list[Path], dest_name: str) -> Path:
        """Concatena varios downloads (texto/CSV). Pula cabecalho repetido em CSV."""
        if not paths:
            raise RuntimeError("Nenhum arquivo para mesclar.")
        if len(paths) == 1:
            return paths[0]
        dest = self.download_dir / dest_name
        first_bytes = paths[0].read_bytes()
        is_csv_like = b"," in first_bytes[:800] or b";" in first_bytes[:800]
        with dest.open("wb") as out:
            for idx, src in enumerate(paths):
                data = src.read_bytes()
                if idx == 0 or not is_csv_like:
                    out.write(data)
                    if not data.endswith(b"\n"):
                        out.write(b"\n")
                    continue
                # Pula 1ª linha (cabecalho) nos arquivos seguintes
                nl = data.find(b"\n")
                if nl >= 0:
                    data = data[nl + 1 :]
                out.write(data)
                if data and not data.endswith(b"\n"):
                    out.write(b"\n")
        self.on_status(f"Mesclado {len(paths)} arquivo(s) → {dest.name}")
        for src in paths:
            try:
                if src.resolve() != dest.resolve() and src.exists():
                    src.unlink()
            except OSError:
                pass
        return dest

    def _patch_blank_popup_fix(self, page) -> None:
        try:
            ok = bool(page.evaluate(_PATCH_CREATE_NEW_DOC))
        except Exception:
            ok = False
        if ok:
            self.on_status("Correção blank.html ativa.")

    def _campo_opcao_menu(self, page):
        especifico = page.locator('input[name="f3"][id="3"]')
        if especifico.count() > 0:
            return especifico.first
        return page.locator('[id="3"]').first

    def _open_menu_option(self, page, option_code: str, *, markers: tuple[str, ...]):
        self.on_status(f"Abrindo opcao {option_code}...")
        page.bring_to_front()
        try:
            page.evaluate(_PATCH_CREATE_NEW_DOC)
        except Exception:
            pass

        menu = self._campo_opcao_menu(page)
        menu.wait_for(state="visible", timeout=20000)
        menu.click()
        menu.fill("")
        page.wait_for_timeout(150)
        menu.fill(option_code)

        existentes = set(page.context.pages)
        with page.expect_popup(timeout=30000) as popup_info:
            try:
                page.evaluate(
                    """(code) => {
                      const el = document.getElementById('3') || document.querySelector('input[name="f3"]');
                      if (el) el.value = code;
                      if (typeof doOption === 'function') doOption();
                      else if (typeof findme === 'function' && el) findme(el.value);
                    }""",
                    option_code,
                )
            except Exception:
                menu.press("Enter")
        popup = popup_info.value
        popup.set_default_timeout(30000)
        try:
            popup.wait_for_load_state("domcontentloaded")
        except Exception:
            pass
        popup = self._aguardar_tela_pronta(page, popup, existentes, option_code, markers)
        popup.bring_to_front()
        return popup

    def _popup_pronta(self, popup, markers: tuple[str, ...]) -> bool:
        try:
            if popup.is_closed():
                return False
        except Exception:
            return False
        url = (popup.url or "").lower()
        if "blank.html" in url or url.startswith("about:blank"):
            return False
        try:
            texto = (popup.locator("body").inner_text(timeout=1500) or "").strip().lower()
        except Exception:
            texto = ""
        if len(texto) < 20:
            return False
        return any(m.lower() in texto for m in markers) or len(texto) > 80

    def _recuperar_html(self, page) -> str:
        try:
            return str(
                page.evaluate(
                    """() => (typeof valSep !== 'undefined' && valSep) ? String(valSep) : ''"""
                )
                or ""
            )
        except Exception:
            return ""

    def _extrair_action(self, html: str) -> str:
        match = re.search(r'action=["\'](/bin/[^"\']+)["\']', html or "", flags=re.IGNORECASE)
        return match.group(1) if match else ""

    def _goto_programa(self, popup, caminho: str, markers: tuple[str, ...]) -> bool:
        if not caminho.startswith("/bin/"):
            return False
        try:
            self.on_status(f"Abrindo programa: {caminho}")
            popup.goto(f"{SSW_ORIGIN}{caminho}", wait_until="domcontentloaded")
            popup.wait_for_timeout(800)
            return self._popup_pronta(popup, markers)
        except Exception as error:
            self.on_status(f"Falha ao abrir {caminho}: {error}")
            return False

    def _recuperar_blank(
        self, page, popup, option_code: str, markers: tuple[str, ...]
    ) -> bool:
        html = self._recuperar_html(page)
        action = self._extrair_action(html)
        if action and self._goto_programa(popup, action, markers):
            return True
        fallback = MENU_PROGRAM.get(option_code, "")
        if fallback and self._goto_programa(popup, fallback, markers):
            return True
        if html and ("<!--html-->" in html or "<html" in html.lower()):
            try:
                popup.set_content(html, wait_until="domcontentloaded")
                return self._popup_pronta(popup, markers)
            except Exception:
                return False
        return False

    def _aguardar_tela_pronta(
        self,
        page,
        popup,
        existentes: set,
        option_code: str,
        markers: tuple[str, ...],
    ):
        deadline = time.time() + 25
        tentou = False
        while time.time() < deadline:
            for candidata in reversed(list(page.context.pages)):
                if candidata in existentes and candidata is not popup:
                    continue
                if self._popup_pronta(candidata, markers):
                    return candidata
            try:
                url = (popup.url or "").lower()
            except Exception:
                url = ""
            if ("blank.html" in url or url.startswith("about:blank")) and not tentou:
                tentou = True
                if self._recuperar_blank(page, popup, option_code, markers):
                    return popup
            page.wait_for_timeout(300)
        if self._recuperar_blank(page, popup, option_code, markers):
            return popup
        raise RuntimeError(f"Opcao {option_code} abriu em branco e nao carregou.")

    def _save_download(self, download, fallback_name: str) -> Path:
        suggested = Path(download.suggested_filename or "").name
        destination = self.download_dir / (suggested or fallback_name)
        if destination.exists():
            destination = destination.with_name(
                f"{destination.stem}_{self.timestamp}{destination.suffix}"
            )
        download.save_as(str(destination))
        return destination

    def _download_coleta(self, page, option_code: str) -> Path:
        code = (option_code or "50").strip() or "50"
        if code == "50":
            return self._download_report_50(page)
        raise RuntimeError(
            f"Opcao de coleta '{code}' ainda nao tem automacao. Use 50 (ssw0157)."
        )

    def _download_entrega(self, page, option_code: str) -> Path:
        code = (option_code or "").strip()
        if not code:
            raise RuntimeError("Opcao de entrega nao definida.")
        if code == "36":
            return self._download_report_36(page)
        raise RuntimeError(
            f"Opcao de entrega '{code}' ainda nao tem automacao. Use 36 (ssw0146)."
        )

    def _download_report_36(self, page) -> Path:
        """
        36 - Consulta romaneios/CTRCs (ssw0146):
          Excel = S
          Unidade vazia = todas (campo opc) — evita 3x REL2 em paralelo com 50/103
          Periodo = D-1 .. hoje (DDMMYY)
          Gerar = ajaxEnvia('REL2') via #btn_env_periodo
        """
        units = self._coleta_units()
        # Unidade (opc): se varias siglas na config, 1 download sem filtro
        # (SSW devolve SPO+LEO+RIS). Override: 1 sigla sozinha ainda filtra.
        if len(units) == 1:
            passes = units
            label = units[0]
        else:
            passes = [""]
            label = "TODAS" if not units else ",".join(units) + " (1x sem filtro)"
        self.on_status(
            f"Gerando 36 Excel | periodo {self.start_date_yy} a {self.end_date_yy} | un={label}..."
        )
        paths: list[Path] = []
        for un in passes:
            paths.append(self._download_report_36_once(page, un))
        if len(paths) == 1:
            return paths[0]
        return self._merge_downloaded_files(
            paths,
            f"entrega_36_{self.start_date_yy}_{self.end_date_yy}_{self.timestamp}_merged.sswweb",
        )

    def _download_report_36_once(self, page, unidade: str) -> Path:
        popup = self._open_menu_option(
            page,
            "36",
            markers=(
                "romaneio",
                "entrega",
                "periodo",
                "unidade",
                "excel",
                "0146",
                "36",
            ),
        )
        try:
            # Fecha alertas SSW que bloqueiam o download
            popup.on("dialog", lambda d: d.accept())
            self._preencher_tela_36(popup, unidade=unidade)
            popup.wait_for_timeout(400)
            # Mesmo padrao do 50/103: expect_download e da Page (popup), nao do Context
            with popup.expect_download(timeout=180000) as download_info:
                self._clicar_gerar_36(popup)
            suffix = (unidade or "todas").lower()
            dest_name = (
                f"entrega_36_{self.start_date_yy}_{self.end_date_yy}_{suffix}_{self.timestamp}.sswweb"
            )
            download = download_info.value
            suggested = (download.suggested_filename or "").lower()
            if suggested.endswith(".xlsx"):
                dest_name = dest_name.replace(".sswweb", ".xlsx")
            elif suggested.endswith(".csv"):
                dest_name = dest_name.replace(".sswweb", ".csv")
            elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
                dest_name = dest_name.replace(".sswweb", ".xls")
            path = self._save_download(download, dest_name)
            self.on_status(f"36 arquivo ({suffix}): {path.name} ({path.stat().st_size} bytes)")
            return path
        finally:
            try:
                popup.close()
            except Exception:
                pass

    def _preencher_tela_36(self, popup, *, unidade: str = "") -> None:
        """
        ssw0146:
          t_excel = S
          t_unidade = sigla ou vazio (todas)
          t_dt_ini / t_dt_fin = DDMMYY
          limpa busca pontual (romaneio/ciot/mdfe/placa/cpf)
        """
        ini, fim = self.start_date_yy, self.end_date_yy
        un = (unidade or "").strip().upper()
        result = popup.evaluate(
            """([ini, fim, unidade]) => {
              const setVal = (id, v) => {
                const el = document.getElementById(String(id));
                if (!el) return false;
                el.focus();
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                try { el.blur(); } catch (e) {}
                return true;
              };
              // Limpa busca pontual — usa so "Romaneios do periodo"
              ['t_sigla_rom','t_nro_rom','t_cod_barras_rom','t_ciot','t_ser_mdfe',
               't_nro_mdfe','t_placa_veic','t_cpf_motorista'].forEach(id => setVal(id, ''));
              const okExcel = setVal('t_excel', 'S');
              const okUn = setVal('t_unidade', unidade || '');
              const okIni = setVal('t_dt_ini', ini);
              const okFim = setVal('t_dt_fin', fim);
              return {
                ok: okExcel && okUn && okIni && okFim,
                values: {
                  excel: (document.getElementById('t_excel') || {}).value || '',
                  unidade: (document.getElementById('t_unidade') || {}).value || '',
                  periodo: [
                    (document.getElementById('t_dt_ini') || {}).value || '',
                    (document.getElementById('t_dt_fin') || {}).value || '',
                  ],
                },
              };
            }""",
            [ini, fim, un],
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"36: falha ao preencher ssw0146: {result}")
        self.on_status(
            f"36 preenchido: periodo {ini}-{fim} | excel=S | un={un or 'TODAS'} | {result.get('values')}"
        )
        popup.wait_for_timeout(500)

    def _clicar_gerar_36(self, popup) -> None:
        loc = popup.locator('#btn_env_periodo, a[onclick*="REL2"]')
        try:
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                self.on_status("36: clique gerar via #btn_env_periodo (REL2)")
                return
        except Exception:
            pass
        clicked = popup.evaluate(
            """() => {
              if (typeof ajaxEnvia === 'function') {
                ajaxEnvia('REL2', 1);
                return true;
              }
              return false;
            }"""
        )
        if clicked:
            self.on_status("36: clique gerar via ajaxEnvia('REL2')")
            return
        raise RuntimeError("36: nao achei botao REL2 / #btn_env_periodo.")

    def run_36(self) -> dict[str, Any]:
        """Baixa somente a opcao 36 (Excel romaneios/CTRCs entrega)."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright nao esta instalado. Rode: pip install playwright && playwright install chromium"
            ) from error

        ensure_dirs()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_before_download()
        period = format_period(self.start_date_ui, self.end_date_ui)
        self.on_status(f"ACE 36 | romaneios/CTRCs {period} | Excel")

        browser = None
        context = None
        with sync_playwright() as playwright:
            try:
                launch_kwargs: dict[str, Any] = {
                    "headless": self.headless,
                    "slow_mo": 0,
                }
                if self.headless:
                    launch_kwargs["args"] = ["--disable-dev-shm-usage"]
                browser = playwright.chromium.launch(**launch_kwargs)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(30000)
                self._login(page)
                self._ensure_unit(page)
                self._patch_blank_popup_fix(page)
                path = self._download_report_36(page)
                self.paths["entrega_36"] = str(path)
                self.on_status(f"36 Excel salvo: {path.name}")
                return {
                    "paths": dict(self.paths),
                    "errors": {},
                    "period": period,
                    "entrega_option": "36",
                    "download_dir": str(self.download_dir),
                }
            finally:
                if not self.keep_open:
                    if context is not None:
                        context.close()
                    if browser is not None:
                        browser.close()


    def _download_report_50(self, page) -> Path:
        """050 - Relacao das Coletas (ssw0157) pelo Periodo de COLETA (hoje)."""
        units = self._coleta_units()
        # [] = sem filtro (1 download); varias siglas = 1 download por unidade
        passes = units if units else [""]
        label = ",".join(passes) if units else "TODAS"
        self.on_status(
            f"Gerando coleta (50) | periodo de coleta "
            f"{format_period(self.start_date_ui, self.end_date_ui)} "
            f"({self.start_date_yy} a {self.end_date_yy}) | un={label}..."
        )
        paths: list[Path] = []
        for un in passes:
            paths.append(self._download_report_50_once(page, un))
        if len(paths) == 1:
            return paths[0]
        return self._merge_downloaded_files(
            paths,
            f"coleta_50_col_{self.start_date_yy}_{self.end_date_yy}_{self.timestamp}_merged.sswweb",
        )

    def _download_report_50_once(self, page, unidade: str) -> Path:
        popup = self._open_menu_option(
            page,
            "50",
            markers=("coleta", "050", "periodo", "ssw0157", "relacao", "cadastr"),
        )
        try:
            popup.locator('[id="4"]').wait_for()
            self._preencher_periodo_coleta_50(popup, unidade=unidade)
            with popup.expect_download(timeout=120000) as download_info:
                popup.locator('[id="21"]').click()
            suffix = (unidade or "todas").lower()
            return self._save_download(
                download_info.value,
                f"coleta_50_col_{self.start_date_yy}_{self.end_date_yy}_{suffix}_{self.timestamp}.sswweb",
            )
        finally:
            try:
                popup.close()
            except Exception:
                pass

    def _preencher_periodo_coleta_50(self, popup, *, unidade: str = "") -> None:
        """
        Tela ssw0157:
        - Usa 'Periodo de coleta (opc)' = HOJE (DDMMYY)
        - Limpa 'Periodo de cadastramento (opc)'
        - Unidade = sigla (SPO/LEO/RIS) ou vazio = todas
        Layout tipico CyberMap: 4/5 = coleta, 6/7 = cadastramento
        """
        ini = self.start_date_yy
        fim = self.end_date_yy
        un = (unidade or "").strip().upper()

        filled = popup.evaluate(
            """([ini, fim, unidade]) => {
              const norm = (t) => String(t || '')
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '');
              const setPair = (a, b, v1, v2) => {
                if (!a || !b) return false;
                a.focus(); a.value = v1;
                a.dispatchEvent(new Event('input', {bubbles:true}));
                a.dispatchEvent(new Event('change', {bubbles:true}));
                b.focus(); b.value = v2;
                b.dispatchEvent(new Event('input', {bubbles:true}));
                b.dispatchEvent(new Event('change', {bubbles:true}));
                return true;
              };
              const clearPair = (a, b) => setPair(a, b, '', '');
              const setVal = (el, v) => {
                if (!el) return false;
                el.focus();
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                return true;
              };
              const nodes = Array.from(document.querySelectorAll('div, span, td, label, font'));
              const findInputsAfter = (pred) => {
                const label = nodes.find(n => pred(norm(n.textContent || '')));
                const inputs = [];
                if (!label) return inputs;
                let el = label;
                for (let i = 0; i < 8 && el; i++) {
                  el = el.nextElementSibling || (el.parentElement && el.parentElement.nextElementSibling);
                  if (!el) break;
                  if (el.tagName === 'INPUT') inputs.push(el);
                  el.querySelectorAll && el.querySelectorAll('input').forEach(inp => inputs.push(inp));
                  if (inputs.length >= 2) break;
                }
                return inputs;
              };
              // Unidade (filtro): limpa ou preenche
              let unOk = false;
              const unLabel = nodes.find(n => {
                const t = norm(n.textContent || '');
                return t.includes('unidade') && !t.includes('cadastr') && (t.length < 40);
              });
              if (unLabel) {
                let el = unLabel;
                for (let i = 0; i < 6 && el; i++) {
                  el = el.nextElementSibling || (el.parentElement && el.parentElement.nextElementSibling);
                  if (!el) break;
                  const inp = el.tagName === 'INPUT' ? el : (el.querySelector && el.querySelector('input'));
                  if (inp && inp.type !== 'hidden') {
                    unOk = setVal(inp, unidade || '');
                    break;
                  }
                }
              }
              if (!unOk) {
                const cand = document.getElementById('2') || document.getElementById('3');
                if (cand) unOk = setVal(cand, unidade || '');
              }
              const coletaInputs = findInputsAfter(t =>
                t.includes('periodo') && t.includes('coleta') && !t.includes('cadastr')
              );
              const cadInputs = findInputsAfter(t =>
                t.includes('cadastramento') && t.includes('periodo')
              );
              if (cadInputs.length >= 2) clearPair(cadInputs[0], cadInputs[1]);
              if (coletaInputs.length >= 2) {
                setPair(coletaInputs[0], coletaInputs[1], ini, fim);
                return {
                  ok: true,
                  via: 'label',
                  ids: [coletaInputs[0].id, coletaInputs[1].id],
                  unidade: unidade || '',
                  unOk,
                };
              }
              return {ok: false, unOk, unidade: unidade || ''};
            }""",
            [ini, fim, un],
        )
        if filled and filled.get("ok"):
            self.on_status(
                f"Periodo de coleta preenchido ({filled.get('via')}): {ini} a {fim} | "
                f"un={un or 'TODAS'}"
            )
            return

        # Fallback CyberMap: preenche coleta 4/5, limpa cadastramento 6/7
        popup.locator('[id="4"]').fill(ini)
        popup.locator('[id="4"]').press("Tab")
        popup.locator('[id="5"]').fill(fim)
        popup.locator('[id="5"]').press("Tab")
        for fid in ("6", "7"):
            try:
                popup.locator(f'[id="{fid}"]').fill("")
            except Exception:
                pass
        # tenta unidade em campos comuns
        for fid in ("2", "3", "8"):
            try:
                loc = popup.locator(f'[id="{fid}"]')
                if loc.count() > 0:
                    loc.first.fill(un)
                    break
            except Exception:
                pass
        self.on_status(
            f"Periodo de coleta (campos 4/5): {ini} a {fim} | un={un or 'TODAS'} | cadastramento limpo"
        )

    def run_103(self) -> dict[str, Any]:
        """Baixa somente a opcao 103 (Excel coletas normais)."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright nao esta instalado. Rode: pip install playwright && playwright install chromium"
            ) from error

        ensure_dirs()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_before_download()
        period = format_period(self.start_date_ui, self.end_date_ui)
        self.on_status(f"ACE 103 | data limite {period} | Excel")

        browser = None
        context = None
        with sync_playwright() as playwright:
            try:
                launch_kwargs: dict[str, Any] = {
                    "headless": self.headless,
                    "slow_mo": 0,
                }
                if self.headless:
                    launch_kwargs["args"] = ["--disable-dev-shm-usage"]
                browser = playwright.chromium.launch(**launch_kwargs)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(30000)
                self._login(page)
                self._ensure_unit(page)
                self._patch_blank_popup_fix(page)
                path = self._download_report_103(page)
                self.paths["coleta_103"] = str(path)
                self.on_status(f"103 Excel salvo: {path.name}")
                return {
                    "paths": dict(self.paths),
                    "errors": {},
                    "period": period,
                    "coleta_option": "103",
                    "download_dir": str(self.download_dir),
                }
            finally:
                if not self.keep_open:
                    if context is not None:
                        context.close()
                    if browser is not None:
                        browser.close()

    def _download_report_103(self, page) -> Path:
        """
        103 - Coletas normais:
          Periodo de pesquisa = HOJE (DDMMYY)
          Por data de = L (limite)
          Mostrar em = E (excel)
          Unidade = cada sigla em config (SPO,LEO,RIS) ou vazio = todas
        """
        units = self._coleta_units()
        passes = units if units else [""]
        label = ",".join(passes) if units else "TODAS"
        self.on_status(
            f"Gerando 103 Excel | limite {self.start_date_yy} a {self.end_date_yy} | un={label}..."
        )
        paths: list[Path] = []
        for un in passes:
            paths.append(self._download_report_103_once(page, un))
        if len(paths) == 1:
            return paths[0]
        # Mescla CSV/sswweb; xlsx multi nao mescla binario — usa o 1º e avisa se misturado
        exts = {p.suffix.lower() for p in paths}
        if exts & {".xlsx", ".xls"} and len(exts) > 1:
            self.on_status("103: formatos mistos no merge — usando concatenacao texto.")
        return self._merge_downloaded_files(
            paths,
            f"coleta_103_lim_{self.start_date_yy}_{self.end_date_yy}_{self.timestamp}_merged.sswweb",
        )

    def _download_report_103_once(self, page, unidade: str) -> Path:
        popup = self._open_menu_option(
            page,
            "103",
            markers=(
                "coleta",
                "normal",
                "pesquisa",
                "limite",
                "inclus",
                "excel",
                "periodo",
                "unidade",
                "103",
            ),
        )
        try:
            self._preencher_tela_103(popup, unidade=unidade)
            with popup.expect_download(timeout=180000) as download_info:
                self._clicar_gerar_103(popup)
            suffix = (unidade or "todas").lower()
            dest_name = (
                f"coleta_103_lim_{self.start_date_yy}_{self.end_date_yy}_{suffix}_{self.timestamp}.sswweb"
            )
            download = download_info.value
            suggested = (download.suggested_filename or "").lower()
            if suggested.endswith(".xlsx"):
                dest_name = dest_name.replace(".sswweb", ".xlsx")
            elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
                dest_name = dest_name.replace(".sswweb", ".xls")
            elif suggested.endswith(".csv"):
                dest_name = dest_name.replace(".sswweb", ".csv")
            return self._save_download(download, dest_name)
        finally:
            try:
                popup.close()
            except Exception:
                pass

    def _preencher_tela_103(self, popup, *, unidade: str = "") -> None:
        """
        ssw0166 · bloco Coletas normais:
          #14/#15 periodo DDMMYY
          #16 Por data de (L=limite)
          #17 Mostrar em (E=excel)
          #19 Unidade de coleta (opc) — SPO/LEO/RIS ou vazio
        """
        ini, fim = self.start_date_yy, self.end_date_yy
        un = (unidade or "").strip().upper()
        result = popup.evaluate(
            """([ini, fim, unidade]) => {
              const setVal = (id, v) => {
                const el = document.getElementById(String(id));
                if (!el) return false;
                el.focus();
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                return true;
              };
              const ok14 = setVal(14, ini);
              const ok15 = setVal(15, fim);
              const ok16 = setVal(16, 'L');
              const ok17 = setVal(17, 'E');
              const ok19 = setVal(19, unidade || '');
              return {
                ok: ok14 && ok15 && ok16 && ok17 && ok19,
                values: {
                  periodo: [
                    (document.getElementById('14') || {}).value || '',
                    (document.getElementById('15') || {}).value || '',
                  ],
                  por_data: (document.getElementById('16') || {}).value || '',
                  mostrar: (document.getElementById('17') || {}).value || '',
                  unidade: (document.getElementById('19') || {}).value || '',
                },
              };
            }""",
            [ini, fim, un],
        )
        if not result or not result.get("ok"):
            raise RuntimeError(
                f"103: falha ao preencher campos ssw0166 (ids 14-17/19): {result}"
            )
        self.on_status(
            f"103 preenchido: periodo {ini}-{fim} | data=L (limite) | excel=E | "
            f"un={un or 'TODAS'} | {result.get('values')}"
        )
        popup.wait_for_timeout(300)

    def _clicar_gerar_103(self, popup) -> None:
        # Coletas normais → seta da Unidade de coleta = ajaxEnvia('FIL_COL', 1)
        candidates = [
            'a[onclick*="FIL_COL"]',
            '[id="20"]',
            'a[onclick*="REM_COL"]',
            'a[onclick*="DES_COL"]',
            'a[onclick*="GRU_COL"]',
        ]
        for sel in candidates:
            loc = popup.locator(sel)
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    self.on_status(f"103: clique gerar via {sel}")
                    return
            except Exception:
                continue
        clicked = popup.evaluate(
            """() => {
              if (typeof ajaxEnvia === 'function') {
                ajaxEnvia('FIL_COL', 1);
                return true;
              }
              return false;
            }"""
        )
        if clicked:
            self.on_status("103: clique gerar via ajaxEnvia('FIL_COL')")
            return
        raise RuntimeError("103: nao achei botao FIL_COL para gerar o Excel.")


def download_ace_reports(
    start_date: str,
    end_date: str,
    *,
    keep_open: bool = False,
    headless: bool = False,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    client = AceSswClient(
        start_date,
        end_date,
        keep_open=keep_open,
        headless=headless,
        on_status=on_status,
        credentials=credentials,
        settings=settings,
        clean_downloads=clean_downloads,
    )
    return client.run()


def download_ace_103(
    start_date: str,
    end_date: str,
    *,
    keep_open: bool = False,
    headless: bool = False,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    client = AceSswClient(
        start_date,
        end_date,
        keep_open=keep_open,
        headless=headless,
        on_status=on_status,
        credentials=credentials,
        settings=settings,
        clean_downloads=clean_downloads,
    )
    return client.run_103()


def download_ace_36(
    start_date: str,
    end_date: str,
    *,
    keep_open: bool = False,
    headless: bool = False,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    client = AceSswClient(
        start_date,
        end_date,
        keep_open=keep_open,
        headless=headless,
        on_status=on_status,
        credentials=credentials,
        settings=settings,
        clean_downloads=clean_downloads,
    )
    return client.run_36()
