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


class FilaSemDados(RuntimeError):
    """Job concluiu na 156 sem arquivo (ex.: NÃO SELECIONOU CTRCS)."""


_EMPTY_FILA_RE = re.compile(
    r"n[aã]o\s+selecionou|sem\s+ctrc|nenhum\s+ctrc|sem\s+dados|n[aã]o\s+h[aá]\s+regist|"
    r"nada\s+a\s+(gerar|emitir)|sem\s+movimento|sem\s+demonstrativ",
    re.IGNORECASE,
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
            status(f"[76/{file_tag}] sem base — pula ({empty_err})")
        except Exception as batch_err:  # noqa: BLE001
            if isinstance(batch_err, FilaSemDados):
                errors[file_tag] = str(batch_err)
                status(f"[76/{file_tag}] sem base — pula ({batch_err})")
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
                    status(f"[76/{key}] sem base — pula ({empty_err})")
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
        # só "sem base" → ok parcial (filial sem movimento); não derruba o fluxo
        only_empty = all(
            "sem base" in str(v).lower() or "nāo selecionou" in str(v).lower()
            or "não selecionou" in str(v).lower()
            or "nao selecionou" in str(v).lower()
            for v in errors.values()
        )
        if only_empty:
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
    """Abre 156 rápido: ajax/goto primeiro (Ver fila só com timeout curto)."""
    status("[76] abrindo fila 156…")

    # 1) ajaxEnvia ssw1440 a partir da página principal
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
        try:
            fila.wait_for_load_state("domcontentloaded", timeout=12000)
        except Exception:
            pass
        status("[76] fila 156 via ajaxEnvia ssw1440")
        _safe_wait(fila, 500)
        return _garantir_url_fila_76(client, page, fila, status)
    except Exception as err:
        status(f"[76] ajax 156: {err}")

    # 2) Ver fila na tela 076 (timeout curto — não fica 12s preso)
    if popup is not None:
        try:
            if not popup.is_closed():
                with context.expect_page(timeout=4000) as pi:
                    clicked = popup.evaluate(
                        """() => {
                          const els = Array.from(document.querySelectorAll('a, span, button'));
                          for (const a of els) {
                            const t = ((a.innerText || a.textContent || '') + '').replace(/\\s+/g, ' ').trim();
                            if (/^Ver fila$/i.test(t) || /ver\\s*fila/i.test(t)) {
                              a.click(); return t;
                            }
                          }
                          return '';
                        }"""
                    )
                    if not clicked:
                        raise RuntimeError("sem Ver fila")
                fila = pi.value
                try:
                    fila.on("dialog", lambda d: d.accept())
                except Exception:
                    pass
                status(f"[76] fila 156 via '{clicked}'")
                _safe_wait(fila, 500)
                return _garantir_url_fila_76(client, page, fila, status)
        except Exception as err:
            status(f"[76] Ver fila: {err}")

    # 3) goto direto
    try:
        try:
            page.bring_to_front()
        except Exception:
            pass
        fila = context.new_page()
        try:
            fila.on("dialog", lambda d: d.accept())
        except Exception:
            pass
        fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=30000)
        status("[76] fila 156 via goto ssw1440")
        _safe_wait(fila, 500)
        return fila
    except Exception as err:
        status(f"[76] goto fila: {err}")

    # 4) menu 156
    try:
        fila = client._open_menu_option(
            page, "156", markers=("fila", "dow", "156", "1440", "processamento", "lotes")
        )
        status("[76] fila 156 via menu")
        return _garantir_url_fila_76(client, page, fila, status)
    except Exception as err:
        status(f"[76] menu 156: {err}")
        raise RuntimeError(f"076: não abriu fila 156 ({err})") from err


