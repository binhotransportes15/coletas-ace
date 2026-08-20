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
from ssw_fila156 import (
    FilaSemDados,
    aguardar_baixar,
    atualizar_fila as _atualizar_fila156,
    esperar_meta_baixar,
    find_baixar_meta,
    job_pronto_baixar,
    ler_jobs as _ler_jobs156,
    abrir_fila as _abrir_fila156,
    safe_wait as _safe_wait156,
)

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

# Padrões da coluna Opção na fila 156 para o 455
_455_OPTION_PATTERNS = (
    r"455\s*-",
    r"\b455\b",
    r"0230",
    r"ssw0230",
    r"fretes?\s+exped",
    r"expedidos",
)


def _sniff_455_suffix(body: bytes, hint: str = "") -> str:
    """Detecta extensão real pelo conteúdo (SSW costuma entregar .sswweb com nome .xlsx)."""
    raw = body or b""
    hint_l = (hint or "").lower()
    if raw[:2] == b"PK":
        return ".xlsx"
    # OLE Compound Document → .xls antigo
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return ".xls"
    head = raw[:240].lstrip().lower()
    if b"<html" in head or b"<!doctype" in head:
        return ".html"
    if "sswweb" in hint_l:
        return ".sswweb"
    if "csv" in hint_l and "sswweb" not in hint_l:
        return ".csv"
    # Texto delimitado típico do SSW
    sample = raw[:800]
    if b";" in sample or b"," in sample or b"\t" in sample:
        return ".sswweb"
    if raw[:1] in (b'"', b"'") or raw[:1].isalnum():
        return ".sswweb"
    if "xlsx" in hint_l and raw[:2] == b"PK":
        return ".xlsx"
    return ".sswweb"


def _normalize_455_path(path: Path, preferred_stem: str = "") -> Path:
    """Renomeia o arquivo baixado para a extensão correta pelo conteúdo."""
    p = Path(path)
    if not p.is_file():
        return p
    try:
        body = p.read_bytes()[:4096]
    except OSError:
        return p
    suffix = _sniff_455_suffix(body, p.name)
    if suffix == ".html":
        raise RuntimeError("455: download veio como HTML (sem arquivo de dados)")
    stem = preferred_stem or p.stem
    # Evita stem com extensão residual no nome
    if stem.lower().endswith((".xlsx", ".xls", ".csv", ".sswweb")):
        stem = Path(stem).stem
    dest = p.with_name(f"{stem}{suffix}")
    if dest.resolve() == p.resolve():
        return p
    if dest.exists():
        dest = p.with_name(f"{stem}_{int(time.time())}{suffix}")
    try:
        p.replace(dest)
        return dest
    except OSError:
        return p


class FilaSemDados455(FilaSemDados):
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
    _safe_wait156(page, ms)


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
        try:
            from ace_stop import register_browser
            register_browser(browser)
        except Exception:
            pass
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
            dest_name = f"emissao_455_{uni or 'ALL'}_{ts}.sswweb"
            status("[455] ► fila 156…")
            path = _gerar_download_455(
                client, context, page, popup, dest_name, status
            )
            path = _normalize_455_path(path, preferred_stem=Path(dest_name).stem)
            status(f"[455] OK {path.name} ({path.stat().st_size} bytes)")
        except FilaSemDados as empty_err:
            status(f"[455] sem dados — desconsidera ({empty_err})")
            return {
                "ok": True,
                "files": [],
                "paths": {},
                "empty": True,
                "error": str(empty_err),
                "period": f"{ini}-{fim}",
                "periodo_fmt": ini_ddmm if ini_ddmm == fim_ddmm else f"{ini_ddmm} – {fim_ddmm}",
                "unidade": uni,
                "download_dir": str(DOWNLOAD_DIR),
            }
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
                try:
                    browser.close()
                except Exception:
                    pass
                try:
                    from ace_stop import unregister_browser
                    unregister_browser(browser)
                except Exception:
                    pass
            except Exception:
                pass

    if path is None or not path.exists():
        raise RuntimeError("455: nenhum Excel baixado")
    size = int(path.stat().st_size)
    # Só cabeçalho / lixo → trata como sem dados (não zera o painel)
    if size < 400:
        status(f"[455] arquivo muito pequeno ({size} bytes) — desconsidera")
        return {
            "ok": True,
            "files": [],
            "paths": {},
            "empty": True,
            "error": f"arquivo vazio/mínimo ({size} bytes)",
            "period": f"{ini}-{fim}",
            "periodo_fmt": ini_ddmm if ini_ddmm == fim_ddmm else f"{ini_ddmm} – {fim_ddmm}",
            "unidade": uni,
            "download_dir": str(DOWNLOAD_DIR),
        }
    return {
        "ok": True,
        "files": [str(path)],
        "paths": {"455": str(path)},
        "period": f"{ini}-{fim}",
        "periodo_fmt": ini_ddmm if ini_ddmm == fim_ddmm else f"{ini_ddmm} – {fim_ddmm}",
        "unidade": uni,
        "download_dir": str(DOWNLOAD_DIR),
    }


