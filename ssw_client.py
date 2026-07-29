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
)
from dates import format_period, normalize_date, to_ssw_ddmmyy

StatusCallback = Callable[[str], None]

SSW_ORIGIN = "https://sistema.ssw.inf.br"

# Codigo menu → programa SSW
MENU_PROGRAM = {
    "50": "/bin/ssw0157",  # Relacao das Coletas
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
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.paths: dict[str, str] = {}

    def run(self) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright nao esta instalado. Rode: pip install playwright && playwright install chromium"
            ) from error

        ensure_dirs()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        period = format_period(self.start_date_ui, self.end_date_ui)
        coleta = (self.settings.coleta_option or "50").strip()
        entrega = (self.settings.entrega_option or "").strip()
        self.on_status(f"ACE | Coleta={coleta} Entrega={entrega or '(a definir)'} | {period}")

        browser = None
        context = None
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    headless=self.headless,
                    slow_mo=0 if self.headless else 200,
                )
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

                if entrega:
                    try:
                        path = self._download_entrega(page, entrega)
                        self.paths["entrega"] = str(path)
                        self.on_status(f"Entrega ({entrega}) salva: {path.name}")
                    except Exception as error:  # noqa: BLE001
                        errors["entrega"] = str(error)
                        self.on_status(f"Entrega ({entrega}) falhou: {error}")
                else:
                    self.on_status("Entrega: opcao ainda nao definida — pulando.")

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

    def _ensure_unit(self, page) -> None:
        unit = str(getattr(self.credentials, "unit", "") or "").strip().upper()
        if not unit:
            return
        campo = page.locator('input[name="f2"][id="2"]')
        if campo.count() <= 0:
            return
        atual = (campo.first.input_value() or "").strip().upper()
        if atual == unit:
            return
        self.on_status(f"Ajustando unidade para {unit}...")
        campo.first.fill(unit)
        campo.first.press("Tab")
        page.wait_for_timeout(500)

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
        raise RuntimeError(
            f"Opcao de entrega '{code}' ainda esta em aberto — automacao sera definida depois."
        )

    def _download_report_50(self, page) -> Path:
        """050 - Relacao das Coletas (ssw0157)."""
        self.on_status(
            f"Gerando coleta (50) de {format_period(self.start_date_ui, self.end_date_ui)}..."
        )
        popup = self._open_menu_option(
            page,
            "50",
            markers=("coleta", "050", "periodo", "ssw0157", "relacao"),
        )
        try:
            # Layout conhecido (CyberMap): campos 4/5 limpos, 6/7 periodo, botao 21
            popup.locator('[id="4"]').wait_for()
            popup.locator('[id="4"]').fill("")
            popup.locator('[id="5"]').fill("")
            popup.locator('[id="6"]').fill(self.start_date)
            popup.locator('[id="6"]').press("Tab")
            popup.locator('[id="7"]').fill(self.end_date)
            popup.locator('[id="7"]').press("Tab")
            with popup.expect_download(timeout=120000) as download_info:
                popup.locator('[id="21"]').click()
            return self._save_download(
                download_info.value,
                f"coleta_50_{self.start_date}_{self.end_date}_{self.timestamp}.sswweb",
            )
        finally:
            try:
                popup.close()
            except Exception:
                pass


def download_ace_reports(
    start_date: str,
    end_date: str,
    *,
    keep_open: bool = False,
    headless: bool = False,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
) -> dict[str, Any]:
    client = AceSswClient(
        start_date,
        end_date,
        keep_open=keep_open,
        headless=headless,
        on_status=on_status,
        credentials=credentials,
        settings=settings,
    )
    return client.run()
