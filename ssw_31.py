"""Download SSW 031 (ssw0495) — CTRCs por código de ocorrência → Excel."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_mes_ate_hoje, to_ssw_ddmmyy
from ocorrencias_pendencia import OCORR_PENDENCIA_CODES, label_ocorrencia
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
    Abre opção 31 e, para cada código, gera Excel (Arquivo excel=S)
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

    ini_ddmm, fim_ddmm = period or periodo_mes_ate_hoje()
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
    status(
        f"SSW 31 | {len(code_list)} código(s) um a um | ocorrência {ini}-{fim} | excel=S"
    )
    status("códigos: " + ", ".join(code_list))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))
        popup = None
        try:
            client._login(page)
            client._ensure_unit(page)
            client._patch_blank_popup_fix(page)

            # SSW 0495: cada código precisa de uma entrada nova na opção 31
            # (reaproveitar o mesmo popup falha / mistura relatório).
            for idx, code in enumerate(code_list, start=1):
                try:
                    status(f"[31/{code}] ({idx}/{len(code_list)}) abrindo opção 31…")
                    popup = _reopen_31(client, page, popup)
                    status(f"[31/{code}] preenchendo…")
                    _preencher_31(
                        popup, ini=ini, fim=fim, codigo=code, on_status=status
                    )
                    status(f"[31/{code}] gerando Excel…")
                    dest_name = f"pendencia_31_{code}_{ts}.xlsx"
                    path = _gerar_download_31(
                        client, context, page, popup, dest_name, code, status
                    )
                    paths[code] = str(path)
                    status(f"[31/{code}] OK {path.name} ({path.stat().st_size} bytes)")
                    page.wait_for_timeout(400)
                except Exception as err:  # noqa: BLE001
                    errors[code] = str(err)
                    status(f"[31/{code}] FALHOU: {err}")
                    try:
                        popup = _reopen_31(client, page, popup)
                    except Exception:
                        popup = None
            try:
                if popup is not None and not popup.is_closed():
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


def _open_31(client: AceSswClient, page):
    return client._open_menu_option(
        page,
        "31",
        markers=("ocorr", "ctrc", "excel", "pendenc", "31", "arquivo", "0495"),
    )


def _reopen_31(client: AceSswClient, page, popup=None):
    """Fecha a tela 31 anterior (se houver) e abre de novo para o próximo código."""
    if popup is not None:
        try:
            if not popup.is_closed():
                popup.close()
        except Exception:
            pass
        try:
            page.wait_for_timeout(350)
        except Exception:
            pass
    fresh = _open_31(client, page)
    try:
        fresh.on("dialog", lambda d: d.accept())
    except Exception:
        pass
    return fresh


def _preencher_31(
    popup,
    *,
    ini: str,
    fim: str,
    codigo: str,
    on_status: StatusCallback | None = None,
) -> None:
    """
    ssw0495:
      #1/#2 emissão (vazio) · #3/#4 data ocorrência · #6 código (2 dígitos)
      #11 situação=T · #12 excel=S

    Importante: NÃO usar evaluate+change no #6 — o alert/lookup do SSW
    trava o evaluate. Playwright fill/Tab trata o dialog normalmente.
    """
    status = on_status or _noop
    cod = str(codigo or "").strip()[:2]
    if not cod:
        raise RuntimeError("31: código vazio")

    try:
        popup.locator('[id="3"]').wait_for(state="visible", timeout=15000)
    except Exception as err:  # noqa: BLE001
        url = ""
        try:
            url = popup.url
        except Exception:
            pass
        raise RuntimeError(f"31: formulário não pronto ({url}): {err}") from err

    status(f"[31/{cod}] datas {ini}-{fim}")
    popup.locator('[id="1"]').fill("")
    popup.locator('[id="2"]').fill("")
    popup.locator('[id="3"]').fill(ini)
    popup.locator('[id="4"]').fill(fim)

    status(f"[31/{cod}] código {cod} (lookup descrição)")
    campo = popup.locator('[id="6"]')
    campo.click()
    campo.fill(cod)
    try:
        campo.press("Tab")
    except Exception:
        pass

    # espera a descrição preencher (lookup AJAX); se não vier, usa rótulo local
    label = label_ocorrencia(cod)
    desc_ok = False
    for _ in range(40):
        try:
            desc = (popup.locator("#ocor_descr").input_value(timeout=1000) or "").strip()
        except Exception:
            desc = ""
        low = desc.lower()
        if desc and len(desc) > 2 and "aguarde" not in low and "..." not in desc:
            desc_ok = True
            status(f"[31/{cod}] descrição: {desc[:60]}")
            break
        popup.wait_for_timeout(300)
    if not desc_ok:
        try:
            popup.locator("#ocor_descr").fill(label)
            status(f"[31/{cod}] descrição local: {label[:60]}")
        except Exception:
            pass
        # dá mais um tempo pro lookup terminar antes do ►
        popup.wait_for_timeout(800)

    popup.locator('[id="11"]').fill("T")
    popup.locator('[id="12"]').fill("S")

    values = {
        "ini": popup.locator('[id="3"]').input_value(),
        "fim": popup.locator('[id="4"]').input_value(),
        "codigo": popup.locator('[id="6"]').input_value(),
        "situacao": popup.locator('[id="11"]').input_value(),
        "excel": popup.locator('[id="12"]').input_value(),
    }
    if str(values.get("codigo") or "").strip() != cod:
        raise RuntimeError(f"31: código não ficou {cod}: {values}")
    if str(values.get("excel") or "").upper() != "S":
        raise RuntimeError(f"31: excel não ficou S: {values}")
    status(
        f"[31/{cod}] OK form · oc={values.get('ini')}-{values.get('fim')} "
        f"cod={values.get('codigo')} excel={values.get('excel')}"
    )
    popup.wait_for_timeout(200)


def _clicar_gerar_31(popup) -> None:
    """Play ► → ajaxEnvia('ENV', 0) (id=13)."""
    clicked = popup.evaluate(
        """() => {
          if (typeof ajaxEnvia === 'function') { ajaxEnvia('ENV', 0); return 'ajax'; }
          const a = document.getElementById('13');
          if (a) { a.click(); return '13'; }
          return '';
        }"""
    )
    if not clicked:
        raise RuntimeError("31: botão gerar (►) não encontrado")


def _gerar_download_31(client, context, page, popup, dest_name: str, code: str, status) -> Path:
    """Tenta download direto (curto); se for pra fila, baixa via ssw1440."""
    try:
        with context.expect_event("download", timeout=20000) as di:
            _clicar_gerar_31(popup)
        download = di.value
        return _save_named(client, download, dest_name)
    except Exception as direct_err:  # noqa: BLE001
        status(f"[31/{code}] sem download imediato; abrindo Ver fila…")
        # dialog “enviado à fila” costuma aparecer aqui
        try:
            popup.wait_for_timeout(800)
        except Exception:
            pass
        return _baixar_via_fila_31(client, context, page, popup, dest_name, code, status)


def _save_named(client, download, dest_name: str) -> Path:
    suggested = (download.suggested_filename or "").lower()
    name = dest_name
    if suggested.endswith(".csv"):
        name = Path(dest_name).with_suffix(".csv").name
    elif suggested.endswith(".xls") and not suggested.endswith(".xlsx"):
        name = Path(dest_name).with_suffix(".xls").name
    elif suggested.endswith(".sswweb"):
        name = Path(dest_name).with_suffix(".sswweb").name
    return client._save_download(download, name)


def _abrir_ver_fila_31(client, context, page, popup, status):
    """Abre ssw1440 (Ver fila) a partir do popup 31 ou do menu."""
    fila = None
    # 1) botão Ver fila no próprio 0495 (id=15 / ajaxEnvia)
    try:
        with context.expect_page(timeout=12000) as pi:
            opened = popup.evaluate(
                """() => {
                  const a = document.getElementById('15');
                  if (a) { a.click(); return '15'; }
                  if (typeof ajaxEnvia === 'function') {
                    ajaxEnvia('', 1, 'ssw1440');
                    return 'ajax';
                  }
                  return '';
                }"""
            )
            if not opened:
                raise RuntimeError("sem botão Ver fila")
        fila = pi.value
        status("[31] Ver fila aberta (popup)")
    except Exception as err:
        status(f"[31] Ver fila popup: {err}")
        fila = None

    if fila is None:
        try:
            if "ssw1440" in (popup.url or ""):
                fila = popup
        except Exception:
            pass

    if fila is None:
        try:
            with context.expect_page(timeout=12000) as pi:
                page.bring_to_front()
                page.evaluate(
                    """() => {
                      if (typeof ajaxEnvia === 'function') ajaxEnvia('', 1, 'ssw1440');
                    }"""
                )
            fila = pi.value
        except Exception:
            for pg in context.pages:
                try:
                    if "ssw1440" in (pg.url or ""):
                        fila = pg
                        break
                except Exception:
                    continue

    if fila is None:
        raise RuntimeError("31: não abriu Ver fila (ssw1440)")

    # blank.html → forçar programa da fila (mesmo padrão das outras telas)
    try:
        url = (fila.url or "").lower()
    except Exception:
        url = ""
    if "blank.html" in url or url.startswith("about:blank") or "ssw1440" not in url:
        status("[31] recuperando ssw1440…")
        try:
            fila.goto(
                "https://sistema.ssw.inf.br/bin/ssw1440",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            fila.wait_for_timeout(1000)
        except Exception as err:
            status(f"[31] goto ssw1440: {err}")
            try:
                if client._recuperar_blank(page, fila, "1440", ("fila", "dow", "relat", "1440")):
                    status("[31] ssw1440 via blank patch")
            except Exception:
                pass
    return fila


def _baixar_via_fila_31(client, context, page, popup, dest_name: str, code: str, status) -> Path:
    """Abre Ver fila (ssw1440) e baixa o relatório 0495 mais recente."""
    fila = _abrir_ver_fila_31(client, context, page, popup, status)
    try:
        fila.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    try:
        fila.on("dialog", lambda d: d.accept())
    except Exception:
        pass
    try:
        fila.bring_to_front()
    except Exception:
        pass

    deadline = time.time() + 150
    last_hint = ""
    while time.time() < deadline:
        info = fila.evaluate(
            """() => {
              const text = (document.body && document.body.innerText || '').slice(0, 3500);
              const links = Array.from(document.querySelectorAll('a[onclick], a[href], img[onclick]'));
              const mapped = links.map((a, i) => {
                const text = ((a.textContent || a.alt || a.title || '') + '').trim().slice(0, 80);
                const onclick = String(a.getAttribute('onclick') || '').slice(0, 220);
                const href = String(a.getAttribute('href') || '').slice(0, 160);
                const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
                return { i, text, onclick, href, blob };
              });
              // Só links reais de download da fila — nunca Imprimir/Correio/menu
              const hits = mapped.filter(x => {
                if (/imprimir|correio|e-mails|emails|retaguarda|voltar|fechar|sair/i.test(x.text)) {
                  return false;
                }
                return /\\bdow\\b|download\\(|ssw0495|\\.xlsx|\\.xls|\\.csv|baixar/.test(x.blob)
                  || (/0495/.test(x.blob) && /dow|href=|http/.test(x.blob));
              });
              return {
                text,
                url: location.href,
                hits: hits.slice(0, 15),
                sample: mapped.slice(0, 20),
              };
            }"""
        )
        last_hint = f"url={info.get('url')} | " + str(info.get("text") or "")[:220]
        hits = info.get("hits") or []
        if "ssw1440" not in str(info.get("url") or ""):
            status(f"[31/{code}] fila ainda não é ssw1440 ({info.get('url')}); retry…")
            fila.wait_for_timeout(2000)
            continue

        pick = None
        for h in hits:
            blob = h.get("blob") or ""
            if re.search(r"\bdow\b|download\(|ssw0495", blob, re.I):
                pick = h
                break
        if not pick and hits:
            pick = hits[0]

        if pick is not None:
            status(
                f"[31/{code}] fila: baixando · {pick.get('text') or pick.get('onclick')}"
            )
            try:
                with context.expect_event("download", timeout=60000) as di:
                    fila.evaluate(
                        """(idx) => {
                          const links = Array.from(document.querySelectorAll('a[onclick], a[href], img[onclick]'));
                          const mapped = links.map((a) => {
                            const text = ((a.textContent || a.alt || a.title || '') + '').trim();
                            const onclick = String(a.getAttribute('onclick') || '');
                            const href = String(a.getAttribute('href') || '');
                            const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
                            return { a, text, blob };
                          }).filter(x => {
                            if (/imprimir|correio|e-mails|emails|retaguarda|voltar|fechar|sair/i.test(x.text)) {
                              return false;
                            }
                            return /\\bdow\\b|download\\(|ssw0495|\\.xlsx|\\.xls|\\.csv|baixar/.test(x.blob)
                              || (/0495/.test(x.blob) && /dow|href=|http/.test(x.blob));
                          });
                          const item = mapped[idx];
                          if (!item) return false;
                          item.a.click();
                          return true;
                        }""",
                        hits.index(pick) if pick in hits else 0,
                    )
                download = di.value
                path = _save_named(client, download, dest_name)
                try:
                    if fila != popup and not fila.is_closed():
                        fila.close()
                except Exception:
                    pass
                return path
            except Exception as err:  # noqa: BLE001
                status(f"[31/{code}] clique fila falhou: {err}")
        else:
            # ainda processando na fila
            if int(time.time()) % 8 < 3:
                status(f"[31/{code}] aguardando DOW na fila…")
        try:
            # refresh leve
            fila.evaluate(
                """() => {
                  if (typeof ajaxEnvia === 'function') { try { ajaxEnvia('ATU', 0); } catch (e) {} }
                }"""
            )
        except Exception:
            pass
        fila.wait_for_timeout(2500)

    raise RuntimeError(f"31: Ver fila sem download em 150s. Hint: {last_hint}")
