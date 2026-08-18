/* BINHO · Gestão multi-setor */
(function () {
  'use strict';

  const PARAMS = new URLSearchParams(location.search);
  let SETOR = String(PARAMS.get('setor') || 'distribuicao').toLowerCase();
  let REL = String(PARAMS.get('rel') || 'coleta').toLowerCase();
  if (REL === '36') REL = 'entrega';
  if (REL === '225') REL = 'agendamento';
  if (REL === '103' || REL === '50') REL = 'coleta';

  const STATE = {
    columns: [],
    rows: [],
    filtered: [],
    colFilters: {},
    busca: '',
    title: '',
  };

  const SECTOR_LABEL = {
    distribuicao: 'Distribuição',
    armazem: 'Armazém',
    pendencia: 'Pendência',
    contratacao: 'Contratação',
    emissao: 'Emissão',
    mapa: 'Mapa',
  };

  const REL_LABEL = {
    coleta: 'Coleta',
    entrega: 'Entrega',
    agendamento: 'Agendamento',
  };

  function esc(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function parseCsvText(text) {
    const lines = String(text || '').replace(/^\uFEFF/, '').split(/\r?\n/).filter((l) => l.length);
    if (!lines.length) return [];
    const delim = lines[0].includes(';') && lines[0].split(';').length >= lines[0].split(',').length
      ? ';'
      : ',';
    const parseLine = (line) => {
      const out = [];
      let cur = '';
      let q = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch === '"') {
          if (q && line[i + 1] === '"') { cur += '"'; i++; }
          else q = !q;
        } else if (ch === delim && !q) {
          out.push(cur); cur = '';
        } else cur += ch;
      }
      out.push(cur);
      return out;
    };
    const headers = parseLine(lines[0]).map((h) => h.trim());
    return lines.slice(1).map((line) => {
      const cells = parseLine(line);
      const row = {};
      headers.forEach((h, i) => { row[h] = cells[i] != null ? String(cells[i]).trim() : ''; });
      return row;
    }).filter((r) => Object.values(r).some((v) => String(v || '').trim()));
  }

  async function loadCsv(path) {
    const url = `${path}${path.includes('?') ? '&' : '?'}t=${Date.now()}`;
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(`Falha ao ler ${path}`);
    return parseCsvText(await res.text());
  }

  async function loadJson(path) {
    const res = await fetch(`${path}?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`Falha ao ler ${path}`);
    return res.json();
  }

  function syncUrl() {
    const q = new URLSearchParams();
    q.set('setor', SETOR);
    if (SETOR === 'distribuicao') q.set('rel', REL);
    history.replaceState(null, '', `gestao.html?${q}`);
  }

  function updateChrome() {
    const title = SETOR === 'distribuicao'
      ? `Gestão · ${SECTOR_LABEL.distribuicao} · ${REL_LABEL[REL] || REL}`
      : `Gestão · ${SECTOR_LABEL[SETOR] || SETOR}`;
    STATE.title = title;
    document.getElementById('pageTitle').textContent = title;
    document.title = `BINHO · ${title}`;
    document.querySelectorAll('.sec-btn').forEach((b) => {
      b.classList.toggle('active', b.getAttribute('data-setor') === SETOR);
    });
    const relBox = document.getElementById('relBox');
    relBox.classList.toggle('show', SETOR === 'distribuicao');
    document.querySelectorAll('.rel-btn').forEach((b) => {
      b.classList.toggle('active', b.getAttribute('data-rel') === REL);
    });
  }

  function colDef(key, label, opts) {
    return { key, label: label || key, type: (opts && opts.type) || 'text', enum: (opts && opts.enum) || null };
  }

  async function loadDataset() {
    if (SETOR === 'distribuicao' && REL === 'coleta') {
      const rows = await loadCsv('data/coletas_103.csv');
      return {
        columns: [
          colDef('coleta_id', 'Coleta'),
          colDef('placa', 'Placa'),
          colDef('placa_carreta', 'Carreta'),
          colDef('motorista', 'Motorista'),
          colDef('status_ace', 'Status', { type: 'enum', enum: ['em_rota', 'realizada', 'parado', 'cancelada'] }),
          colDef('situacao_atual', 'Situação'),
          colDef('hora', 'Hora'),
        ],
        rows,
      };
    }
    if (SETOR === 'distribuicao' && REL === 'entrega') {
      const rows = await loadCsv('data/entregas_36.csv');
      return {
        columns: [
          colDef('ctrc_id', 'CTRC'),
          colDef('romaneio', 'Romaneio'),
          colDef('placa', 'Placa'),
          colDef('motorista', 'Motorista'),
          colDef('destinatario', 'Destinatário'),
          colDef('status_ace', 'Status', { type: 'enum', enum: ['em_rota', 'realizada', 'pendencia', 'excluido'] }),
          colDef('ocorrencia', 'Ocorrência'),
          colDef('data_ocorrencia', 'Data'),
          colDef('hora_ocorrencia', 'Hora'),
        ],
        rows: rows.filter((r) => String(r.excluido || '') !== '1'),
      };
    }
    if (SETOR === 'distribuicao' && REL === 'agendamento') {
      const rows = await loadCsv('data/agendamentos_225.csv');
      return {
        columns: [
          colDef('ctrc', 'CTRC'),
          colDef('remetente', 'Remetente'),
          colDef('destinatario', 'Destinatário'),
          colDef('destino', 'Destino'),
          colDef('peso', 'Peso'),
          colDef('volumes', 'Volumes'),
          colDef('frete', 'Frete'),
          colDef('agendado_em', 'Agendado em'),
          colDef('agendado_para', 'Agendado para'),
          colDef('status_ace', 'Status', { type: 'enum', enum: ['em_rota', 'parado', 'concluido'] }),
        ],
        rows,
      };
    }
    if (SETOR === 'armazem') {
      const rows = await loadCsv('data/armazem/veiculos_78.csv');
      return {
        columns: [
          colDef('origem', 'Origem'),
          colDef('cavalo', 'Cavalo'),
          colDef('carreta', 'Carreta'),
          colDef('manifesto', 'Manifesto'),
          colDef('peso', 'Peso'),
          colDef('status', 'Status'),
          colDef('atrasado', 'Atrasado'),
          colDef('saida', 'Saída'),
          colDef('chegada', 'Chegada'),
          colDef('tempo_descarga_min', 'Tempo desc. (min)'),
        ],
        rows,
      };
    }
    if (SETOR === 'pendencia') {
      const rows = await loadCsv('data/pendencia/pendencias_31.csv');
      return {
        columns: [
          colDef('ctrc', 'CTRC'),
          colDef('data_emissao', 'Emissão'),
          colDef('ultima_ocorrencia', 'Última ocorrência'),
          colDef('codigo', 'Código'),
          colDef('descricao_codigo', 'Descrição código'),
          colDef('descricao_ocorrencia', 'Descrição'),
          colDef('complemento_ocorrencia', 'Complemento'),
        ],
        rows,
      };
    }
    if (SETOR === 'contratacao') {
      let rows = [];
      try { rows = await loadCsv('data/contratacao/veiculos_073.csv'); } catch (_) {}
      if (!rows.length) {
        try { rows = await loadCsv('data/contratacao/ctrbs_073.csv'); } catch (_) {}
      }
      const keys = rows[0] ? Object.keys(rows[0]) : ['placa', 'motorista', 'frete', 'status'];
      return {
        columns: keys.map((k) => colDef(k, k)),
        rows,
      };
    }
    if (SETOR === 'emissao') {
      const rows = await loadCsv('data/emissao/expedidores_455.csv');
      return {
        columns: [
          colDef('nome', 'Expedidor'),
          colDef('nome_exibicao', 'Exibição'),
          colDef('qtd', 'Qtd'),
          colDef('pct', '%'),
        ],
        rows,
      };
    }
    if (SETOR === 'mapa') {
      const data = await loadJson('data/mapa/mapa_distribuicao.json');
      const veics = Array.isArray(data.veiculos) ? data.veiculos : [];
      const rows = [];
      veics.forEach((v) => {
        const ent = Array.isArray(v.paradas) ? v.paradas : [];
        const col = Array.isArray(v.paradas_coleta) ? v.paradas_coleta : [];
        const stops = [
          ...ent.map((p) => ({ ...p, _kind: 'E' })),
          ...col.map((p) => ({ ...p, _kind: 'C' })),
        ];
        if (!stops.length) {
          rows.push({
            placa: v.placa || '',
            motorista: v.motorista || '',
            tipo: v.tipo || '',
            kind: '—',
            seq: '',
            ctrc: '',
            cliente: '',
            status: '',
            peso: '',
            frete: '',
            servico: `${v.servico_feitas || 0}/${v.servico_total || 0}`,
          });
          return;
        }
        stops.forEach((p) => {
          rows.push({
            placa: v.placa || '',
            motorista: v.motorista || '',
            tipo: v.tipo || '',
            kind: p._kind,
            seq: p.seq || '',
            ctrc: p.ctrc || '',
            cliente: p.cliente || '',
            status: p.status_ace || p.status || '',
            peso: p.peso != null ? String(p.peso) : '',
            frete: p.frete != null ? String(p.frete) : '',
            servico: `${v.servico_feitas || 0}/${v.servico_total || 0}`,
          });
        });
      });
      return {
        columns: [
          colDef('placa', 'Placa'),
          colDef('motorista', 'Motorista'),
          colDef('tipo', 'Tipo veículo'),
          colDef('kind', 'E/C'),
          colDef('seq', 'Seq'),
          colDef('ctrc', 'CTRC'),
          colDef('cliente', 'Cliente'),
          colDef('status', 'Status'),
          colDef('peso', 'Peso'),
          colDef('frete', 'Frete'),
          colDef('servico', 'Serviço'),
        ],
        rows,
        meta: data.atualizado ? `Mapa atualizado ${data.atualizado}` : '',
      };
    }
    return { columns: [], rows: [] };
  }

  function applyFilters() {
    const q = String(STATE.busca || '').trim().toLowerCase();
    STATE.filtered = STATE.rows.filter((row) => {
      for (const col of STATE.columns) {
        const f = String(STATE.colFilters[col.key] || '').trim().toLowerCase();
        if (!f) continue;
        const val = String(row[col.key] ?? '').toLowerCase();
        if (!val.includes(f)) return false;
      }
      if (!q) return true;
      const blob = STATE.columns.map((c) => String(row[c.key] ?? '')).join(' ').toLowerCase();
      return blob.includes(q);
    });
  }

  function activeFiltersSummary() {
    const parts = [];
    if (STATE.busca) parts.push(`busca="${STATE.busca}"`);
    STATE.columns.forEach((c) => {
      const f = STATE.colFilters[c.key];
      if (f) parts.push(`${c.label}=${f}`);
    });
    return parts.length ? parts.join(' · ') : 'sem filtros';
  }

  function renderTable() {
    applyFilters();
    const head = document.getElementById('gestaoHead');
    const body = document.getElementById('gestaoBody');
    const empty = document.getElementById('gestaoEmpty');
    const count = document.getElementById('rowCount');
    count.textContent = `${STATE.filtered.length} de ${STATE.rows.length} linhas`;

    if (!STATE.columns.length) {
      head.innerHTML = '';
      body.innerHTML = '';
      empty.hidden = false;
      return;
    }
    empty.hidden = true;

    const ths = STATE.columns.map((c) => `<th>${esc(c.label)}</th>`).join('');
    const filters = STATE.columns.map((c) => {
      if (c.type === 'enum' && Array.isArray(c.enum) && c.enum.length) {
        const opts = [`<option value="">(todos)</option>`]
          .concat(c.enum.map((v) => {
            const sel = String(STATE.colFilters[c.key] || '') === v ? ' selected' : '';
            return `<option value="${esc(v)}"${sel}>${esc(v)}</option>`;
          }));
        return `<th><select data-col="${esc(c.key)}">${opts.join('')}</select></th>`;
      }
      const val = esc(STATE.colFilters[c.key] || '');
      return `<th><input type="search" data-col="${esc(c.key)}" value="${val}" placeholder="Filtrar…" /></th>`;
    }).join('');

    head.innerHTML = `<tr>${ths}</tr><tr class="filters">${filters}</tr>`;
    if (!STATE.filtered.length) {
      body.innerHTML = `<tr><td colspan="${STATE.columns.length}" class="muted">Nenhuma linha com os filtros atuais.</td></tr>`;
    } else {
      body.innerHTML = STATE.filtered.map((row) => {
        const tds = STATE.columns.map((c) => `<td>${esc(row[c.key] || '')}</td>`).join('');
        return `<tr>${tds}</tr>`;
      }).join('');
    }

    head.querySelectorAll('input[data-col], select[data-col]').forEach((el) => {
      const key = el.getAttribute('data-col');
      const apply = () => {
        STATE.colFilters[key] = el.value || '';
        renderTable();
      };
      el.addEventListener('input', apply);
      el.addEventListener('change', apply);
    });
  }

  async function reload() {
    updateChrome();
    syncUrl();
    const meta = document.getElementById('pageMeta');
    meta.textContent = 'Carregando…';
    try {
      const ds = await loadDataset();
      STATE.columns = ds.columns || [];
      STATE.rows = ds.rows || [];
      STATE.colFilters = {};
      renderTable();
      meta.textContent = ds.meta || `${STATE.rows.length} registro(s) · ${activeFiltersSummary()}`;
    } catch (err) {
      STATE.columns = [];
      STATE.rows = [];
      renderTable();
      meta.textContent = String(err.message || err);
    }
  }

  function exportExcel() {
    if (!window.XLSX) {
      alert('Biblioteca Excel ainda carregando. Tente de novo em 1s.');
      return;
    }
    applyFilters();
    const header = STATE.columns.map((c) => c.label);
    const keys = STATE.columns.map((c) => c.key);
    const aoa = [
      [`BINHO · ${STATE.title}`],
      [`Gerado em ${new Date().toLocaleString('pt-BR')} · ${activeFiltersSummary()}`],
      [],
      header,
      ...STATE.filtered.map((row) => keys.map((k) => row[k] ?? '')),
    ];
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    ws['!cols'] = header.map(() => ({ wch: 16 }));
    XLSX.utils.book_append_sheet(wb, ws, 'Gestao');
    const name = `BINHO_Gestao_${SETOR}_${REL || 'all'}_${new Date().toISOString().slice(0, 10)}.xlsx`;
    XLSX.writeFile(wb, name);
  }

  function exportPdf() {
    const jspdf = window.jspdf;
    if (!jspdf || !jspdf.jsPDF) {
      alert('Biblioteca PDF ainda carregando. Tente de novo em 1s.');
      return;
    }
    applyFilters();
    const { jsPDF } = jspdf;
    const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' });
    doc.setFontSize(14);
    doc.text(`BINHO · ${STATE.title}`, 40, 36);
    doc.setFontSize(9);
    doc.setTextColor(80);
    doc.text(`Gerado em ${new Date().toLocaleString('pt-BR')} · ${activeFiltersSummary()} · ${STATE.filtered.length} linhas`, 40, 52);
    doc.setTextColor(0);
    const head = [STATE.columns.map((c) => c.label)];
    const body = STATE.filtered.map((row) => STATE.columns.map((c) => String(row[c.key] ?? '')));
    doc.autoTable({
      startY: 64,
      head,
      body,
      styles: { fontSize: 7, cellPadding: 3 },
      headStyles: { fillColor: [15, 23, 42], textColor: 255 },
      alternateRowStyles: { fillColor: [245, 247, 250] },
      margin: { left: 28, right: 28 },
    });
    const name = `BINHO_Gestao_${SETOR}_${REL || 'all'}_${new Date().toISOString().slice(0, 10)}.pdf`;
    doc.save(name);
  }

  document.querySelectorAll('.sec-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      SETOR = btn.getAttribute('data-setor');
      if (SETOR !== 'distribuicao') REL = 'coleta';
      reload();
    });
  });
  document.querySelectorAll('.rel-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      REL = btn.getAttribute('data-rel');
      reload();
    });
  });
  document.getElementById('buscaGlobal').addEventListener('input', (e) => {
    STATE.busca = e.target.value || '';
    renderTable();
    document.getElementById('pageMeta').textContent = `${STATE.filtered.length} registro(s) · ${activeFiltersSummary()}`;
  });
  document.getElementById('btnLimpar').addEventListener('click', () => {
    STATE.busca = '';
    STATE.colFilters = {};
    document.getElementById('buscaGlobal').value = '';
    renderTable();
  });
  document.getElementById('btnExcel').addEventListener('click', exportExcel);
  document.getElementById('btnPdf').addEventListener('click', exportPdf);
  document.getElementById('btnReload').addEventListener('click', reload);

  reload();
})();
