"""Download SSW 076 — Demonstrativo de remuneração (frete coleta/entrega por carro).

Formulário:
  Sigla · Período · Veículo (opcional) · Arquivo=E (excel) · E-mail=N
  ► gera e manda pra fila 156 → DOW.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_mes_ate_hoje, to_ssw_ddmmyy
from ssw_client import AceSswClient, cleanup_downloads
from ssw_fila156 import (
    FilaSemDados,
    aguardar_baixar,
    atualizar_fila as _atualizar_fila156,
    esperar_meta_baixar,
    find_baixar_meta,
    ler_jobs as _ler_jobs156,
    abrir_fila as _abrir_fila156,
)

StatusCallback = Callable[[str], None]

SSW_076_MARKERS = (
    "076",
    "remuner",
    "demonstrativo",
    "veiculo",
    "arquivo",
    "sigla",
    "periodo",
    "ver fila",
)

_076_OPTION_PATTERNS = (
    r"076\s*-",
    r"\b076\b",
    r"remuner",
    r"demonstrativo",
    r"coletas?/entrega",
    r"ssw0?76",
)


_EMPTY_FILA_RE = re.compile(
    r"n[aã]o\s+selecionou|nao\s+selecionou|sem\s+ctrcs?|nenhum\s+ctrcs?|"
    r"sem\s+dados|n[aã]o\s+h[aá]\s+regist|nada\s+a\s+(gerar|emitir)|"
    r"sem\s+movimento|sem\s+demonstrativ|sem\s+base",
    re.IGNORECASE,
)


def _is_empty_fila_msg(text: str) -> bool:
    s = str(text or "").lower()
    if not s:
        return False
    if "sem base" in s:
        return True
    return bool(_EMPTY_FILA_RE.search(s))


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
    arquivo: str = "E",
    unidade: str = "",
    email: str = "N",
    # tag no nome do arquivo (ex.: filial GYN)
    tag: str = "",
    # compat: callers antigos passavam operacao="R"
    operacao: str | None = None,
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
    Gera 076 com Arquivo=E (excel) → fila 156 → DOW.
    `unidade` vazia = não mexer na Sigla (herda do menu).
    """
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)
    plate_list = [str(p).strip().upper() for p in (placas or []) if str(p).strip()]
    runs = plate_list or [""]

    # Arquivo: E-excel (R=relatório / X=resumo). Ignora operacao legado se for R.
    arq = (arquivo or "E").strip().upper()[:1] or "E"
    if operacao is not None and arq == "E":
        op_legacy = str(operacao).strip().upper()[:1]
        if op_legacy in {"E", "X"}:
            arq = op_legacy
    unidade_sigla = (unidade or "").strip().upper()
    file_tag = (tag or unidade_sigla or "ALL").strip().upper() or "ALL"
    envia_email = (email or "N").strip().upper()[:1] or "N"

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
        f"SSW 76 | arquivo={arq} | sigla={unidade_sigla or '(menu)'} | {ini}-{fim} | "
        f"{len(plate_list) or 'todas'} placa(s)"
        + (" · sessão reusada" if reuse else "")
    )

    def _run(sess_client, sess_context, sess_page) -> None:
        nonlocal paths, errors
        popup = None
        try:
            status(f"[76/{file_tag}] abrindo opção 76…")
            popup = _reopen_76(sess_client, sess_page, popup)
            _preencher_76(
                popup,
                ini=ini,
                fim=fim,
                unidade=unidade_sigla,
                arquivo=arq,
                email=envia_email,
                placa="",
                on_status=status,
            )
            dest = f"contratacao_076_{file_tag}_{ts}.sswweb"
            path = _gerar_download_76(
                sess_client, sess_context, sess_page, popup, dest, file_tag, status
            )
            paths.append(str(path))
            status(f"[76/{file_tag}] OK {path.name}")
        except FilaSemDados as empty_err:
            errors[file_tag] = str(empty_err)
            status(f"[76/{file_tag}] sem CTRCs — desconsidera ({empty_err})")
        except Exception as batch_err:  # noqa: BLE001
            if isinstance(batch_err, FilaSemDados):
                errors[file_tag] = str(batch_err)
                status(f"[76/{file_tag}] sem CTRCs — desconsidera ({batch_err})")
                return
            status(f"[76/{file_tag}] lote falhou ({batch_err}); tentando por placa…")
            for idx, placa in enumerate(runs[:40], start=1):
                key = placa or file_tag
                try:
                    status(f"[76/{key}] ({idx}) abrindo…")
                    popup = _reopen_76(sess_client, sess_page, popup)
                    _preencher_76(
                        popup,
                        ini=ini,
                        fim=fim,
                        unidade=unidade_sigla,
                        arquivo=arq,
                        email=envia_email,
                        placa=placa,
                        on_status=status,
                    )
                    dest = f"contratacao_076_{file_tag}_{key or 'ALL'}_{ts}.sswweb"
                    path = _gerar_download_76(
                        sess_client, sess_context, sess_page, popup, dest, key, status
                    )
                    paths.append(str(path))
                except FilaSemDados as empty_err:
                    errors[key] = str(empty_err)
                    status(f"[76/{key}] sem CTRCs — desconsidera ({empty_err})")
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
        try:
            from ace_stop import register_browser
            register_browser(browser)
        except Exception:
            pass
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
                try:
                    browser.close()
                except Exception:
                    pass
                try:
                    from ace_stop import unregister_browser
                    unregister_browser(browser)
                except Exception:
                    pass

    if not paths and errors:
        # «NÃO SELECIONOU CTRCS…» / sem base → ok parcial; não derruba nem contabiliza
        only_empty = all(_is_empty_fila_msg(v) for v in errors.values())
        if only_empty:
            status(f"[76/{file_tag}] sem CTRCs — desconsiderado (não contabiliza)")
            return {
                "ok": True,
                "files": [],
                "errors": errors,
                "empty": True,
                "period": (ini_ddmm, fim_ddmm),
                "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
                "arquivo": arq,
                "operacao": arq,
                "unidade": unidade_sigla,
                "tag": file_tag,
                "placas": plate_list,
            }
        raise RuntimeError("076 falhou: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))

    return {
        "ok": bool(paths),
        "files": paths,
        "errors": errors,
        "period": (ini_ddmm, fim_ddmm),
        "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
        "arquivo": arq,
        "operacao": arq,  # compat
        "unidade": unidade_sigla,
        "tag": file_tag,
        "placas": plate_list,
    }


def _reopen_76(client, page, popup):
    try:
        if popup is not None and not popup.is_closed():
            popup.close()
    except Exception:
        pass
    return client._open_menu_option(page, "76", markers=SSW_076_MARKERS)


def _preencher_76(
    popup,
    *,
    ini: str,
    fim: str,
    unidade: str,
    arquivo: str,
    email: str,
    placa: str,
    on_status,
) -> None:
    """Preenche 076: Sigla · Período · Veículo · Arquivo=E · E-mail=N."""
    status = on_status
    popup.wait_for_timeout(400)
    filled = popup.evaluate(
        """({ ini, fim, unidade, arquivo, email, placa }) => {
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

          // Sigla vazia = herda do menu (não sobrescreve)
          const okUnid = unidade ? set(byHint(['sigla', 'unidade']), unidade) : true;

          const ddmmyy = inputs.filter((el) => Number(el.maxLength) === 6 || Number(el.size) === 6);
          let okIni = false, okFim = false;
          if (ddmmyy.length >= 2) {
            okIni = set(ddmmyy[0], ini);
            okFim = set(ddmmyy[1], fim);
          }

          const okVeic = placa
            ? set(byHint(['veiculo', 'placa', 'cavalo']), placa)
            : true;

          // Arquivo: R-relatório / E-excel / X-resumo  ← NÃO confundir com "operação"
          let okArq = set(byHint(['arquivo']), arquivo);
          if (!okArq) {
            // fallback: input de 1 char com valor R/E/X
            for (const el of inputs) {
              const v = (el.value || '').toUpperCase();
              if (v === 'R' || v === 'E' || v === 'X') {
                okArq = set(el, arquivo);
                break;
              }
            }
          }

          let okMail = set(byHint(['e-mail', 'email', 'envia']), email);
          if (!okMail) {
            for (const el of inputs) {
              const v = (el.value || '').toUpperCase();
              if (v === 'S' || v === 'N') {
                const lab = norm(near(el));
                if (lab.includes('mail') || lab.includes('envia')) {
                  okMail = set(el, email);
                  break;
                }
              }
            }
          }

          return {
            okUnid, okIni, okFim, okVeic, okArq, okMail,
            n: inputs.length,
            vals: inputs.slice(0, 8).map((el) => el.value || ''),
          };
        }""",
        {
            "ini": ini,
            "fim": fim,
            "unidade": unidade,
            "arquivo": arquivo,
            "email": email,
            "placa": placa or "",
        },
    )
    status(f"[76] form {filled}")

    # Playwright fill se evaluate falhou no Arquivo
    if not filled.get("okArq"):
        for cand in ("4", "5", "6", "7", "8"):
            try:
                loc = popup.locator(f'[id="{cand}"]')
                if not loc.count():
                    continue
                cur = (loc.first.input_value() or "").strip().upper()
                if cur in {"R", "E", "X", ""}:
                    loc.first.fill(arquivo)
                    status(f"[76] Arquivo via #{cand}={arquivo}")
                    break
            except Exception:
                continue
    popup.wait_for_timeout(200)


def _clicar_gerar_76(popup) -> str:
    """Clica ► para gerar (manda pra fila 156)."""
    try:
        loc = popup.get_by_text("►", exact=True)
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            return "►"
    except Exception:
        pass
    try:
        loc = popup.locator("a", has_text="►")
        if loc.count() > 0:
            loc.first.click(timeout=5000)
            return "a:►"
    except Exception:
        pass
    return popup.evaluate(
        """() => {
          const links = Array.from(document.querySelectorAll('a, span, button, img'));
          for (const a of links) {
            const t = ((a.innerText || a.textContent || a.alt || a.title || '') + '').trim();
            if (t === '►' || t === '▶' || t === '>') { a.click(); return 'play'; }
          }
          // fallback: 1º link após a linha (geralmente ►)
          const as = Array.from(document.querySelectorAll('a'));
          if (as.length) { as[0].click(); return 'a0'; }
          return '';
        }"""
    )


def _gerar_download_76(client, context, page, popup, dest_name: str, key: str, status) -> Path:
    """076: Arquivo=E + ► → fila 156 → DOW.

    Não abre/fecha 156 antes do ► (isso travava). Abre a fila depois,
    espera o job novo e baixa com clique robusto (download OU nova aba).
    """
    clicked = _clicar_gerar_76(popup)
    if not clicked:
        raise RuntimeError("076: botão ► não encontrado")
    status(f"[76/{key}] gerou com ► → fila 156")
    try:
        popup.wait_for_timeout(800)
    except Exception:
        pass
    enqueue_t0 = time.time()
    return _baixar_via_fila_76(
        client,
        context,
        page,
        popup,
        dest_name,
        key,
        status,
        known_done=set(),
        min_seq=0,
        enqueue_t0=enqueue_t0,
    )


def _snapshot_fila_076_before(client, context, page, popup, status) -> tuple[set[str], int]:
    """LEGADO — não usar (abria/fechava 156 cedo e travava o fluxo)."""
    status("[76] pré-fila desativada (evita abrir 156 antes do ►)")
    return set(), 0


SSW_FILA_URL = "https://sistema.ssw.inf.br/bin/ssw1440"


def _safe_wait(page, ms: int) -> None:
    try:
        if page is None or (hasattr(page, "is_closed") and page.is_closed()):
            time.sleep(ms / 1000.0)
            return
        page.wait_for_timeout(ms)
    except Exception:
        time.sleep(ms / 1000.0)


def _abrir_fila_156_76(client, context, page, status, popup=None):
    return _abrir_fila156(client, context, page, status, popup=popup, tag="76")


def _garantir_url_fila_76(client, page, fila, status):
    return fila


def _ler_jobs_fila_76(fila) -> list[dict]:
    jobs = _ler_jobs156(fila)
    for j in jobs:
        blob = f"{j.get('opcao') or ''} {' '.join(str(x) for x in (j.get('cells') or []))}".lower()
        j["is076"] = bool(
            re.search(r"076|remuner|demonstrativo|ssw0?76|coletas?/entrega", blob, re.I)
        )
    return jobs


def _job_076_sem_dados(job: dict) -> bool:
    """Concluído sem DOW (mensagem típica: NÃO SELECIONOU CTRCS…)."""
    if not job.get("concluido") or job.get("hasDow"):
        return False
    msg = str(job.get("mensagem") or job.get("situacao") or "")
    if _EMPTY_FILA_RE.search(msg):
        return True
    return True


def _atualizar_fila_76(fila) -> None:
    _atualizar_fila156(fila)


def _baixar_via_fila_76(
    client,
    context,
    page,
    popup,
    dest_name: str,
    key: str,
    status,
    *,
    known_done: set[str] | None = None,
    min_seq: int = 0,
    enqueue_t0: float | None = None,
) -> Path:
    """Espera job 076 do login na 156 e clica Baixar. Sem base → FilaSemDados."""
    _ = known_done, min_seq
    t0 = float(enqueue_t0 or time.time())
    login_user = str(getattr(getattr(client, "credentials", None), "user", "") or "").strip()
    fila, job = aguardar_baixar(
        client,
        context,
        page,
        popup,
        status,
        login_user=login_user,
        option_patterns=_076_OPTION_PATTERNS,
        enqueue_t0=t0,
        tag=f"76/{key}",
        timeout_s=240.0,
        stuck_sem_dow_s=90.0,
    )
    seq = str(job.get("seq") or "")
    status(
        f"[76/{key}] DOW na fila · seq={seq} · user={job.get('usuario')} · "
        f"{job.get('data_hora')} · {job.get('opcao') or ''}"
    )
    path = _clicar_dow_76(client, context, fila, job, dest_name, status, key)
    size = path.stat().st_size if path.exists() else 0
    if size < 64:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(f"arquivo suspeito ({size} bytes)")
    status(f"[76/{key}] OK {path.name} ({size} bytes)")
    try:
        if fila is not None and not fila.is_closed():
            fila.close()
    except Exception:
        pass
    return path


def _clicar_dow_76(client, context, fila, job: dict, dest_name: str, status, key: str) -> Path:
    """Clica Baixar/DOW com fallbacks (evento download, nova aba, fetch URL)."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    seq = str(job.get("seq") or "")
    _atualizar_fila_76(fila)
    _safe_wait(fila, 300)

    meta = find_baixar_meta(fila, seq)
    if not meta or not meta.get("ok"):
        why = (meta or {}).get("why") or ""
        if why == "ainda_processando" or (meta or {}).get("interromper"):
            status(f"[76/{key}] ainda Interromper/gerando · seq={seq} — esperando Baixar…")
            meta = esperar_meta_baixar(fila, seq, status, tag=f"76/{key}", timeout_s=180.0)
        if not meta or not meta.get("ok"):
            raise RuntimeError(
                f"76/{key}: Baixar da seq={seq} não encontrado "
                f"({(meta or {}).get('why') or 'desconhecido'})"
            )

    def _trigger() -> str:
        return str(
            fila.evaluate(
                """({ seq }) => {
                  const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                  const want = String(seq || '').replace(/\\D/g, '');
                  const rows = Array.from(document.querySelectorAll('tr'));
                  for (const tr of rows) {
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
                      if (/interrom|cancelar\\s*gera|parar\\s*gera/i.test(text)) continue;
                      if (/imprimir|correio|atualizar|voltar|fechar|sair/i.test(text)
                          && !/\\b(dow|baixar)\\b/i.test(text)) continue;
                      let score = 0;
                      if (/^(dow|baixar)$/i.test(text)) score += 50;
                      if (/\\b(dow|baixar)\\b/i.test(text)) score += 20;
                      if (/\\bdow\\b|baixar|download\\(|\\.xlsx|\\.csv|\\.sswweb|arquivo/.test(blob)
                          && !/interrom/i.test(blob)) score += 15;
                      if (score > 0) pick.push({ a, score, onclick });
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
                      const child = last.querySelector('a, img, input, button, font, b, span') || last;
                      try { child.click(); return 'td-last'; } catch (e6) {}
                    }
                    return 'sem_link';
                  }
                  return 'seq_sumiu';
                }""",
                {"seq": seq},
            )
            or ""
        )

    # 1) evento download
    try:
        with context.expect_event("download", timeout=22000) as di:
            how = _trigger()
            status(f"[76/{key}] clique DOW={how}")
            if how in {"seq_sumiu", "sem_link", "click-fail", ""}:
                raise RuntimeError(f"trigger falhou ({how})")
        return client._save_download(di.value, dest_name)
    except PlaywrightTimeoutError:
        status(f"[76/{key}] sem evento download — tentando nova aba…")
    except RuntimeError:
        raise
    except Exception as err:
        status(f"[76/{key}] download context: {err}")

    # 2) nova aba / popup
    pages_before = list(context.pages)
    new_page = None
    try:
        with context.expect_page(timeout=10000) as pi:
            how = _trigger()
            status(f"[76/{key}] clique(aba) DOW={how}")
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
                status(f"[76/{key}] aba aberta · {url[:80]}")
                if url and not url.startswith("about:") and "blank" not in url.lower():
                    resp = context.request.get(url, timeout=30000)
                    body = resp.body()
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
                status(f"[76/{key}] fetch aba: {err}")
            try:
                new_page.close()
            except Exception:
                pass

    raise RuntimeError(f"076/{key}: DOW não gerou download (seq={seq})")