def _fill_id(popup, fid: str, value: str, *, tab: bool = False) -> None:
    """Preenche campo SSW como os outros módulos (fill + Tab) — .value JS sozinho falha no 455."""
    loc = popup.locator(f'[id="{fid}"]')
    loc.wait_for(state="visible", timeout=15000)
    loc.fill(str(value if value is not None else ""))
    if tab:
        try:
            loc.press("Tab")
        except Exception:
            pass


def _read_455_form(popup) -> dict[str, str]:
    return popup.evaluate(
        """() => {
          const g = (id) => ((document.getElementById(id) || {}).value || '');
          return {
            un: g('2'), tipo: g('3'),
            emiIni: g('9'), emiFim: g('10'),
            autIni: g('11'), autFim: g('12'),
            liq: g('21'), arq: g('35'), compl: g('37'),
          };
        }"""
    )


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

    uni = (unidade or "SPO").strip().upper()[:3] or "SPO"
    tip = (tipo or "E").strip().upper()[:1] or "E"
    arq = "E"

    # Unidade / tipo
    _fill_id(popup, "2", uni, tab=True)
    _fill_id(popup, "3", tip, tab=True)

    # Período de EMISSÃO (obrigatório) — fill+Tab como 031/client
    _fill_id(popup, "9", ini, tab=True)
    _fill_id(popup, "10", fim, tab=True)

    # Limpa outros períodos (autorização/previsão/entrega) para o filtro não misturar
    for fid in ("11", "12", "13", "14", "15", "16"):
        try:
            _fill_id(popup, fid, "", tab=False)
        except Exception:
            pass

    _fill_id(popup, "19", "T")  # tipo frete: todos
    # T = todos exceto cancelados/anulados/substituídos (X devolve lixo histórico)
    _fill_id(popup, "21", "T")
    _fill_id(popup, "22", "T")  # entrega: todos
    _fill_id(popup, "35", arq, tab=True)
    try:
        _fill_id(popup, "37", "N")  # sem dados complementares
    except Exception:
        pass

    # 2ª passada: datas de emissão (SSW às vezes apaga ao mexer em liquidação/arquivo)
    _fill_id(popup, "9", ini, tab=True)
    _fill_id(popup, "10", fim, tab=True)
    for fid in ("11", "12"):
        try:
            _fill_id(popup, fid, "")
        except Exception:
            pass

    _safe_wait(popup, 250)
    read = _read_455_form(popup)
    status(f"[455] form {read}")

    if str(read.get("emiIni") or "").strip() != str(ini).strip() or str(
        read.get("emiFim") or ""
    ).strip() != str(fim).strip():
        status("[455] datas emissão divergentes — reforçando…")
        _fill_id(popup, "9", ini, tab=True)
        _fill_id(popup, "10", fim, tab=True)
        read = _read_455_form(popup)
        status(f"[455] form(retry) {read}")

    if str(read.get("autIni") or "").strip() or str(read.get("autFim") or "").strip():
        for fid in ("11", "12"):
            try:
                _fill_id(popup, fid, "")
            except Exception:
                pass
        status("[455] autorização limpa (2ª passada)")

    if str(read.get("arq") or "").upper() != "E":
        _fill_id(popup, "35", "E", tab=True)
        status("[455] arquivo forçado E")

    read = _read_455_form(popup)
    if str(read.get("emiIni") or "").strip() != str(ini).strip() or str(
        read.get("emiFim") or ""
    ).strip() != str(fim).strip():
        raise RuntimeError(
            f"455: período emissão não gravou no form "
            f"(lido={read.get('emiIni')}-{read.get('emiFim')} esperado={ini}-{fim})"
        )
    if str(read.get("liq") or "").strip().upper() != "T":
        status(f"[455] liquidação lida={read.get('liq')!r} — forçando T")
        _fill_id(popup, "21", "T")
        read = _read_455_form(popup)
    status(f"[455] form(ok) {read}")
    _safe_wait(popup, 200)


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
    login_user = str(getattr(getattr(client, "credentials", None), "user", "") or "").strip()
    return _baixar_via_fila_455(
        client,
        context,
        page,
        popup,
        dest_name,
        status,
        enqueue_t0=enqueue_t0,
        login_user=login_user,
    )


