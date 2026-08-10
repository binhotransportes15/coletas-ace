"""Download SSW 200 (ssw0644) — Relação de Manifestos Operacionais.

Formulário:
  Período emissão · Unidade origem (= destino do 073) · Tipo arquivo=E (excel)
  ► → download DIRETO do CSV (não vai pra fila 156).
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

SSW_200_MARKERS = (
    "200",
    "0644",
    "manifesto",
    "relacao de manifesto",
    "periodo de emiss",
    "tipo de arquivo",
    "unidade origem",
)

SSW_FILA_URL = "https://sistema.ssw.inf.br/bin/ssw1440"


class FilaSemDados(RuntimeError):
    """Mantido por compat — 200 atual não usa fila; sem arquivo = download falhou."""


_EMPTY_FILA_RE = re.compile(
    r"n[aã]o\s+selecionou|sem\s+ctrc|nenhum|sem\s+dados|n[aã]o\s+h[aá]\s+regist|"
    r"nada\s+a\s+(gerar|emitir)|sem\s+movimento|sem\s+manifesto",
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


def _safe_wait(page, ms: int) -> None:
    try:
        if page is None or page.is_closed():
            return
        page.wait_for_timeout(ms)
    except Exception:
        pass


def download_reports_200(
    *,
    period: tuple[str, str] | None = None,
    unidade_origem: str = "",
    tipo_arquivo: str = "E",
    tag: str = "",
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    client: AceSswClient | None = None,
    context=None,
    page=None,
) -> dict[str, Any]:
    """Gera 200 com Tipo=E → download direto CSV. origem = destino do 073."""
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)

    unid = (unidade_origem or "").strip().upper()
    tipo = (tipo_arquivo or "E").strip().upper()[:1] or "E"
    file_tag = (tag or unid or "ALL").strip().upper() or "ALL"

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
    status(
        f"SSW 200 | tipo={tipo} | origem={unid or '(tudo)'} | tag={file_tag} | {ini}-{fim}"
        + (" · sessão reusada" if reuse else "")
    )

    def _run(sess_client, sess_context, sess_page) -> None:
        nonlocal paths
        popup = None
        empty_msg = ""
        try:
            status(f"[200/{file_tag}] abrindo opção 200…")
            popup = sess_client._open_menu_option(sess_page, "200", markers=SSW_200_MARKERS)
            _preencher_200(
                popup, ini=ini, fim=fim, unidade=unid, tipo=tipo, on_status=status
            )
            dest = f"contratacao_200_{file_tag}_{ts}.csv"
            path = _gerar_download_200(
                sess_client, sess_context, sess_page, popup, dest, status
            )
            paths.append(str(path))
            status(f"[200/{file_tag}] OK {path.name}")
        except FilaSemDados as empty_err:
            empty_msg = str(empty_err)
            status(f"[200/{file_tag}] sem base — pula ({empty_err})")
        finally:
            try:
                if popup is not None and not popup.is_closed():
                    popup.close()
            except Exception:
                pass
        return empty_msg

    empty_note = ""
    if reuse:
        empty_note = _run(own_client, context, page) or ""
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
                empty_note = _run(own_client, ctx, pg) or ""
            finally:
                browser.close()

    if not paths:
        if empty_note:
            return {
                "ok": True,
                "files": [],
                "empty": True,
                "error": empty_note,
                "period": (ini_ddmm, fim_ddmm),
                "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
                "unidade_origem": unid,
                "tipo_arquivo": tipo,
                "tag": file_tag,
            }
        raise RuntimeError("200: nenhum arquivo baixado")

    return {
        "ok": True,
        "files": paths,
        "period": (ini_ddmm, fim_ddmm),
        "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
        "unidade_origem": unid,
        "tipo_arquivo": tipo,
        "tag": file_tag,
    }


def _preencher_200(popup, *, ini: str, fim: str, unidade: str, tipo: str, on_status) -> None:
    """
    ssw0644 (tela 200):
      Período emissão · Unidade origem · Unidade destino (opc)
      Placa (opc) · CPF (opc) · Tipo arquivo (T-texto / E-excel)

    Download é DIRETO (não vai pra fila 156).
    Unidade origem = destino do 073 (sigla da filial).
    """
    status = on_status
    origem = (unidade or "").strip().upper()
    arq = (tipo or "E").strip().upper()[:1] or "E"
    popup.wait_for_timeout(400)

    filled = {
        "okIni": False,
        "okFim": False,
        "okOrigem": False,
        "okTipo": False,
        "vals": [],
    }
    try:
        # Preferência: fill por rótulo (mais fiel ao print da tela)
        filled_js = popup.evaluate(
            """({ ini, fim, origem, tipo }) => {
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
                el.focus();
                el.value = val;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              };

              // Datas: maxlength/size 6 (ddmmyy)
              const dates = inputs.filter((el) => Number(el.maxLength) === 6 || Number(el.size) === 6);
              let okIni = false, okFim = false;
              if (dates.length >= 2) {
                okIni = set(dates[0], ini);
                okFim = set(dates[1], fim);
              }

              // Unidade origem (não confundir com destino opc)
              let elOrig = null;
              for (const el of inputs) {
                const lab = norm(near(el));
                if (lab.includes('unidade origem') || (lab.includes('origem') && !lab.includes('destino'))) {
                  elOrig = el; break;
                }
              }
              if (!elOrig && dates.length >= 2) {
                // ordem do form: ini, fim, origem, destino, placa, cpf, tipo
                const idx = inputs.indexOf(dates[1]);
                if (idx >= 0 && idx + 1 < inputs.length) elOrig = inputs[idx + 1];
              }
              const okOrigem = origem ? set(elOrig, origem) : true;

              // limpa destino opc / placa / cpf se vierem sujos
              for (const el of inputs) {
                const lab = norm(near(el));
                if (lab.includes('unidade destino') || lab.includes('placa') || lab.includes('cpf')) {
                  if ((el.value || '').trim()) set(el, '');
                }
              }

              // Tipo de arquivo: T/E de 1 char
              let elTipo = byHint(['tipo de arquivo', 'tipo arquivo']);
              if (!elTipo) {
                for (let i = inputs.length - 1; i >= 0; i--) {
                  const el = inputs[i];
                  const v = (el.value || '').toUpperCase().trim();
                  const ml = Number(el.maxLength || 0);
                  const sz = Number(el.size || 0);
                  if ((ml === 1 || sz === 1 || ml === 0) && (v === 'T' || v === 'E' || v === '')) {
                    elTipo = el; break;
                  }
                }
              }
              const okTipo = set(elTipo, tipo);

              return {
                okIni, okFim, okOrigem, okTipo,
                n: inputs.length,
                vals: inputs.slice(0, 8).map((el) => el.value || ''),
              };
            }""",
            {"ini": ini, "fim": fim, "origem": origem, "tipo": arq},
        )
        filled.update(filled_js or {})
    except Exception as err:  # noqa: BLE001
        status(f"[200] fill evaluate: {err}")

    # Playwright fallback se evaluate falhou em algo crítico
    try:
        inputs = popup.locator('input[type=text], input:not([type])')
        n = inputs.count()
        if not filled.get("okIni") or not filled.get("okFim"):
            date_idxs: list[int] = []
            for i in range(n):
                try:
                    ml = inputs.nth(i).evaluate(
                        "e => ({ ml: Number(e.maxLength||0), sz: Number(e.size||0) })"
                    )
                    if ml.get("ml") == 6 or ml.get("sz") == 6:
                        date_idxs.append(i)
                except Exception:
                    continue
            if len(date_idxs) >= 2:
                inputs.nth(date_idxs[0]).fill(ini)
                inputs.nth(date_idxs[1]).fill(fim)
                filled["okIni"] = True
                filled["okFim"] = True
                # origem = 1º campo após as datas
                if origem and not filled.get("okOrigem"):
                    oi = date_idxs[1] + 1
                    if oi < n:
                        inputs.nth(oi).fill(origem)
                        filled["okOrigem"] = True
        if not filled.get("okTipo"):
            for i in range(n - 1, -1, -1):
                try:
                    meta = inputs.nth(i).evaluate(
                        """e => ({
                          ml: Number(e.maxLength||0),
                          sz: Number(e.size||0),
                          v: (e.value||'').toUpperCase().trim(),
                        })"""
                    )
                    if meta.get("v") in {"T", "E"} or meta.get("ml") == 1 or meta.get("sz") == 1:
                        inputs.nth(i).fill(arq)
                        filled["okTipo"] = True
                        status(f"[200] Tipo arquivo input[{i}]={arq}")
                        break
                except Exception:
                    continue
        if origem and not filled.get("okOrigem") and n >= 3:
            # índice 2 = Unidade origem (print da tela)
            inputs.nth(2).fill(origem)
            filled["okOrigem"] = True
        filled["vals"] = [(inputs.nth(i).input_value() or "") for i in range(min(n, 8))]
    except Exception as err:  # noqa: BLE001
        status(f"[200] fill playwright: {err}")

    status(f"[200] form origem={origem or '—'} · {filled}")
    if not filled.get("okTipo"):
        raise RuntimeError(f"200: não achei Tipo de arquivo (E). form={filled}")
    if origem and not filled.get("okOrigem"):
        raise RuntimeError(f"200: não preencheu Unidade origem={origem}. form={filled}")
    popup.wait_for_timeout(200)


def _clicar_gerar_200(popup) -> str:
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
            if (t === '►' || t === '▶') { a.click(); return 'play'; }
          }
          const as = Array.from(document.querySelectorAll('a'));
          if (as.length) { as[0].click(); return 'a0'; }
          return '';
        }"""
    )


