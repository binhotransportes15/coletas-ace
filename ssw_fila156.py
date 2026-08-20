"""Fila SSW 156 (ssw1440) — acompanhamento único para todos os relatórios em lote.

Estratégia (pedido operacional):
  1) Após gerar (►), abrir/atualizar a fila
  2) Localizar o job pelo login + opção (+ data/hora perto do enqueue)
  3) Travar a Sequência
  4) Ficar apertando «Atualizar» até aparecer «Baixar» (ou erro / sem dados)
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

StatusCallback = Callable[[str], None]

SSW_FILA_URL = "https://sistema.ssw.inf.br/bin/ssw1440"

_EMPTY_RE = re.compile(
    r"n[aã]o\s+selecionou|"
    r"nao\s+selecionou|"
    r"sem\s+ctrcs?|"
    r"nenhum\s+ctrcs?|"
    r"sem\s+dados|"
    r"n[aã]o\s+h[aá]\s+regist|"
    r"nada\s+a\s+(gerar|emitir)|"
    r"sem\s+movimento|"
    r"nenhum\s+registro|"
    r"sem\s+ocorr|"
    r"n[aã]o\s+h[aá]\s+pend|"
    r"sem\s+base|"
    r"sem\s+demonstrativ|"
    r"vazio",
    re.IGNORECASE,
)


class FilaSemDados(RuntimeError):
    """Job concluído na 156 sem arquivo para baixar."""


def _noop(_: str) -> None:
    return None


def _safe_accept_dialog(dialog) -> None:
    """Aceita alert sem explodir se outro handler já tratou."""
    try:
        dialog.accept()
    except Exception:
        pass


def safe_wait(page, ms: int) -> None:
    try:
        if page is None or (hasattr(page, "is_closed") and page.is_closed()):
            time.sleep(ms / 1000.0)
            return
        page.wait_for_timeout(ms)
    except Exception:
        time.sleep(ms / 1000.0)


def norm_user(s: str | None) -> str:
    return re.sub(r"\s+", "", str(s or "").strip().lower())


def abrir_fila(
    client,
    context,
    page,
    status: StatusCallback | None = None,
    *,
    popup=None,
    tag: str = "156",
):
    """Abre a tela 156 (ajax → Ver fila → goto → menu)."""
    st = status or _noop
    st(f"[{tag}] abrindo fila 156…")

    # 1) ajaxEnvia ssw1440
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
            fila.on("dialog", lambda d: _safe_accept_dialog(d))
        except Exception:
            pass
        st(f"[{tag}] fila via ajax")
        safe_wait(fila, 500)
        return _garantir_url_fila(client, page, fila, st, tag=tag)
    except Exception as err:
        st(f"[{tag}] ajax fila: {err}")

    # 2) Ver fila no popup do relatório
    if popup is not None:
        try:
            if not popup.is_closed():
                with context.expect_page(timeout=4000) as pi:
                    ok = popup.evaluate(
                        """() => {
                          const a = document.getElementById('42');
                          if (a) { a.click(); return '42'; }
                          const els = Array.from(document.querySelectorAll('a, button, span'));
                          for (const el of els) {
                            const t = ((el.innerText || el.textContent || '') + '').trim();
                            if (/^Ver fila$/i.test(t) || /ver\\s*fila/i.test(t)) {
                              el.click(); return t.slice(0, 20);
                            }
                          }
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
                    fila.on("dialog", lambda d: _safe_accept_dialog(d))
                except Exception:
                    pass
                st(f"[{tag}] fila via Ver fila")
                safe_wait(fila, 500)
                return _garantir_url_fila(client, page, fila, st, tag=tag)
        except Exception as err:
            st(f"[{tag}] Ver fila: {err}")

    # 3) goto direto
    try:
        fila = context.new_page()
        try:
            fila.on("dialog", lambda d: _safe_accept_dialog(d))
        except Exception:
            pass
        fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=30000)
        st(f"[{tag}] fila via goto")
        safe_wait(fila, 500)
        return fila
    except Exception as err:
        st(f"[{tag}] goto fila: {err}")

    # 4) menu 156
    try:
        fila = client._open_menu_option(
            page, "156", markers=("fila", "dow", "156", "1440", "processamento", "lotes")
        )
        st(f"[{tag}] fila via menu")
        return _garantir_url_fila(client, page, fila, st, tag=tag)
    except Exception as err:
        raise RuntimeError(f"{tag}: não abriu fila 156 ({err})") from err


