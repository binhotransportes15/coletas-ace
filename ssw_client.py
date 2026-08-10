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
    "225": "/bin/ssw2862",  # 225 - Acompanhamento dos agendamentos de entrega
    "31": "/bin/ssw0495",  # 031 - CTRCs com determinada ocorrência
    "73": "/bin/ssw0332",  # 073 - Consulta de CTRBs e OSs (CSVssw0332)
    "156": "/bin/ssw1440",  # 156 - Fila de processamento em lotes
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

    def set_period(self, start_date: str, end_date: str) -> None:
        """Atualiza o periodo dos downloads seguintes (mesma sessao/login)."""
        self.start_date_ui = normalize_date(start_date)
        self.end_date_ui = normalize_date(end_date)
        self.start_date = normalize_date(start_date)
        self.end_date = normalize_date(end_date)
        self.start_date_yy = to_ssw_ddmmyy(start_date)
        self.end_date_yy = to_ssw_ddmmyy(end_date)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

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
        try:
            page.on("dialog", lambda d: d.accept())
        except Exception:
            pass
        page.goto(creds.url, wait_until="domcontentloaded")
        page.locator('[id="1"]').wait_for()
        page.locator('[id="1"]').fill(creds.domain)
        page.locator('[id="2"]').fill(creds.document)
        page.locator('[id="3"]').fill(creds.user)
        page.locator('[id="3"]').press("Tab")
        page.locator('[id="4"]').fill(creds.password)

        # Confirma que os campos ficaram preenchidos (SSW às vezes limpa)
        try:
            got = {
                "1": (page.locator('[id="1"]').input_value() or "").strip(),
                "2": (page.locator('[id="2"]').input_value() or "").strip(),
                "3": (page.locator('[id="3"]').input_value() or "").strip(),
                "4": (page.locator('[id="4"]').input_value() or "").strip(),
            }
            if not got["1"] or not got["2"] or not got["3"] or not got["4"]:
                page.locator('[id="1"]').fill(creds.domain)
                page.locator('[id="2"]').fill(creds.document)
                page.locator('[id="3"]').fill(creds.user)
                page.locator('[id="4"]').fill(creds.password)
        except Exception:
            pass

        # Submit: o botão da tela de login é o ► (Enter sozinho não entra)
        submitted = False
        for attempt in (
            lambda: page.get_by_text("►", exact=True).first.click(timeout=3000),
            lambda: page.locator("a", has_text="►").first.click(timeout=3000),
            lambda: page.locator("a").first.click(timeout=3000),
            lambda: page.locator('[id="4"]').press("Enter"),
        ):
            try:
                attempt()
                submitted = True
                break
            except Exception:
                continue
        if not submitted:
            raise RuntimeError("Falha no login: botão ► / Enter nao encontrado.")

        deadline = time.time() + 25
        body_text = ""
        while time.time() < deadline:
            try:
                url = (page.url or "").lower()
            except Exception:
                url = ""
            try:
                body_text = page.locator("body").inner_text(timeout=3000) or ""
            except Exception:
                body_text = ""
            if "Menu Principal" in body_text or "menu01" in url:
                self.on_status("Login concluido.")
                return

            low = body_text.lower()
            # Sessão presa (crash anterior / Chrome aberto): tenta forçar entrada
            if any(
                t in low
                for t in (
                    "ja conectado",
                    "já conectado",
                    "usuario conectado",
                    "usuário conectado",
                    "sessao ativa",
                    "sessão ativa",
                    "em uso",
                )
            ):
                self.on_status("SSW: sessão já conectada — tentando forçar…")
                clicked = False
                for label in ("Sim", "OK", "Continuar", "Forçar", "Forcar", "Entrar"):
                    try:
                        loc = page.get_by_text(label, exact=True)
                        if loc.count() > 0:
                            loc.first.click(timeout=2000)
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    try:
                        page.evaluate(
                            """() => {
                              const els = Array.from(document.querySelectorAll('a, button, input[type=button], input[type=submit]'));
                              for (const el of els) {
                                const t = ((el.value || el.innerText || el.textContent || '') + '').trim();
                                if (/^(sim|ok|continuar|for[cç]ar|entrar)$/i.test(t)) { el.click(); return t; }
                              }
                              return '';
                            }"""
                        )
                    except Exception:
                        pass
                page.wait_for_timeout(1500)
                continue

            page.wait_for_timeout(800)

        snippet = " ".join((body_text or "").split())
        if len(snippet) > 220:
            snippet = snippet[:220] + "…"
        hint = snippet or "(página sem texto)"
        raise RuntimeError(
            "Falha no login: menu principal do SSW nao foi carregado. "
            f"Tela: {hint}"
        )

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

    # Alias: callers usam "_form" (histórico)
    _patch_blank_popup_form = _patch_blank_popup_fix
    _patch_blank_popup_forms = _patch_blank_popup_fix

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
          Unidade = sempre SPO (entregas)
          Periodo = seg: SEXTA..hoje | demais: D-1..hoje (DDMMYY)
          Gerar = #btn_env_periodo → ajaxEnvia('REL2')
        """
        unidade = "SPO"
        self.on_status(
            f"Gerando 36 Excel | periodo {self.start_date_yy} a {self.end_date_yy} | un={unidade}..."
        )
        return self._download_report_36_once(page, unidade)

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
            self._fechar_lookup_36(popup)
            popup.wait_for_timeout(400)
            with popup.expect_download(timeout=180000) as download_info:
                self._clicar_gerar_36(popup)
            suffix = (unidade or "spo").lower()
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

    def _preencher_tela_36(self, popup, *, unidade: str = "SPO") -> None:
        """
        ssw0146:
          t_excel = S, t_unidade = SPO (sempre), periodo seg=sex..hoje / demais=D-1..hoje
          Unidade NAO usa fill/focus — o SSW abre findfil (tela de selecao).
        """
        ini, fim = self.start_date_yy, self.end_date_yy
        un = (unidade or "SPO").strip().upper() or "SPO"
        popup.locator("#t_excel").wait_for()
        # Seta tudo via JS (sem focus em t_unidade / lnk_unidade → findfil)
        values = popup.evaluate(
            """([ini, fim, unidade]) => {
              const setSilent = (id, v) => {
                const el = document.getElementById(String(id));
                if (!el) return false;
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                return true;
              };
              ['t_sigla_rom','t_nro_rom','t_cod_barras_rom','t_ciot','t_ser_mdfe',
               't_nro_mdfe','t_placa_veic','t_cpf_motorista'].forEach(id => setSilent(id, ''));
              const okExcel = setSilent('t_excel', 'S');
              const okUn = setSilent('t_unidade', unidade);
              const okIni = setSilent('t_dt_ini', ini);
              const okFim = setSilent('t_dt_fin', fim);
              // Evita foco no link Unidade (findfil)
              try {
                const excel = document.getElementById('t_excel');
                if (excel) excel.focus();
              } catch (e) {}
              return {
                ok: okExcel && okUn && okIni && okFim,
                excel: (document.getElementById('t_excel') || {}).value || '',
                unidade: (document.getElementById('t_unidade') || {}).value || '',
                periodo: [
                  (document.getElementById('t_dt_ini') || {}).value || '',
                  (document.getElementById('t_dt_fin') || {}).value || '',
                ],
              };
            }""",
            [ini, fim, un],
        )
        self._fechar_lookup_36(popup)
        if not values or not values.get("ok"):
            raise RuntimeError(f"36: falha ao preencher ssw0146: {values}")
        if (values.get("excel") or "").upper() != "S" or (values.get("unidade") or "").upper() != un:
            raise RuntimeError(f"36: valores incorretos apos preencher: {values}")
        self.on_status(
            f"36 preenchido: periodo {ini}-{fim} | excel=S | un={un} | {values}"
        )
        popup.wait_for_timeout(300)

    def _fechar_lookup_36(self, popup) -> None:
        """Ignora/fecha tela de selecao (findfil unidade), sem fechar o ssw0146."""
        if popup.is_closed():
            return
        # Fecha so janelas extras (lookup/findfil), nunca menu nem ssw0146.
        # NAO usa btnClose/Escape no form principal — isso fecha o proprio 36.
        try:
            main = None
            try:
                main = popup.context.pages[0]
            except Exception:
                pass
            for pg in list(popup.context.pages):
                if pg is popup or pg is main:
                    continue
                url = (pg.url or "").lower()
                if "ssw0146" in url or "menu01" in url:
                    continue
                try:
                    self.on_status(f"36: ignorando tela selecao ({url or 'about:blank'})")
                    pg.close()
                except Exception:
                    pass
        except Exception:
            pass
        if popup.is_closed():
            return
        try:
            popup.evaluate(
                """() => {
                  ['#errormsg','#scontentbar','.errormsg','#lookup','#divlookup',
                   '#divFind','#finddiv','#layerFind'].forEach(sel => {
                    const el = document.querySelector(sel);
                    if (el) {
                      try { el.style.display = 'none'; } catch (e) {}
                    }
                  });
                }"""
            )
        except Exception:
            pass
        try:
            if not popup.is_closed():
                popup.bring_to_front()
        except Exception:
            pass

    def _clicar_gerar_36(self, popup) -> None:
        self._fechar_lookup_36(popup)
        btn = popup.locator("#btn_env_periodo")
        try:
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                self.on_status("36: clique gerar via #btn_env_periodo (REL2)")
                return
        except Exception:
            pass
        loc = popup.locator('a[onclick*="REL2"]')
        try:
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                self.on_status("36: clique gerar via a[REL2]")
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

    def _download_report_225(self, page) -> Path:
        """
        225 - Acompanhamento agendamentos (ssw2862):
          Agendamento obrigatorio = S
          Situacao = A
          Unidade entrega = SPO
          Arquivo = R (relatorio .sswweb — inclui hora em AGEND PARA)
          Previsao entrega = mes corrente 01→ultimo dia (DDMMYY)
          Gerar = #act_rel → ajaxEnvia('REL', 0)
        """
        unidade = "SPO"
        self.on_status(
            f"Gerando 225 relatorio R | mes {self.start_date_yy} a {self.end_date_yy} | un={unidade}..."
        )
        popup = self._open_menu_option(
            page,
            "225",
            markers=(
                "agend",
                "225",
                "previs",
                "entrega",
                "obrigat",
                "situac",
                "arquivo",
                "2862",
            ),
        )
        try:
            popup.on("dialog", lambda d: d.accept())
            self._preencher_tela_225(popup, unidade=unidade)
            popup.wait_for_timeout(400)
            with popup.expect_download(timeout=180000) as download_info:
                self._clicar_gerar_225(popup)
            dest_name = (
                f"agendamento_225_{self.start_date_yy}_{self.end_date_yy}_{unidade.lower()}_{self.timestamp}.sswweb"
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
            self.on_status(f"225 arquivo: {path.name} ({path.stat().st_size} bytes)")
            return path
        finally:
            try:
                popup.close()
            except Exception:
                pass

    def _preencher_tela_225(self, popup, *, unidade: str = "SPO") -> None:
        """ssw2862 — campos a_rel_* + tp_arquivo=R (relatorio com horario)."""
        ini, fim = self.start_date_yy, self.end_date_yy
        un = (unidade or "SPO").strip().upper() or "SPO"
        popup.locator("#a_rel_prev_ini").wait_for()
        values = popup.evaluate(
            """([ini, fim, unidade]) => {
              const setSilent = (id, v) => {
                const el = document.getElementById(String(id));
                if (!el) return false;
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                return true;
              };
              const okObrig = setSilent('a_rel_agend_obrig', 'S');
              const okSit = setSilent('a_rel_situacao', 'A');
              const okUn = setSilent('a_rel_unid_ent', unidade);
              setSilent('a_rel_cnpj_emit', '');
              setSilent('a_rel_cnpj_dest', '');
              const okIni = setSilent('a_rel_prev_ini', ini);
              const okFim = setSilent('a_rel_prev_fin', fim);
              const okArq = setSilent('tp_arquivo', 'R');
              try {
                const arq = document.getElementById('tp_arquivo');
                if (arq) arq.focus();
              } catch (e) {}
              return {
                ok: okObrig && okSit && okUn && okIni && okFim && okArq,
                obrig: (document.getElementById('a_rel_agend_obrig') || {}).value || '',
                situacao: (document.getElementById('a_rel_situacao') || {}).value || '',
                unidade: (document.getElementById('a_rel_unid_ent') || {}).value || '',
                ini: (document.getElementById('a_rel_prev_ini') || {}).value || '',
                fim: (document.getElementById('a_rel_prev_fin') || {}).value || '',
                arquivo: (document.getElementById('tp_arquivo') || {}).value || '',
              };
            }""",
            [ini, fim, un],
        )
        if not values or not values.get("ok"):
            raise RuntimeError(f"225: falha ao preencher ssw2862: {values}")
        self.on_status(
            f"225 preenchido: obrig={values.get('obrig')} sit={values.get('situacao')} "
            f"un={values.get('unidade')} periodo={values.get('ini')}-{values.get('fim')} "
            f"arquivo={values.get('arquivo')}"
        )

    def _clicar_gerar_225(self, popup) -> None:
        btn = popup.locator("#act_rel")
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            self.on_status("225: clique gerar via #act_rel (REL)")
            return
        clicked = popup.evaluate(
            """() => {
              if (typeof ajaxEnvia === 'function') {
                ajaxEnvia('REL', 0);
                return true;
              }
              return false;
            }"""
        )
        if clicked:
            self.on_status("225: clique gerar via ajaxEnvia('REL')")
            return
        raise RuntimeError("225: nao achei botao #act_rel / ajaxEnvia('REL').")

    def run_225(self) -> dict[str, Any]:
        """Baixa somente a opcao 225 (relatorio R agendamentos do mes)."""
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
        self.on_status(f"ACE 225 | agendamentos mes {period} | relatorio R")

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
                path = self._download_report_225(page)
                self.paths["agendamento_225"] = str(path)
                self.on_status(f"225 relatorio R salvo: {path.name}")
                return {
                    "paths": dict(self.paths),
                    "errors": {},
                    "period": period,
                    "agendamento_option": "225",
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
            f"Gerando coleta (50) | periodo de cadastramento "
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
            try:
                popup.on("dialog", lambda d: d.accept())
            except Exception:
                pass
            return self._gerar_download_50_popup(popup, unidade)
        finally:
            try:
                if not popup.is_closed():
                    popup.close()
            except Exception:
                pass

    def _preencher_periodo_coleta_50(self, popup, *, unidade: str = "") -> None:
        """
        Tela ssw0157 (confirmado 2026-08):
          #2 unidade · #3 tipo (A)
          #4/#5 Periodo de CADASTRAMENTO (opc)  ← gera o .sswweb
          #6/#7 Periodo de COLETA (opc)         ← nao dispara download sozinho
          #21 Play → ajaxEnvia('ENV', 0)

        Preenche CADASTRAMENTO = HOJE e limpa coleta.
        (Teste: coleta-only = timeout; cadastramento = download OK.)
        """
        ini = self.start_date_yy
        fim = self.end_date_yy
        un = (unidade or "").strip().upper()

        filled = popup.evaluate(
            """([ini, fim, unidade]) => {
              const setVal = (el, v) => {
                if (!el) return false;
                el.focus();
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                return true;
              };
              const f2 = document.getElementById('2');
              const f3 = document.getElementById('3');
              const f4 = document.getElementById('4');
              const f5 = document.getElementById('5');
              const f6 = document.getElementById('6');
              const f7 = document.getElementById('7');
              if (!f4 || !f5 || !f6 || !f7) {
                return {ok: false, reason: 'campos 4-7 ausentes'};
              }
              setVal(f2, unidade || (f2 && f2.value) || '');
              if (f3 && !String(f3.value || '').trim()) setVal(f3, 'A');
              // cadastramento (#4/#5) = periodo que dispara o download
              setVal(f4, ini);
              setVal(f5, fim);
              // limpa coleta (#6/#7)
              setVal(f6, '');
              setVal(f7, '');
              return {
                ok: true,
                via: 'fixed-ids',
                unidade: (f2 && f2.value) || '',
                tipo: (f3 && f3.value) || '',
                cad: [(f4 && f4.value) || '', (f5 && f5.value) || ''],
                col: [(f6 && f6.value) || '', (f7 && f7.value) || ''],
              };
            }""",
            [ini, fim, un],
        )
        if filled and filled.get("ok"):
            self.on_status(
                f"Periodo de cadastramento (#4/#5): {ini} a {fim} | "
                f"un={filled.get('unidade') or 'TODAS'} | coleta limpa"
            )
            return

        # Fallback Playwright fill (mesmo mapa)
        try:
            popup.locator('[id="2"]').fill(un)
        except Exception:
            pass
        try:
            tipo = popup.locator('[id="3"]')
            if tipo.count() and not (tipo.first.input_value() or "").strip():
                tipo.first.fill("A")
        except Exception:
            pass
        popup.locator('[id="4"]').fill(ini)
        popup.locator('[id="4"]').press("Tab")
        popup.locator('[id="5"]').fill(fim)
        popup.locator('[id="5"]').press("Tab")
        for fid in ("6", "7"):
            try:
                popup.locator(f'[id="{fid}"]').fill("")
            except Exception:
                pass
        self.on_status(
            f"Periodo de cadastramento (campos 4/5): {ini} a {fim} | "
            f"un={un or 'TODAS'} | coleta limpa"
        )

    def _clicar_gerar_50(self, popup) -> None:
        """Play ► → ajaxEnvia('ENV', 0) (#21)."""
        via = popup.evaluate(
            """() => {
              if (typeof ajaxEnvia === 'function') {
                ajaxEnvia('ENV', 0);
                return 'ajax';
              }
              const el = document.getElementById('21');
              if (el) { el.click(); return 'click21'; }
              return null;
            }"""
        )
        if via:
            self.on_status(f"50: gerar via {via}")
            return
        btn = popup.locator('[id="21"]')
        if btn.count():
            btn.first.click()
            self.on_status("50: clique #21")
            return
        raise RuntimeError("50: nao achei botao #21 / ajaxEnvia('ENV').")

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

    def run_shared_cycle(
        self,
        *,
        period_50: tuple[str, str],
        period_103: tuple[str, str],
        period_225: tuple[str, str],
        period_36: tuple[str, str] | None = None,
        run_36: bool = False,
        on_report_ready: Callable[[str, str, str], None] | None = None,
    ) -> dict[str, Any]:
        """
        1) Login 1x e MANTÉM o browser aberto (SSW exige a sessão viva).
        2) Abre 50 / 103 / 36 / 225 em sequência (mesma sessão).
        3) Preenche e baixa cada um; opcionalmente dispara callback após cada OK
           (analisar + Sheets na hora, sem esperar o ciclo inteiro).
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright nao esta instalado. Rode: pip install playwright && playwright install chromium"
            ) from error

        ensure_dirs()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_before_download()
        self.paths = {}
        errors: dict[str, str] = {}

        mode = "oculto (headless)" if self.headless else "visível (janela)"
        self.on_status(f"SSW | viz={mode} | login 1x · abre telas juntas · baixa na mesma sessão")

        browser = None
        context = None
        with sync_playwright() as playwright:
            try:
                launch_kwargs: dict[str, Any] = {
                    "headless": self.headless,
                    "slow_mo": 0 if self.headless else 20,
                }
                if self.headless:
                    launch_kwargs["args"] = ["--disable-dev-shm-usage"]
                browser = playwright.chromium.launch(**launch_kwargs)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(45000)

                self._login(page)
                self._ensure_unit(page)
                self._patch_blank_popup_fix(page)

                # --- Sequencial: abre → baixa → fecha (login permanece) ---
                open_plan: list[tuple[str, str, tuple[str, str], tuple[str, ...]]] = [
                    (
                        "50",
                        "coleta",
                        period_50,
                        ("coleta", "050", "periodo", "ssw0157", "relacao", "cadastr"),
                    ),
                    (
                        "103",
                        "coleta_103",
                        period_103,
                        (
                            "coleta", "normal", "pesquisa", "limite", "inclus",
                            "excel", "periodo", "unidade", "103",
                        ),
                    ),
                ]
                if run_36 and period_36:
                    open_plan.append(
                        (
                            "36",
                            "entrega_36",
                            period_36,
                            ("romaneio", "entrega", "periodo", "unidade", "excel", "0146", "36"),
                        )
                    )
                open_plan.append(
                    (
                        "225",
                        "agendamento_225",
                        period_225,
                        (
                            "agend", "225", "previs", "entrega", "obrigat",
                            "situac", "arquivo", "2862",
                        ),
                    )
                )

                self.on_status(
                    f"Baixando {len(open_plan)} relatório(s) em sequência "
                    "(abre → baixa → fecha · login permanece)"
                )
                multi_50 = len(self._coleta_units() or []) > 1
                multi_103 = len(self._coleta_units() or []) > 1

                # Sequencial: abrir todos juntos mata popups ao baixar o primeiro.
                for label, path_key, period, markers in open_plan:
                    popup = None
                    try:
                        self.set_period(period[0], period[1])
                        self.on_status(
                            f"[{label}] abrindo · "
                            f"{format_period(self.start_date_ui, self.end_date_ui)}"
                        )
                        page.bring_to_front()
                        page.wait_for_timeout(200)
                        popup = self._open_menu_option(page, label, markers=markers)
                        try:
                            popup.on("dialog", lambda d: d.accept())
                        except Exception:
                            pass
                        self.on_status(f"[{label}] gerando…")

                        if label == "50":
                            if multi_50:
                                try:
                                    popup.close()
                                except Exception:
                                    pass
                                popup = None
                                path = self._download_report_50(page)
                            else:
                                un = (self._coleta_units() or [""])[0]
                                path = self._gerar_download_50_popup(popup, un)
                                popup = None  # já fechado no helper
                        elif label == "103":
                            if multi_103:
                                try:
                                    popup.close()
                                except Exception:
                                    pass
                                popup = None
                                path = self._download_report_103(page)
                            else:
                                un = (self._coleta_units() or [""])[0]
                                path = self._gerar_download_103_popup(popup, un)
                                popup = None
                        elif label == "36":
                            path = self._gerar_download_36_popup(popup, "SPO")
                            popup = None
                        elif label == "225":
                            path = self._gerar_download_225_popup(popup, "SPO")
                            popup = None
                        else:
                            raise RuntimeError(f"relatorio desconhecido: {label}")
                        self.paths[path_key] = str(path)
                        errors.pop(label, None)
                        self.on_status(f"[{label}] OK {path.name}")
                        if on_report_ready is not None:
                            try:
                                on_report_ready(label, path_key, str(path))
                            except Exception as cb_err:  # noqa: BLE001
                                self.on_status(f"[{label}] pos-download: {cb_err}")
                    except Exception as err:  # noqa: BLE001
                        errors[label] = str(err)
                        self.on_status(f"[{label}] FALHOU: {err}")
                        if popup is not None:
                            try:
                                popup.close()
                            except Exception:
                                pass

                # Retry individuais na sessão viva (só o que faltou)
                retries: list[tuple[str, str, tuple[str, str], Any]] = []
                if "coleta" not in self.paths:
                    retries.append(("50", "coleta", period_50, self._download_report_50))
                if "coleta_103" not in self.paths:
                    retries.append(("103", "coleta_103", period_103, self._download_report_103))
                if run_36 and period_36 and "entrega_36" not in self.paths:
                    retries.append(("36", "entrega_36", period_36, lambda p: self._download_report_36(p)))
                if "agendamento_225" not in self.paths:
                    retries.append(("225", "agendamento_225", period_225, self._download_report_225))

                for label, path_key, period, downloader in retries:
                    try:
                        self.on_status(f"[{label}] nova tentativa na mesma sessão…")
                        self.set_period(period[0], period[1])
                        page.bring_to_front()
                        path = downloader(page)
                        self.paths[path_key] = str(path)
                        errors.pop(label, None)
                        self.on_status(f"[{label}] OK (retry) {path.name}")
                        if on_report_ready is not None:
                            try:
                                on_report_ready(label, path_key, str(path))
                            except Exception as cb_err:  # noqa: BLE001
                                self.on_status(f"[{label}] pos-download: {cb_err}")
                    except Exception as err:  # noqa: BLE001
                        errors[label] = str(err)
                        self.on_status(f"[{label}] retry falhou: {err}")

                if not self.paths:
                    details = "; ".join(f"{k}: {v}" for k, v in errors.items())
                    raise RuntimeError(f"Nenhum arquivo baixado. {details}")

                return {
                    "paths": dict(self.paths),
                    "errors": errors,
                    "download_dir": str(self.download_dir),
                    "shared_session": True,
                    "parallel_open": False,
                    "sequential": True,
                    "headless": self.headless,
                }
            finally:
                if not self.keep_open:
                    if context is not None:
                        try:
                            context.close()
                        except Exception:
                            pass
                    if browser is not None:
                        try:
                            browser.close()
                        except Exception:
                            pass

    def _gerar_download_50_popup(self, popup, unidade: str) -> Path:
        try:
            popup.on("dialog", lambda d: d.accept())
        except Exception:
            pass
        popup.locator('[id="4"]').wait_for()
        self._preencher_periodo_coleta_50(popup, unidade=unidade)
        # Confirma mapeamento antes de gerar
        check = popup.evaluate(
            """() => {
              const g = id => (document.getElementById(String(id)) || {}).value || '';
              return {un:g(2), tipo:g(3), cad:[g(4),g(5)], col:[g(6),g(7)]};
            }"""
        )
        self.on_status(f"50 tela: {check}")
        if not (check and check.get("cad") and check["cad"][0] and check["cad"][1]):
            raise RuntimeError(
                f"50: periodo de cadastramento vazio (tela={check}). "
                "Sem #4/#5 o SSW nao gera download."
            )
        # Download pode disparar no context se o popup fechar — espera no context.
        context = popup.context
        suffix = (unidade or "todas").lower()
        dest_name = (
            f"coleta_50_col_{self.start_date_yy}_{self.end_date_yy}_{suffix}_{self.timestamp}.sswweb"
        )
        try:
            with context.expect_event("download", timeout=180000) as download_info:
                self._clicar_gerar_50(popup)
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
                if not popup.is_closed():
                    popup.close()
            except Exception:
                pass

    def _gerar_download_103_popup(self, popup, unidade: str) -> Path:
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
        try:
            path = self._save_download(download, dest_name)
        finally:
            try:
                popup.close()
            except Exception:
                pass
        return path

    def _gerar_download_36_popup(self, popup, unidade: str) -> Path:
        self._preencher_tela_36(popup, unidade=unidade)
        self._fechar_lookup_36(popup)
        popup.wait_for_timeout(400)
        with popup.expect_download(timeout=180000) as download_info:
            self._clicar_gerar_36(popup)
        suffix = (unidade or "spo").lower()
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
        try:
            path = self._save_download(download, dest_name)
        finally:
            try:
                popup.close()
            except Exception:
                pass
        return path

    def _gerar_download_225_popup(self, popup, unidade: str) -> Path:
        self._preencher_tela_225(popup, unidade=unidade)
        popup.wait_for_timeout(400)
        with popup.expect_download(timeout=180000) as download_info:
            self._clicar_gerar_225(popup)
        dest_name = (
            f"agendamento_225_{self.start_date_yy}_{self.end_date_yy}_"
            f"{unidade.lower()}_{self.timestamp}.sswweb"
        )
        download = download_info.value
        suggested = (download.suggested_filename or "").lower()
        if suggested.endswith(".xlsx"):
            dest_name = dest_name.replace(".sswweb", ".xlsx")
        elif suggested.endswith(".csv"):
            dest_name = dest_name.replace(".sswweb", ".csv")
        elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
            dest_name = dest_name.replace(".sswweb", ".xls")
        try:
            path = self._save_download(download, dest_name)
        finally:
            try:
                popup.close()
            except Exception:
                pass
        return path


def download_ace_shared_cycle(
    *,
    period_50: tuple[str, str],
    period_103: tuple[str, str],
    period_225: tuple[str, str],
    period_36: tuple[str, str] | None = None,
    run_36: bool = False,
    headless: bool = True,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
    on_report_ready: Callable[[str, str, str], None] | None = None,
) -> dict[str, Any]:
    """Login 1x; baixa 50+103+(36)+225 e dispara on_report_ready após cada OK."""
    client = AceSswClient(
        period_50[0],
        period_50[1],
        keep_open=False,
        headless=headless,
        on_status=on_status,
        credentials=credentials,
        settings=settings,
        clean_downloads=clean_downloads,
    )
    return client.run_shared_cycle(
        period_50=period_50,
        period_103=period_103,
        period_225=period_225,
        period_36=period_36,
        run_36=run_36,
        on_report_ready=on_report_ready,
    )


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


def download_ace_225(
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
    return client.run_225()
