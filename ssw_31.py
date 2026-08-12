"""Download SSW 031 (ssw0495) — CTRCs por código de ocorrência → Excel.

Fluxo:
  1) 1 login · abre N telas 31 · preenche · ► (mapeia código→seq na 156)
  2) Várias abas 156 em paralelo — cada uma baixa o próprio seq
"""
from __future__ import annotations

import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DOWNLOAD_DIR, AceSettings, SswCredentials, ensure_dirs, load_credentials, load_settings
from dates import periodo_mes_ate_hoje, to_ssw_ddmmyy
from ocorrencias_pendencia import OCORR_PENDENCIA_CODES, label_ocorrencia
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]

# Opção 156 = Fila de processamento em lotes (programa ssw1440)
SSW_FILA_URL = "https://sistema.ssw.inf.br/bin/ssw1440"
SSW_FILA_MARKERS = ("fila", "processamento", "lote", "156", "atualizar", "sequ")

# Job 031 concluiu na 156 sem Excel (código sem CTRC no período)
_EMPTY_FILA_RE = re.compile(
    r"n[aã]o\s+selecionou|sem\s+ctrc|nenhum\s+ctrc|sem\s+dados|n[aã]o\s+h[aá]\s+regist|"
    r"nada\s+a\s+(gerar|emitir)|sem\s+movimento|sem\s+ocorr|nenhuma\s+ocorr|"
    r"nenhum\s+registro\s+encontrado|registro\s+n[aã]o\s+encontrado",
    re.IGNORECASE,
)

_STATUS_LOCK = threading.Lock()


class FilaSemDados31(RuntimeError):
    """Job 031 concluído na 156 sem arquivo para baixar."""


def _noop(_: str) -> None:
    return None


def _status_safe(status: StatusCallback, msg: str) -> None:
    with _STATUS_LOCK:
        status(msg)


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


def _seq_num(seq: str) -> int:
    return int(re.sub(r"\D", "", str(seq or "")) or 0)


def _safe_wait(page_or_popup, ms: int) -> None:
    try:
        if page_or_popup is None:
            time.sleep(ms / 1000.0)
            return
        if hasattr(page_or_popup, "is_closed") and page_or_popup.is_closed():
            time.sleep(ms / 1000.0)
            return
        page_or_popup.wait_for_timeout(ms)
    except Exception:
        time.sleep(ms / 1000.0)