def _abrir_fila_455(client, context, page, status, popup=None):
    return _abrir_fila156(client, context, page, status, popup=popup, tag="455")


def _ler_jobs_455(fila) -> list[dict]:
    return _ler_jobs156(fila)


def _atualizar_fila(fila) -> None:
    _atualizar_fila156(fila)


def _baixar_via_fila_455(
    client,
    context,
    page,
    popup,
    dest_name: str,
    status,
    *,
    enqueue_t0: float,
    login_user: str = "",
) -> Path:
    """Acompanha 156 por usuário/opção/sequência até Baixar."""
    user = (login_user or "").strip() or str(
        getattr(getattr(client, "credentials", None), "user", "") or ""
    ).strip()
    fila, job = aguardar_baixar(
        client,
        context,
        page,
        popup,
        status,
        login_user=user,
        option_patterns=_455_OPTION_PATTERNS,
        enqueue_t0=enqueue_t0,
        tag="455",
        timeout_s=420.0,
        stuck_sem_dow_s=120.0,
    )
    seq = str(job.get("seq") or "")
    status(
        f"[455] DOW · seq={seq} · user={job.get('usuario')} · "
        f"{job.get('data_hora')} · {(job.get('opcao') or '')[:40]}"
    )
    dow_fails = 0
    last_err = ""
    while dow_fails < 6:
        try:
            try:
                fila.bring_to_front()
            except Exception:
                pass
            path = _clicar_dow_455(client, context, fila, job, dest_name, status)
            try:
                if fila and not fila.is_closed():
                    fila.close()
            except Exception:
                pass
            return path
        except Exception as err:
            last_err = str(err)
            dow_fails += 1
            status(f"[455] retry DOW #{dow_fails} ({last_err[:80]})")
            try:
                if fila and not fila.is_closed():
                    fila.reload(wait_until="domcontentloaded")
                else:
                    fila = _abrir_fila_455(client, context, page, status, popup=None)
            except Exception:
                try:
                    fila = _abrir_fila_455(client, context, page, status, popup=None)
                except Exception:
                    pass
            _safe_wait(fila, 2000)
            # revalida job na seq
            try:
                jobs = _ler_jobs_455(fila)
                hit = next((j for j in jobs if str(j.get("seq")) == seq), None)
                if hit and job_pronto_baixar(hit):
                    job = hit
            except Exception:
                pass
    raise RuntimeError(f"455: DOW falhou {dow_fails}x na seq={seq} ({last_err})")