def _garantir_url_fila(client, page, fila, status, *, tag: str = "156"):
    try:
        url = (fila.url or "").lower()
        if "ssw1440" in url and "blank" not in url:
            return fila
        try:
            fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=30000)
            safe_wait(fila, 600)
        except Exception:
            try:
                client._recuperar_blank(page, fila, "1440", ("fila", "dow", "156", "1440"))
            except Exception:
                pass
    except Exception as err:
        status(f"[{tag}] garantir url: {err}")
    return fila


def atualizar_fila(fila) -> str:
    """Clica Atualizar / ajaxEnvia('',0) — o botão que o operador aperta na 156."""
    try:
        return str(
            fila.evaluate(
                """() => {
                  if (typeof ajaxEnvia === 'function') {
                    try { ajaxEnvia('', 0); return 'ajax0'; } catch (e) {}
                    try { ajaxEnvia('ATU', 0); return 'ATU'; } catch (e) {}
                  }
                  const byId = document.getElementById('2');
                  if (byId) { byId.click(); return 'id2'; }
                  const els = Array.from(document.querySelectorAll(
                    'a, button, input[type=button], input[type=submit], span, font'
                  ));
                  for (const el of els) {
                    const t = ((el.innerText || el.textContent || el.value || '') + '').trim();
                    if (/^atualizar$/i.test(t)) { el.click(); return 'txt'; }
                  }
                  return '';
                }"""
            )
            or ""
        )
    except Exception:
        return ""


