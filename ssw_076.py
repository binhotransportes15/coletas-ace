"""Download SSW 076 — Demonstrativo de remuneração (frete coleta/entrega por carro).

Sempre gera com operação R. Usa placas vindas do 073.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_mes_ate_hoje, to_ssw_ddmmyy
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]

SSW_076_MARKERS = (
    "076",
    "remuner",
    "demonstrativo",
    "placa",
    "frete",
    "arquivo excel",
    "periodo",
)


def _noop(_: str) -> None:
    return None


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def download_reports_076(
    *,
    placas: list[str] | tuple[str, ...] | None = None,
    period: tuple[str, str] | None = None,
    operacao: str = "R",
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    # sessão existente (1 login compartilhado com o 073)
    client: AceSswClient | None = None,
    context=None,
    page=None,
) -> dict[str, Any]:
    """
    Gera 076 (operação R). Se `page`/`client` forem passados, reusa a sessão
    (sem novo login). Senão abre browser próprio.
    """
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)
    plate_list = [str(p).strip().upper() for p in (placas or []) if str(p).strip()]
    runs = plate_list or [""]

    ini_ddmm, fim_ddmm = period or periodo_mes_ate_hoje()
    ini = to_ssw_ddmmyy(ini_ddmm)
    fim = to_ssw_ddmmyy(fim_ddmm)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    reuse = page is not None and context is not None and client is not None
    own_client = client or AceSswClient(
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

    paths: list[str] = []
    errors: dict[str, str] = {}
    status(
        f"SSW 76 | op={operacao} | {ini}-{fim} | {len(plate_list) or 'todas'} placa(s)"
        + (" · sessão reusada" if reuse else "")
    )

    def _run(sess_client, sess_context, sess_page) -> None:
        nonlocal paths, errors
        popup = None
        try:
            status("[76] abrindo opção 76 (lote)…")
            popup = _reopen_76(sess_client, sess_page, popup)
            _preencher_76(popup, ini=ini, fim=fim, operacao=operacao, placa="", on_status=status)
            dest = f"contratacao_076_ALL_{ts}.sswweb"
            path = _gerar_download_76(sess_client, sess_context, sess_page, popup, dest, "ALL", status)
            paths.append(str(path))
            status(f"[76/ALL] OK {path.name}")
        except Exception as batch_err:  # noqa: BLE001
            status(f"[76] lote falhou ({batch_err}); tentando por placa…")
            for idx, placa in enumerate(runs[:40], start=1):
                key = placa or "ALL"
                try:
                    status(f"[76/{key}] ({idx}) abrindo…")
                    popup = _reopen_76(sess_client, sess_page, popup)
                    _preencher_76(
                        popup, ini=ini, fim=fim, operacao=operacao, placa=placa, on_status=status
                    )
                    dest = f"contratacao_076_{key or 'ALL'}_{ts}.sswweb"
                    path = _gerar_download_76(
                        sess_client, sess_context, sess_page, popup, dest, key, status
                    )
                    paths.append(str(path))
                except Exception as err:  # noqa: BLE001
                    errors[key] = str(err)
                    status(f"[76/{key}] FALHOU: {err}")
        try:
            if popup is not None and not popup.is_closed():
                popup.close()
        except Exception:
            pass

    if reuse:
        _run(own_client, context, page)
    else:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
            ctx = browser.new_context(accept_downloads=True)
            pg = ctx.new_page()
            pg.set_default_timeout(60000)
            pg.on("dialog", lambda d: d.accept())
            ctx.on("page", lambda p2: p2.on("dialog", lambda d: d.accept()))
            try:
                own_client._login(pg)
                own_client._ensure_unit(pg)
                own_client._patch_blank_popup_form(pg)
                _run(own_client, ctx, pg)
            finally:
                browser.close()

    if not paths and errors:
        raise RuntimeError("076 falhou: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))

    return {
        "ok": bool(paths),
        "files": paths,
        "errors": errors,
        "period": (ini_ddmm, fim_ddmm),
        "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
        "operacao": operacao,
        "placas": plate_list,
    }


def _reopen_76(client, page, popup):
    try:
        if popup is not None and not popup.is_closed():
            popup.close()
    except Exception:
        pass
    return client._open_menu_option(page, "76", markers=SSW_076_MARKERS)


def _preencher_76(popup, *, ini: str, fim: str, operacao: str, placa: str, on_status) -> None:
    status = on_status
    popup.wait_for_timeout(400)
    filled = popup.evaluate(
        """({ ini, fim, operacao, placa }) => {
          const norm = (s) => (s || '').toLowerCase().normalize('NFD')
            .replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim();
          const inputs = Array.from(document.querySelectorAll('input[type=text], input:not([type])'));
          const near = (el) => {
            let t = '';
            if (el.previousElementSibling) t = el.previousElementSibling.innerText || '';
            if (!t && el.parentElement) t = el.parentElement.innerText || '';
            return t;
          };
          const byHint = (hints) => {
            const hs = hints.map(norm);
            for (const el of inputs) {
              const lab = norm(near(el));
              if (hs.some((h) => lab.includes(h))) return el;
            }
            return null;
          };
          const set = (el, val) => {
            if (!el) return false;
            el.focus(); el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          const ddmmyy = inputs.filter((el) => Number(el.maxLength) === 6 || Number(el.size) === 6);
          let okIni = false, okFim = false;
          if (ddmmyy.length >= 2) {
            okIni = set(ddmmyy[0], ini);
            okFim = set(ddmmyy[1], fim);
          }
          const okOp = set(byHint(['operacao', 'opera', 'tipo']), operacao);
          const okPlaca = placa ? set(byHint(['placa', 'cavalo']), placa) : true;
          return { okIni, okFim, okOp, okPlaca, n: inputs.length };
        }""",
        {"ini": ini, "fim": fim, "operacao": operacao, "placa": placa},
    )
    status(f"[76] form {filled}")
    if not filled.get("okOp"):
        # ainda tenta ids comuns
        for cand in ("5", "6", "7", "8"):
            try:
                loc = popup.locator(f'[id="{cand}"]')
                if loc.count():
                    loc.first.fill(operacao)
                    status(f"[76] operação via #{cand}={operacao}")
                    break
            except Exception:
                continue
    popup.wait_for_timeout(200)


def _clicar_excel_76(popup) -> str:
    """Clica somente o link 'Arquivo Excel'."""
    try:
        loc = popup.get_by_text("Arquivo Excel", exact=True)
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            return "Arquivo Excel"
    except Exception:
        pass
    try:
        loc = popup.locator("a", has_text="Arquivo Excel")
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            return "a:Arquivo Excel"
    except Exception:
        pass
    return popup.evaluate(
        """() => {
          const links = Array.from(document.querySelectorAll('a, span, button'));
          for (const a of links) {
            const t = ((a.innerText || a.textContent || '') + '').replace(/\\s+/g, ' ').trim();
            if (/^Arquivo Excel$/i.test(t)) { a.click(); return 'excel-exact'; }
          }
          return '';
        }"""
    )


def _gerar_download_76(client, context, page, popup, dest_name: str, key: str, status) -> Path:
    """076: Arquivo Excel → fila 156 → DOW (download direto é raro)."""
    clicked = ""
    try:
        with context.expect_event("download", timeout=8000) as di:
            clicked = _clicar_excel_76(popup)
            if not clicked:
                raise RuntimeError("076: link 'Arquivo Excel' não encontrado")
            status(f"[76/{key}] clique={clicked} (aguardando…)")
        return client._save_download(di.value, dest_name)
    except RuntimeError:
        raise
    except Exception as direct_err:  # noqa: BLE001
        if not clicked:
            clicked = _clicar_excel_76(popup)
            if not clicked:
                raise RuntimeError("076: link 'Arquivo Excel' não encontrado") from direct_err
            status(f"[76/{key}] clique={clicked}")
        status(f"[76/{key}] foi pra fila 156 ({direct_err})")
        try:
            popup.wait_for_timeout(800)
        except Exception:
            pass
        return _baixar_via_fila_76(client, context, page, popup, dest_name, key, status)


SSW_FILA_URL = "https://sistema.ssw.inf.br/bin/ssw1440"


def _safe_wait(page, ms: int) -> None:
    try:
        if page is None or page.is_closed():
            return
        page.wait_for_timeout(ms)
    except Exception:
        pass


def _abrir_fila_156_76(client, context, page, status):
    """Abre 156 de forma estável (preferir goto; menu pode crashar após vários popups)."""
    status("[76] abrindo fila 156…")
    fila = None

    # 1) Ver fila a partir do popup 76, se ainda aberto
    # (caller passa page = menu; popup separado)

    # 2) goto direto — mais estável
    try:
        # garante menu vivo
        try:
            page.bring_to_front()
        except Exception:
            pass
        fila = context.new_page()
        fila.on("dialog", lambda d: d.accept())
        fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=45000)
        status("[76] fila 156 via goto ssw1440")
        _safe_wait(fila, 800)
        return fila
    except Exception as err:
        status(f"[76] goto fila: {err}")
        try:
            if fila is not None and not fila.is_closed():
                fila.close()
        except Exception:
            pass
        fila = None

    # 3) menu 156
    try:
        fila = client._open_menu_option(
            page, "156", markers=("fila", "dow", "156", "1440", "processamento", "lotes")
        )
        status("[76] fila 156 via menu")
        return fila
    except Exception as err:
        status(f"[76] menu 156: {err}")
        raise RuntimeError(f"076: não abriu fila 156 ({err})") from err


def _ler_jobs_fila_76(fila) -> list[dict]:
    return fila.evaluate(
        """() => {
          const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
          const jobs = [];
          for (const tr of Array.from(document.querySelectorAll('tr'))) {
            const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
            if (cells.length < 4) continue;
            const seq = (cells[0] || '').replace(/\\D/g, '');
            if (!seq || seq.length < 4) continue;
            const opcao = cells[1] || '';
            const sit = cells.find(c => /conclu|process|fila|erro|abort/i.test(c)) || cells[6] || '';
            const links = Array.from(tr.querySelectorAll('a[onclick], a[href], img[onclick]')).map(a => {
              const text = norm(a.textContent || a.alt || a.title || '');
              const onclick = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
              return { text, onclick, href, blob };
            });
            const dows = links.filter(x => {
              if (/imprimir|correio|atualizar|voltar|fechar|sair/i.test(x.text)) return false;
              return /\\bdow\\b|download\\(|\\.xlsx|\\.xls|\\.csv|\\.sswweb|baixar|arquivo/.test(x.blob);
            });
            const blobAll = (opcao + ' ' + cells.join(' ') + ' ' + links.map(l => l.blob).join(' ')).toLowerCase();
            jobs.push({
              seq,
              opcao,
              situacao: sit,
              concluido: /conclu/i.test(sit),
              is076: /076|remuner|demonstrativo|ssw0?76/i.test(blobAll),
              hasDow: dows.length > 0,
              dows,
            });
          }
          return jobs;
        }"""
    )


def _atualizar_fila_76(fila) -> None:
    try:
        fila.evaluate(
            """() => {
              if (typeof ajaxEnvia === 'function') {
                try { ajaxEnvia('', 0); return 'atu'; } catch (e) {}
                try { ajaxEnvia('ATU', 0); return 'ATU'; } catch (e) {}
              }
              const a = document.getElementById('2');
              if (a) { a.click(); return '2'; }
              return '';
            }"""
        )
    except Exception:
        pass


def _baixar_via_fila_76(client, context, page, popup, dest_name: str, key: str, status) -> Path:
    """Espera job 076 concluído na 156 e clica DOW."""
    _ = popup
    # só seqs já prontas (concluído+DOW) — jobs em processamento entram depois
    known_ready: set[str] = set()
    fila = None
    try:
        fila = _abrir_fila_156_76(client, context, page, status)
        _safe_wait(fila, 500)
        for j in _ler_jobs_fila_76(fila):
            seq = str(j.get("seq") or "")
            if seq and j.get("concluido") and j.get("hasDow"):
                known_ready.add(seq)
        status(f"[76] fila aberta · {len(known_ready)} pronto(s) antigo(s)")
    except Exception as err:
        status(f"[76] snapshot fila: {err}")

    if fila is None or fila.is_closed():
        fila = _abrir_fila_156_76(client, context, page, status)

    deadline = time.time() + 240
    last_err = ""
    while time.time() < deadline:
        try:
            if fila is None or fila.is_closed():
                fila = _abrir_fila_156_76(client, context, page, status)
            try:
                fila.bring_to_front()
            except Exception:
                pass
            _atualizar_fila_76(fila)
            _safe_wait(fila, 1200)
            jobs = _ler_jobs_fila_76(fila)
            cands = [
                j
                for j in jobs
                if j.get("concluido")
                and j.get("hasDow")
                and (
                    j.get("is076")
                    or str(j.get("seq") or "") not in known_ready
                )
            ]
            # prioriza is076; entre eles, seq mais nova
            def sk(j: dict) -> tuple:
                seq = str(j.get("seq") or "")
                try:
                    num = int("".join(ch for ch in seq if ch.isdigit()) or 0)
                except Exception:
                    num = 0
                return (0 if j.get("is076") else 1, -num)

            cands.sort(key=sk)
            if not cands:
                if int(time.time()) % 8 < 2:
                    status(f"[76/{key}] aguardando DOW na fila 156…")
                _safe_wait(fila, 2000)
                continue

            job = cands[0]
            seq = str(job.get("seq") or "")
            status(f"[76/{key}] DOW na fila · seq={seq} · {job.get('opcao') or ''}")
            with context.expect_event("download", timeout=90000) as di:
                ok = fila.evaluate(
                    """({ seq }) => {
                      const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                      const rows = Array.from(document.querySelectorAll('tr'));
                      for (const tr of rows) {
                        const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
                        if (!cells.length) continue;
                        const s = (cells[0] || '').replace(/\\D/g, '');
                        if (seq && s !== String(seq).replace(/\\D/g, '')) continue;
                        const links = Array.from(tr.querySelectorAll('a[onclick], a[href], img[onclick]'));
                        for (const a of links) {
                          const text = norm(a.textContent || a.alt || a.title || '');
                          const onclick = String(a.getAttribute('onclick') || '');
                          const href = String(a.getAttribute('href') || '');
                          const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
                          if (/imprimir|correio|atualizar|voltar|fechar/i.test(text)) continue;
                          if (/\\bdow\\b|download\\(|\\.xlsx|\\.csv|\\.sswweb|baixar|arquivo/.test(blob)) {
                            a.click();
                            return 'row';
                          }
                        }
                      }
                      return '';
                    }""",
                    {"seq": seq},
                )
                if not ok:
                    raise RuntimeError("076: DOW não encontrado na linha")
                status(f"[76/{key}] clique DOW={ok}")
            download = di.value
            try:
                if fila is not None and not fila.is_closed():
                    fila.close()
            except Exception:
                pass
            return client._save_download(download, dest_name)
        except Exception as err:  # noqa: BLE001
            last_err = str(err)
            status(f"[76/{key}] fila loop: {err}")
            # só reabre se a aba morreu — fechar sempre crashava o Chromium
            crashed = (
                "crash" in last_err.lower()
                or "closed" in last_err.lower()
                or "target" in last_err.lower()
            )
            if crashed:
                try:
                    if fila is not None and not fila.is_closed():
                        fila.close()
                except Exception:
                    pass
                fila = None
            time.sleep(2)

    raise RuntimeError(f"076/{key}: timeout na fila 156 ({last_err})")