def _try_dismiss_nenhum_registro(popup) -> str:
    """
    Aviso SSW: 'Nenhum registro encontrado para os parâmetros informados.'
    Fecha com '7. OK' / OK / X e devolve texto se era esse aviso.
    """
    try:
        if popup is None or popup.is_closed():
            return ""
    except Exception:
        return ""
    try:
        return str(
            popup.evaluate(
                """() => {
                  const body = ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ');
                  if (!/nenhum registro encontrado/i.test(body)) return '';
                  const clickables = Array.from(
                    document.querySelectorAll('a, button, span, input[type=button], input[type=submit]')
                  );
                  for (const a of clickables) {
                    const t = ((a.innerText || a.textContent || a.value || a.alt || a.title || '') + '')
                      .replace(/\\s+/g, ' ').trim();
                    if (/^7\\.?\\s*OK$/i.test(t) || /^OK$/i.test(t)) {
                      a.click();
                      return t || 'OK';
                    }
                  }
                  // X do cabeçalho "Aviso"
                  for (const a of clickables) {
                    const t = ((a.innerText || a.textContent || a.title || '') + '').trim();
                    if (t === 'X' || t === '×' || /fechar|close/i.test(t)) {
                      a.click();
                      return 'X';
                    }
                  }
                  return 'aviso';
                }"""
            )
            or ""
        )
    except Exception:
        return ""