def ler_jobs(fila) -> list[dict[str, Any]]:
    """Lê a grade 156 com colunas oficiais + Baixar.

    Colunas típicas:
      Sequência | Opção | Data/Hora Solicitação | Usuário | Unidade | Fone | Situação | Duração | (Baixar)
    """
    return fila.evaluate(
        """() => {
          const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
          const jobs = [];
          const now = new Date();
          for (const tr of Array.from(document.querySelectorAll('tr'))) {
            const tds = Array.from(tr.querySelectorAll('td'));
            const cells = tds.map(td => norm(td.innerText));
            if (cells.length < 4) continue;
            const seq = (cells[0] || '').replace(/\\D/g, '');
            if (!seq || seq.length < 4) continue;

            const opcao = cells[1] || '';
            const dataHora = cells[2] || '';
            const usuario = cells[3] || '';
            const unidade = cells[4] || '';

            let sit = '';
            for (const c of cells) {
              if (/^(conclu[ií]d[oa]|processando|na fila|em fila|erro|abortad)/i.test(c)) {
                sit = c; break;
              }
            }
            if (!sit) {
              sit = cells.find(c => /conclu|processando|na\\s*fila|erro|abort/i.test(c))
                || cells[6] || '';
            }

            let quando = dataHora;
            let quando_epoch = 0;
            const m = (dataHora || '').match(
              /(\\d{1,2})\\/(\\d{1,2})\\/(\\d{2,4})(?:\\s+(\\d{1,2}):(\\d{2})(?::(\\d{2}))?)?/
            );
            if (m) {
              let yy = parseInt(m[3], 10);
              if (yy < 100) yy += 2000;
              const hh = parseInt(m[4] || '0', 10);
              const mm = parseInt(m[5] || '0', 10);
              const ss = parseInt(m[6] || '0', 10);
              const dt = new Date(yy, parseInt(m[2], 10) - 1, parseInt(m[1], 10), hh, mm, ss);
              if (!isNaN(dt.getTime())) {
                quando_epoch = Math.floor(dt.getTime() / 1000);
                if (quando_epoch > Math.floor(now.getTime() / 1000) + 86400) quando_epoch = 0;
              }
            }

            let duracao = '';
            for (const c of cells) {
              if (/^\\d{1,2}:\\d{2}(:\\d{2})?$/.test(c)) { duracao = c; break; }
            }

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

            const rowBlob = cells.join(' ');
            const semDados = /n[aã]o\\s+selecionou|nao\\s+selecionou|sem\\s+ctrcs?|nenhum\\s+ctrcs?|sem\\s+dados|sem\\s+demonstrativ|nenhum\\s+registro|sem\\s+base/i.test(rowBlob);

            const links = Array.from(tr.querySelectorAll(
              'a[onclick], a[href], img[onclick], input[onclick], button[onclick]'
            )).map(a => {
              const text = norm(a.textContent || a.alt || a.title || a.value || '');
              const onclick = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              return { text, onclick, href, blob: (onclick + ' ' + text + ' ' + href).toLowerCase() };
            });
            // Enquanto processa, o SSW mostra «Interromper» no lugar do Baixar — esperar
            const hasInterromper = links.some(x =>
              /interrom|cancelar\\s*gera|parar\\s*gera|abortar/i.test(x.text)
              || /interrom/i.test(x.blob)
            ) || cells.some(c => /^(interrom|cancelar\\s*gera)/i.test(c || ''));
            // Nunca trate «NÃO SELECIONOU…» nem «Interromper» como Baixar
            const dows = links.filter(x => {
              if (/interrom|cancelar\\s*gera|parar\\s*gera|abortar/i.test(x.text)) return false;
              if (/selecionou|sem\\s+ctrc|demonstrativ/i.test(x.text)) return false;
              if (/imprimir|correio|atualizar|voltar|fechar|sair/i.test(x.text)
                  && !/\\b(dow|baixar)\\b/i.test(x.text)) return false;
              if (/^(dow|baixar)$/i.test(x.text) || /\\b(dow|baixar)\\b/i.test(x.text)) return true;
              return /\\bdow\\b|download\\(|\\.xlsx|\\.xls|\\.csv|\\.sswweb|baixar|arquivo/.test(x.blob)
                && !/interrom/i.test(x.blob);
            });
            if (!dows.length && !semDados) {
              for (const td of tds) {
                const t = norm(td.innerText || '');
                if (/interrom|selecionou|sem\\s+ctrc|demonstrativ/i.test(t)) continue;
                if (/^(dow|baixar)$/i.test(t) || (t.length <= 10 && /\\b(dow|baixar)\\b/i.test(t))) {
                  dows.push({ text: t, onclick: '', href: '', blob: 'dow baixar' });
                  break;
                }
              }
            }

            const sitLow = (sit || '').toLowerCase();
            const concluido = /conclu/.test(sitLow) && !/n[aã]o\\s*conclu|inconclu/.test(sitLow);
            const erro = /erro|abort/.test(sitLow) && !/interrom/i.test(sitLow);
            const hasDow = !semDados && dows.length > 0;
            // «Interromper» no lugar do Baixar = ainda gerando — continua Atualizar
            const aindaGerando = Boolean(hasInterromper && !hasDow);
            const processando = !concluido && !erro && !semDados && (
              aindaGerando
              || /processando|na\\s*fila|em\\s*fila|aguard|gerando/.test(sitLow)
              || !hasDow
            );

            jobs.push({
              seq,
              opcao,
              data_hora: dataHora,
              usuario,
              unidade,
              situacao: sit,
              duracao,
              mensagem: mensagem || (semDados ? rowBlob : ''),
              quando,
              quando_epoch,
              concluido: concluido || semDados,
              processando,
              erro,
              semDados,
              hasInterromper: aindaGerando,
              hasDow,
              dows: semDados ? [] : dows,
              cells,
            });
          }
          return jobs;
        }"""
    )


