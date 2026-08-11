"""
Teste interativo do relatorio 36 (ssw0146).

1) Login SSW + abre opcao 36 (browser visivel)
2) Mostra o que a automacao ACE preencheria (seg=sex..hoje / demais=D-1..hoje, Excel=S, unidade)
3) Aplica o preenchimento automatico
4) Deixa o browser aberto para voce conferir / corrigir campos
5) A cada 20s re-dumpa os valores atuais em data/cache/dump_36_live.json
6) NAO clica em gerar sozinho — voce valida e, se quiser, clica no ► do periodo

Uso:
  py -3 tools_test_36.py
  py -3 tools_test_36.py --gerar     # tambem tenta baixar (apos 15s de inspecao)
  py -3 tools_test_36.py --unidade SPO
"""

from __future__ import annotations

import _root  # noqa: F401

import argparse
import json
import sys
import time
from datetime import datetime

from config import CACHE_DIR, load_credentials, load_settings
from dates import format_period, periodo_36_ontem_hoje, to_ssw_ddmmyy
from ssw_client import AceSswClient, login_unit, parse_coleta_units

OUT = CACHE_DIR / "dump_36_fields.json"
LIVE = CACHE_DIR / "dump_36_live.json"
HTML_OUT = CACHE_DIR / "dump_36.html"


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def _snapshot(popup) -> dict:
    return popup.evaluate(
        """() => {
          const ids = [
            't_sigla_rom','t_nro_rom','t_cod_barras_rom','t_ciot',
            't_ser_mdfe','t_nro_mdfe','t_excel','t_unidade',
            't_placa_veic','t_cpf_motorista','t_dt_ini','t_dt_fin'
          ];
          const values = {};
          ids.forEach(id => {
            const el = document.getElementById(id);
            values[id] = el ? (el.value || '') : null;
          });
          const btn = document.getElementById('btn_env_periodo');
          return {
            url: location.href,
            title: document.title || '',
            values,
            btn_periodo: btn ? {
              id: btn.id,
              onclick: (btn.getAttribute('onclick') || '').slice(0, 120),
            } : null,
            bodyHint: (document.body && document.body.innerText || '').slice(0, 600),
          };
        }"""
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Teste visual preenchimento SSW 36")
    parser.add_argument("--unidade", default="SPO", help="Sigla (36 sempre SPO)")
    parser.add_argument(
        "--gerar",
        action="store_true",
        help="Apos 15s de inspecao, clica REL2 e tenta baixar o Excel",
    )
    parser.add_argument(
        "--minutos",
        type=int,
        default=10,
        help="Minutos com browser aberto (default 10)",
    )
    parser.add_argument(
        "--sem-preencher",
        action="store_true",
        help="Nao aplica preenchimento automatico — so abre a tela",
    )
    args = parser.parse_args(argv)

    creds = load_credentials()
    settings = load_settings()
    ini_ui, fim_ui = periodo_36_ontem_hoje()
    ini = to_ssw_ddmmyy(ini_ui)
    fim = to_ssw_ddmmyy(fim_ui)
    units = parse_coleta_units(creds.unit)
    unidade = (args.unidade or login_unit(creds.unit) or (units[0] if units else "")).strip().upper()

    _safe_print("=" * 60)
    _safe_print("TESTE SSW 36 · Relacao romaneios/CTRCs (ssw0146)")
    _safe_print("=" * 60)
    _safe_print(f"Periodo ACE (36): {format_period(ini_ui, fim_ui)}  =>  {ini} a {fim}")
    _safe_print(f"Excel: S")
    _safe_print(f"Unidade neste teste: {unidade or '(vazia = TODAS)'}")
    _safe_print(f"Unidades config: {','.join(units) or '*'}")
    _safe_print(f"Gerar download automatico: {'SIM' if args.gerar else 'NAO (so inspecao)'}")
    _safe_print("-" * 60)
    _safe_print("Campos que a automacao usa:")
    _safe_print("  t_excel     = S")
    _safe_print(f"  t_unidade   = {unidade or ''}")
    _safe_print(f"  t_dt_ini    = {ini}")
    _safe_print(f"  t_dt_fin    = {fim}")
    _safe_print("  limpa: romaneio/ciot/mdfe/placa/cpf")
    _safe_print("  botao: #btn_env_periodo  ajaxEnvia('REL2', 1)")
    _safe_print("-" * 60)

    client = AceSswClient(
        ini_ui,
        fim_ui,
        credentials=creds,
        settings=settings,
        keep_open=True,
        headless=False,
        on_status=_safe_print,
        clean_downloads=False,
    )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(30000)

        client._login(page)
        client._ensure_unit(page)
        client._patch_blank_popup_fix(page)
        popup = client._open_menu_option(
            page,
            "36",
            markers=(
                "romaneio",
                "entrega",
                "periodo",
                "unidade",
                "excel",
                "0146",
                "36",
            ),
        )

        before = _snapshot(popup)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            json.dumps({"phase": "antes", **before}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        HTML_OUT.write_text(popup.content(), encoding="utf-8", errors="replace")
        _safe_print(f"Dump inicial: {OUT}")
        _safe_print(f"Valores ANTES: {before.get('values')}")

        if not args.sem_preencher:
            _safe_print("\n>>> Aplicando preenchimento automatico ACE...")
            client._preencher_tela_36(popup, unidade=unidade)
            after = _snapshot(popup)
            LIVE.write_text(
                json.dumps(
                    {
                        "phase": "apos_auto",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        **after,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            _safe_print(f"Valores DEPOIS do auto-fill: {after.get('values')}")
            _safe_print(f"Live dump: {LIVE}")
        else:
            _safe_print("\n>>> Sem auto-fill. Preencha manualmente na tela.")

        _safe_print("\n" + "=" * 60)
        _safe_print("BROWSER ABERTO — confira na tela do SSW:")
        _safe_print("  1) Excel = S")
        _safe_print(f"  2) Periodo = {ini} a {fim}")
        _safe_print(f"  3) Unidade = {unidade or '(vazio)'}")
        _safe_print("  4) Botao ► ao lado do periodo (Romaneios do periodo)")
        _safe_print("")
        _safe_print("Pode ALTERAR os campos a vontade.")
        _safe_print(f"A cada 20s salvo o estado em: {LIVE}")
        if args.gerar:
            _safe_print("Em 15s a automacao clica em gerar (REL2) e tenta baixar.")
        else:
            _safe_print("NAO vou clicar em gerar. Se quiser testar o arquivo, clique no ► voce mesmo.")
        _safe_print("=" * 60)

        if args.gerar:
            _safe_print("Aguardando 15s para voce olhar antes do download...")
            page.wait_for_timeout(15_000)
            try:
                with popup.expect_download(timeout=180000) as download_info:
                    client._clicar_gerar_36(popup)
                dest = client._save_download(
                    download_info.value,
                    f"teste_36_{ini}_{fim}_{datetime.now():%H%M%S}.sswweb",
                )
                _safe_print(f"DOWNLOAD OK: {dest}")
            except Exception as err:  # noqa: BLE001
                _safe_print(f"Download falhou/nao veio: {err}")
                _safe_print("Deixe o browser aberto e tente o ► manualmente.")

        # Loop de inspecao: re-dump periodico (usa popup; nao cai se fechar)
        deadline = time.time() + max(1, args.minutos) * 60
        tick = 0
        _safe_print("Loop de inspecao ativo. Feche o Chromium quando terminar.")
        while time.time() < deadline:
            try:
                popup.wait_for_timeout(20_000)
            except Exception:
                try:
                    page.wait_for_timeout(20_000)
                except Exception as err:
                    _safe_print(f"Browser fechado ({err}). Encerrando.")
                    break
            tick += 1
            try:
                if popup.is_closed():
                    _safe_print("Popup da 36 foi fechada. Encerrando.")
                    break
                snap = _snapshot(popup)
            except Exception as err:  # noqa: BLE001
                _safe_print(f"Popup fechou ou erro: {err}")
                break
            LIVE.write_text(
                json.dumps(
                    {
                        "phase": "live",
                        "tick": tick,
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        **snap,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            vals = snap.get("values") or {}
            _safe_print(
                f"[{tick}] excel={vals.get('t_excel')} un={vals.get('t_unidade')} "
                f"periodo={vals.get('t_dt_ini')}-{vals.get('t_dt_fin')}"
            )

        _safe_print("Encerrando teste (feche as janelas se ainda abertas).")
        try:
            if not browser.is_connected():
                return 0
            browser.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
