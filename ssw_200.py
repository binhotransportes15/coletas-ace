"""Download SSW 200 (ssw0644) — Relação de Manifestos Operacionais.

Formulário:
  Período emissão · Unidade origem · Tipo arquivo=E (excel)
  ► → fila 156 → DOW (CSV com FRETE-R$).
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
    headless: bool | None = None,
    on_status: StatusCallback | None = None,
    credentials: SswCredentials | None = None,
    settings: AceSettings | None = None,
    client: AceSswClient | None = None,
    context=None,
    page=None,
) -> dict[str, Any]:
    """Gera 200 com Tipo=E → fila 156 → CSV. Reusa sessão se page/client passados."""
    status = on_status or _noop
    ensure_dirs()
    _ensure_playwright_path()
    creds = credentials or load_credentials()
    cfg = settings or load_settings()
    use_headless = cfg.headless if headless is None else bool(headless)

    unid = (unidade_origem or "").strip().upper()
    tipo = (tipo_arquivo or "E").strip().upper()[:1] or "E"

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
        f"SSW 200 | tipo={tipo} | origem={unid} | {ini}-{fim}"
        + (" · sessão reusada" if reuse else "")
    )

    def _run(sess_client, sess_context, sess_page) -> None:
        nonlocal paths
        popup = None
        try:
            status("[200] abrindo opção 200 (ssw0644)…")
            popup = sess_client._open_menu_option(sess_page, "200", markers=SSW_200_MARKERS)
            _preencher_200(
                popup, ini=ini, fim=fim, unidade=unid, tipo=tipo, on_status=status
            )
            dest = f"contratacao_200_{ts}.csv"
            path = _gerar_download_200(
                sess_client, sess_context, sess_page, popup, dest, status
            )
            paths.append(str(path))
            status(f"[200] OK {path.name}")
        finally:
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

    if not paths:
        raise RuntimeError("200: nenhum arquivo baixado")

    return {
        "ok": True,
        "files": paths,
        "period": (ini_ddmm, fim_ddmm),
        "periodo_fmt": f"{ini_ddmm} – {fim_ddmm}",
        "unidade_origem": unid,
        "tipo_arquivo": tipo,
    }


def _preencher_200(popup, *, ini: str, fim: str, unidade: str, tipo: str, on_status) -> None:
    """Período + Tipo=E. Unidade origem fica vazia (não forçar SPO)."""
    status = on_status
    _ = unidade  # origem em branco = todos (screenshot / CSV manual)
    popup.wait_for_timeout(400)

    # Playwright: mais estável que byHint (labels SSW misturam o texto do form)
    filled = {"okIni": False, "okFim": False, "okTipo": False, "vals": []}
    try:
        inputs = popup.locator('input[type=text], input:not([type])')
        n = inputs.count()
        # datas: primeiros maxlength/size 6
        date_idxs: list[int] = []
        tipo_idx = -1
        for i in range(n):
            el = inputs.nth(i)
            try:
                ml = el.evaluate(
                    """e => ({
                      ml: Number(e.maxLength || 0),
                      sz: Number(e.size || 0),
                      v: (e.value || '').toUpperCase().trim(),
                    })"""
                )
            except Exception:
                continue
            if ml.get("ml") == 6 or ml.get("sz") == 6:
                date_idxs.append(i)
            if ml.get("v") in {"T", "E"} and (ml.get("ml") in {0, 1, 2} or ml.get("sz") in {0, 1, 2}):
                tipo_idx = i  # último T/E de 1 char = Tipo de arquivo

        if len(date_idxs) >= 2:
            inputs.nth(date_idxs[0]).fill(ini)
            inputs.nth(date_idxs[1]).fill(fim)
            filled["okIni"] = True
            filled["okFim"] = True

        if tipo_idx < 0:
            # fallback: último input de 1 caractere
            for i in range(n - 1, -1, -1):
                try:
                    ml = inputs.nth(i).evaluate(
                        "e => ({ ml: Number(e.maxLength||0), sz: Number(e.size||0) })"
                    )
                    if ml.get("ml") == 1 or ml.get("sz") == 1:
                        tipo_idx = i
                        break
                except Exception:
                    continue

        if tipo_idx >= 0:
            inputs.nth(tipo_idx).fill(tipo)
            filled["okTipo"] = True
            status(f"[200] Tipo arquivo input[{tipo_idx}]={tipo}")

        # limpa Unidade origem se alguém (ou default) deixou sigla de 3 letras no 3º campo
        # ordem típica: ini, fim, origem, destino, placa, cpf, tipo
        if n >= 3 and tipo_idx != 2:
            try:
                cur = (inputs.nth(2).input_value() or "").strip().upper()
                if cur in {"SPO", "LEO", "RIS"} or (len(cur) == 3 and cur.isalpha()):
                    inputs.nth(2).fill("")
                    status(f"[200] Unidade origem limpa (era {cur})")
            except Exception:
                pass

        filled["vals"] = [
            (inputs.nth(i).input_value() or "") for i in range(min(n, 8))
        ]
    except Exception as err:  # noqa: BLE001
        status(f"[200] fill playwright: {err}")

    status(f"[200] form {filled}")
    if not filled.get("okTipo"):
        raise RuntimeError(f"200: não achei Tipo de arquivo (E). form={filled}")
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


def _gerar_download_200(client, context, page, popup, dest_name: str, status) -> Path:
    """Tipo=E + ► → (download direto raro) ou fila 156."""
    clicked = ""
    try:
        with context.expect_event("download", timeout=10000) as di:
            clicked = _clicar_gerar_200(popup)
            if not clicked:
                raise RuntimeError("200: botão ► não encontrado")
            status(f"[200] clique={clicked} (aguardando download…)")
        return client._save_download(di.value, dest_name)
    except RuntimeError:
        raise
    except Exception as direct_err:  # noqa: BLE001
        if not clicked:
            clicked = _clicar_gerar_200(popup)
            if not clicked:
                raise RuntimeError("200: botão ► não encontrado") from direct_err
            status(f"[200] clique={clicked}")
        status(f"[200] foi pra fila 156 ({direct_err})")
        try:
            popup.wait_for_timeout(800)
        except Exception:
            pass
        return _baixar_via_fila_200(client, context, page, popup, dest_name, status)


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
              is200: /200|0644|manifesto|ssw0?644/i.test(blobAll),
              hasDow: dows.length > 0,
              dows,
            });
          }
          return jobs;
        }"""
    )


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