def download_reports_31(
    *,
    codes: tuple[str, ...] | list[str] | None = None,
    period: tuple[str, str] | None = None,
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    clean_downloads: bool = True,
) -> dict[str, Any]:
    """
    1 login · N telas 31 · ► com mapa código→seq · downloads em paralelo (abas 156).
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
    if clean_downloads:
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
    queued: list[dict[str, Any]] = []
    status(
        f"SSW 31 | {len(code_list)} tela(s) → fila 156 (download paralelo) | "
        f"ocorrência {ini}-{fim} | excel=S"
    )
    status("códigos: " + ", ".join(code_list))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=use_headless, slow_mo=0 if use_headless else 40)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))
        screens: list[tuple[str, Any]] = []
        fila_map = None
        try:
            client._login(page)
            client._ensure_unit(page)
            client._patch_blank_popup_form(page)

            # ── Fase 1: abrir N telas · preencher · ► + mapear seq ────
            status(f"[31] fase 1/2 · abrindo {len(code_list)} tela(s) 31…")
            known_seqs = _snapshot_fila_seqs(client, context, page, status)
            status(f"[31] fila 156: {len(known_seqs)} job(s) já existentes (ignorados)")

            for idx, code in enumerate(code_list, start=1):
                try:
                    status(f"[31/{code}] ({idx}/{len(code_list)}) abrindo tela…")
                    popup = _open_31_rapido(client, context, page, status)
                    try:
                        popup.on("dialog", lambda d: d.accept())
                    except Exception:
                        pass
                    screens.append((code, popup))
                    status(f"[31/{code}] tela aberta")
                except Exception as err:  # noqa: BLE001
                    errors[code] = str(err)
                    status(f"[31/{code}] FALHOU ao abrir: {err}")

            status(f"[31] preenchendo {len(screens)} tela(s)…")
            for code, popup in screens:
                if code in errors:
                    continue
                try:
                    try:
                        popup.bring_to_front()
                    except Exception:
                        pass
                    status(f"[31/{code}] preenchendo…")
                    _preencher_31(popup, ini=ini, fim=fim, codigo=code, on_status=status)
                except Exception as err:  # noqa: BLE001
                    errors[code] = str(err)
                    status(f"[31/{code}] FALHOU no form: {err}")

            # Mapa código→seq: ► um a um + lê a 156 (rápido) para pegar a seq nova
            status(f"[31] enviando {len(screens)} relatório(s) e mapeando seq na 156…")
            fila_map = _abrir_fila_156(client, context, page, status)
            claimed: set[str] = set(known_seqs)

            for idx, (code, popup) in enumerate(screens, start=1):
                if code in errors:
                    continue
                try:
                    try:
                        popup.bring_to_front()
                    except Exception:
                        pass
                    before = set(claimed)
                    status(f"[31/{code}] ► fila 156…")
                    t0 = time.time()
                    _enviar_fila_31(popup, status, code)
                    _safe_wait(popup, 400)
                    try:
                        if popup is not None and not popup.is_closed():
                            popup.close()
                    except Exception:
                        pass
                    seq = _esperar_nova_seq_0495(
                        fila_map, before=before, claimed=claimed, status=status, code=code
                    )
                    if not seq:
                        errors[code] = "seq não apareceu na 156 após ►"
                        status(f"[31/{code}] FALHOU: seq não apareceu na 156")
                        continue
                    claimed.add(seq)
                    queued.append({"code": code, "seq": seq, "t": t0, "idx": idx})
                    status(f"[31/{code}] na fila · seq={seq}")
                except Exception as err:  # noqa: BLE001
                    errors[code] = str(err)
                    status(f"[31/{code}] FALHOU ao enfileirar: {err}")
                    try:
                        if popup is not None and not popup.is_closed():
                            popup.close()
                    except Exception:
                        pass

            screens = []
            try:
                if fila_map is not None and not fila_map.is_closed():
                    fila_map.close()
            except Exception:
                pass
            fila_map = None

            if not queued:
                raise RuntimeError(
                    "31: nenhum relatório enfileirado. "
                    + "; ".join(f"{k}:{v}" for k, v in errors.items())
                )

            # ── Fase 2: cada seq em browser próprio (paralelo) ───────
            status(
                f"[31] fase 2/2 · {len(queued)} download(s) em paralelo "
                f"(até {min(5, len(queued))} abas 156)…"
            )
            try:
                storage_state = context.storage_state()
            except Exception:
                storage_state = None

            paths = _baixar_seqs_paralelo(
                storage_state,
                client,
                queued=queued,
                ts=ts,
                status=status,
                max_workers=min(5, len(queued)),
            )
        finally:
            for _code, popup in screens:
                try:
                    if popup is not None and not popup.is_closed():
                        popup.close()
                except Exception:
                    pass
            try:
                if fila_map is not None and not fila_map.is_closed():
                    fila_map.close()
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

    if not paths:
        raise RuntimeError(
            "31: nenhum Excel baixado. " + "; ".join(f"{k}:{v}" for k, v in errors.items())
        )
    return {
        "ok": True,
        "paths": paths,
        "errors": errors,
        "queued": queued,
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


def _open_31_rapido(client, context, page, status):
    """Abre 31 via ajax (mais rápido); fallback menu."""
    try:
        with context.expect_page(timeout=18000) as pi:
            page.evaluate(
                """() => {
                  if (typeof ajaxEnvia === 'function') {
                    try { ajaxEnvia('', 1, 'ssw0495'); return '0495'; } catch (e) {}
                    try { ajaxEnvia('', 1, 'ssw031'); return '031'; } catch (e2) {}
                  }
                  return '';
                }"""
            )
        popup = pi.value
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=12000)
        except Exception:
            pass
        return popup
    except Exception as err:
        status(f"[31] ajax tela: {err} — tentando menu")
        return _open_31(client, page)


def _esperar_nova_seq_0495(
    fila,
    *,
    before: set[str],
    claimed: set[str],
    status,
    code: str,
    timeout_s: float = 28.0,
) -> str:
    """Após ►, espera a seq 031 nova aparecer na 156."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            _atualizar_fila(fila)
            _safe_wait(fila, 700)
            jobs = _ler_jobs_fila(fila)
        except Exception:
            time.sleep(1)
            continue
        novos = [
            j
            for j in jobs
            if j.get("is0495")
            and str(j.get("seq") or "")
            and str(j.get("seq") or "") not in before
            and str(j.get("seq") or "") not in claimed
        ]
        if not novos:
            time.sleep(0.6)
            continue
        novos.sort(key=lambda j: _seq_num(str(j.get("seq") or "")))
        seq = str(novos[-1].get("seq") or "")
        if seq:
            return seq
    status(f"[31/{code}] timeout aguardando seq nova na 156")
    return ""


