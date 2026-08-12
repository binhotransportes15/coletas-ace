"""Download SSW 455 (ssw0230) — Fretes Expedidos/Recebidos · CTRCs → Excel.

Formulário chave:
  Unidade (#2) · tipo A (#3) · Período emissão (#9/#10)
  Arquivo=E (#35) · Dados complementares=N (#37)
  ► = ajaxEnvia('E1', 0) → fila 156 → DOW

Máx. 31 dias no período.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_mes_ate_hoje, to_ssw_ddmmyy
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]

SSW_455_PATH = "/bin/ssw0230"
SSW_FILA_URL = "https://sistema.ssw.inf.br/bin/ssw1440"
SSW_455_MARKERS = (
    "455",
    "frete",
    "expedid",
    "recebid",
    "ctrc",
    "arquivo",
    "excel",
    "emiss",
    "ver fila",
)

_EMPTY_FILA_RE = re.compile(
    r"n[aã]o\s+selecionou|sem\s+ctrc|nenhum\s+ctrc|sem\s+dados|n[aã]o\s+h[aá]\s+regist|"
    r"nada\s+a\s+(gerar|emitir)|sem\s+movimento|nenhum\s+registro",
    re.IGNORECASE,
)


class FilaSemDados455(RuntimeError):
    """Job 455 concluído sem arquivo."""


def _noop(_: str) -> None:
    return None


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def _safe_wait(page, ms: int) -> None:
    try:
        if page is None or (hasattr(page, "is_closed") and page.is_closed()):
            time.sleep(ms / 1000.0)
            return
        page.wait_for_timeout(ms)
    except Exception:
        time.sleep(ms / 1000.0)


def _cap_period_31d(ini_ddmm: str, fim_ddmm: str) -> tuple[str, str]:
    """455 aceita no máx. 31 dias."""
    try:
        d0 = datetime.strptime(ini_ddmm.replace("/", ""), "%d%m%Y") if len(
            re.sub(r"\D", "", ini_ddmm)
        ) >= 8 else datetime.strptime(
            to_ssw_ddmmyy(ini_ddmm)[:4] + datetime.now().strftime("%Y"), "%d%m%Y"
        )
        # Prefer parse with dates helpers via digits
        dig_i = re.sub(r"\D", "", ini_ddmm)
        dig_f = re.sub(r"\D", "", fim_ddmm)
        if len(dig_i) == 8:
            d0 = datetime.strptime(dig_i, "%d%m%Y")
        elif len(dig_i) == 4:
            d0 = datetime.strptime(dig_i + datetime.now().strftime("%Y"), "%d%m%Y")
        else:
            d0 = datetime.strptime(to_ssw_ddmmyy(ini_ddmm), "%d%m%y")
        if len(dig_f) == 8:
            d1 = datetime.strptime(dig_f, "%d%m%Y")
        elif len(dig_f) == 4:
            d1 = datetime.strptime(dig_f + datetime.now().strftime("%Y"), "%d%m%Y")
        else:
            d1 = datetime.strptime(to_ssw_ddmmyy(fim_ddmm), "%d%m%y")
        if (d1 - d0).days > 30:
            d0 = d1 - timedelta(days=30)
        return d0.strftime("%d%m%Y")[:4] + d0.strftime("%Y")[2:], d1.strftime("%d%m%y")
    except Exception:
        return to_ssw_ddmmyy(ini_ddmm), to_ssw_ddmmyy(fim_ddmm)


def download_reports_455(
    *,
    period: tuple[str, str] | None = None,
    unidade: str = "",
    tipo_unidade: str = "A",
    arquivo: str = "E",
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """1 login · 455 · Excel → fila 156 → DOW."""
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)

    ini_ddmm, fim_ddmm = period or periodo_mes_ate_hoje()
    ini, fim = _cap_period_31d(ini_ddmm, fim_ddmm)
    # normalize to ddmmyy
    ini = to_ssw_ddmmyy(ini if len(re.sub(r"\D", "", ini)) >= 4 else ini_ddmm)
    fim = to_ssw_ddmmyy(fim if len(re.sub(r"\D", "", fim)) >= 4 else fim_ddmm)
    # re-cap after normalize
    try:
        d0 = datetime.strptime(ini, "%d%m%y")
        d1 = datetime.strptime(fim, "%d%m%y")
        if (d1 - d0).days > 30:
            d0 = d1 - timedelta(days=30)
            ini = d0.strftime("%d%m%y")
    except Exception:
        pass

    uni = (unidade or "").strip().upper()
    tipo = (tipo_unidade or "A").strip().upper()[:1] or "A"
    arq = (arquivo or "E").strip().upper()[:1] or "E"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if clean_downloads:
        cleanup_downloads(DOWNLOAD_DIR, on_status=status)

    client = AceSswClient(
        ini_ddmm,
        fim_ddmm,
        keep_open=True,
        headless=use_headless,
        on_status=status,
        credentials=creds,
        settings=cfg,
        clean_downloads=False,
    )

    from playwright.sync_api import sync_playwright

    status(f"SSW 455 | emissão {ini}-{fim} | un={uni or '(todas)'} tipo={tipo} | excel={arq}")
    path: Path | None = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))
        popup = None
        fila = None
        try:
            client._login(page)
            client._ensure_unit(page)
            client._patch_blank_popup_form(page)

            status("[455] abrindo opção…")
            popup = client._open_menu_option(page, "455", markers=SSW_455_MARKERS)
            try:
                popup.on("dialog", lambda d: d.accept())
            except Exception:
                pass

            status("[455] preenchendo…")
            _preencher_455(
                popup,
                ini=ini,
                fim=fim,
                unidade=uni,
                tipo=tipo,
                arquivo=arq,
                on_status=status,
            )
            dest_name = f"emissao_455_{uni or 'ALL'}_{ts}.xlsx"
            status("[455] ► fila 156…")
            path = _gerar_download_455(
                client, context, page, popup, dest_name, status
            )
            status(f"[455] OK {path.name} ({path.stat().st_size} bytes)")
        finally:
            for pg in (popup, fila):
                try:
                    if pg is not None and not pg.is_closed():
                        pg.close()
                except Exception:
                    pass
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    if path is None or not path.exists():
        raise RuntimeError("455: nenhum Excel baixado")
    return {
        "ok": True,
        "files": [str(path)],
        "paths": {"455": str(path)},
        "period": f"{ini}-{fim}",
        "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
        "unidade": uni,
        "download_dir": str(DOWNLOAD_DIR),
    }


def _preencher_455(
    popup,
    *,
    ini: str,
    fim: str,
    unidade: str,
    tipo: str,
    arquivo: str,
    on_status: StatusCallback | None = None,
) -> None:
    status = on_status or _noop
    try:
        popup.locator('[id="9"]').wait_for(state="visible", timeout=15000)
    except Exception as err:
        raise RuntimeError(f"455: formulário não pronto: {err}") from err

    filled = popup.evaluate(
        """({ ini, fim, unidade, tipo, arquivo }) => {
          const set = (id, val) => {
            const el = document.getElementById(id);
            if (!el) return false;
            el.focus();
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          };
          return {
            unidade: set('2', unidade),
            tipo: set('3', tipo || 'A'),
            emiIni: set('9', ini),
            emiFim: set('10', fim),
            autIni: set('11', ''),
            autFim: set('12', ''),
            prevIni: set('13', ''),
            prevFim: set('14', ''),
            entIni: set('15', ''),
            entFim: set('16', ''),
            frete: set('19', 'T'),
            liq: set('21', 'X'),
            entrega: set('22', 'T'),
            arquivo: set('35', arquivo || 'E'),
            compl: set('37', 'N'),
          };
        }""",
        {
            "ini": ini,
            "fim": fim,
            "unidade": unidade or "",
            "tipo": tipo or "A",
            "arquivo": arquivo or "E",
        },
    )
    status(f"[455] form {filled}")
    _safe_wait(popup, 300)


def _gerar_download_455(client, context, page, popup, dest_name: str, status) -> Path:
    clicked = popup.evaluate(
        """() => {
          if (typeof ajaxEnvia === 'function') {
            try { ajaxEnvia('E1', 0); return 'E1'; } catch (e) {}
          }
          const a = document.getElementById('40');
          if (a) { a.click(); return '40'; }
          return '';
        }"""
    )
    if not clicked:
        raise RuntimeError("455: botão ► não encontrado")
    status(f"[455] ► {clicked}")
    _safe_wait(popup, 800)
    enqueue_t0 = time.time()
    return _baixar_via_fila_455(
        client, context, page, popup, dest_name, status, enqueue_t0=enqueue_t0
    )


def _abrir_fila_455(client, context, page, status, popup=None):
    status("[455] abrindo fila 156…")
    try:
        try:
            page.bring_to_front()
        except Exception:
            pass
        with context.expect_page(timeout=10000) as pi:
            page.evaluate(
                """() => {
                  if (typeof ajaxEnvia === 'function') {
                    try { ajaxEnvia('', 1, 'ssw1440'); return '1440'; } catch (e) {}
                  }
                  return '';
                }"""
            )
        fila = pi.value
        try:
            fila.on("dialog", lambda d: d.accept())
        except Exception:
            pass
        status("[455] fila via ajax")
        _safe_wait(fila, 500)
        return fila
    except Exception as err:
        status(f"[455] ajax fila: {err}")

    if popup is not None:
        try:
            if not popup.is_closed():
                with context.expect_page(timeout=4000) as pi:
                    ok = popup.evaluate(
                        """() => {
                          const a = document.getElementById('42');
                          if (a) { a.click(); return '42'; }
                          if (typeof ajaxEnvia === 'function') {
                            try { ajaxEnvia('', 1, 'ssw1440'); return 'ajax'; } catch (e) {}
                          }
                          return '';
                        }"""
                    )
                    if not ok:
                        raise RuntimeError("sem Ver fila")
                fila = pi.value
                try:
                    fila.on("dialog", lambda d: d.accept())
                except Exception:
                    pass
                status("[455] fila via Ver fila")
                return fila
        except Exception as err:
            status(f"[455] Ver fila: {err}")

    fila = context.new_page()
    try:
        fila.on("dialog", lambda d: d.accept())
    except Exception:
        pass
    fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=30000)
    status("[455] fila via goto")
    return fila


def _ler_jobs_455(fila) -> list[dict]:
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
            let sit = cells.find(c => /^(conclu|processando|na fila|em fila|erro)/i.test(c)) || '';
            if (!sit) sit = cells.find(c => /conclu|process|fila|erro/i.test(c)) || '';
            const links = Array.from(tr.querySelectorAll('a,img,font,b,span,button')).map(a =>
              norm(a.textContent || a.alt || a.title || '')
            );
            const hasDow = links.some(t => /^(dow|baixar)$/i.test(t))
              || /\\b(dow|baixar)\\b/i.test(cells.join(' '));
            let mensagem = '';
            for (let i = cells.length - 1; i >= 0; i--) {
              const c = cells[i] || '';
              if (c.length >= 8 && !/^(conclu|process|fila)/i.test(c)) { mensagem = c; break; }
            }
            const blob = (opcao + ' ' + cells.join(' ')).toLowerCase();
            jobs.push({
              seq,
              opcao,
              situacao: sit,
              mensagem,
              concluido: /conclu/i.test(sit),
              is455: /0230|455\\s*-|fretes\\s+exped|ssw0230/.test(blob),
              hasDow,
            });
          }
          return jobs;
        }"""
    )