def job_matches_option(job: dict[str, Any], option_res: list[re.Pattern[str]] | tuple[re.Pattern[str], ...]) -> bool:
    blob = " ".join(
        [
            str(job.get("opcao") or ""),
            str(job.get("mensagem") or ""),
            " ".join(str(x) for x in (job.get("cells") or [])),
        ]
    )
    return any(rx.search(blob) for rx in option_res)


def compile_option_res(patterns: list[str] | tuple[str, ...] | None) -> list[re.Pattern[str]]:
    out: list[re.Pattern[str]] = []
    for p in patterns or ():
        s = str(p or "").strip()
        if not s:
            continue
        try:
            out.append(re.compile(s, re.IGNORECASE))
        except re.error:
            out.append(re.compile(re.escape(s), re.IGNORECASE))
    return out


def job_is_ours(
    job: dict[str, Any],
    *,
    login_user: str,
    option_res: list[re.Pattern[str]],
    tracked_seq: str | None = None,
    enqueue_t0: float | None = None,
    time_slack_s: float = 180.0,
) -> bool:
    """Match: sequência travada OU (usuário do login + opção [+ horário])."""
    seq = str(job.get("seq") or "")
    if not seq:
        return False
    if tracked_seq:
        return seq == str(tracked_seq)

    want_user = norm_user(login_user)
    got_user = norm_user(job.get("usuario"))
    if want_user and got_user and got_user != want_user:
        return False
    if option_res and not job_matches_option(job, option_res):
        return False

    # Horário: se a linha tem epoch, exige >= enqueue - slack
    if enqueue_t0:
        ep = int(job.get("quando_epoch") or 0)
        if ep > 0 and ep < int(enqueue_t0 - time_slack_s):
            return False
    return True


def pick_tracked_seq(
    jobs: list[dict[str, Any]],
    *,
    login_user: str,
    option_res: list[re.Pattern[str]],
    enqueue_t0: float | None = None,
) -> str | None:
    """Escolhe a sequência do nosso job (prioriza processando, depois mais nova).

    Com enqueue_t0: NÃO cai em job antigo da mesma opção (isso zerava/sujava a Emissão).
    """
    cand = [
        j
        for j in jobs
        if job_is_ours(
            j,
            login_user=login_user,
            option_res=option_res,
            tracked_seq=None,
            enqueue_t0=enqueue_t0,
        )
    ]
    if not cand:
        return None

    def _num(j: dict) -> int:
        return int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0)

    processing = [j for j in cand if not j.get("concluido") and not j.get("erro")]
    pool = processing or cand
    pool.sort(key=_num)
    return str(pool[-1].get("seq") or "") or None


def job_blob(job: dict[str, Any]) -> str:
    parts = [
        str(job.get("opcao") or ""),
        str(job.get("mensagem") or ""),
        str(job.get("situacao") or ""),
        " ".join(str(x) for x in (job.get("cells") or [])),
    ]
    for d in job.get("dows") or []:
        if isinstance(d, dict):
            parts.append(str(d.get("text") or ""))
    return " ".join(parts)


def job_sem_dados(job: dict[str, Any]) -> bool:
    """True quando a 156 concluiu sem arquivo (ex.: NÃO SELECIONOU CTRCS…).

    Essa frase aparece no lugar do «Baixar» — desconsiderar, não baixar, não contar.
    """
    blob = job_blob(job)
    if _EMPTY_RE.search(blob):
        return True
    if job.get("semDados"):
        return True
    # Concluído sem Baixar e com mensagem típica
    if job.get("concluido") and not job.get("hasDow"):
        msg = str(job.get("mensagem") or "").strip()
        if msg and _EMPTY_RE.search(msg):
            return True
    return False