def _baixar_seqs_paralelo(
    storage_state: dict[str, Any] | None,
    client: AceSswClient,
    *,
    queued: list[dict[str, Any]],
    ts: str,
    status: StatusCallback,
    max_workers: int = 5,
) -> dict[str, str]:
    """Cada (código, seq) baixa no próprio browser Playwright (thread-safe)."""
    paths: dict[str, str] = {}
    workers = max(1, min(max_workers, len(queued)))
    status(f"[31] workers paralelo: {workers} · jobs: {len(queued)}")

    def _job(item: dict[str, Any]) -> tuple[str, str | None, str | None]:
        code = str(item.get("code") or "")
        seq = str(item.get("seq") or "")
        try:
            path = _baixar_um_seq_worker(
                storage_state,
                client,
                code=code,
                seq=seq,
                ts=ts,
                status=status,
                headless=True,
            )
            return code, path, None
        except FilaSemDados31 as err:
            _status_safe(status, f"[31/{code}] sem dados · seq={seq} · skip · {err}")
            return code, None, "vazio"
        except Exception as err:  # noqa: BLE001
            _status_safe(status, f"[31/{code}] download falhou: {err}")
            return code, None, str(err)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ace31") as pool:
        futs = [pool.submit(_job, item) for item in queued]
        for fut in as_completed(futs):
            code, path, err = fut.result()
            if path:
                paths[code] = path
                _status_safe(status, f"[31/{code}] OK {Path(path).name}")
            elif err and err != "vazio":
                _status_safe(status, f"[31/{code}] sem arquivo ({err[:80]})")

    return paths


def _baixar_um_seq_worker(
    storage_state: dict[str, Any] | None,
    client: AceSswClient,
    *,
    code: str,
    seq: str,
    ts: str,
    status: StatusCallback,
    headless: bool = True,
) -> str:
    """Browser próprio na thread: abre 156, espera seq pronta, clica Baixar."""
    from playwright.sync_api import sync_playwright

    _ensure_playwright_path()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=0)
        ctx = browser.new_context(
            accept_downloads=True,
            storage_state=storage_state if storage_state else None,
        )
        fila = ctx.new_page()
        fila.set_default_timeout(45000)
        try:
            fila.on("dialog", lambda d: d.accept())
        except Exception:
            pass
        try:
            _status_safe(status, f"[31/{code}] aba 156 · seq={seq}")
            fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=45000)
            _safe_wait(fila, 600)

            deadline = time.time() + 180
            last_log = 0.0
            while time.time() < deadline:
                _atualizar_fila(fila)
                _safe_wait(fila, 900)
                jobs = _ler_jobs_fila(fila)
                job = next((j for j in jobs if str(j.get("seq") or "") == seq), None)
                now = time.time()
                if now - last_log >= 5:
                    last_log = now
                    if job:
                        _status_safe(
                            status,
                            f"[31/{code}] seq={seq} · {job.get('situacao') or '?'} · "
                            f"baixar={'sim' if job.get('hasDow') else 'nao'}",
                        )
                    else:
                        _status_safe(status, f"[31/{code}] seq={seq} ainda não listada…")

                if not job:
                    continue
                if job.get("concluido") and not job.get("hasDow"):
                    if _EMPTY_FILA_RE.search(str(job.get("mensagem") or "")):
                        raise FilaSemDados31(str(job.get("mensagem") or "")[:80])
                    if _job_31_sem_dados(job, since=now - 35):
                        raise FilaSemDados31(str(job.get("mensagem") or "sem DOW")[:80])
                if job.get("concluido") and job.get("hasDow"):
                    dest_name = f"pendencia_31_{code}_{ts}.xlsx"
                    path = _clicar_dow_job(client, ctx, fila, job, dest_name, status, code)
                    size = path.stat().st_size if path.exists() else 0
                    if size < 64:
                        try:
                            path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        raise RuntimeError(f"arquivo suspeito ({size} bytes)")
                    return str(path)
            raise RuntimeError(f"timeout aguardando Baixar na seq={seq}")
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass


def _reopen_31(client: AceSswClient, page, popup=None):
    """Fecha a tela 31 anterior (se houver) e abre de novo para o próximo código."""
    if popup is not None:
        try:
            if not popup.is_closed():
                popup.close()
        except Exception:
            pass
        _safe_wait(page, 350)
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

    label = label_ocorrencia(cod)
    desc_ok = False
    for _ in range(40):
        try:
            if popup.is_closed():
                raise RuntimeError("31: popup fechou no lookup")
            desc = (popup.locator("#ocor_descr").input_value(timeout=1000) or "").strip()
        except Exception as err:
            if "closed" in str(err).lower() or "Target page" in str(err):
                raise
            desc = ""
        low = desc.lower()
        if desc and len(desc) > 2 and "aguarde" not in low and "..." not in desc:
            desc_ok = True
            status(f"[31/{cod}] descrição: {desc[:60]}")
            break
        _safe_wait(popup, 300)
    if not desc_ok:
        try:
            popup.locator("#ocor_descr").fill(label)
            status(f"[31/{cod}] descrição local: {label[:60]}")
        except Exception:
            pass
        _safe_wait(popup, 800)

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
    _safe_wait(popup, 200)


def _enviar_fila_31(popup, status, code: str) -> None:
    """Clica ► e aceita o envio à fila (sem esperar download)."""
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
    status(f"[31/{code}] ► {clicked}")
    _safe_wait(popup, 1200)


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


def _abrir_fila_156(client, context, page, status):
    """Abre opção 156 (ssw1440) — Fila de processamento em lotes."""
    status("[31] abrindo fila 156…")
    fila = None
    # 1) menu 156
    try:
        fila = client._open_menu_option(page, "156", markers=SSW_FILA_MARKERS)
        status("[31] fila 156 via menu")
    except Exception as err:
        status(f"[31] menu 156: {err}")
        fila = None

    # 2) goto direto
    if fila is None:
        try:
            with context.expect_page(timeout=12000) as pi:
                page.evaluate(
                    """() => {
                      if (typeof ajaxEnvia === 'function') ajaxEnvia('', 1, 'ssw1440');
                    }"""
                )
            fila = pi.value
            status("[31] fila 156 via ajaxEnvia ssw1440")
        except Exception:
            fila = context.new_page()
            fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=30000)
            status("[31] fila 156 via goto")

    try:
        fila.on("dialog", lambda d: d.accept())
    except Exception:
        pass

    # blank → forçar URL
    try:
        url = (fila.url or "").lower()
    except Exception:
        url = ""
    if "blank" in url or "ssw1440" not in url:
        status("[31] recuperando ssw1440…")
        try:
            fila.goto(SSW_FILA_URL, wait_until="domcontentloaded", timeout=30000)
            _safe_wait(fila, 800)
        except Exception as err:
            status(f"[31] goto ssw1440: {err}")
            try:
                client._recuperar_blank(page, fila, "1440", ("fila", "dow", "156", "1440"))
            except Exception:
                pass
    return fila