def _atualizar_fila(fila) -> None:
    try:
        fila.evaluate(
            """() => {
              if (typeof ajaxEnvia === 'function') {
                try { ajaxEnvia('', 0); return; } catch (e) {}
              }
              const a = document.getElementById('2');
              if (a) a.click();
            }"""
        )
    except Exception:
        pass


def _baixar_via_fila_455(
    client, context, page, popup, dest_name: str, status, *, enqueue_t0: float
) -> Path:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    fila = _abrir_fila_455(client, context, page, status, popup=popup)
    _safe_wait(fila, 600)
    floor = 0
    tracked: set[str] = set()
    try:
        jobs = _ler_jobs_455(fila)
        only = [j for j in jobs if j.get("is455") and j.get("seq")]
        only.sort(key=lambda j: int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0))
        processing = [j for j in only if not j.get("concluido")]
        concluded = [j for j in only if j.get("concluido")]
        if processing:
            for j in processing:
                tracked.add(str(j.get("seq") or ""))
            if concluded:
                floor = max(
                    int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0) for j in concluded
                )
        elif only:
            tracked.add(str(only[-1].get("seq") or ""))
            floor = int(re.sub(r"\D", "", str(only[-1].get("seq") or "")) or 0) - 1
        status(f"[455] fila · tracked={len(tracked)} · floor={floor}")
    except Exception as err:
        status(f"[455] bootstrap: {err}")

    deadline = time.time() + 240
    last_log = 0.0
    last_err = ""
    while time.time() < deadline:
        try:
            if fila is None or fila.is_closed():
                fila = _abrir_fila_455(client, context, page, status, popup=None)
            _atualizar_fila(fila)
            _safe_wait(fila, 1000)
            jobs = _ler_jobs_455(fila)
            for j in jobs:
                if j.get("is455") and not j.get("concluido") and j.get("seq"):
                    tracked.add(str(j.get("seq")))

            def _num(j: dict) -> int:
                return int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0)

            def _nosso(j: dict) -> bool:
                if not j.get("is455") or not j.get("seq"):
                    return False
                seq = str(j.get("seq"))
                if tracked and seq in tracked:
                    return True
                return _num(j) > floor

            nossos = [j for j in jobs if _nosso(j)]
            if not nossos:
                only = sorted(
                    [j for j in jobs if j.get("is455")],
                    key=_num,
                )
                if only and not tracked:
                    nossos = only[-1:]
                    tracked.add(str(only[-1].get("seq") or ""))

            now = time.time()
            if now - last_log >= 4:
                last_log = now
                proc = [j for j in nossos if not j.get("concluido")]
                status(
                    f"[455] aguardando DOW · {len(proc)} processando · "
                    f"{sum(1 for j in nossos if j.get('hasDow'))} prontos"
                )

            vazios = [
                j
                for j in nossos
                if j.get("concluido")
                and not j.get("hasDow")
                and (
                    _EMPTY_FILA_RE.search(str(j.get("mensagem") or ""))
                    or (time.time() - enqueue_t0) > 35
                )
            ]
            if vazios and not any(j.get("hasDow") for j in nossos):
                raise FilaSemDados455(
                    f"sem base · seq={vazios[-1].get('seq')} · "
                    f"{str(vazios[-1].get('mensagem') or '')[:60]}"
                )

            ready = [j for j in nossos if j.get("concluido") and j.get("hasDow")]
            if not ready:
                _safe_wait(fila, 1800)
                continue

            job = sorted(ready, key=_num)[-1]
            seq = str(job.get("seq") or "")
            status(f"[455] DOW · seq={seq}")

            def _trigger() -> str:
                return str(
                    fila.evaluate(
                        """({ seq }) => {
                          const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                          const want = String(seq || '').replace(/\\D/g, '');
                          for (const tr of document.querySelectorAll('tr')) {
                            const s = ((tr.querySelector('td') || {}).innerText || '').replace(/\\D/g, '');
                            if (s !== want) continue;
                            for (const a of tr.querySelectorAll('a[onclick], a[href], img[onclick], font, b, span, button')) {
                              const t = norm(a.textContent || a.alt || '');
                              const oc = String(a.getAttribute('onclick') || '');
                              if (/^(dow|baixar)$/i.test(t) || /\\bdow\\b|baixar|download/i.test(oc + ' ' + t)) {
                                try { a.click(); return 'click:' + t; } catch (e) {}
                              }
                            }
                            const last = [...tr.querySelectorAll('td')].pop();
                            if (last) {
                              const c = last.querySelector('a, img, font, b, span') || last;
                              try { c.click(); return 'last'; } catch (e2) {}
                            }
                            if (typeof ajaxEnvia === 'function') {
                              try { ajaxEnvia('DOW', want); return 'ajax'; } catch (e3) {}
                            }
                          }
                          return '';
                        }""",
                        {"seq": seq},
                    )
                    or ""
                )

            try:
                with context.expect_event("download", timeout=25000) as di:
                    how = _trigger()
                    status(f"[455] clique={how}")
                    if not how:
                        raise RuntimeError("trigger vazio")
                path = client._save_download(di.value, dest_name)
                try:
                    if fila and not fila.is_closed():
                        fila.close()
                except Exception:
                    pass
                return path
            except PlaywrightTimeoutError:
                status("[455] sem evento download — nova aba…")
                pages_before = list(context.pages)
                new_page = None
                try:
                    with context.expect_page(timeout=10000) as pi:
                        _trigger()
                    new_page = pi.value
                except PlaywrightTimeoutError:
                    after = [pg for pg in context.pages if pg not in pages_before]
                    if after:
                        new_page = after[-1]
                if new_page is not None:
                    try:
                        with new_page.expect_download(timeout=20000) as di:
                            pass
                        path = client._save_download(di.value, dest_name)
                        try:
                            new_page.close()
                        except Exception:
                            pass
                        return path
                    except Exception as err:
                        last_err = str(err)
                        try:
                            url = new_page.url or ""
                            if url and "blank" not in url.lower():
                                body = context.request.get(url, timeout=30000).body()
                                if body and len(body) > 64:
                                    dest = Path(client.download_dir) / dest_name
                                    dest.write_bytes(body)
                                    return dest
                        except Exception as err2:
                            last_err = str(err2)
                        try:
                            new_page.close()
                        except Exception:
                            pass
                raise RuntimeError(f"455: download falhou ({last_err})")
        except FilaSemDados455:
            raise
        except Exception as err:
            last_err = str(err)
            status(f"[455] loop: {err}")
            time.sleep(1.5)

    raise RuntimeError(f"455: timeout na fila 156 ({last_err})")