def job_pronto_baixar(job: dict[str, Any]) -> bool:
    """True só quando o link real «Baixar»/DOW está na linha.

    Se ainda aparece «Interromper», continua esperando — não clicar.
    """
    if not job or job_sem_dados(job):
        return False
    if job.get("hasInterromper") and not job.get("hasDow"):
        return False
    if job.get("processando") and not job.get("hasDow"):
        return False
    return bool(job.get("hasDow"))


def aguardar_baixar(
    client,
    context,
    page,
    popup,
    status: StatusCallback,
    *,
    login_user: str,
    option_patterns: list[str] | tuple[str, ...],
    enqueue_t0: float,
    tag: str = "156",
    timeout_s: float = 420.0,
    stuck_sem_dow_s: float = 120.0,
) -> tuple[Any, dict[str, Any]]:
    """Fica em Atualizar até o job do login ter Baixar.

    Retorna (fila_page, job_dict). Levanta FilaSemDados / RuntimeError.
    """
    st = status or _noop
    option_res = compile_option_res(option_patterns)
    user = str(login_user or "").strip()
    if not user:
        raise RuntimeError(f"{tag}: login_user vazio — impossível achar o job na 156")

    fila = abrir_fila(client, context, page, st, popup=popup, tag=tag)
    safe_wait(fila, 600)

    tracked: str | None = None
    try:
        jobs0 = ler_jobs(fila)
        tracked = pick_tracked_seq(
            jobs0, login_user=user, option_res=option_res, enqueue_t0=enqueue_t0
        )
        st(
            f"[{tag}] 156 · user={user} · seq={tracked or 'procurando'} · "
            f"opções={len(option_res)}"
        )
        if tracked:
            hit = next((j for j in jobs0 if str(j.get("seq")) == tracked), None)
            if hit:
                st(
                    f"[{tag}] trilha · seq={tracked} · "
                    f"{(hit.get('opcao') or '')[:48]} · "
                    f"{hit.get('data_hora') or '?'} · {hit.get('usuario') or '?'}"
                )
    except Exception as err:
        st(f"[{tag}] bootstrap 156: {err}")

    deadline = time.time() + float(timeout_s)
    last_log = 0.0
    last_err = ""
    concluido_sem_dow_since: float | None = None

    while time.time() < deadline:
        try:
            if fila is None or fila.is_closed():
                fila = abrir_fila(client, context, page, st, popup=None, tag=tag)
            try:
                fila.bring_to_front()
            except Exception:
                pass

            how = atualizar_fila(fila)
            safe_wait(fila, 1000)
            jobs = ler_jobs(fila)

            if not tracked:
                tracked = pick_tracked_seq(
                    jobs, login_user=user, option_res=option_res, enqueue_t0=enqueue_t0
                )
                if tracked:
                    st(f"[{tag}] sequência travada: {tracked}")

            nossos = [
                j
                for j in jobs
                if job_is_ours(
                    j,
                    login_user=user,
                    option_res=option_res,
                    tracked_seq=tracked,
                    enqueue_t0=enqueue_t0 if not tracked else None,
                )
            ]

            now = time.time()
            if now - last_log >= 4:
                last_log = now
                if not nossos:
                    st(
                        f"[{tag}] Atualizar({how or '?'}) · "
                        f"ainda sem linha de {user}…"
                    )
                else:
                    j0 = nossos[0]
                    fase = (
                        "Interromper→aguardando Baixar"
                        if j0.get("hasInterromper") and not j0.get("hasDow")
                        else ("Baixar" if j0.get("hasDow") else "gerando")
                    )
                    st(
                        f"[{tag}] Atualizar · seq={j0.get('seq')} · "
                        f"{j0.get('situacao') or '?'} · {fase} · "
                        f"{(j0.get('opcao') or '')[:40]}"
                    )

            if not nossos:
                safe_wait(fila, 1500)
                continue

            job = sorted(
                nossos,
                key=lambda j: int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0),
            )[-1]
            tracked = str(job.get("seq") or tracked or "")

            if job.get("erro") and not job_sem_dados(job):
                raise FilaSemDados(
                    f"erro na fila · seq={tracked} · {job.get('situacao') or ''} · "
                    f"{str(job.get('mensagem') or '')[:80]}"
                )

            if job_sem_dados(job):
                msg = str(job.get("mensagem") or job_blob(job) or "sem dados").strip()
                # Frase típica no lugar do Baixar — desconsiderar
                raise FilaSemDados(
                    f"sem base · seq={tracked} · {msg[:100]}"
                )

            # Ainda «Interromper» → não clica; espera o Baixar aparecer
            if job.get("hasInterromper") and not job.get("hasDow"):
                concluido_sem_dow_since = None
                safe_wait(fila, 900)
                continue

            if job_pronto_baixar(job):
                st(
                    f"[{tag}] Baixar pronto · seq={tracked} · "
                    f"{job.get('usuario')} · {job.get('data_hora')} · "
                    f"{(job.get('opcao') or '')[:42]}"
                )
                return fila, job

            if job.get("concluido") and not job.get("hasDow"):
                if concluido_sem_dow_since is None:
                    concluido_sem_dow_since = now
                elif (now - concluido_sem_dow_since) >= stuck_sem_dow_s:
                    raise FilaSemDados(
                        f"sem Baixar após {int(stuck_sem_dow_s)}s · seq={tracked} · "
                        f"{str(job.get('mensagem') or job.get('situacao') or '')[:60]}"
                    )
            else:
                concluido_sem_dow_since = None

            # Poll mais curto quando ainda processando — baixa assim que o link surgir
            safe_wait(fila, 900 if not job.get("concluido") else 1400)
        except FilaSemDados:
            raise
        except Exception as err:
            last_err = str(err)
            st(f"[{tag}] loop 156: {err}")
            time.sleep(1.5)

    raise RuntimeError(
        f"{tag}: timeout na fila 156 (user={user} seq={tracked or '?'}) ({last_err})"
    )