def _ler_jobs_fila(fila) -> list[dict[str, Any]]:
    """Lê linhas da fila 156 (seq, opção, situação, tem download)."""
    return fila.evaluate(
        """() => {
          const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
          const jobs = [];
          const rows = Array.from(document.querySelectorAll('tr'));
          for (const tr of rows) {
            const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
            if (cells.length < 4) continue;
            const seq = (cells[0] || '').replace(/\\D/g, '');
            if (!seq || seq.length < 4) continue;
            const opcao = cells[1] || '';
            // situação: prioriza célula que parece status (não pega 'fila' genérico)
            let sit = '';
            for (const c of cells) {
              if (/^(conclu[ií]d[oa]|processando|na fila|em fila|erro|abortad)/i.test(c)) {
                sit = c; break;
              }
            }
            if (!sit) {
              sit = cells.find(c => /conclu|processando|na\\s*fila|erro|abort/i.test(c)) || cells[6] || '';
            }
            // mensagem (última coluna longa) — ex.: sem CTRCs / sem ocorrência
            let mensagem = '';
            for (let i = cells.length - 1; i >= 0; i--) {
              const c = cells[i] || '';
              if (!c) continue;
              if (/^(conclu|process|fila|erro|abort|\\d)/i.test(c) && c.length < 24) continue;
              if (/^\\d{1,2}\\/\\d{1,2}/.test(c)) continue;
              if (/^dow$/i.test(c) || /^baixar$/i.test(c)) continue;
              if (c.length >= 8) { mensagem = c; break; }
            }
            const nodes = Array.from(tr.querySelectorAll(
              'a[onclick], a[href], img[onclick], input[onclick], font, b, span, td, button'
            ));
            const links = nodes.map(a => {
              const text = norm(a.textContent || a.alt || a.title || a.value || '');
              const onclick = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
              return { text, onclick, href, blob, tag: (a.tagName || '').toLowerCase() };
            });
            const dows = links.filter(x => {
              if (/imprimir|correio|e-mails|emails|retaguarda|voltar|fechar|sair|atualizar/i.test(x.text)
                  && !/\\bdow\\b|baixar/i.test(x.text)) return false;
              if (/^(dow|baixar)$/i.test(x.text) || /\\bdow\\b/i.test(x.text)) return true;
              return /\\bdow\\b|download\\(|ssw0495|\\.xlsx|\\.xls|\\.csv|\\.sswweb|baixar|arquivo/.test(x.blob)
                || (/0495|031/.test(x.blob) && /dow|baixar|href=|http|download/.test(x.blob));
            });
            // fallback: célula com texto DOW / Baixar
            if (!dows.length) {
              for (const td of Array.from(tr.querySelectorAll('td'))) {
                const t = norm(td.innerText || '');
                if (/^(dow|baixar)$/i.test(t) || (t.length <= 8 && /\\b(dow|baixar)\\b/i.test(t))) {
                  dows.push({
                    text: t,
                    onclick: String(td.getAttribute('onclick') || ''),
                    href: '',
                    blob: ('dow baixar ' + t).toLowerCase(),
                    tag: 'td',
                  });
                  break;
                }
              }
            }
            const blobAll = (opcao + ' ' + cells.join(' ') + ' ' + links.map(l => l.blob).join(' ')).toLowerCase();
            const sitLow = sit.toLowerCase();
            const concluido = /conclu/.test(sitLow) && !/n[aã]o\\s*conclu|inconclu/.test(sitLow);
            const processando = /processando|na\\s*fila|em\\s*fila|aguard|gerando/.test(sitLow)
              || (!concluido && !/erro|abort/.test(sitLow) && dows.length === 0);
            jobs.push({
              seq,
              opcao,
              situacao: sit,
              mensagem,
              concluido,
              processando,
              is0495: /0495|031\\s*-|ocorr|ssw0495/.test(blobAll),
              hasDow: dows.length > 0,
              dows,
            });
          }
          return jobs;
        }"""
    )


def _job_31_sem_dados(job: dict, *, since: float | None = None, grace_s: float = 28.0) -> bool:
    """Concluído sem DOW → vazio (mensagem típica) ou grace esgotada."""
    if not job.get("concluido") or job.get("hasDow"):
        return False
    msg = str(job.get("mensagem") or "") + " " + str(job.get("situacao") or "")
    if _EMPTY_FILA_RE.search(msg):
        return True
    if since is not None and (time.time() - since) >= grace_s:
        return True
    return False


def _atualizar_fila(fila) -> None:
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


