"""Dump ao vivo: 073 → Arquivo Excel → fila 156 (ssw1440).

Uso:
  py -3 tools_dump_73_fila.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from config import CACHE_DIR, load_credentials, load_settings
from dates import periodo_mes_ate_hoje, to_ssw_ddmmyy
from ssw_073 import JOBS_073, _clicar_excel_73, _preencher_73, _reopen_73
from ssw_client import AceSswClient

OUT = CACHE_DIR / "dump_73_fila.json"
HTML = CACHE_DIR / "dump_73_fila.html"
FORM = CACHE_DIR / "dump_73_form.json"


def _ler_jobs(fila) -> list[dict]:
    return fila.evaluate(
        """() => {
          const norm = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
          const jobs = [];
          for (const tr of Array.from(document.querySelectorAll('tr'))) {
            const cells = Array.from(tr.querySelectorAll('td')).map(td => norm(td.innerText));
            if (cells.length < 3) continue;
            const seq = (cells[0] || '').replace(/\\D/g, '');
            if (!seq || seq.length < 3) continue;
            const blob = cells.join(' | ');
            const links = Array.from(tr.querySelectorAll('a, img')).map(a => ({
              text: norm(a.textContent || a.alt || a.title || ''),
              href: String(a.getAttribute('href') || ''),
              onclick: String(a.getAttribute('onclick') || '').slice(0, 200),
            }));
            jobs.push({
              seq,
              cells: cells.slice(0, 10),
              blob: blob.slice(0, 240),
              links,
              is0332: /0332|073|ctrb|ssw0332/i.test(blob + ' ' + JSON.stringify(links)),
              hasDow: links.some(l => /\\bdow\\b|download|\\.xlsx|\\.csv|sswweb|baixar/i.test(
                (l.text + ' ' + l.href + ' ' + l.onclick).toLowerCase()
              )),
            });
          }
          return {
            url: location.href,
            title: document.title || '',
            nTr: document.querySelectorAll('tr').length,
            body: (document.body && document.body.innerText || '').slice(0, 2500),
            jobs,
          };
        }"""
    )


def main() -> None:
    creds = load_credentials()
    cfg = load_settings()
    ini_ui, fim_ui = periodo_mes_ate_hoje()
    ini, fim = to_ssw_ddmmyy(ini_ui), to_ssw_ddmmyy(fim_ui)
    job = JOBS_073[0]  # F + A
    client = AceSswClient(
        ini_ui,
        fim_ui,
        credentials=creds,
        settings=cfg,
        keep_open=True,
        headless=False,
        on_status=print,
    )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=80)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(45000)
        page.on("dialog", lambda d: d.accept())
        context.on("page", lambda pg: pg.on("dialog", lambda d: d.accept()))

        client._login(page)
        client._ensure_unit(page)
        client._patch_blank_popup_form(page)

        popup = _reopen_73(client, page, None)
        _preencher_73(
            popup,
            ini=ini,
            fim=fim,
            tipo=job["tipo"],
            unidade="SPO",
            propriedade=job["propriedade"],
            operacao="T",
            considerar="T",
            on_status=print,
            job_key=job["key"],
        )
        FORM.write_text(
            json.dumps(
                popup.evaluate(
                    """() => {
                      const inputs = Array.from(document.querySelectorAll('input')).map(el => ({
                        id: el.id, name: el.name, value: el.value, maxLength: el.maxLength
                      }));
                      const links = Array.from(document.querySelectorAll('a,img,button')).slice(0,60).map(a => ({
                        text: ((a.innerText||a.textContent||a.alt||'')+'').trim().slice(0,80),
                        onclick: (a.getAttribute('onclick')||'').slice(0,180),
                        id: a.id||''
                      }));
                      return { url: location.href, title: document.title, inputs, links,
                        body: (document.body.innerText||'').slice(0,2000) };
                    }"""
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("form dump →", FORM)

        print("clicando Arquivo Excel…")
        clicked = _clicar_excel_73(popup)
        print("clique=", clicked)
        time.sleep(3)

        # abre fila
        fila = None
        try:
            with context.expect_page(timeout=15000) as pi:
                opened = popup.evaluate(
                    """() => {
                      const links = Array.from(document.querySelectorAll('a,button,span'));
                      for (const a of links) {
                        const t = ((a.innerText||a.textContent||'')+'').toLowerCase();
                        if (t.includes('ver fila')) { a.click(); return 'ver fila'; }
                      }
                      if (typeof ajaxEnvia === 'function') {
                        try { ajaxEnvia('', 1, 'ssw1440'); return 'ajax1440'; } catch(_){}
                      }
                      return '';
                    }"""
                )
                print("fila open=", opened)
                if opened:
                    fila = pi.value
        except Exception as err:
            print("expect_page:", err)
            fila = None
        if fila is None:
            fila = context.new_page()
            fila.goto("https://sistema.ssw.inf.br/bin/ssw1440", wait_until="domcontentloaded")
            print("fila goto ssw1440")

        fila.wait_for_timeout(1500)
        try:
            fila.evaluate(
                """() => { if (typeof ajaxEnvia==='function') try{ajaxEnvia('ATU',0)}catch(_){}}"""
            )
        except Exception:
            pass
        fila.wait_for_timeout(1000)

        info = _ler_jobs(fila)
        # poll um pouco
        for i in range(8):
            time.sleep(2)
            try:
                fila.evaluate(
                    """() => { if (typeof ajaxEnvia==='function') try{ajaxEnvia('ATU',0)}catch(_){}}"""
                )
            except Exception:
                pass
            info = _ler_jobs(fila)
            n0332 = sum(1 for j in info.get("jobs") or [] if j.get("is0332"))
            ndow = sum(1 for j in info.get("jobs") or [] if j.get("hasDow") and j.get("is0332"))
            print(f"poll {i}: jobs={len(info.get('jobs') or [])} 0332={n0332} dow0332={ndow} url={info.get('url')}")
            if ndow:
                break

        OUT.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            HTML.write_text(fila.content(), encoding="utf-8", errors="replace")
        except Exception as err:
            HTML.write_text(str(err), encoding="utf-8")
        print("wrote", OUT, HTML)
        print("title:", info.get("title"))
        for j in (info.get("jobs") or [])[:8]:
            print(" job", j.get("seq"), "0332=", j.get("is0332"), "dow=", j.get("hasDow"), j.get("cells"))
        browser.close()
        print("dump ok")


if __name__ == "__main__":
    main()