def _garantir_url_fila_76(client, page, fila, status):
    try:
        url = (fila.url or "").lower()
    except Exception:
        url = ""
    if "blank" in url or "ssw1440" not in url:
        status("[76] recuperando ssw1440…")
        try:
            fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=30000)
            _safe_wait(fila, 600)
        except Exception as err:
            status(f"[76] goto ssw1440: {err}")
            try:
                client._recuperar_blank(page, fila, "1440", ("fila", "dow", "156", "1440"))
            except Exception:
                pass
    return fila

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
            let sit = '';
            for (const c of cells) {
              if (/^(conclu[ií]d[oa]|processando|na fila|em fila|erro|abortad)/i.test(c)) {
                sit = c; break;
              }
            }
            if (!sit) {
              sit = cells.find(c => /conclu|processando|na\\s*fila|erro|abort/i.test(c)) || cells[6] || '';
            }
            const links = Array.from(tr.querySelectorAll(
              'a[onclick], a[href], img[onclick], input[onclick], button[onclick]'
            )).map(a => {
              const text = norm(a.textContent || a.alt || a.title || a.value || '');
              const onclick = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
              return { text, onclick, href, blob };
            });
            const dows = links.filter(x => {
              if (/imprimir|correio|atualizar|voltar|fechar|sair/i.test(x.text)
                  && !/\\b(dow|baixar)\\b/i.test(x.text)) return false;
              if (/^(dow|baixar)$/i.test(x.text) || /\\bdow\\b/i.test(x.text)) return true;
              return /\\bdow\\b|download\\(|\\.xlsx|\\.xls|\\.csv|\\.sswweb|baixar|arquivo/.test(x.blob);
            });
            if (!dows.length) {
              for (const td of Array.from(tr.querySelectorAll('td'))) {
                const t = norm(td.innerText || '');
                if (/^(dow|baixar)$/i.test(t) || (t.length <= 8 && /\\b(dow|baixar)\\b/i.test(t))) {
                  dows.push({ text: t, onclick: '', href: '', blob: 'dow baixar' });
                  break;
                }
              }
            }
            // última coluna costuma ser a mensagem (ex.: NÃO SELECIONOU CTRCS…)
            let mensagem = '';
            for (let i = cells.length - 1; i >= 0; i--) {
              const c = cells[i] || '';
              if (!c) continue;
              if (/^(conclu|process|fila|erro|abort|\\d)/i.test(c) && c.length < 20) continue;
              if (/^\\d{1,2}\\/\\d{1,2}/.test(c)) continue;
              if (/^dow$/i.test(c) || /^baixar$/i.test(c)) continue;
              if (c.length >= 8) { mensagem = c; break; }
            }
            const blobAll = (opcao + ' ' + cells.join(' ') + ' ' + links.map(l => l.blob).join(' ')).toLowerCase();
            const concluido = /conclu/i.test(sit) && !/n[aã]o\\s*conclu|inconclu/i.test(sit);
            jobs.push({
              seq,
              opcao,
              situacao: sit,
              mensagem,
              concluido,
              is076: /076|remuner|demonstrativo|ssw0?76|coletas\\/entrega/i.test(blobAll),
              hasDow: dows.length > 0,
              dows,
            });
          }
          return jobs;
        }"""
    )


def _job_076_sem_dados(job: dict) -> bool:
    """Concluído sem DOW (mensagem típica: NÃO SELECIONOU CTRCS…)."""
    if not job.get("concluido") or job.get("hasDow"):
        return False
    msg = str(job.get("mensagem") or job.get("situacao") or "")
    if _EMPTY_FILA_RE.search(msg):
        return True
    # Concluído 076 sem link DOW = sem base (não fica esperando eternamente)
    return bool(job.get("is076"))


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
    """Espera job 076 concluído na 156 e clica DOW. Sem base → FilaSemDados."""
    done = set(known_done or ())
    floor = int(min_seq or 0)
    t0 = float(enqueue_t0 or time.time())
    fila = None

    # Abre 156 UMA vez (depois do ►) e define floor no 1º poll
    fila = _abrir_fila_156_76(client, context, page, status, popup=popup)
    _safe_wait(fila, 600)
    tracked: set[str] = set()  # seqs desta rodada
    try:
        bootstrap = _ler_jobs_fila_76(fila)
        only76 = [j for j in bootstrap if j.get("is076") and str(j.get("seq") or "")]
        only76.sort(key=lambda j: int("".join(ch for ch in str(j.get("seq") or "") if ch.isdigit()) or 0))
        processing = [j for j in only76 if not j.get("concluido")]
        concluded = [j for j in only76 if j.get("concluido")]
        if processing:
            for j in processing:
                tracked.add(str(j.get("seq") or ""))
            if concluded:
                floor = max(
                    int("".join(ch for ch in str(j.get("seq") or "") if ch.isdigit()) or 0)
                    for j in concluded
                )
                for j in concluded:
                    done.add(str(j.get("seq") or ""))
        elif only76:
            # Job pode ter concluído antes do 1º poll — pega o mais novo
            newest = only76[-1]
            nseq = str(newest.get("seq") or "")
            tracked.add(nseq)
            floor = int("".join(ch for ch in nseq if ch.isdigit()) or 0) - 1
            for j in only76[:-1]:
                if j.get("concluido"):
                    done.add(str(j.get("seq") or ""))
        status(
            f"[76] fila wait · tracked={len(tracked) or 'auto'} · "
            f"floor={floor} · known={len(done)}"
        )
    except Exception as err:
        status(f"[76] bootstrap fila: {err}")

    def _seq_num(j: dict) -> int:
        seq = str(j.get("seq") or "")
        try:
            return int("".join(ch for ch in seq if ch.isdigit()) or 0)
        except Exception:
            return 0

    def _is_nosso(j: dict) -> bool:
        seq = str(j.get("seq") or "")
        if not seq or not j.get("is076"):
            return False
        if seq in done:
            return False
        if tracked and seq in tracked:
            return True
        num = _seq_num(j)
        if floor > 0 and num <= floor:
            return False
        return True

    def _ensure_fila():
        nonlocal fila
        try:
            if fila is not None and not fila.is_closed():
                url = (fila.url or "").lower()
                if "ssw1440" in url and "blank" not in url:
                    return fila
        except Exception:
            pass
        status("[76] 156 caiu — reabrindo…")
        fila = _abrir_fila_156_76(client, context, page, status, popup=None)
        _safe_wait(fila, 600)
        return fila

    deadline = time.time() + 240
    last_err = ""
    last_log = 0.0
    while time.time() < deadline:
        try:
            f = _ensure_fila()
            try:
                f.bring_to_front()
            except Exception:
                pass
            _atualizar_fila_76(f)
            _safe_wait(f, 1000)
            jobs = _ler_jobs_fila_76(f)

            # Novos processando → entram no tracked
            for j in jobs:
                if j.get("is076") and not j.get("concluido"):
                    seq = str(j.get("seq") or "")
                    if seq:
                        tracked.add(seq)

            nossos = [j for j in jobs if _is_nosso(j)]
            if not nossos:
                only76 = [j for j in jobs if j.get("is076") and str(j.get("seq") or "")]
                only76.sort(key=_seq_num)
                if only76 and not tracked:
                    nossos = only76[-1:]
                    tracked.add(str(only76[-1].get("seq") or ""))

            vazios = [j for j in nossos if _job_076_sem_dados(j)]
            if vazios:
                vazios.sort(key=_seq_num, reverse=True)
                job = vazios[0]
                msg = str(job.get("mensagem") or "sem DOW")
                raise FilaSemDados(f"sem base · seq={job.get('seq')} · {msg[:80]}")

            cands = [
                j for j in nossos if j.get("concluido") and j.get("hasDow")
            ]
            cands.sort(key=_seq_num, reverse=True)

            now = time.time()
            if now - last_log >= 4:
                last_log = now
                proc = [j for j in nossos if not j.get("concluido")]
                status(
                    f"[76/{key}] aguardando Concluído+DOW na 156 "
                    f"({len(proc)} processando · {len(cands)} prontos)…"
                )
                for j in proc[:3]:
                    status(
                        f"[76]   ⏳ seq={j.get('seq')} · {j.get('situacao') or '?'} · "
                        f"{(j.get('opcao') or '')[:40]}"
                    )

            if not cands:
                _safe_wait(f, 1800)
                continue

            job = cands[0]
            seq = str(job.get("seq") or "")
            status(f"[76/{key}] DOW na fila · seq={seq} · {job.get('opcao') or ''}")
            path = _clicar_dow_76(client, context, f, job, dest_name, status, key)
            size = path.stat().st_size if path.exists() else 0
            if size < 64:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise RuntimeError(f"arquivo suspeito ({size} bytes)")
            status(f"[76/{key}] OK {path.name} ({size} bytes)")
            # mantém 156 aberta? fecha só no fim deste download
            try:
                if f is not None and not f.is_closed():
                    f.close()
            except Exception:
                pass
            return path
        except FilaSemDados:
            try:
                if fila is not None and not fila.is_closed():
                    fila.close()
            except Exception:
                pass
            raise
        except Exception as err:  # noqa: BLE001
            last_err = str(err)
            status(f"[76/{key}] fila loop: {err}")
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
            time.sleep(1.5)

    try:
        if fila is not None and not fila.is_closed():
            fila.close()
    except Exception:
        pass
    raise RuntimeError(f"076/{key}: timeout na fila 156 ({last_err})")


def _clicar_dow_76(client, context, fila, job: dict, dest_name: str, status, key: str) -> Path:
    """Clica Baixar/DOW com fallbacks (evento download, nova aba, fetch URL)."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    seq = str(job.get("seq") or "")
    _atualizar_fila_76(fila)
    _safe_wait(fila, 300)

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
                      if (/imprimir|correio|atualizar|voltar|fechar|sair/i.test(text)
                          && !/\\b(dow|baixar)\\b/i.test(text)) continue;
                      let score = 0;
                      if (/^(dow|baixar)$/i.test(text)) score += 50;
                      if (/\\b(dow|baixar)\\b/i.test(text)) score += 20;
                      if (/\\bdow\\b|baixar|download\\(|\\.xlsx|\\.csv|\\.sswweb|arquivo/.test(blob)) score += 15;
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