def _gerar_download_200(client, context, page, popup, dest_name: str, status) -> Path:
    """
    Tipo=E + ► → download DIRETO do CSV.
    Se aparecer 'Nenhum registro encontrado…' → fecha OK e segue (FilaSemDados).
    """
    _ = page
    try:
        popup.on("dialog", lambda d: d.accept())
    except Exception:
        pass

    downloads: list[Any] = []

    def _on_dl(d) -> None:
        downloads.append(d)

    try:
        context.on("download", _on_dl)
    except Exception:
        pass

    try:
        clicked = _clicar_gerar_200(popup)
        if not clicked:
            raise RuntimeError("200: botão ► não encontrado")
        status(f"[200] clique={clicked} · download direto…")

        deadline = time.time() + 60
        while time.time() < deadline:
            if downloads:
                path = client._save_download(downloads[0], dest_name)
                status(f"[200] download OK · {path.name}")
                return path

            dismissed = _try_dismiss_nenhum_registro(popup)
            if dismissed:
                status(f"[200] sem registro — OK ({dismissed})")
                raise FilaSemDados("sem base · nenhum registro encontrado")

            try:
                popup.wait_for_timeout(350)
            except Exception:
                time.sleep(0.35)

        # última checagem do aviso antes de falhar
        dismissed = _try_dismiss_nenhum_registro(popup)
        if dismissed:
            status(f"[200] sem registro — OK ({dismissed})")
            raise FilaSemDados("sem base · nenhum registro encontrado")
        raise RuntimeError("200: timeout sem download (e sem aviso de vazio)")
    finally:
        try:
            context.remove_listener("download", _on_dl)
        except Exception:
            pass


def _snapshot_fila_200_before(client, context, page, status) -> tuple[set[str], int]:
    known_done: set[str] = set()
    min_seq = 0
    fila = None
    try:
        fila = _abrir_fila_156_200(client, context, page, status)
        _safe_wait(fila, 400)
        for j in _ler_jobs_fila_200(fila):
            if not j.get("is200"):
                continue
            seq = str(j.get("seq") or "")
            if not seq:
                continue
            try:
                num = int("".join(ch for ch in seq if ch.isdigit()) or 0)
            except Exception:
                num = 0
            if num > min_seq:
                min_seq = num
            if j.get("concluido"):
                known_done.add(seq)
        status(f"[200] pré-fila · {len(known_done)} concluído(s) · max_seq={min_seq}")
    except Exception as err:
        status(f"[200] pré-fila: {err}")
    finally:
        try:
            if fila is not None and not fila.is_closed():
                fila.close()
        except Exception:
            pass
    return known_done, min_seq


def _abrir_fila_156_200(client, context, page, status):
    status("[200] abrindo fila 156…")
    try:
        try:
            page.bring_to_front()
        except Exception:
            pass
        fila = context.new_page()
        fila.on("dialog", lambda d: d.accept())
        fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=45000)
        status("[200] fila 156 via goto ssw1440")
        _safe_wait(fila, 800)
        return fila
    except Exception as err:
        status(f"[200] goto fila: {err}")
    try:
        fila = client._open_menu_option(
            page, "156", markers=("fila", "dow", "156", "1440", "processamento", "lotes")
        )
        status("[200] fila 156 via menu")
        return fila
    except Exception as err:
        raise RuntimeError(f"200: não abriu fila 156 ({err})") from err