def _baixar_via_fila_200(client, context, page, popup, dest_name: str, status) -> Path:
    _ = popup
    known_ready: set[str] = set()
    fila = None
    try:
        fila = _abrir_fila_156_200(client, context, page, status)
        _safe_wait(fila, 500)
        for j in _ler_jobs_fila_200(fila):
            seq = str(j.get("seq") or "")
            if seq and j.get("concluido") and j.get("hasDow"):
                known_ready.add(seq)
        status(f"[200] fila aberta · {len(known_ready)} pronto(s) antigo(s)")
    except Exception as err:
        status(f"[200] snapshot fila: {err}")

    if fila is None or fila.is_closed():
        fila = _abrir_fila_156_200(client, context, page, status)

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
            cands = [
                j
                for j in jobs
                if j.get("concluido")
                and j.get("hasDow")
                and (j.get("is200") or str(j.get("seq") or "") not in known_ready)
            ]

            def sk(j: dict) -> tuple:
                seq = str(j.get("seq") or "")
                try:
                    num = int("".join(ch for ch in seq if ch.isdigit()) or 0)
                except Exception:
                    num = 0
                return (0 if j.get("is200") else 1, -num)

            cands.sort(key=sk)
            if not cands:
                if int(time.time()) % 8 < 2:
                    status("[200] aguardando DOW na fila 156…")
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
