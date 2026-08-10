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
    """076: Arquivo=E + ► → fila 156 → DOW."""
    clicked = _clicar_gerar_76(popup)
    if not clicked:
        raise RuntimeError("076: botão ► não encontrado")
    status(f"[76/{key}] gerou com ► → fila 156")
    try:
        popup.wait_for_timeout(1000)
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


def _abrir_fila_156_76(client, context, page, status, popup=None):
    """Abre 156: preferir link 'Ver fila' na tela 76; senão goto ssw1440."""
    status("[76] abrindo fila 156…")

    # 1) Link "Ver fila" na própria tela 076
    if popup is not None:
        try:
            if not popup.is_closed():
                with context.expect_page(timeout=12000) as pi:
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
                fila.on("dialog", lambda d: d.accept())
                status(f"[76] fila 156 via '{clicked}'")
                _safe_wait(fila, 800)
                return fila
        except Exception as err:
            status(f"[76] Ver fila: {err}")

    # 2) goto direto — estável
    try:
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
            let sit = '';
            for (const c of cells) {
              if (/^(conclu[ií]d[oa]|processando|na fila|em fila|erro|abortad)/i.test(c)) {
                sit = c; break;
              }
            }
            if (!sit) {
              sit = cells.find(c => /conclu|processando|na\\s*fila|erro|abort/i.test(c)) || cells[6] || '';
            }
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
            // última coluna costuma ser a mensagem (ex.: NÃO SELECIONOU CTRCS…)
            let mensagem = '';
            for (let i = cells.length - 1; i >= 0; i--) {
              const c = cells[i] || '';
              if (!c) continue;
              if (/^(conclu|process|fila|erro|abort|\\d)/i.test(c) && c.length < 20) continue;
              if (/^\\d{1,2}\\/\\d{1,2}/.test(c)) continue;
              if (c.length >= 8) { mensagem = c; break; }
            }
            const blobAll = (opcao + ' ' + cells.join(' ') + ' ' + links.map(l => l.blob).join(' ')).toLowerCase();
            const concluido = /conclu/i.test(sit);
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


def _baixar_via_fila_76(client, context, page, popup, dest_name: str, key: str, status) -> Path:
    """Espera job 076 concluído na 156 e clica DOW. Sem base → FilaSemDados."""
    # seqs já concluídas (com ou sem DOW) — ignoradas
    known_done: set[str] = set()
    fila = None
    try:
        fila = _abrir_fila_156_76(client, context, page, status, popup=popup)
        _safe_wait(fila, 500)
        for j in _ler_jobs_fila_76(fila):
            seq = str(j.get("seq") or "")
            if seq and j.get("is076") and j.get("concluido"):
                known_done.add(seq)
        status(f"[76] fila aberta · {len(known_done)} 076 já concluído(s)")
    except Exception as err:
        status(f"[76] snapshot fila: {err}")

    if fila is None or fila.is_closed():
        fila = _abrir_fila_156_76(client, context, page, status, popup=popup)

    deadline = time.time() + 240
    last_err = ""
    while time.time() < deadline:
        try:
            if fila is None or fila.is_closed():
                fila = _abrir_fila_156_76(client, context, page, status, popup=None)
            try:
                fila.bring_to_front()
            except Exception:
                pass
            _atualizar_fila_76(fila)
            _safe_wait(fila, 1200)
            jobs = _ler_jobs_fila_76(fila)

            # nosso job concluiu sem arquivo → pula filial (não trava)
            vazios = [
                j
                for j in jobs
                if j.get("is076")
                and str(j.get("seq") or "") not in known_done
                and _job_076_sem_dados(j)
            ]
            if vazios:
                vazios.sort(
                    key=lambda j: int(
                        "".join(ch for ch in str(j.get("seq") or "") if ch.isdigit()) or 0
                    ),
                    reverse=True,
                )
                job = vazios[0]
                msg = str(job.get("mensagem") or "sem DOW")
                try:
                    if fila is not None and not fila.is_closed():
                        fila.close()
                except Exception:
                    pass
                raise FilaSemDados(
                    f"sem base · seq={job.get('seq')} · {msg[:80]}"
                )

            cands = [
                j
                for j in jobs
                if j.get("concluido")
                and j.get("hasDow")
                and j.get("is076")
                and str(j.get("seq") or "") not in known_done
            ]

            def sk(j: dict) -> tuple:
                seq = str(j.get("seq") or "")
                try:
                    num = int("".join(ch for ch in seq if ch.isdigit()) or 0)
                except Exception:
                    num = 0
                return (-num,)

            cands.sort(key=sk)
            if not cands:
                proc = [
                    j
                    for j in jobs
                    if j.get("is076")
                    and str(j.get("seq") or "") not in known_done
                    and not j.get("concluido")
                ]
                if int(time.time()) % 8 < 2:
                    status(
                        f"[76/{key}] aguardando Concluído+DOW na 156 "
                        f"({len(proc)} processando)…"
                    )
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
        except FilaSemDados:
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
            time.sleep(2)

    raise RuntimeError(f"076/{key}: timeout na fila 156 ({last_err})")
