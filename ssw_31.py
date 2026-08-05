"""Download SSW 031 (ssw0495) — CTRCs por código de ocorrência → Excel."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_mes_corrente, to_ssw_ddmmyy
from ocorrencias_pendencia import OCORR_PENDENCIA_CODES
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def download_reports_31(
    *,
    codes: tuple[str, ...] | list[str] | None = None,
    period: tuple[str, str] | None = None,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
) -> dict[str, Any]:
    """
    Abre opção 31 uma vez e, para cada código, gera Excel (Arquivo excel=S)
    com Data da ocorrência = mês corrente (ou período informado).
    """
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)
    code_list = [str(c).strip() for c in (codes or OCORR_PENDENCIA_CODES) if str(c).strip()]
    if not code_list:
        raise RuntimeError("31: nenhum código de ocorrência")

    ini_ddmm, fim_ddmm = period or periodo_mes_corrente()
    ini = to_ssw_ddmmyy(ini_ddmm)
    fim = to_ssw_ddmmyy(fim_ddmm)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_downloads(DOWNLOAD_DIR, on_status=status)

    client = AceSswClient(
        ini_ddmm,
        fim_ddmm,
        keep_open=False,
        headless=use_headless,
        on_status=status,
        credentials=creds,
        settings=cfg,
        clean_downloads=False,
    )

    from playwright.sync_api import sync_playwright

    paths: dict[str, str] = {}
    errors: dict[str, str] = {}
    status(f"SSW 31 | {len(code_list)} código(s) | ocorrência {ini}-{fim} | excel=S")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        try:
            client._login(page)
            client._ensure_unit(page)
            client._patch_blank_popup_fix(page)
            popup = client._open_menu_option(
                page,
                "31",
                markers=("ocorr", "ctrc", "excel", "pendenc", "31", "arquivo", "0495"),
            )
            for code in code_list:
                try:
                    status(f"[31/{code}] preenchendo…")
                    _preencher_31(popup, ini=ini, fim=fim, codigo=code)
                    dest = DOWNLOAD_DIR / f"pendencia_31_{code}_{ts}.xlsx"
                    with popup.expect_download(timeout=180000) as di:
                        _clicar_gerar_31(popup)
                    download = di.value
                    suggested = (download.suggested_filename or "").lower()
                    if suggested.endswith(".csv"):
                        dest = dest.with_suffix(".csv")
                    elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
                        dest = dest.with_suffix(".xls")
                    path = client._save_download(download, dest.name)
                    paths[code] = str(path)
                    status(f"[31/{code}] OK {path.name} ({path.stat().st_size} bytes)")
                    popup.wait_for_timeout(400)
                except Exception as err:  # noqa: BLE001
                    errors[code] = str(err)
                    status(f"[31/{code}] FALHOU: {err}")
                    try:
                        # tenta reabrir se popup fechou
                        if popup.is_closed():
                            popup = client._open_menu_option(
                                page,
                                "31",
                                markers=("ocorr", "ctrc", "excel", "31", "0495"),
                            )
                    except Exception:
                        pass
            try:
                if not popup.is_closed():
                    popup.close()
            except Exception:
                pass
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    if not paths:
        raise RuntimeError(
            "31: nenhum Excel baixado. " + "; ".join(f"{k}:{v}" for k, v in errors.items())
        )
    return {
        "ok": True,
        "paths": paths,
        "errors": errors,
        "period": f"{ini}-{fim}",
        "codes": code_list,
        "download_dir": str(DOWNLOAD_DIR),
    }


def _preencher_31(popup, *, ini: str, fim: str, codigo: str) -> None:
    """ssw0495: #3/#4 ocorrência, #6 código, #11=T, #12=S. Emissão (#1/#2) vazia."""
    # ids numéricos: #3 é inválido em CSS — usar [id="3"]
    popup.locator('[id="3"]').wait_for(timeout=20000)
    values = popup.evaluate(
        """([ini, fim, codigo]) => {
          const set = (id, v) => {
            const el = document.getElementById(String(id));
            if (!el) return false;
            el.value = v;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          set('1', '');
          set('2', '');
          const okIni = set('3', ini);
          const okFim = set('4', fim);
          const okCod = set('6', String(codigo || '').slice(0, 2));
          const okSit = set('11', 'T');
          const okExcel = set('12', 'S');
          try {
            const el = document.getElementById('12');
            if (el) el.focus();
          } catch (e) {}
          return {
            ok: okIni && okFim && okCod && okSit && okExcel,
            ini: (document.getElementById('3') || {}).value || '',
            fim: (document.getElementById('4') || {}).value || '',
            codigo: (document.getElementById('6') || {}).value || '',
            situacao: (document.getElementById('11') || {}).value || '',
            excel: (document.getElementById('12') || {}).value || '',
          };
        }""",
        [ini, fim, str(codigo).strip()],
    )
    if not values or not values.get("ok"):
        raise RuntimeError(f"31: falha ao preencher: {values}")
    if str(values.get("excel") or "").upper() != "S":
        raise RuntimeError(f"31: excel não ficou S: {values}")
    popup.wait_for_timeout(200)


def _clicar_gerar_31(popup) -> None:
    """Play ► → ajaxEnvia('ENV', 0) (id=13)."""
    clicked = popup.evaluate(
        """() => {
          const a = document.getElementById('13');
          if (a) { a.click(); return '13'; }
          if (typeof ajaxEnvia === 'function') { ajaxEnvia('ENV', 0); return 'ajax'; }
          return '';
        }"""
    )
    if not clicked:
        raise RuntimeError("31: botão gerar (►) não encontrado")