def _snapshot_fila_seqs(client, context, page, status) -> set[str]:
    """Abre 156 rapidinho, lê seqs atuais, fecha."""
    fila = None
    try:
        fila = _abrir_fila_156(client, context, page, status)
        _safe_wait(fila, 600)
        jobs = _ler_jobs_fila(fila)
        return {str(j.get("seq") or "") for j in jobs if j.get("seq")}
    except Exception as err:
        status(f"[31] snapshot fila: {err}")
        return set()
    finally:
        try:
            if fila is not None and not fila.is_closed():
                fila.close()
        except Exception:
            pass


def _baixar_todos_da_fila(
    client,
    context,
    page,
    fila,
    *,
    queued: list[dict[str, Any]],
    known_before: set[str],
    ts: str,
    status,
) -> dict[str, str]:
    """
    Espera TODOS os jobs NOVOS na 156 aparecerem e finalizarem
    (Concluído+Baixar/DOW ou NENHUM REGISTRO) — só então baixa.
    Não baixa job antigo, nem de outra opção, nem enquanto ainda processa.
    """
    paths: dict[str, str] = {}
    want = len(queued)
    codes_order = [q["code"] for q in queued]
    deadline = time.time() + max(300, 75 * want)
    downloaded_seqs: set[str] = set()
    skipped_seqs: set[str] = set()
    skipped_codes: set[str] = set()
    our_seqs: set[str] = set()  # seqs novas vistas após o enqueue
    concluido_sem_dow_since: dict[str, float] = {}
    last_log = 0.0
    all_ready_announced = False

    def _done_count() -> int:
        return len(paths) + len(skipped_codes)

    def _next_code() -> str | None:
        return next(
            (c for c in codes_order if c not in paths and c not in skipped_codes),
            None,
        )

    def _poll() -> list[dict[str, Any]]:
        nonlocal fila
        if fila is None or fila.is_closed():
            fila = _abrir_fila_156(client, context, page, status)
        try:
            fila.bring_to_front()
        except Exception:
            pass
        _atualizar_fila(fila)
        _safe_wait(fila, 1200)
        return _ler_jobs_fila(fila)

    def _classify(jobs: list[dict[str, Any]]) -> tuple[list, list, list, list]:
        """Retorna (pool_nosso, prontos, ainda_processando, vazios)."""
        novos = [
            j
            for j in jobs
            if str(j.get("seq") or "")
            and str(j.get("seq") or "") not in known_before
        ]
        novos.sort(
            key=lambda j: int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0)
        )
        only0495 = [j for j in novos if j.get("is0495")]

        # Trava as seqs "nossas" na 1ª vez que aparecerem o suficiente
        if len(our_seqs) < want:
            for j in only0495 if only0495 else novos:
                seq = str(j.get("seq") or "")
                if seq and seq not in our_seqs:
                    our_seqs.add(seq)
                if len(our_seqs) >= want:
                    break

        pool = [j for j in jobs if str(j.get("seq") or "") in our_seqs]
        pool.sort(
            key=lambda j: int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0)
        )

        now = time.time()
        for j in pool:
            seq = str(j.get("seq") or "")
            if not seq or seq in downloaded_seqs or seq in skipped_seqs:
                continue
            if j.get("concluido") and not j.get("hasDow"):
                concluido_sem_dow_since.setdefault(seq, now)
            else:
                concluido_sem_dow_since.pop(seq, None)

        prontos = [
            j
            for j in pool
            if j.get("concluido")
            and j.get("hasDow")
            and str(j.get("seq") or "") not in downloaded_seqs
            and str(j.get("seq") or "") not in skipped_seqs
        ]
        vazios = [
            j
            for j in pool
            if str(j.get("seq") or "") not in downloaded_seqs
            and str(j.get("seq") or "") not in skipped_seqs
            and _job_31_sem_dados(
                j,
                since=concluido_sem_dow_since.get(str(j.get("seq") or "")),
            )
        ]
        pendentes = [
            j
            for j in pool
            if str(j.get("seq") or "") not in downloaded_seqs
            and str(j.get("seq") or "") not in skipped_seqs
            and not (j.get("concluido") and j.get("hasDow"))
            and j not in vazios
        ]
        # se ainda não travamos want seqs, pendentes = tudo novo sem DOW
        if len(our_seqs) < want:
            pendentes = [
                j
                for j in (only0495 or novos)
                if str(j.get("seq") or "") not in downloaded_seqs
                and str(j.get("seq") or "") not in skipped_seqs
                and not (j.get("concluido") and j.get("hasDow"))
                and not _job_31_sem_dados(
                    j,
                    since=concluido_sem_dow_since.get(str(j.get("seq") or "")),
                )
            ] or pendentes
        return pool, prontos, pendentes, vazios

    while time.time() < deadline and _done_count() < want:
        try:
            jobs = _poll()
            _pool, prontos, pendentes, vazios = _classify(jobs)

            now = time.time()
            if now - last_log >= 4:
                last_log = now
                status(
                    f"[31] fila 156 · baixados {len(paths)}/{want} · "
                    f"prontos {len(prontos)} · processando {len(pendentes)} · "
                    f"vazios {len(vazios)} · seqs novas {len(our_seqs)}"
                )
                for j in pendentes[:6]:
                    seq = str(j.get("seq") or "")
                    wait_s = ""
                    if j.get("concluido") and not j.get("hasDow"):
                        t0 = concluido_sem_dow_since.get(seq)
                        if t0:
                            wait_s = f" · semDOW {int(now - t0)}s"
                    status(
                        f"[31]   ⏳ seq={seq} · {j.get('situacao') or '?'} · "
                        f"dow={'sim' if j.get('hasDow') else 'nao'}{wait_s} · "
                        f"{(j.get('opcao') or '')[:36]}"
                    )

            # Ideal: NÃO baixar enquanto faltar job aparecer OU ainda processar
            still_waiting = len(our_seqs) < want or len(pendentes) > 0
            if still_waiting:
                if not all_ready_announced:
                    status(
                        f"[31] aguardando TODOS na 156 "
                        f"({len(our_seqs)}/{want} seqs · "
                        f"{len(pendentes)} processando · "
                        f"{len(prontos)} Baixar · {len(vazios)} vazios)…"
                    )
                _safe_wait(fila, 2500)
                continue

            if not all_ready_announced:
                all_ready_announced = True
                status(
                    f"[31] todos prontos na 156 · "
                    f"{len(prontos)} Baixar + {len(vazios)} sem registro — iniciando downloads…"
                )

            # Concluído sem arquivo (NENHUM REGISTRO…) → skip
            for job in vazios:
                if _done_count() >= want:
                    break
                seq = str(job.get("seq") or "")
                if not seq or seq in skipped_seqs or seq in downloaded_seqs:
                    continue
                code = _next_code()
                if not code:
                    break
                msg = str(job.get("mensagem") or job.get("situacao") or "sem DOW")
                skipped_seqs.add(seq)
                skipped_codes.add(code)
                concluido_sem_dow_since.pop(seq, None)
                status(
                    f"[31/{code}] sem dados · seq={seq} · skip · {msg[:70]}"
                )

            if not prontos:
                if _done_count() >= want:
                    break
                _safe_wait(fila, 2000)
                continue

            # FIFO: baixa prontos (Baixar/DOW), do seq mais antigo
            prontos.sort(
                key=lambda j: int(re.sub(r"\D", "", str(j.get("seq") or "")) or 0)
            )

            for job in prontos:
                if _done_count() >= want:
                    break
                seq = str(job.get("seq") or "")
                if seq in downloaded_seqs or seq in skipped_seqs:
                    continue
                code = _next_code()
                if not code:
                    break

                if not job.get("concluido") or not job.get("hasDow"):
                    continue

                dest_name = f"pendencia_31_{code}_{ts}.xlsx"
                status(
                    f"[31/{code}] Baixar · seq={seq} · "
                    f"{job.get('situacao') or 'Concluído'} · "
                    f"{(job.get('opcao') or '')[:50]}"
                )
                try:
                    path = _clicar_dow_job(
                        client, context, fila, job, dest_name, status, code
                    )
                    size = path.stat().st_size if path.exists() else 0
                    if size < 64:
                        status(f"[31/{code}] arquivo suspeito ({size} bytes) — ignorando, re-tenta")
                        try:
                            path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        all_ready_announced = False  # re-poll / re-esperar
                        _safe_wait(fila, 2000)
                        break
                    paths[code] = str(path)
                    downloaded_seqs.add(seq)
                    status(f"[31/{code}] OK {path.name} ({size} bytes)")
                except Exception as err:  # noqa: BLE001
                    status(f"[31/{code}] download falhou: {err}")
                    all_ready_announced = False
                    _safe_wait(fila, 2000)
                    break  # re-poll após falha
        except Exception as err:  # noqa: BLE001
            status(f"[31] fila 156 loop: {err}")
            try:
                fila = _abrir_fila_156(client, context, page, status)
            except Exception:
                pass
            time.sleep(2)

    missing = [c for c in codes_order if c not in paths and c not in skipped_codes]
    if skipped_codes:
        status(
            f"[31] códigos sem CTRC no período (skip): {', '.join(sorted(skipped_codes, key=codes_order.index))}"
        )
    if missing:
        status(
            f"[31] timeout/parcial na fila — faltou: {', '.join(missing)} "
            f"(baixados {len(paths)}/{want}; skip vazios {len(skipped_codes)}; "
            f"não seguiu com DOW incompleto)"
        )
    return paths