def _ler_jobs_fila_200(fila) -> list[dict]:
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
            let mensagem = '';
            for (let i = cells.length - 1; i >= 0; i--) {
              const c = cells[i] || '';
              if (!c) continue;
              if (/^(conclu|process|fila|erro|abort|\\d)/i.test(c) && c.length < 20) continue;
              if (/^\\d{1,2}\\/\\d{1,2}/.test(c)) continue;
              if (c.length >= 8) { mensagem = c; break; }
            }
            const blobAll = (opcao + ' ' + cells.join(' ') + ' ' + links.map(l => l.blob).join(' ')).toLowerCase();
            jobs.push({
              seq,
              opcao,
              situacao: sit,
              mensagem,
              concluido: /conclu/i.test(sit),
              is200: /200|0644|manifesto|ssw0?644/i.test(blobAll),
              hasDow: dows.length > 0,
              dows,
            });
          }
          return jobs;
        }"""
    )


def _job_200_sem_dados(job: dict) -> bool:
    if not job.get("concluido") or job.get("hasDow"):
        return False
    msg = str(job.get("mensagem") or job.get("situacao") or "")
    if _EMPTY_FILA_RE.search(msg):
        return True
    return bool(job.get("is200"))


def _atualizar_fila_200(fila) -> None:
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


def _baixar_via_fila_200(
    client,
    context,
    page,
    popup,
    dest_name: str,
    status,
    *,
    known_done: set[str] | None = None,
    min_seq: int = 0,
) -> Path:
    _ = popup
    done = set(known_done or ())
    floor = int(min_seq or 0)
    fila = None
    if not done and floor <= 0:
        try:
            fila = _abrir_fila_156_200(client, context, page, status)
            _safe_wait(fila, 500)
            for j in _ler_jobs_fila_200(fila):
                if not j.get("is200"):
                    continue
                seq = str(j.get("seq") or "")
                if not seq:
                    continue
                try:
                    num = int("".join(ch for ch in seq if ch.isdigit()) or 0)
                except Exception:
                    num = 0
                if num > floor:
                    floor = num
                if j.get("concluido"):
                    done.add(seq)
            status(f"[200] fila aberta · {len(done)} 200 já concluído(s) · floor={floor}")
        except Exception as err:
            status(f"[200] snapshot fila: {err}")
    else:
        status(f"[200] fila wait · known={len(done)} · floor={floor}")

    if fila is None or fila.is_closed():
        fila = _abrir_fila_156_200(client, context, page, status)

    def _seq_num(j: dict) -> int:
        seq = str(j.get("seq") or "")
        try:
            return int("".join(ch for ch in seq if ch.isdigit()) or 0)
        except Exception:
            return 0

    def _is_nosso(j: dict) -> bool:
        seq = str(j.get("seq") or "")
        if not seq or not j.get("is200"):
            return False
        num = _seq_num(j)
        if floor > 0 and num <= floor:
            return False
        if seq in done:
            return False
        return True

    deadline = time.time() + 240
    last_err = ""
    while time.time() < deadline:
        try:
            if fila is None or fila.is_closed():
                fila = _abrir_fila_156_200(client, context, page, status)
            try:
                fila.bring_to_front()
            except Exception:
                pass
            _atualizar_fila_200(fila)
            _safe_wait(fila, 1200)
            jobs = _ler_jobs_fila_200(fila)

            vazios = [j for j in jobs if _is_nosso(j) and _job_200_sem_dados(j)]
            if vazios:
                vazios.sort(key=_seq_num, reverse=True)
                job = vazios[0]
                msg = str(job.get("mensagem") or "sem DOW")
                try:
                    if fila is not None and not fila.is_closed():
                        fila.close()
                except Exception:
                    pass
                raise FilaSemDados(f"sem base · seq={job.get('seq')} · {msg[:80]}")

            cands = [
                j
                for j in jobs
                if _is_nosso(j) and j.get("concluido") and j.get("hasDow")
            ]
            cands.sort(key=_seq_num, reverse=True)
            if not cands:
                proc = [j for j in jobs if _is_nosso(j) and not j.get("concluido")]
                if int(time.time()) % 8 < 2:
                    status(
                        f"[200] aguardando Concluído+DOW na 156 "
                        f"({len(proc)} processando)…"
                    )
                _safe_wait(fila, 2000)
                continue

            job = cands[0]
            seq = str(job.get("seq") or "")
            status(f"[200] DOW na fila · seq={seq} · {job.get('opcao') or ''}")
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
                    raise RuntimeError("200: DOW não encontrado na linha")
                status(f"[200] clique DOW={ok}")
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
            status(f"[200] fila loop: {err}")
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

    raise RuntimeError(f"200: timeout na fila 156 ({last_err})")
