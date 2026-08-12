"""Download SSW 455 (ssw0230) — Fretes Expedidos/Recebidos · CTRCs → Excel.

Formulário fixo ACE:
  Unidade = SPO · tipo = E (expedidora)
  Período de emissão = dia atual (nunca autorização)
  Arquivo = E (Excel) · Dados complementares = N
  ► = ajaxEnvia('E1', 0) → fila 156 → DOW
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_hoje, to_ssw_ddmmyy
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
    unidade: str = "SPO",
    tipo_unidade: str = "E",
    arquivo: str = "E",
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """1 login · 455 · Excel do dia (emissão) · SPO/E → fila 156 → DOW."""
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)

    # Emissão = sempre o dia de hoje no PERÍODO DE EMISSÃO (ini = fim)
    ini_ddmm, fim_ddmm = period or periodo_hoje()
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

    # Fixos do painel ACE: SPO · E-expedidora · Excel
    uni = (unidade or "SPO").strip().upper()[:3] or "SPO"
    tipo = (tipo_unidade or "E").strip().upper()[:1] or "E"
    if tipo not in {"E", "R", "A", "F"}:
        tipo = "E"
    arq = "E"  # sempre Excel
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

    status(
        f"SSW 455 | emissão {ini}-{fim} | un={uni} tipo={tipo}(exped) | excel={arq}"
    )
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
        "periodo_fmt": ini_ddmm if ini_ddmm == fim_ddmm else f"{ini_ddmm} – {fim_ddmm}",
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
            el.value = String(val == null ? '' : val);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            return true;
          };
          // Regras ACE: SPO · E-expedidora · emissão (nunca autorização) · Excel
          return {
            unidade: set('2', unidade || 'SPO'),
            tipo: set('3', tipo || 'E'),
            emiIni: set('9', ini),
            emiFim: set('10', fim),
            // limpa autorização / outros períodos para não misturar filtro
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
            // confirma valores lidos (debug CRT)
            read: {
              un: (document.getElementById('2') || {}).value || '',
              tipo: (document.getElementById('3') || {}).value || '',
              emiIni: (document.getElementById('9') || {}).value || '',
              emiFim: (document.getElementById('10') || {}).value || '',
              autIni: (document.getElementById('11') || {}).value || '',
              autFim: (document.getElementById('12') || {}).value || '',
              arq: (document.getElementById('35') || {}).value || '',
            },
          };
        }""",
        {
            "ini": ini,
            "fim": fim,
            "unidade": unidade or "SPO",
            "tipo": tipo or "E",
            "arquivo": "E",
        },
    )
    status(f"[455] form {filled}")
    read = (filled or {}).get("read") or {}
    if str(read.get("autIni") or "").strip() or str(read.get("autFim") or "").strip():
        # força limpar autorização de novo (SSW às vezes recoloca)
        popup.evaluate(
            """() => {
              for (const id of ['11', '12']) {
                const el = document.getElementById(id);
                if (!el) continue;
                el.value = '';
                el.dispatchEvent(new Event('change', { bubbles: true }));
              }
            }"""
        )
        status("[455] autorização limpa (2ª passada)")
    if str(read.get("arq") or "").upper() != "E":
        popup.evaluate(
            """() => {
              const el = document.getElementById('35');
              if (el) { el.value = 'E'; el.dispatchEvent(new Event('change', { bubbles: true })); }
            }"""
        )
        status("[455] arquivo forçado E")
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
            const links = Array.from(tr.querySelectorAll(
              'a[onclick], a[href], img[onclick], font, b, span, button'
            )).map(a => {
              const text = norm(a.textContent || a.alt || a.title || '');
              const oc = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              return text + ' ' + oc + ' ' + href;
            });
            const hasDow = links.some(t =>
              /^(dow|baixar)$/i.test(norm(t)) || /\\b(dow|baixar)\\b/i.test(t)
                || /\\.xlsx|\\.xls|download\\(/i.test(t)
            ) || /\\b(dow|baixar)\\b/i.test(cells.join(' '));
            let mensagem = '';
            for (let i = cells.length - 1; i >= 0; i--) {
              const c = cells[i] || '';
              if (!c) continue;
              if (/^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(c)) continue;
              if (/^(conclu|process|fila|erro)/i.test(c) && c.length < 24) continue;
              if (/^\\d{1,2}\\/\\d{1,2}/.test(c)) continue;
              if (/^(dow|baixar)$/i.test(c)) continue;
              if (c.length >= 8 && !/^(conclu|process|fila)/i.test(c)) { mensagem = c; break; }
            }
            const blob = (opcao + ' ' + cells.join(' ') + ' ' + links.join(' ')).toLowerCase();
            jobs.push({
              seq,
              opcao,
              situacao: sit,
              mensagem,
              concluido: /conclu/i.test(sit) && !/n[aã]o\\s*conclu|inconclu/i.test(sit),
              is455: /0230|455\\s*-|455\\b|fretes\\s+exped|ssw0230|expedidos/.test(blob),
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
    fila = _abrir_fila_455(client, context, page, status, popup=popup)
    _safe_wait(fila, 600)
    floor = 0
    tracked: set[str] = set()
    try:
        jobs0 = _ler_jobs_455(fila)
        only0 = [j for j in jobs0 if j.get("is455") and j.get("seq")]

        def _n0(j: dict) -> int:
            return int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0)

        # floor = maior seq JÁ concluída (jobs de outros / rodadas antigas)
        concluded0 = [j for j in only0 if j.get("concluido")]
        if concluded0:
            floor = max(_n0(j) for j in concluded0)
        # processando agora = candidato(s) desta rodada
        for j in only0:
            if not j.get("concluido"):
                tracked.add(str(j.get("seq") or ""))
        # se o nosso já concluiu entre ► e abrir fila: pega o mais novo com Baixar
        if not tracked:
            with_dow = sorted(
                [j for j in only0 if j.get("hasDow")],
                key=_n0,
            )
            if with_dow and _n0(with_dow[-1]) >= floor:
                tracked.add(str(with_dow[-1].get("seq") or ""))
                # não sobe floor acima dele — senão some do filtro
                if concluded0:
                    older = [j for j in concluded0 if _n0(j) < _n0(with_dow[-1])]
                    floor = max((_n0(j) for j in older), default=0)
        status(f"[455] fila · floor={floor} · tracked={sorted(tracked)}")
    except Exception as err:
        status(f"[455] bootstrap: {err}")

    deadline = time.time() + 300
    last_log = 0.0
    last_err = ""
    while time.time() < deadline:
        try:
            if fila is None or fila.is_closed():
                fila = _abrir_fila_455(client, context, page, status, popup=None)
            _atualizar_fila(fila)
            _safe_wait(fila, 1000)
            jobs = _ler_jobs_455(fila)

            def _num(j: dict) -> int:
                return int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0)

            for j in jobs:
                if not j.get("is455") or not j.get("seq"):
                    continue
                n = _num(j)
                seq = str(j.get("seq"))
                if n > floor:
                    tracked.add(seq)
                elif not j.get("concluido") and (time.time() - enqueue_t0) < 90:
                    tracked.add(seq)

            def _nosso(j: dict) -> bool:
                if not j.get("is455") or not j.get("seq"):
                    return False
                seq = str(j.get("seq"))
                if tracked and seq in tracked:
                    return True
                return _num(j) > floor

            nossos = [j for j in jobs if _nosso(j)]
            if not nossos:
                proc = sorted(
                    [j for j in jobs if j.get("is455") and not j.get("concluido")],
                    key=_num,
                )
                if proc:
                    nossos = proc[-1:]
                    tracked.add(str(proc[-1].get("seq") or ""))

            now = time.time()
            if now - last_log >= 4:
                last_log = now
                proc = [j for j in nossos if not j.get("concluido")]
                status(
                    f"[455] aguardando DOW · {len(proc)} processando · "
                    f"{sum(1 for j in nossos if j.get('hasDow'))} prontos · floor={floor}"
                )
                for j in proc[:2]:
                    status(
                        f"[455]   ⏳ seq={j.get('seq')} · {j.get('situacao') or '?'} · "
                        f"{(j.get('opcao') or '')[:42]}"
                    )

            def _sem_dados(j: dict) -> bool:
                if not j.get("concluido") or j.get("hasDow"):
                    return False
                msg = str(j.get("mensagem") or "").strip()
                # duração 00:00:04 NÃO é "sem base"
                if not msg or re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", msg):
                    return False
                return bool(_EMPTY_FILA_RE.search(msg))

            vazios = [j for j in nossos if _sem_dados(j)]
            if vazios and not any(j.get("hasDow") for j in nossos):
                vazios.sort(key=_num)
                jv = vazios[-1]
                raise FilaSemDados455(
                    f"sem base · seq={jv.get('seq')} · "
                    f"{str(jv.get('mensagem') or '')[:80]}"
                )

            # Concluído sem DOW e sem msg explícita: espera o link (não aborta em 35s)
            stuck = [
                j
                for j in nossos
                if j.get("concluido") and not j.get("hasDow") and not _sem_dados(j)
            ]
            if (
                stuck
                and (time.time() - enqueue_t0) > 120
                and not any(j.get("hasDow") for j in nossos)
            ):
                jv = sorted(stuck, key=_num)[-1]
                raise FilaSemDados455(
                    f"sem DOW após 120s · seq={jv.get('seq')} · "
                    f"{str(jv.get('mensagem') or jv.get('situacao') or '')[:60]}"
                )

            ready = [j for j in nossos if j.get("concluido") and j.get("hasDow")]
            if not ready:
                _safe_wait(fila, 1800)
                continue

            job = sorted(ready, key=_num)[-1]
            seq = str(job.get("seq") or "")
            status(f"[455] DOW · seq={seq} · {(job.get('opcao') or '')[:40]}")
            try:
                path = _clicar_dow_455(
                    client, context, fila, job, dest_name, status
                )
                try:
                    if fila and not fila.is_closed():
                        fila.close()
                except Exception:
                    pass
                return path
            except Exception as err:
                last_err = str(err)
                status(f"[455] retry DOW ({last_err[:80]})")
                _safe_wait(fila, 1500)
                continue
        except FilaSemDados455:
            raise
        except Exception as err:
            last_err = str(err)
            status(f"[455] loop: {err}")
            time.sleep(1.5)

    raise RuntimeError(f"455: timeout na fila 156 ({last_err})")


def _clicar_dow_455(client, context, fila, job: dict, dest_name: str, status) -> Path:
    """Clica Baixar/DOW só na seq alvo (fila pública) com fallbacks 076/31.

    Não clica font/span soltos — só a/img/input/button com onclick/href.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    seq = str(job.get("seq") or "")
    _atualizar_fila(fila)
    _safe_wait(fila, 400)

    meta = fila.evaluate(
        """({ seq }) => {
          const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
          const want = String(seq || '').replace(/\\D/g, '');
          if (!want) return { ok: false, why: 'seq_vazia' };
          for (const tr of document.querySelectorAll('tr')) {
            const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
            const s = (cells[0] || '').replace(/\\D/g, '');
            if (s !== want) continue;
            const links = Array.from(tr.querySelectorAll(
              'a[onclick], a[href], img[onclick], input[onclick], button[onclick]'
            ));
            const scored = [];
            for (const a of links) {
              const text = norm(a.textContent || a.alt || a.title || a.value || '');
              const onclick = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
              if (/imprimir|correio|atualizar|voltar|fechar|sair/i.test(text)
                  && !/\\b(dow|baixar)\\b/i.test(text)) continue;
              let score = 0;
              if (/^(dow|baixar)$/i.test(text)) score += 50;
              if (/\\b(dow|baixar)\\b/i.test(text)) score += 20;
              if (/\\bdow\\b|baixar|download\\(|\\.xlsx|\\.xls|\\.csv|\\.sswweb|arquivo/.test(blob)) score += 15;
              if (onclick) score += 10;
              if (href && href !== '#' && !/^javascript:/i.test(href)) score += 8;
              if (score > 0) scored.push({
                text, onclick, href, score, tag: (a.tagName || '').toLowerCase()
              });
            }
            scored.sort((x, y) => y.score - x.score);
            if (scored.length) {
              return { ok: true, why: 'link', best: scored[0], n: scored.length };
            }
            for (const td of Array.from(tr.querySelectorAll('td'))) {
              const t = norm(td.innerText || '');
              if (!(/^(dow|baixar)$/i.test(t) || (t.length <= 10 && /\\b(dow|baixar)\\b/i.test(t)))) continue;
              const child = td.querySelector(
                'a[onclick], a[href], img[onclick], input[onclick], button[onclick]'
              );
              if (child) {
                return {
                  ok: true,
                  why: 'td-child',
                  best: {
                    text: t,
                    onclick: String(child.getAttribute('onclick') || ''),
                    href: String(child.getAttribute('href') || ''),
                    score: 40,
                    tag: (child.tagName || '').toLowerCase(),
                  },
                  n: 1,
                };
              }
            }
            return { ok: false, why: 'sem_dow_real', cells: cells.slice(0, 8) };
          }
          return { ok: false, why: 'seq_sumiu' };
        }""",
        {"seq": seq},
    )
    if not meta or not meta.get("ok"):
        why = (meta or {}).get("why") or "desconhecido"
        raise RuntimeError(f"455: Baixar da seq={seq} não encontrado ({why})")

    best = meta.get("best") or {}
    status(
        f"[455] Baixar meta · tag={best.get('tag')} · "
        f"txt={(best.get('text') or '')[:20]} · "
        f"onclick={(best.get('onclick') or '')[:70]}"
    )

    # href direto (sem javascript) → fetch autenticado no context
    href = str(best.get("href") or "").strip()
    if href and href not in {"#", ""} and not href.lower().startswith("javascript:"):
        try:
            abs_url = href
            if href.startswith("/"):
                abs_url = "https://sistema.ssw.inf.br" + href
            elif not href.lower().startswith("http"):
                abs_url = "https://sistema.ssw.inf.br/bin/" + href.lstrip("./")
            status(f"[455] fetch href · {abs_url[:90]}")
            body = context.request.get(abs_url, timeout=60000).body()
            if body and len(body) > 64:
                dest = Path(client.download_dir) / dest_name
                if dest.exists():
                    dest = dest.with_name(
                        f"{dest.stem}_{int(time.time())}{dest.suffix}"
                    )
                dest.write_bytes(body)
                return dest
        except Exception as err:
            status(f"[455] fetch href falhou: {err}")

    def _trigger() -> str:
        return str(
            fila.evaluate(
                """({ seq }) => {
                  const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                  const want = String(seq || '').replace(/\\D/g, '');
                  for (const tr of document.querySelectorAll('tr')) {
                    const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
                    const s = (cells[0] || '').replace(/\\D/g, '');
                    if (s !== want) continue;
                    const links = Array.from(tr.querySelectorAll(
                      'a[onclick], a[href], img[onclick], input[onclick], button[onclick]'
                    ));
                    const pick = [];
                    for (const a of links) {
                      const text = norm(a.textContent || a.alt || a.title || a.value || '');
                      const onclick = String(a.getAttribute('onclick') || '');
                      const href = String(a.getAttribute('href') || '');
                      const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
                      if (/imprimir|correio|atualizar|voltar|fechar|sair/i.test(text)
                          && !/\\b(dow|baixar)\\b/i.test(text)) continue;
                      let score = 0;
                      if (/^(dow|baixar)$/i.test(text)) score += 50;
                      if (/\\b(dow|baixar)\\b/i.test(text)) score += 20;
                      if (/\\bdow\\b|baixar|download\\(|\\.xlsx|\\.csv|\\.sswweb|arquivo/.test(blob)) score += 15;
                      if (score > 0) pick.push({ a, score, onclick, href });
                    }
                    pick.sort((x, y) => y.score - x.score);
                    if (pick.length) {
                      const el = pick[0].a;
                      const oc = pick[0].onclick || '';
                      try { el.click(); return 'click'; } catch (e1) {}
                      if (oc) {
                        try { (function(){ eval(oc); })(); return 'eval-onclick'; } catch (e2) {}
                      }
                      return 'click-fail';
                    }
                    if (typeof ajaxEnvia === 'function') {
                      try { ajaxEnvia('DOW', want); return 'ajax-DOW'; } catch (e4) {}
                      try { ajaxEnvia('DOW', 0); return 'ajax-DOW0'; } catch (e5) {}
                    }
                    const tds = tr.querySelectorAll('td');
                    if (tds.length) {
                      const last = tds[tds.length - 1];
                      const child = last.querySelector(
                        'a[onclick], a[href], img[onclick], input[onclick], button[onclick]'
                      ) || last.querySelector('a, img, input, button');
                      if (child) {
                        try { child.click(); return 'td-child'; } catch (e6) {}
                      }
                    }
                    return 'sem_link';
                  }
                  return 'seq_sumiu';
                }""",
                {"seq": seq},
            )
            or ""
        )

    # 1) evento download no context
    try:
        with context.expect_event("download", timeout=25000) as di:
            how = _trigger()
            status(f"[455] clique DOW={how}")
            if how in {"seq_sumiu", "sem_link", "click-fail", ""}:
                raise RuntimeError(f"trigger falhou ({how})")
        return client._save_download(di.value, dest_name)
    except PlaywrightTimeoutError:
        status("[455] sem evento download — nova aba / fetch…")
    except RuntimeError:
        raise
    except Exception as err:
        status(f"[455] download context: {err}")

    # 2) nova aba / popup
    pages_before = list(context.pages)
    new_page = None
    try:
        with context.expect_page(timeout=12000) as pi:
            how = _trigger()
            status(f"[455] clique(aba) DOW={how}")
        new_page = pi.value
    except PlaywrightTimeoutError:
        after = [p for p in context.pages if p not in pages_before]
        if after:
            new_page = after[-1]

    if new_page is not None:
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        try:
            with new_page.expect_download(timeout=15000) as di:
                try:
                    new_page.wait_for_load_state("load", timeout=4000)
                except Exception:
                    pass
            path = client._save_download(di.value, dest_name)
            try:
                new_page.close()
            except Exception:
                pass
            return path
        except PlaywrightTimeoutError:
            try:
                url = new_page.url or ""
                status(f"[455] aba · {url[:90]}")
                if url and not url.startswith("about:") and "blank" not in url.lower():
                    body = context.request.get(url, timeout=60000).body()
                    if body and len(body) > 64:
                        dest = Path(client.download_dir) / dest_name
                        if dest.exists():
                            dest = dest.with_name(
                                f"{dest.stem}_{int(time.time())}{dest.suffix}"
                            )
                        dest.write_bytes(body)
                        try:
                            new_page.close()
                        except Exception:
                            pass
                        return dest
            except Exception as err:
                status(f"[455] fetch aba: {err}")
            try:
                new_page.close()
            except Exception:
                pass

    # 3) ajaxEnvia('DOW', seq) forçado + download
    try:
        with context.expect_event("download", timeout=20000) as di:
            how = str(
                fila.evaluate(
                    """({ seq }) => {
                      const want = String(seq || '').replace(/\\D/g, '');
                      if (typeof ajaxEnvia === 'function') {
                        try { ajaxEnvia('DOW', want); return 'ajax-DOW'; } catch (e) {}
                        try { ajaxEnvia('DOW', 0); return 'ajax-DOW0'; } catch (e2) {}
                      }
                      return 'sem-ajax';
                    }""",
                    {"seq": seq},
                )
                or ""
            )
            status(f"[455] force {how}")
            if how.startswith("sem"):
                raise RuntimeError(how)
        return client._save_download(di.value, dest_name)
    except Exception as err:
        status(f"[455] force DOW: {err}")

    raise RuntimeError(f"455: DOW não gerou download (seq={seq})")