def _clicar_dow_job(client, context, fila, job: dict, dest_name: str, status, code: str) -> Path:
    """Clica Baixar/DOW da linha da seq — espera download na página (não no context)."""
    _atualizar_fila(fila)
    _safe_wait(fila, 400)

    with fila.expect_download(timeout=45000) as di:
        ok = fila.evaluate(
            """({ seq }) => {
              const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
              const want = String(seq || '').replace(/\\D/g, '');
              if (!want) return '';
              const rows = Array.from(document.querySelectorAll('tr'));
              for (const tr of rows) {
                const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
                if (!cells.length) continue;
                const s = (cells[0] || '').replace(/\\D/g, '');
                if (s !== want) continue;
                const sit = cells.find(c => /conclu|process|fila|erro|abort/i.test(c)) || '';
                if (sit && /processando|na\\s*fila|em\\s*fila/i.test(sit) && !/conclu/i.test(sit)) {
                  return 'ainda_processando';
                }
                const links = Array.from(tr.querySelectorAll(
                  'a[onclick], a[href], img[onclick], input[onclick], button, font, b, span'
                ));
                for (const a of links) {
                  const text = norm(a.textContent || a.alt || a.title || a.value || '');
                  const onclick = String(a.getAttribute('onclick') || '');
                  const href = String(a.getAttribute('href') || '');
                  const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
                  if (/imprimir|correio|atualizar|voltar|fechar/i.test(text)
                      && !/\\b(dow|baixar)\\b/i.test(text)) continue;
                  if (/^(dow|baixar)$/i.test(text) || /\\b(dow|baixar)\\b/i.test(text)
                      || /\\bdow\\b|download\\(|ssw0495|\\.xlsx|\\.xls|\\.csv|\\.sswweb|baixar|arquivo/.test(blob)
                      || (/0495|031/.test(blob) && /dow|baixar|href=|http|download/.test(blob))) {
                    const clickEl = a.closest('a,button') || a;
                    clickEl.click();
                    return 'row';
                  }
                }
                for (const td of Array.from(tr.querySelectorAll('td'))) {
                  const t = norm(td.innerText || '');
                  if (!(/^(dow|baixar)$/i.test(t) || (t.length <= 10 && /\\b(dow|baixar)\\b/i.test(t)))) continue;
                  const clickEl = td.querySelector('a, button, img, [onclick]') || td;
                  clickEl.click();
                  return 'row-td';
                }
                return 'sem_dow';
              }
              return 'seq_sumiu';
            }""",
            {"seq": job.get("seq") or ""},
        )
        if ok == "ainda_processando":
            raise RuntimeError(f"31/{code}: seq ainda processando — não clicou Baixar")
        if ok not in ("row", "row-td"):
            raise RuntimeError(f"31/{code}: Baixar/DOW da seq não encontrado ({ok})")
        status_fn = status
        try:
            status_fn(f"[31/{code}] clique Baixar={ok}")
        except Exception:
            _status_safe(status, f"[31/{code}] clique Baixar={ok}")
    download = di.value
    return _save_named(client, download, dest_name)