def _clicar_dow_455(client, context, fila, job: dict, dest_name: str, status) -> Path:
    """Clica Baixar/DOW da seq (fila 156) com fallbacks fortes.

    SSW 455 usa ajaxEnvia('DOW' + seq) — muitas vezes NÃO dispara evento
    'download' do Playwright. Prioriza esse ajax + poll no disco / response.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    seq = str(job.get("seq") or "")
    seq_digits = re.sub(r"\D", "", seq)
    try:
        fila.bring_to_front()
    except Exception:
        pass
    _safe_wait(fila, 400)

    meta = find_baixar_meta(fila, seq)
    if not meta or not meta.get("ok"):
        why = (meta or {}).get("why") or "desconhecido"
        if why == "ainda_processando" or (meta or {}).get("interromper"):
            status(f"[455] ainda Interromper/gerando · seq={seq} — esperando Baixar…")
            meta = esperar_meta_baixar(fila, seq, status, tag="455", timeout_s=180.0)
        if not meta or not meta.get("ok"):
            why = (meta or {}).get("why") or "desconhecido"
            if why == "ainda_processando":
                raise RuntimeError("455: seq ainda processando — não clicou Baixar")
            raise RuntimeError(f"455: Baixar da seq={seq} não encontrado ({why})")

    best = meta.get("best") or {}
    status(
        f"[455] Baixar meta · tag={best.get('tag')} · why={meta.get('why')} · "
        f"txt={(best.get('text') or '')[:20]} · "
        f"onclick={(best.get('onclick') or '')[:70]}"
    )

    def _write_body(body: bytes, hint: str = "") -> Path | None:
        if not body or len(body) < 64:
            return None
        head = body[:200].lower()
        if b"<html" in head or b"<!doctype" in head:
            return None
        suffix = _sniff_455_suffix(body, hint)
        if suffix == ".html":
            return None
        stem = Path(dest_name).stem
        dest = Path(client.download_dir) / f"{stem}{suffix}"
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{int(time.time())}{dest.suffix}")
        dest.write_bytes(body)
        return dest

    # href direto → fetch autenticado
    href = str(best.get("href") or "").strip()
    if href and href not in {"#", ""} and not href.lower().startswith("javascript:"):
        try:
            abs_url = href
            if href.startswith("/"):
                abs_url = "https://sistema.ssw.inf.br" + href
            elif not href.lower().startswith("http"):
                abs_url = "https://sistema.ssw.inf.br/bin/" + href.lstrip("./")
            status(f"[455] fetch href · {abs_url[:90]}")
            body = context.request.get(abs_url, timeout=90000).body()
            got = _write_body(body, abs_url)
            if got:
                return got
        except Exception as err:
            status(f"[455] fetch href falhou: {err}")

    # URL embutida no onclick
    oc = str(best.get("onclick") or "")
    m_url = re.search(
        r"""['"]((?:https?:)?//[^'"]+\.(?:xlsx|xls|csv|sswweb)[^'"]*)['"]""",
        oc,
        re.I,
    ) or re.search(
        r"""['"](/bin/[^'"]+)['"]""",
        oc,
        re.I,
    )
    if m_url:
        try:
            u = m_url.group(1)
            if u.startswith("//"):
                u = "https:" + u
            elif u.startswith("/"):
                u = "https://sistema.ssw.inf.br" + u
            status(f"[455] fetch onclick-url · {u[:90]}")
            body = context.request.get(u, timeout=90000).body()
            got = _write_body(body, u)
            if got:
                return got
        except Exception as err:
            status(f"[455] fetch onclick-url: {err}")

    before_files = {
        p.name: p.stat().st_mtime
        for p in Path(client.download_dir).glob("*")
        if p.is_file()
    }
    captured_bodies: list[tuple[str, bytes]] = []

    def _on_response(resp) -> None:
        try:
            url = str(resp.url or "")
            headers = resp.headers or {}
            ct = str(headers.get("content-type") or headers.get("Content-Type") or "").lower()
            cd = str(
                headers.get("content-disposition") or headers.get("Content-Disposition") or ""
            ).lower()
            look = (url + " " + ct + " " + cd).lower()
            if not any(
                x in look
                for x in (
                    ".sswweb",
                    ".xlsx",
                    ".xls",
                    ".csv",
                    "octet-stream",
                    "spreadsheet",
                    "excel",
                    "attachment",
                    "dow",
                )
            ):
                return
            if resp.status and int(resp.status) >= 400:
                return
            body = resp.body()
            if body and len(body) >= 64:
                captured_bodies.append((url, body))
        except Exception:
            pass

    try:
        context.on("response", _on_response)
    except Exception:
        pass

    def _poll_new_file(wait_s: float = 3.0) -> Path | None:
        deadline = time.time() + wait_s
        stem = Path(dest_name).stem
        while time.time() < deadline:
            for url, body in list(captured_bodies):
                got = _write_body(body, url)
                if got:
                    return got
            for p in Path(client.download_dir).glob("*"):
                if not p.is_file():
                    continue
                mtime = p.stat().st_mtime
                if p.name not in before_files or mtime > before_files.get(p.name, 0) + 0.2:
                    if p.stat().st_size > 64 and p.suffix.lower() in {
                        ".xlsx",
                        ".xls",
                        ".csv",
                        ".sswweb",
                        ".zip",
                        "",
                    }:
                        try:
                            return _normalize_455_path(p, preferred_stem=stem)
                        except Exception:
                            return p
            time.sleep(0.25)
        return None

    def _trigger(prefer_ajax: bool = True) -> str:
        return str(
            fila.evaluate(
                """({ seq, preferAjax }) => {
                  const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                  const want = String(seq || '').replace(/\\D/g, '');
                  const wantNum = parseInt(want, 10) || 0;
                  // Forma real do SSW 455: ajaxEnvia('DOW494628') — 1 argumento
                  if (preferAjax && typeof ajaxEnvia === 'function' && want) {
                    try { ajaxEnvia('DOW' + want); return 'ajax-DOW-concat'; } catch (e0) {}
                    try { ajaxEnvia('DOW' + wantNum); return 'ajax-DOW-concat-n'; } catch (e1) {}
                  }
                  for (const tr of document.querySelectorAll('tr')) {
                    const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
                    const s = (cells[0] || '').replace(/\\D/g, '');
                    if (s !== want) continue;
                    try {
                      const first = tr.querySelector('td');
                      if (first) first.click();
                    } catch (eSel) {}
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
                      if (/dow\\d+|ajaxenvia\\s*\\(\\s*['\"]dow/i.test(blob)) score += 40;
                      if (/\\bdow\\b|baixar|download\\(|\\.xlsx|\\.csv|\\.sswweb|arquivo/.test(blob)) score += 15;
                      if (score > 0) pick.push({ a, score, onclick, href });
                    }
                    pick.sort((x, y) => y.score - x.score);
                    if (pick.length) {
                      const el = pick[0].a;
                      const oc = pick[0].onclick || '';
                      try { el.scrollIntoView({ block: 'center' }); } catch (eS) {}
                      // Se o onclick já é ajaxEnvia('DOW'+seq), prefira eval direto
                      const m = oc.match(/ajaxEnvia\\s*\\(\\s*['\"]DOW(\\d+)['\"]\\s*\\)/i);
                      if (m && typeof ajaxEnvia === 'function') {
                        try { ajaxEnvia('DOW' + m[1]); return 'ajax-from-onclick'; } catch (eA) {}
                      }
                      try { el.click(); return 'click'; } catch (e1) {}
                      if (oc) {
                        try { (function(){ eval(oc); })(); return 'eval-onclick'; } catch (e2) {}
                      }
                      return 'click-fail';
                    }
                    for (const td of Array.from(tr.querySelectorAll('td'))) {
                      const t = norm(td.innerText || '');
                      if (!(/^(dow|baixar)$/i.test(t) || (t.length <= 10 && /\\b(dow|baixar)\\b/i.test(t)))) continue;
                      const child = td.querySelector('a, img, input, button, font, b, span') || td;
                      try {
                        let el = child;
                        while (el && el !== tr) {
                          const oc2 = el.getAttribute && el.getAttribute('onclick');
                          if (oc2) {
                            try { (function(){ eval(oc2); })(); return 'eval-parent'; } catch (eP) {}
                          }
                          el = el.parentElement;
                        }
                        child.click();
                        return 'td-baixar';
                      } catch (e3) {}
                    }
                    if (typeof ajaxEnvia === 'function') {
                      try { ajaxEnvia('DOW' + want); return 'ajax-DOW-concat'; } catch (e4) {}
                      try { ajaxEnvia('DOW', wantNum); return 'ajax-DOW-num'; } catch (e5) {}
                      try { ajaxEnvia('DOW', want); return 'ajax-DOW'; } catch (e6) {}
                    }
                    return 'sem_link';
                  }
                  return 'seq_sumiu';
                }""",
                {"seq": seq_digits or seq, "preferAjax": prefer_ajax},
            )
            or ""
        )

    # 1) ajaxEnvia('DOW'+seq) + poll disco/response (caminho mais confiável)
    try:
        how = _trigger(prefer_ajax=True)
        status(f"[455] clique DOW={how}")
        if how in {"seq_sumiu", "sem_link", "click-fail", ""}:
            raise RuntimeError(f"trigger falhou ({how})")
        got = _poll_new_file(18.0)
        if got:
            status(f"[455] arquivo no disco · {got.name}")
            return got
    except RuntimeError:
        raise
    except Exception as err:
        status(f"[455] ajax/poll: {err}")

    # 2) evento download (alguns ambientes ainda usam)
    try:
        with context.expect_event("download", timeout=12000) as di:
            how = _trigger(prefer_ajax=False)
            status(f"[455] clique(download) DOW={how}")
            if how in {"seq_sumiu", "sem_link", "click-fail", ""}:
                raise RuntimeError(f"trigger falhou ({how})")
        saved = client._save_download(di.value, dest_name)
        return _normalize_455_path(saved, preferred_stem=Path(dest_name).stem)
    except PlaywrightTimeoutError:
        status("[455] sem evento download — nova aba / poll…")
        got = _poll_new_file(4.0)
        if got:
            status(f"[455] arquivo no disco · {got.name}")
            return got
    except RuntimeError:
        raise
    except Exception as err:
        status(f"[455] download context: {err}")

    # 3) nova aba / popup
    pages_before = list(context.pages)
    new_page = None
    try:
        with context.expect_page(timeout=10000) as pi:
            how = _trigger(prefer_ajax=True)
            status(f"[455] clique(aba) DOW={how}")
        new_page = pi.value
    except PlaywrightTimeoutError:
        after = [p for p in context.pages if p not in pages_before]
        if after:
            new_page = after[-1]

    if new_page is not None:
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        try:
            with new_page.expect_download(timeout=20000) as di:
                try:
                    new_page.wait_for_load_state("load", timeout=5000)
                except Exception:
                    pass
            path = client._save_download(di.value, dest_name)
            try:
                new_page.close()
            except Exception:
                pass
            return _normalize_455_path(path, preferred_stem=Path(dest_name).stem)
        except PlaywrightTimeoutError:
            try:
                url = new_page.url or ""
                status(f"[455] aba · {url[:90]}")
                if url and not url.startswith("about:") and "blank" not in url.lower():
                    body = context.request.get(url, timeout=90000).body()
                    got = _write_body(body, url)
                    if got:
                        try:
                            new_page.close()
                        except Exception:
                            pass
                        return got
            except Exception as err:
                status(f"[455] fetch aba: {err}")
            try:
                new_page.close()
            except Exception:
                pass
        got = _poll_new_file(3.0)
        if got:
            return got

    # 4) locator Playwright na linha
    try:
        row = fila.locator("tr").filter(has_text=re.compile(rf"^\s*{re.escape(seq)}"))
        link = row.locator(
            "a[onclick], a[href], img[onclick], td:has-text('Baixar'), td:has-text('DOW')"
        ).first
        with context.expect_event("download", timeout=15000) as di:
            link.click(timeout=5000, force=True)
            status("[455] clique DOW=locator")
        return _normalize_455_path(
            client._save_download(di.value, dest_name),
            preferred_stem=Path(dest_name).stem,
        )
    except Exception as err:
        status(f"[455] locator: {err}")
        got = _poll_new_file(4.0)
        if got:
            return got

    # 5) último poll após responses capturadas
    got = _poll_new_file(6.0)
    if got:
        return got

    raise RuntimeError(f"455: DOW não gerou download (seq={seq})")