def find_baixar_meta(fila, seq: str) -> dict[str, Any]:
    """Localiza o controle Baixar/DOW da sequência (para clique).

    Se a linha ainda mostra «Interromper», retorna ainda_processando —
    nunca tratar Interromper como Baixar.
    """
    return fila.evaluate(
        """({ seq }) => {
          const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
          const want = String(seq || '').replace(/\\D/g, '');
          if (!want) return { ok: false, why: 'seq_vazia' };
          for (const tr of document.querySelectorAll('tr')) {
            const tds = Array.from(tr.querySelectorAll('td'));
            const cells = tds.map(td => norm(td.innerText));
            const s = (cells[0] || '').replace(/\\D/g, '');
            if (s !== want) continue;
            const sit = cells.find(c => /conclu|process|fila|erro|abort/i.test(c)) || '';
            const links = Array.from(tr.querySelectorAll(
              'a[onclick], a[href], img[onclick], input[onclick], button[onclick]'
            ));
            const hasInterromper = links.some(a => {
              const text = norm(a.textContent || a.alt || a.title || a.value || '');
              const blob = (String(a.getAttribute('onclick') || '') + ' ' + text).toLowerCase();
              return /interrom|cancelar\\s*gera|parar\\s*gera/i.test(text) || /interrom/i.test(blob);
            }) || cells.some(c => /^(interrom|cancelar\\s*gera)/i.test(c || ''));

            const scored = [];
            for (const a of links) {
              const text = norm(a.textContent || a.alt || a.title || a.value || '');
              const onclick = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
              if (/interrom|cancelar\\s*gera|parar\\s*gera|abortar/i.test(text)) continue;
              if (/imprimir|correio|atualizar|voltar|fechar|sair/i.test(text)
                  && !/\\b(dow|baixar)\\b/i.test(text)) continue;
              let score = 0;
              if (/^(dow|baixar)$/i.test(text)) score += 50;
              if (/\\b(dow|baixar)\\b/i.test(text)) score += 20;
              if (/\\bdow\\b|baixar|download\\(|\\.xlsx|\\.xls|\\.csv|\\.sswweb|arquivo/.test(blob)
                  && !/interrom/i.test(blob)) score += 15;
              if (onclick && /\\bdow\\b|baixar/i.test(onclick)) score += 10;
              else if (onclick && !/interrom/i.test(onclick)) score += 4;
              if (href && href !== '#' && !/^javascript:/i.test(href)) score += 8;
              if (score > 0) scored.push({
                text, onclick, href, score, tag: (a.tagName || '').toLowerCase()
              });
            }
            scored.sort((x, y) => y.score - x.score);
            const hasDow = scored.length > 0;

            if ((sit && /processando|na\\s*fila|em\\s*fila/i.test(sit) && !/conclu/i.test(sit))
                || (hasInterromper && !hasDow)) {
              return { ok: false, why: 'ainda_processando', interromper: !!hasInterromper };
            }
            if (hasDow) {
              return { ok: true, why: 'link', best: scored[0], n: scored.length };
            }
            for (const td of tds) {
              const t = norm(td.innerText || '');
              if (/interrom/i.test(t)) continue;
              if (!(/^(dow|baixar)$/i.test(t) || (t.length <= 10 && /\\b(dow|baixar)\\b/i.test(t)))) continue;
              const child = td.querySelector(
                'a[onclick], a[href], img[onclick], input[onclick], button[onclick]'
              );
              if (child) {
                const oc = String(child.getAttribute('onclick') || '');
                if (/interrom/i.test(oc)) continue;
                return {
                  ok: true,
                  why: 'td-child',
                  best: {
                    text: t,
                    onclick: oc,
                    href: String(child.getAttribute('href') || ''),
                    score: 40,
                    tag: (child.tagName || '').toLowerCase(),
                  },
                  n: 1,
                };
              }
              return {
                ok: true,
                why: 'td-text',
                best: { text: t, onclick: String(td.getAttribute('onclick') || ''), href: '', score: 30, tag: 'td' },
                n: 1,
              };
            }
            if (hasInterromper) {
              return { ok: false, why: 'ainda_processando', interromper: true };
            }
            return { ok: false, why: 'sem_dow_real', cells: cells.slice(0, 8) };
          }
          return { ok: false, why: 'seq_sumiu' };
        }""",
        {"seq": str(seq or "")},
    )


def esperar_meta_baixar(
    fila,
    seq: str,
    status: StatusCallback | None = None,
    *,
    tag: str = "156",
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    """Atualiza a fila até «Baixar» real (não Interromper) da seq."""
    st = status or _noop
    deadline = time.time() + float(timeout_s)
    last_log = 0.0
    last: dict[str, Any] = {"ok": False, "why": "inicio"}
    while time.time() < deadline:
        try:
            atualizar_fila(fila)
            safe_wait(fila, 800)
            last = find_baixar_meta(fila, seq) or {}
            if last.get("ok"):
                return last
            why = str(last.get("why") or "")
            now = time.time()
            if now - last_log >= 4:
                last_log = now
                extra = " · Interromper" if last.get("interromper") else ""
                st(f"[{tag}] aguardando Baixar · seq={seq} · {why}{extra}")
            if why in {"ainda_processando", "sem_dow_real"}:
                safe_wait(fila, 900)
                continue
            if why == "seq_sumiu":
                safe_wait(fila, 1200)
                continue
            safe_wait(fila, 1000)
        except Exception as err:
            st(f"[{tag}] esperar Baixar: {err}")
            time.sleep(1.2)
    return last if isinstance(last, dict) else {"ok": False, "why": "timeout"}
