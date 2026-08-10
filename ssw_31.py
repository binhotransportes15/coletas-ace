"""Download SSW 031 (ssw0495) — CTRCs por código de ocorrência → Excel.

Fluxo (rápido):
  1) 1 login · abre N telas 31 (1 por código) · preenche todas · ► em todas → fila 156
  2) Abre opção 156 (ssw1440) uma vez e baixa todos os Excel 0495 concluídos
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
from ocorrencias_pendencia import OCORR_PENDENCIA_CODES, label_ocorrencia
from ssw_client import AceSswClient, cleanup_downloads

StatusCallback = Callable[[str], None]

# Opção 156 = Fila de processamento em lotes (programa ssw1440)
SSW_FILA_URL = "https://sistema.ssw.inf.br/bin/ssw1440"
SSW_FILA_MARKERS = ("fila", "processamento", "lote", "156", "atualizar", "sequ")


def _noop(_: str) -> None:
    return None


def _ensure_playwright_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(local) / "ms-playwright")


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
) -> dict[str, Any]:
    """
    1 login · N telas 31 em paralelo · ► em todas → fila 156 · baixa todos.
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
    queued: list[dict[str, Any]] = []
    status(
        f"SSW 31 | {len(code_list)} tela(s) em paralelo → fila 156 | "
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
        fila = None
        try:
            client._login(page)
            client._ensure_unit(page)
            client._patch_blank_popup_form(page)

            # ── Fase 1: abrir N telas · preencher · ► em todas ───────
            status(f"[31] fase 1/2 · abrindo {len(code_list)} tela(s) 31…")
            known_seqs = _snapshot_fila_seqs(client, context, page, status)
            status(f"[31] fila 156: {len(known_seqs)} job(s) já existentes (ignorados)")

            for idx, code in enumerate(code_list, start=1):
                try:
                    status(f"[31/{code}] ({idx}/{len(code_list)}) abrindo tela…")
                    popup = _open_31(client, page)
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

            status(f"[31] enviando {len(screens)} relatório(s) pra fila 156…")
            for idx, (code, popup) in enumerate(screens, start=1):
                if code in errors:
                    continue
                try:
                    try:
                        popup.bring_to_front()
                    except Exception:
                        pass
                    status(f"[31/{code}] ► fila 156…")
                    t0 = time.time()
                    _enviar_fila_31(popup, status, code)
                    _safe_wait(popup, 600)
                    queued.append({"code": code, "seq": "", "t": t0, "idx": idx})
                    status(f"[31/{code}] enviado à fila 156")
                except Exception as err:  # noqa: BLE001
                    errors[code] = str(err)
                    status(f"[31/{code}] FALHOU ao enfileirar: {err}")

            for _code, popup in screens:
                try:
                    if popup is not None and not popup.is_closed():
                        popup.close()
                except Exception:
                    pass
            screens = []

            if not queued:
                raise RuntimeError(
                    "31: nenhum relatório enfileirado. "
                    + "; ".join(f"{k}:{v}" for k, v in errors.items())
                )

            status("[31] aguardando fila registrar os jobs…")
            time.sleep(3)

            # ── Fase 2: baixar na 156 ────────────────────────────────
            status(
                f"[31] fase 2/2 · baixando {len(queued)} Excel na fila 156…"
            )
            fila = _abrir_fila_156(client, context, page, status)
            paths = _baixar_todos_da_fila(
                client,
                context,
                page,
                fila,
                queued=queued,
                known_before=known_seqs,
                ts=ts,
                status=status,
            )
        finally:
            for _code, popup in screens:
                try:
                    if popup is not None and not popup.is_closed():
                        popup.close()
                except Exception:
                    pass
            try:
                if fila is not None and not fila.is_closed():
                    fila.close()
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
          // tenta tabela
          const rows = Array.from(document.querySelectorAll('tr'));
          for (const tr of rows) {
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
              if (/imprimir|correio|e-mails|emails|retaguarda|voltar|fechar|sair|atualizar/i.test(x.text)) return false;
              return /\\bdow\\b|download\\(|ssw0495|\\.xlsx|\\.xls|\\.csv|baixar|arquivo/.test(x.blob)
                || (/0495|031/.test(x.blob) && /dow|href=|http/.test(x.blob));
            });
            jobs.push({
              seq,
              opcao,
              situacao: sit,
              concluido: /conclu/i.test(sit),
              is0495: /0495|031\\s*-|ocorr/i.test(opcao + ' ' + links.map(l => l.blob).join(' ')),
              hasDow: dows.length > 0,
              dows,
            });
          }
          // fallback: varrer links DOW globais com contexto
          if (!jobs.length) {
            const all = Array.from(document.querySelectorAll('a[onclick], a[href], img[onclick]'));
            all.forEach((a, i) => {
              const text = norm(a.textContent || a.alt || a.title || '');
              const onclick = String(a.getAttribute('onclick') || '');
              const href = String(a.getAttribute('href') || '');
              const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
              if (/imprimir|correio|atualizar/i.test(text)) return;
              if (!(/\\bdow\\b|download\\(|ssw0495|\\.xlsx|baixar/.test(blob))) return;
              jobs.push({
                seq: 'L' + i,
                opcao: text || 'download',
                situacao: 'Concluído',
                concluido: true,
                is0495: /0495|031|ocorr|xlsx|csv|sswweb/.test(blob),
                hasDow: true,
                dows: [{ text, onclick, href, blob }],
                linkIndex: i,
              });
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
    Espera jobs concluídos na 156 e baixa Excel na ordem dos códigos enfileirados.
    """
    paths: dict[str, str] = {}
    want = len(queued)
    codes_order = [q["code"] for q in queued]
    seq_by_code = {q["code"]: q.get("seq") or "" for q in queued}
    deadline = time.time() + max(180, 45 * want)
    downloaded_seqs: set[str] = set()

    while time.time() < deadline and len(paths) < want:
        try:
            if fila is None or fila.is_closed():
                fila = _abrir_fila_156(client, context, page, status)
            try:
                fila.bring_to_front()
            except Exception:
                pass
            _atualizar_fila(fila)
            _safe_wait(fila, 1000)
            jobs = _ler_jobs_fila(fila)

            # candidatos: 0495/031 concluídos com DOW, não baixados ainda
            ours = [
                j
                for j in jobs
                if j.get("concluido")
                and j.get("hasDow")
                and str(j.get("seq") or "") not in downloaded_seqs
                and j.get("is0495")
                and (
                    str(j.get("seq") or "") not in known_before
                    or str(j.get("seq") or "")
                    in {seq_by_code[c] for c in codes_order if seq_by_code.get(c)}
                )
            ]
            # fallback: qualquer job novo na fila (caso is0495 falhe no parse)
            if len(ours) < (want - len(paths)):
                extras = [
                    j
                    for j in jobs
                    if j.get("concluido")
                    and j.get("hasDow")
                    and str(j.get("seq") or "") not in downloaded_seqs
                    and str(j.get("seq") or "") not in known_before
                    and j not in ours
                ]
                ours = ours + extras
            cands = ours
            # FIFO: seq conhecida → código; senão seq asc (mais antigo = 1º enfileirado)
            def sort_key(j: dict[str, Any]) -> tuple:
                seq = str(j.get("seq") or "")
                mapped = 0 if seq and seq in seq_by_code.values() else 1
                try:
                    num = int(re.sub(r"\D", "", seq) or 0)
                except Exception:
                    num = 0
                return (mapped, num)

            cands.sort(key=sort_key)

            if not cands:
                if int(time.time()) % 10 < 3:
                    status(
                        f"[31] fila 156: aguardando conclusão "
                        f"({len(paths)}/{want} baixados)…"
                    )
                _safe_wait(fila, 2500)
                continue

            for job in cands:
                if len(paths) >= want:
                    break
                seq = str(job.get("seq") or "")
                code = None
                for c in codes_order:
                    if c in paths:
                        continue
                    if seq and seq_by_code.get(c) == seq:
                        code = c
                        break
                if code is None:
                    # próximo código sem arquivo (ordem de enfileiramento = FIFO)
                    for c in codes_order:
                        if c not in paths:
                            code = c
                            break
                if not code:
                    break

                dest_name = f"pendencia_31_{code}_{ts}.xlsx"
                status(
                    f"[31/{code}] baixando da fila 156"
                    + (f" · seq={seq}" if seq else "")
                    + f" · {job.get('opcao') or ''}"
                )
                try:
                    path = _clicar_dow_job(client, context, fila, job, dest_name, status, code)
                    paths[code] = str(path)
                    if seq:
                        downloaded_seqs.add(seq)
                        seq_by_code[code] = seq
                    status(f"[31/{code}] OK {path.name} ({path.stat().st_size} bytes)")
                except Exception as err:  # noqa: BLE001
                    status(f"[31/{code}] download falhou: {err}")
                    _safe_wait(fila, 1500)
        except Exception as err:  # noqa: BLE001
            status(f"[31] fila 156 loop: {err}")
            try:
                fila = _abrir_fila_156(client, context, page, status)
            except Exception:
                pass
            time.sleep(2)

    missing = [c for c in codes_order if c not in paths]
    if missing:
        status(f"[31] sem download para: {', '.join(missing)}")
    return paths


def _clicar_dow_job(client, context, fila, job: dict, dest_name: str, status, code: str) -> Path:
    """Clica o link DOW do job na fila e salva o arquivo."""
    dows = job.get("dows") or []
    link_index = job.get("linkIndex")
    with context.expect_event("download", timeout=90000) as di:
        ok = fila.evaluate(
            """({ seq, linkIndex }) => {
              const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
              // por índice global (fallback)
              if (linkIndex != null) {
                const all = Array.from(document.querySelectorAll('a[onclick], a[href], img[onclick]'));
                const a = all[linkIndex];
                if (a) { a.click(); return 'idx'; }
              }
              // por linha da seq
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
                  if (/\\bdow\\b|download\\(|ssw0495|\\.xlsx|\\.xls|\\.csv|baixar|arquivo/.test(blob)
                      || (/0495|031/.test(blob) && /dow|href=|http/.test(blob))) {
                    a.click();
                    return 'row';
                  }
                }
              }
              // último recurso: primeiro DOW da página
              const all = Array.from(document.querySelectorAll('a[onclick], a[href], img[onclick]'));
              for (const a of all) {
                const text = norm(a.textContent || a.alt || a.title || '');
                const onclick = String(a.getAttribute('onclick') || '');
                const href = String(a.getAttribute('href') || '');
                const blob = (onclick + ' ' + text + ' ' + href).toLowerCase();
                if (/imprimir|correio|atualizar/i.test(text)) continue;
                if (/\\bdow\\b|download\\(|ssw0495|\\.xlsx|baixar/.test(blob)) {
                  a.click();
                  return 'first';
                }
              }
              return '';
            }""",
            {"seq": job.get("seq") or "", "linkIndex": link_index},
        )
        if not ok:
            raise RuntimeError(f"31/{code}: DOW não encontrado na linha")
        status(f"[31/{code}] clique DOW={ok}")
    download = di.value
    return _save_named(client, download, dest_name)
