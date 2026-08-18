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
        const selected = STATE.colFilters[col.key];
        if (selected == null) continue; // sem filtro nesta coluna
        const set = selected instanceof Set
          ? selected
          : new Set(Array.isArray(selected) ? selected.map(String) : [String(selected)]);
        if (set.size === 0) return false; // desmarcado tudo
        const val = String(row[col.key] ?? '').trim();
        if (!set.has(val)) return false;
      }
      if (!q) return true;
      const blob = STATE.columns.map((c) => String(row[c.key] ?? '')).join(' ').toLowerCase();
      return blob.includes(q);
    });
  }

  /** null = sem filtro (todos). Set (mesmo vazio) = filtro ativo. */
  function normalizeFilterSet(raw) {
    if (raw == null) return null;
    if (raw instanceof Set) return raw.size ? raw : null; // só para rótulo "parcial"
    if (Array.isArray(raw)) return raw.length ? new Set(raw.map(String)) : null;
    const s = String(raw).trim();
    return s ? new Set([s]) : null;
  }

  function filterIsActive(key) {
    const raw = STATE.colFilters[key];
    if (raw == null) return false;
    if (raw instanceof Set) return true; // inclusive vazio = "Nenhum"
    return true;
  }

  function activeFiltersSummary() {
    const parts = [];
    if (STATE.busca) parts.push(`busca="${STATE.busca}"`);
    STATE.columns.forEach((c) => {
      const raw = STATE.colFilters[c.key];
      if (raw == null) return;
      if (raw instanceof Set && raw.size === 0) {
        parts.push(`${c.label}=∅`);
        return;
      }
      const selected = normalizeFilterSet(raw);
      if (!selected) return;
      const vals = [...selected];
      const shown = vals.length <= 2 ? vals.join('|') : `${vals.length} valores`;
      parts.push(`${c.label}=${shown}`);
    });
    return parts.length ? parts.join(' · ') : 'sem filtros';
  }

  function uniqueColValues(key) {
    const set = new Set();
    for (const row of STATE.rows) {
      const v = String(row[key] ?? '').trim();
      if (v) set.add(v);
    }
    // Prefer enum order when declared
    const col = STATE.columns.find((c) => c.key === key);
    if (col && col.type === 'enum' && Array.isArray(col.enum) && col.enum.length) {
      const ordered = [];
      col.enum.forEach((v) => { if (set.has(v)) ordered.push(v); });
      [...set].sort((a, b) => a.localeCompare(b, 'pt-BR')).forEach((v) => {
        if (!ordered.includes(v)) ordered.push(v);
      });
      return ordered;
    }
    return [...set].sort((a, b) => a.localeCompare(b, 'pt-BR'));
  }

  function msButtonLabel(key, options) {
    const selected = normalizeFilterSet(STATE.colFilters[key]);
    if (!selected) return 'Todos';
    if (options.length && selected.size >= options.length) return 'Todos';
    if (selected.size === 1) return [...selected][0];
    return `${selected.size} selecionados`;
  }

  function closeAllMultis(except) {
    document.querySelectorAll('.ms.open').forEach((el) => {
      if (except && el === except) return;
      el.classList.remove('open');
    });
  }

  function paintBodyOnly() {
    applyFilters();
    const body = document.getElementById('gestaoBody');
    const empty = document.getElementById('gestaoEmpty');
    const count = document.getElementById('rowCount');
    const meta = document.getElementById('pageMeta');
    count.textContent = `${STATE.filtered.length} de ${STATE.rows.length} linhas`;
    if (meta && STATE.columns.length) {
      meta.textContent = `${STATE.filtered.length} registro(s) · ${activeFiltersSummary()}`;
    }
    if (!STATE.columns.length) {
      body.innerHTML = '';
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    if (!STATE.filtered.length) {
      body.innerHTML = `<tr><td colspan="${STATE.columns.length}" class="muted">Nenhuma linha com os filtros atuais.</td></tr>`;
    } else {
      body.innerHTML = STATE.filtered.map((row) => {
        const tds = STATE.columns.map((c) => `<td>${esc(row[c.key] || '')}</td>`).join('');
        return `<tr>${tds}</tr>`;
      }).join('');
    }
    // Atualiza rótulos dos botões sem fechar painéis abertos
    document.querySelectorAll('.ms[data-col]').forEach((ms) => {
      const key = ms.getAttribute('data-col');
      const opts = uniqueColValues(key);
      const btn = ms.querySelector('.ms-btn');
      const lab = ms.querySelector('.ms-label');
      if (lab) lab.textContent = msButtonLabel(key, opts);
      if (btn) btn.classList.toggle('has-filter', filterIsActive(key));
    });
  }

  function buildMultiSelect(col) {
    const key = col.key;
    const options = uniqueColValues(key);
    const selected = STATE.colFilters[key] instanceof Set && STATE.colFilters[key].size === 0
      ? new Set()
      : normalizeFilterSet(STATE.colFilters[key]);
    const isEmptyDeny = STATE.colFilters[key] instanceof Set && STATE.colFilters[key].size === 0;
    const label = isEmptyDeny ? 'Nenhum' : msButtonLabel(key, options);
    const hasFilter = filterIsActive(key);

    const optsHtml = options.length
      ? options.map((v) => {
        const isOn = isEmptyDeny ? false : (!selected || selected.has(v));
        return `<label class="ms-opt" data-val="${esc(v)}">
          <input type="checkbox" value="${esc(v)}"${isOn ? ' checked' : ''} />
          <span>${esc(v)}</span>
        </label>`;
      }).join('')
      : '<div class="ms-empty">Sem valores</div>';

    return `<div class="ms" data-col="${esc(key)}">
      <button type="button" class="ms-btn${hasFilter ? ' has-filter' : ''}" aria-haspopup="listbox">
        <span class="ms-label">${esc(label)}</span>
        <span class="ms-caret">▾</span>
      </button>
      <div class="ms-panel" role="listbox">
        <div class="ms-actions">
          <button type="button" data-act="all">Marcar tudo</button>
          <button type="button" data-act="none">Desmarcar</button>
        </div>
        ${options.length > 8 ? `<input type="search" class="ms-search" placeholder="Buscar…" />` : ''}
        <div class="ms-list">${optsHtml}</div>
      </div>
    </div>`;
  }

  function bindMultiSelects(head) {
    head.querySelectorAll('.ms[data-col]').forEach((ms) => {
      const key = ms.getAttribute('data-col');
      const btn = ms.querySelector('.ms-btn');
      const panel = ms.querySelector('.ms-panel');
      const list = ms.querySelector('.ms-list');
      const search = ms.querySelector('.ms-search');

      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = !ms.classList.contains('open');
        closeAllMultis();
        if (willOpen) ms.classList.add('open');
      });
      panel.addEventListener('click', (e) => e.stopPropagation());

      ms.querySelectorAll('[data-act]').forEach((b) => {
        b.addEventListener('click', () => {
          const act = b.getAttribute('data-act');
          const boxes = [...list.querySelectorAll('input[type="checkbox"]')];
          if (act === 'all') {
            boxes.forEach((c) => { c.checked = true; });
            STATE.colFilters[key] = null; // todos = sem filtro
          } else {
            boxes.forEach((c) => { c.checked = false; });
            // Desmarcar tudo → nenhum valor passa (Set vazio especial)
            // Usamos Set vazio explícito via array marker
            STATE.colFilters[key] = new Set(); // empty set = nenhuma linha
          }
          paintBodyOnly();
          syncMsVisual(ms, key);
        });
      });

      list.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        cb.addEventListener('change', () => {
          const boxes = [...list.querySelectorAll('input[type="checkbox"]')];
          const checked = boxes.filter((c) => c.checked).map((c) => c.value);
          if (checked.length === 0) {
            STATE.colFilters[key] = new Set();
          } else if (checked.length === boxes.length) {
            STATE.colFilters[key] = null;
          } else {
            STATE.colFilters[key] = new Set(checked);
          }
          paintBodyOnly();
          syncMsVisual(ms, key);
        });
      });

      if (search) {
        search.addEventListener('input', () => {
          const q = search.value.trim().toLowerCase();
          list.querySelectorAll('.ms-opt').forEach((lab) => {
            const t = (lab.getAttribute('data-val') || '').toLowerCase();
            lab.style.display = !q || t.includes(q) ? '' : 'none';
          });
        });
      }
    });
  }

  function syncMsVisual(ms, key) {
    const opts = uniqueColValues(key);
    const selected = normalizeFilterSet(STATE.colFilters[key]);
    const lab = ms.querySelector('.ms-label');
    const btn = ms.querySelector('.ms-btn');
    // Empty Set means "nenhum" — show that explicitly
    const isEmptyDeny = STATE.colFilters[key] instanceof Set && STATE.colFilters[key].size === 0;
    if (lab) {
      lab.textContent = isEmptyDeny ? 'Nenhum' : msButtonLabel(key, opts);
    }
    if (btn) btn.classList.toggle('has-filter', !!selected || isEmptyDeny);
    ms.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      if (isEmptyDeny) cb.checked = false;
      else if (!selected) cb.checked = true;
      else cb.checked = selected.has(cb.value);
    });
  }

  function renderTable() {
    const head = document.getElementById('gestaoHead');
    const empty = document.getElementById('gestaoEmpty');

    if (!STATE.columns.length) {
      head.innerHTML = '';
      document.getElementById('gestaoBody').innerHTML = '';
      empty.hidden = false;
      document.getElementById('rowCount').textContent = '0 linhas';
      return;
    }
    empty.hidden = true;

    const ths = STATE.columns.map((c) => `<th>${esc(c.label)}</th>`).join('');
    const filters = STATE.columns.map((c) => `<th>${buildMultiSelect(c)}</th>`).join('');
    head.innerHTML = `<tr>${ths}</tr><tr class="filters">${filters}</tr>`;
    bindMultiSelects(head);
    paintBodyOnly();
  }

  document.addEventListener('click', () => closeAllMultis());
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAllMultis();
  });

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

  function sectorHeading() {
    const setor = SECTOR_LABEL[SETOR] || SETOR;
    const relatorio = SETOR === 'distribuicao' ? (REL_LABEL[REL] || REL) : '';
    return {
      setor,
      relatorio,
      title: STATE.title || `Gestão · ${setor}`,
    };
  }

  let LOGO_CACHE = null;
  async function loadLogoBase64() {
    if (LOGO_CACHE) return LOGO_CACHE;
    try {
      const res = await fetch(`logo-binho.png?t=${Date.now()}`, { cache: 'force-cache' });
      if (!res.ok) throw new Error('logo');
      const blob = await res.blob();
      const dataUrl = await new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(String(fr.result || ''));
        fr.onerror = reject;
        fr.readAsDataURL(blob);
      });
      const m = dataUrl.match(/^data:image\/\w+;base64,(.+)$/);
      LOGO_CACHE = { dataUrl, base64: m ? m[1] : '', ext: 'png' };
      return LOGO_CACHE;
    } catch (_) {
      LOGO_CACHE = null;
      return null;
    }
  }

  function exportFileName(ext) {
    const slug = [SETOR, SETOR === 'distribuicao' ? REL : '']
      .filter(Boolean)
      .join('_');
    return `BINHO_Gestao_${slug}_${new Date().toISOString().slice(0, 10)}.${ext}`;
  }

  async function exportExcel() {
    applyFilters();
    const head = sectorHeading();
    const logo = await loadLogoBase64();
    const header = STATE.columns.map((c) => c.label);
    const keys = STATE.columns.map((c) => c.key);
    const meta = `Gerado em ${new Date().toLocaleString('pt-BR')} · ${activeFiltersSummary()} · ${STATE.filtered.length} linhas`;

    if (window.ExcelJS && logo && logo.base64) {
      const wb = new ExcelJS.Workbook();
      wb.creator = 'BINHO Gestão';
      const ws = wb.addWorksheet('Gestao', {
        views: [{ state: 'frozen', ySplit: 5 }],
      });

      // Faixa do logo (cols A–C) + setor à direita (col E)
      ws.mergeCells(1, 1, 3, 3);
      ws.getRow(1).height = 28;
      ws.getRow(2).height = 22;
      ws.getRow(3).height = 18;

      const imgId = wb.addImage({
        base64: logo.base64,
        extension: 'png',
      });
      ws.addImage(imgId, {
        tl: { col: 0, row: 0 },
        ext: { width: 200, height: 78 },
      });

      const setorCell = ws.getCell(1, 5);
      setorCell.value = String(head.setor || '').toUpperCase();
      setorCell.font = { name: 'Calibri', bold: true, size: 18, color: { argb: 'FF0F172A' } };
      setorCell.alignment = { vertical: 'middle', horizontal: 'left' };

      if (head.relatorio) {
        const relCell = ws.getCell(2, 5);
        relCell.value = head.relatorio;
        relCell.font = { name: 'Calibri', bold: true, size: 12, color: { argb: 'FF0369A1' } };
      }

      ws.getCell(4, 1).value = `BINHO · ${head.title}`;
      ws.getCell(4, 1).font = { name: 'Calibri', bold: true, size: 11 };
      ws.getCell(5, 1).value = meta;
      ws.getCell(5, 1).font = { name: 'Calibri', size: 9, color: { argb: 'FF64748B' } };

      const headerRowIdx = 7;
      const headerRow = ws.getRow(headerRowIdx);
      header.forEach((label, i) => {
        const cell = headerRow.getCell(i + 1);
        cell.value = label;
        cell.font = { bold: true, color: { argb: 'FFFFFFFF' } };
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF0F172A' } };
        cell.alignment = { vertical: 'middle' };
      });
      headerRow.height = 18;

      STATE.filtered.forEach((row, ri) => {
        const r = ws.getRow(headerRowIdx + 1 + ri);
        keys.forEach((k, i) => { r.getCell(i + 1).value = row[k] ?? ''; });
      });
      header.forEach((_, i) => { ws.getColumn(i + 1).width = 16; });
      if ((header.length || 0) < 5) ws.getColumn(5).width = 18;

      const buf = await wb.xlsx.writeBuffer();
      const blob = new Blob([buf], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = exportFileName('xlsx');
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1500);
      return;
    }

    if (!window.XLSX) {
      alert('Biblioteca Excel ainda carregando. Tente de novo em 1s.');
      return;
    }
    const aoa = [
      ['BINHO Transportes'],
      [String(head.setor || '').toUpperCase() + (head.relatorio ? ` · ${head.relatorio}` : '')],
      [meta],
      [],
      header,
      ...STATE.filtered.map((row) => keys.map((k) => row[k] ?? '')),
    ];
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    ws['!cols'] = header.map(() => ({ wch: 16 }));
    XLSX.utils.book_append_sheet(wb, ws, 'Gestao');
    XLSX.writeFile(wb, exportFileName('xlsx'));
  }

  async function exportPdf() {
    const jspdf = window.jspdf;
    if (!jspdf || !jspdf.jsPDF) {
      alert('Biblioteca PDF ainda carregando. Tente de novo em 1s.');
      return;
    }
    applyFilters();
    const head = sectorHeading();
    const logo = await loadLogoBase64();
    const { jsPDF } = jspdf;
    const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' });
    const pageW = doc.internal.pageSize.getWidth();

    doc.setFillColor(5, 8, 15);
    doc.rect(0, 0, pageW, 78, 'F');
    if (logo && logo.dataUrl) {
      try {
        doc.addImage(logo.dataUrl, 'PNG', 28, 14, 150, 52);
      } catch (_) { /* ignore */ }
    } else {
      doc.setTextColor(255, 255, 255);
      doc.setFontSize(16);
      doc.setFont('helvetica', 'bold');
      doc.text('BINHO', 28, 44);
    }

    doc.setTextColor(226, 232, 240);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(18);
    doc.text(String(head.setor || '').toUpperCase(), 200, 38);
    if (head.relatorio) {
      doc.setFontSize(12);
      doc.setFont('helvetica', 'normal');
      doc.setTextColor(56, 189, 248);
      doc.text(head.relatorio, 200, 56);
    }

    doc.setTextColor(100, 116, 139);
    doc.setFontSize(9);
    doc.setFont('helvetica', 'normal');
    doc.text(
      `Gerado em ${new Date().toLocaleString('pt-BR')} · ${activeFiltersSummary()} · ${STATE.filtered.length} linhas`,
      28,
      96
    );

    const tableHead = [STATE.columns.map((c) => c.label)];
    const body = STATE.filtered.map((row) => STATE.columns.map((c) => String(row[c.key] ?? '')));
    doc.autoTable({
      startY: 108,
      head: tableHead,
      body,
      styles: { fontSize: 7, cellPadding: 3 },
      headStyles: { fillColor: [15, 23, 42], textColor: 255 },
      alternateRowStyles: { fillColor: [245, 247, 250] },
      margin: { left: 28, right: 28 },
    });
    doc.save(exportFileName('pdf'));
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
    paintBodyOnly();
  });
  document.getElementById('btnLimpar').addEventListener('click', () => {
    STATE.busca = '';
    STATE.colFilters = {};
    document.getElementById('buscaGlobal').value = '';
    closeAllMultis();
    renderTable();
    document.getElementById('pageMeta').textContent = `${STATE.filtered.length} registro(s) · ${activeFiltersSummary()}`;
  });
  document.getElementById('btnExcel').addEventListener('click', () => {
    exportExcel().catch((err) => alert(String(err.message || err)));
  });
  document.getElementById('btnPdf').addEventListener('click', () => {
    exportPdf().catch((err) => alert(String(err.message || err)));
  });
  document.getElementById('btnReload').addEventListener('click', reload);

  reload();
})();
