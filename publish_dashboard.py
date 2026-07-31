from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config import DASHBOARD_DIR, BASE_DIR, AceSettings, load_settings
from parser_ssw0157 import COLETAS_CSV, RESUMO_CSV
from parser_ssw103 import COLETAS_103_CSV, RESUMO_103_CSV
from parser_ssw0146 import ENTREGAS_36_CSV, ROMANEIOS_36_CSV, RESUMO_36_CSV

StatusCallback = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _copy_cache_to_dashboard() -> dict[str, str]:
    data_dir = DASHBOARD_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for src, name in (
        (COLETAS_CSV, "coletas.csv"),
        (RESUMO_CSV, "resumo_diario.csv"),
        (COLETAS_103_CSV, "coletas_103.csv"),
        (RESUMO_103_CSV, "resumo_103.csv"),
        (ENTREGAS_36_CSV, "entregas_36.csv"),
        (ROMANEIOS_36_CSV, "romaneios_36.csv"),
        (RESUMO_36_CSV, "resumo_36.csv"),
    ):
        dest = data_dir / name
        if src.exists():
            shutil.copy2(src, dest)
            paths[name] = str(dest)
        elif not dest.exists():
            with dest.open("w", encoding="utf-8-sig", newline="") as fh:
                if name == "resumo_diario.csv":
                    fh.write("data_cadastro,total_coletas,cadastrada,comandada,coletada,cancelada\n")
                elif name == "resumo_103.csv":
                    fh.write("periodo,total,parado,em_rota,realizada,cancelada,outro\n")
                elif name == "coletas_103.csv":
                    fh.write(
                        "coleta_id,situacao_atual,status_ace,hora,hora_antes_meio_dia,"
                        "cadastrada_ref_AI,placa,placa_carreta,motorista\n"
                    )
                elif name == "entregas_36.csv":
                    fh.write(
                        "ctrc_id,romaneio,situacao,status_ace,placa,placa_carreta,"
                        "motorista,destinatario,ocorrencia,data_ocorrencia,excluido,motivo_exclusao\n"
                    )
                elif name == "romaneios_36.csv":
                    fh.write(
                        "romaneio,placa,placa_carreta,motorista,total,realizada,em_rota,pendencia,pct\n"
                    )
                elif name == "resumo_36.csv":
                    fh.write("periodo,total,realizada,em_rota,pendencia,excluido\n")
                else:
                    fh.write("coleta_id,coleta,situacao_atual\n")
            paths[name] = str(dest)
    meta = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "files": paths,
    }
    (data_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return paths


def ensure_dashboard_files() -> None:
    """Garante HTML/JS do dashboard e CSVs locais (nao sobrescreve index real)."""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    (DASHBOARD_DIR / "data").mkdir(parents=True, exist_ok=True)
    index = DASHBOARD_DIR / "index.html"
    # Nunca sobrescrever o dashboard BINHO com o HTML legado embutido.
    if not index.exists():
        index.write_text(_DASHBOARD_HTML, encoding="utf-8")
    _copy_cache_to_dashboard()


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ACE · Coletas</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root { color-scheme: dark; font-family: Segoe UI, system-ui, sans-serif; }
    body { margin: 0; background: #0b1220; color: #e2e8f0; }
    header { padding: 24px 28px 8px; }
    h1 { margin: 0 0 6px; font-size: 28px; }
    .sub { color: #94a3b8; margin: 0; }
    .grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; padding: 16px 28px 28px; }
    .card { background: #020617; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; }
    .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 0 28px 8px; }
    .kpi { background: #020617; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; }
    .kpi b { display: block; font-size: 22px; margin-top: 4px; }
    .muted { color: #94a3b8; font-size: 12px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid #1e293b; padding: 8px; text-align: left; }
    th { color: #67e8f9; }
    @media (max-width: 900px) {
      .grid, .kpis { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>ACE · Analisador Coleta</h1>
    <p class="sub" id="updated">Carregando dados...</p>
  </header>
  <section class="kpis">
    <div class="kpi"><span class="muted">Total</span><b id="kTotal">0</b></div>
    <div class="kpi"><span class="muted">Coletadas</span><b id="kColetada">0</b></div>
    <div class="kpi"><span class="muted">Comandadas</span><b id="kComandada">0</b></div>
    <div class="kpi"><span class="muted">Canceladas</span><b id="kCancelada">0</b></div>
  </section>
  <section class="grid">
    <div class="card">
      <h3>Situações por dia de cadastro</h3>
      <canvas id="chartResumo" height="140"></canvas>
    </div>
    <div class="card">
      <h3>Distribuição atual</h3>
      <canvas id="chartPizza" height="140"></canvas>
    </div>
    <div class="card" style="grid-column: 1 / -1;">
      <h3>Últimas coletas</h3>
      <div style="overflow:auto; max-height:320px;">
        <table>
          <thead>
            <tr>
              <th>Coleta</th><th>Situação</th><th>Cadastrada</th><th>Comandada</th>
              <th>Coletada</th><th>Motorista</th><th>Destino</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </div>
  </section>
  <script>
    async function loadCsv(path) {
      const res = await fetch(path + '?t=' + Date.now());
      const text = await res.text();
      const lines = text.trim().split(/\\r?\\n/);
      if (lines.length < 2) return [];
      const headers = lines[0].split(',');
      return lines.slice(1).map(line => {
        const cols = [];
        let cur = '', q = false;
        for (let i = 0; i < line.length; i++) {
          const ch = line[i];
          if (ch === '"') { q = !q; continue; }
          if (ch === ',' && !q) { cols.push(cur); cur = ''; continue; }
          cur += ch;
        }
        cols.push(cur);
        const obj = {};
        headers.forEach((h, i) => obj[h] = (cols[i] || '').trim());
        return obj;
      });
    }

    function sum(rows, key) {
      return rows.reduce((a, r) => a + (parseInt(r[key] || '0', 10) || 0), 0);
    }

    async function main() {
      let meta = {};
      try { meta = await (await fetch('data/meta.json?t=' + Date.now())).json(); } catch (e) {}
      document.getElementById('updated').textContent =
        'Atualizado em: ' + (meta.updated_at || 'cache local / anterior');

      const resumo = await loadCsv('data/resumo_diario.csv');
      const coletas = await loadCsv('data/coletas.csv');

      const total = sum(resumo, 'total') || coletas.length;
      const coletada = sum(resumo, 'coletada');
      const comandada = sum(resumo, 'comandada');
      const cancelada = sum(resumo, 'cancelada');
      const cadastrada = sum(resumo, 'cadastrada');
      document.getElementById('kTotal').textContent = total;
      document.getElementById('kColetada').textContent = coletada;
      document.getElementById('kComandada').textContent = comandada;
      document.getElementById('kCancelada').textContent = cancelada;

      const labels = resumo.map(r => r.data_cadastro);
      new Chart(document.getElementById('chartResumo'), {
        type: 'bar',
        data: {
          labels,
          datasets: [
            { label: 'Coletada', data: resumo.map(r => +r.coletada || 0), backgroundColor: '#22c55e' },
            { label: 'Comandada', data: resumo.map(r => +r.comandada || 0), backgroundColor: '#38bdf8' },
            { label: 'Cadastrada', data: resumo.map(r => +r.cadastrada || 0), backgroundColor: '#f59e0b' },
            { label: 'Cancelada', data: resumo.map(r => +r.cancelada || 0), backgroundColor: '#ef4444' },
          ]
        },
        options: { responsive: true, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } }
      });

      new Chart(document.getElementById('chartPizza'), {
        type: 'doughnut',
        data: {
          labels: ['Coletada', 'Comandada', 'Cadastrada', 'Cancelada'],
          datasets: [{ data: [coletada, comandada, cadastrada, cancelada],
            backgroundColor: ['#22c55e', '#38bdf8', '#f59e0b', '#ef4444'] }]
        },
        options: { plugins: { legend: { position: 'bottom' } } }
      });

      const tbody = document.getElementById('tbody');
      coletas.slice(-100).reverse().forEach(c => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${c.coleta_id || ''}</td><td>${c.situacao_atual || ''}</td>
          <td>${c.cadastrada_data || ''} ${c.cadastrada_hora || ''}</td>
          <td>${c.comandada_data || ''} ${c.comandada_hora || ''}</td>
          <td>${c.coletada_data || ''} ${c.coletada_hora || ''}</td>
          <td>${c.motorista || ''}</td><td>${c.dest_cidade || c.dest || ''}</td>`;
        tbody.appendChild(tr);
      });
    }
    main().catch(err => {
      document.getElementById('updated').textContent = 'Usando ultimo cache disponivel. ' + err;
    });
  </script>
</body>
</html>
"""


def publish_dashboard(
    settings: AceSettings | None = None,
    *,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """
    Copia CSV para dashboard/ e, se configurado, faz git commit/push.
    Em falha de push, mantém o ultimo HTML/CSV ja publicado.
    """
    status = on_status or _noop
    cfg = settings or load_settings()
    ensure_dashboard_files()
    paths = _copy_cache_to_dashboard()
    result: dict[str, Any] = {"ok": True, "local": paths, "pushed": False}

    if not cfg.enable_github_publish:
        status("Dashboard local atualizado (publish GitHub desabilitado).")
        result["skipped_push"] = True
        return result

    token = os.environ.get(cfg.github_token_env or "GH_TOKEN", "").strip()
    if not cfg.github_repo:
        status("Dashboard: github_repo nao configurado — so arquivo local.")
        result["skipped_push"] = True
        return result
    if not token:
        status(f"Dashboard: token {cfg.github_token_env} ausente — so arquivo local.")
        result["skipped_push"] = True
        return result

    try:
        dash = DASHBOARD_DIR
        # Se dashboard estiver dentro do repo ACE, faz commit so dos arquivos do dashboard
        repo_root = BASE_DIR
        env = os.environ.copy()
        # remote com token
        remote = f"https://x-access-token:{token}@github.com/{cfg.github_repo}.git"
        branch = cfg.github_branch or "main"

        def run(cmd: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(
                cmd,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

        run(["git", "add", "dashboard"])
        msg = f"chore(dashboard): atualiza dados ACE {datetime.now():%Y-%m-%d %H:%M}"
        commit = run(["git", "commit", "-m", msg])
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
            # pode nao ser repo git — tenta push de pasta isolada via gh?
            result["commit_stderr"] = commit.stderr
        # configura remote temporario se necessario
        remotes = run(["git", "remote"])
        if "origin" not in remotes.stdout:
            run(["git", "remote", "add", "origin", remote])
        else:
            run(["git", "remote", "set-url", "origin", remote])
        push = run(["git", "push", "-u", "origin", branch])
        if push.returncode == 0:
            result["pushed"] = True
            status("Dashboard publicado no GitHub.")
        else:
            result["ok"] = True  # local ok
            result["push_error"] = push.stderr
            status(f"Push GitHub falhou (mantendo pagina anterior): {push.stderr[:200]}")
        return result
    except Exception as error:  # noqa: BLE001
        result["ok"] = True
        result["error"] = str(error)
        status(f"Publish dashboard falhou (cache local ok): {error}")
        return result
