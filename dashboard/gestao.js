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
        if (selected == null) continue;
        const set = selected instanceof Set
          ? selected
          : new Set([].concat(selected).map(String));
        if (set.size === 0) return false;
        const val = String(row[col.key] ?? '').trim();
        if (!set.has(val)) return false;
      }
      if (!q) return true;
      const blob = STATE.columns.map((c) => String(row[c.key] ?? '')).join(' ').toLowerCase();
      return blob.includes(q);
    });
  }

  function filterIsActive(key) {
    return STATE.colFilters[key] != null;
  }

  function activeFiltersSummary() {
    const parts = [];
    if (STATE.busca) parts.push(`busca="${STATE.busca}"`);
    STATE.columns.forEach((c) => {
      const raw = STATE.colFilters[c.key];
      if (raw == null) return;
      if (raw instanceof Set && raw.size === 0) {
        parts.push(`${c.label}=nenhum`);
        return;
      }
      const vals = [...(raw instanceof Set ? raw : [raw])];
      parts.push(vals.length <= 2 ? `${c.label}=${vals.join('|')}` : `${c.label}=${vals.length} valores`);
    });
    return parts.length ? parts.join(' · ') : 'sem filtros';
  }

  function uniqueColValues(key) {
    const set = new Set();
    for (const row of STATE.rows) {
      const v = String(row[key] ?? '').trim();
      if (v) set.add(v);
    }
    const col = STATE.columns.find((c) => c.key === key);
    if (col && col.type === 'enum' && Array.isArray(col.enum) && col.enum.length) {
      const ordered = [];
      col.enum.forEach((v) => { if (set.has(v)) ordered.push(v); });
      [...set].sort((a, b) => a.localeCompare(b, 'pt-BR')).forEach((v) => {
        if (!ordered.includes(v)) ordered.push(v);
      });
      return ordered.slice(0, 200);
    }
    return [...set].sort((a, b) => a.localeCompare(b, 'pt-BR')).slice(0, 200);
  }

  function msButtonLabel(key, options) {
    const raw = STATE.colFilters[key];
    if (raw == null) return 'Todos';
    if (raw instanceof Set && raw.size === 0) return 'Nenhum';
    const selected = raw instanceof Set ? raw : new Set([String(raw)]);
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

  function positionPanel(ms) {
    const btn = ms.querySelector('.ms-btn');
    const panel = ms.querySelector('.ms-panel');
    if (!btn || !panel) return;
    const fit = panel.classList.contains('ms-panel--fit');
    const r = btn.getBoundingClientRect();
    const pw = Math.min(380, Math.max(260, r.width + 40));
    let left = r.left;
    if (left + pw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - pw - 8);
    const maxH = fit
      ? Math.min(window.innerHeight - 24, 640)
      : Math.min(520, Math.floor(window.innerHeight * 0.72));
    let top = r.bottom + 6;
    // Mede depois de aplicar largura para caber acima/abaixo
    panel.style.width = `${Math.round(pw)}px`;
    panel.style.maxHeight = fit ? 'none' : `${Math.round(maxH)}px`;
    panel.style.left = `${Math.round(left)}px`;
    panel.style.top = `${Math.round(top)}px`;
    const ph = panel.getBoundingClientRect().height || 200;
    if (top + ph > window.innerHeight - 8) {
      top = Math.max(8, r.top - ph - 6);
      panel.style.top = `${Math.round(top)}px`;
    }
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
    document.querySelectorAll('.ms[data-col]').forEach((ms) => {
      const key = ms.getAttribute('data-col');
      syncMsVisual(ms, key);
    });
  }

  function buildMultiSelect(col) {
    const key = col.key;
    const options = uniqueColValues(key);
    const raw = STATE.colFilters[key];
    const isEmptyDeny = raw instanceof Set && raw.size === 0;
    const selected = raw instanceof Set && raw.size ? raw : (raw == null || isEmptyDeny ? null : new Set([String(raw)]));
    const label = msButtonLabel(key, options);
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

    const fitList = options.length > 0 && options.length <= 14;
    const showSearch = options.length > 14;

    return `<div class="ms" data-col="${esc(key)}">
      <span class="ms-cap">${esc(col.label)}</span>
      <button type="button" class="ms-btn${hasFilter ? ' has-filter' : ''}" aria-haspopup="listbox">
        <span class="ms-label">${esc(label)}</span>
        <span class="ms-caret">▾</span>
      </button>
      <div class="ms-panel${fitList ? ' ms-panel--fit' : ''}" role="listbox">
        <div class="ms-actions">
          <button type="button" data-act="all">Marcar tudo</button>
          <button type="button" data-act="none">Desmarcar</button>
        </div>
        ${showSearch ? '<input type="search" class="ms-search" placeholder="Buscar…" />' : ''}
        <div class="ms-list${fitList ? ' ms-list--fit' : ''}">${optsHtml}</div>
      </div>
    </div>`;
  }

  function syncMsVisual(ms, key) {
    const opts = uniqueColValues(key);
    const raw = STATE.colFilters[key];
    const isEmptyDeny = raw instanceof Set && raw.size === 0;
    const selected = raw instanceof Set && raw.size ? raw : null;
    const lab = ms.querySelector('.ms-label');
    const btn = ms.querySelector('.ms-btn');
    if (lab) lab.textContent = msButtonLabel(key, opts);
    if (btn) btn.classList.toggle('has-filter', filterIsActive(key));
    ms.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      if (isEmptyDeny) cb.checked = false;
      else if (raw == null) cb.checked = true;
      else if (selected) cb.checked = selected.has(cb.value);
      else cb.checked = false;
    });
  }

  function bindMultiSelects(root) {
    root.querySelectorAll('.ms[data-col]').forEach((ms) => {
      const key = ms.getAttribute('data-col');
      const btn = ms.querySelector('.ms-btn');
      const panel = ms.querySelector('.ms-panel');
      const list = ms.querySelector('.ms-list');
      const search = ms.querySelector('.ms-search');

      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const willOpen = !ms.classList.contains('open');
        closeAllMultis();
        if (willOpen) {
          ms.classList.add('open');
          positionPanel(ms);
        }
      });
      panel.addEventListener('click', (e) => e.stopPropagation());
      panel.addEventListener('mousedown', (e) => e.stopPropagation());

      ms.querySelectorAll('[data-act]').forEach((b) => {
        b.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const act = b.getAttribute('data-act');
          const boxes = [...list.querySelectorAll('input[type="checkbox"]')];
          if (act === 'all') {
            boxes.forEach((c) => { c.checked = true; });
            STATE.colFilters[key] = null;
          } else {
            boxes.forEach((c) => { c.checked = false; });
            STATE.colFilters[key] = new Set();
          }
          paintBodyOnly();
        });
      });

      list.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        cb.addEventListener('change', (e) => {
          e.stopPropagation();
          const boxes = [...list.querySelectorAll('input[type="checkbox"]')];
          const checked = boxes.filter((c) => c.checked).map((c) => c.value);
          if (checked.length === 0) STATE.colFilters[key] = new Set();
          else if (checked.length === boxes.length) STATE.colFilters[key] = null;
          else STATE.colFilters[key] = new Set(checked);
          paintBodyOnly();
        });
      });

      if (search) {
        search.addEventListener('input', (e) => {
          e.stopPropagation();
          const q = search.value.trim().toLowerCase();
          list.querySelectorAll('.ms-opt').forEach((lab) => {
            const t = (lab.getAttribute('data-val') || '').toLowerCase();
            lab.style.display = !q || t.includes(q) ? '' : 'none';
          });
        });
        search.addEventListener('click', (e) => e.stopPropagation());
      }
    });
  }

  function renderFilterBar() {
    const bar = document.getElementById('gestaoFilterBar');
    if (!bar) return;
    if (!STATE.columns.length) {
      bar.innerHTML = '';
      return;
    }
    bar.innerHTML = STATE.columns.map((c) => buildMultiSelect(c)).join('');
    bindMultiSelects(bar);
  }

  function renderTable() {
    const head = document.getElementById('gestaoHead');
    const empty = document.getElementById('gestaoEmpty');
    if (!STATE.columns.length) {
      head.innerHTML = '';
      document.getElementById('gestaoBody').innerHTML = '';
      document.getElementById('gestaoFilterBar').innerHTML = '';
      empty.hidden = false;
      document.getElementById('rowCount').textContent = '0 linhas';
      return;
    }
    empty.hidden = true;
    head.innerHTML = `<tr>${STATE.columns.map((c) => `<th>${esc(c.label)}</th>`).join('')}</tr>`;
    renderFilterBar();
    paintBodyOnly();
  }

  function dashboardUrl() {
    const view = SETOR === 'distribuicao' ? (REL || 'coleta') : 'live';
    if (SETOR === 'distribuicao') return `index.html#${SETOR}/${view}`;
    return `index.html#${SETOR}`;
  }

  function goBackToDashboard() {
    const url = dashboardUrl();
    try {
      // Só fecha janela popup (desktop). No iframe do Sites, só navega de volta.
      const embedded = (() => {
        try { return window.self !== window.top; }
        catch (_) { return true; }
      })();
      if (!embedded && window.opener && !window.opener.closed) {
        window.opener.location.href = url;
        window.opener.focus();
        window.close();
        return;
      }
    } catch (_) { /* cross-origin */ }
    window.location.href = url;
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
  function loadImageEl(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = src;
    });
  }

  async function loadLogoImage() {
    try {
      let img = document.getElementById('brandLogo');
      if (img && img.complete && img.naturalWidth) return img;
      return await loadImageEl(`logo-binho.png?t=${Date.now()}`);
    } catch (_) {
      return null;
    }
  }

  async function loadLogoBase64() {
    if (LOGO_CACHE && LOGO_CACHE.base64) return LOGO_CACHE;
    try {
      const img = await loadLogoImage();
      if (!img) throw new Error('no img');
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth || 640;
      canvas.height = img.naturalHeight || 240;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#05080f';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL('image/png');
      LOGO_CACHE = { dataUrl, base64: dataUrl.split(',')[1] || '', ext: 'png' };
      return LOGO_CACHE;
    } catch (_) {
      LOGO_CACHE = null;
      return null;
    }
  }

  /** Faixa de cabeçalho pronta (logo + setor) — evita corte/texto branco sumindo no Excel. */
  async function buildReportBanner(head) {
    const W = 1400;
    const H = 140;
    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = '#0B1220';
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#0EA5E9';
    ctx.fillRect(0, H - 4, W, 4);

    let logoRight = 28;
    try {
      const img = await loadLogoImage();
      if (img && img.naturalWidth) {
        const maxH = 100;
        const scale = Math.min(maxH / img.naturalHeight, 320 / img.naturalWidth);
        const lw = Math.round(img.naturalWidth * scale);
        const lh = Math.round(img.naturalHeight * scale);
        const lx = 24;
        const ly = Math.round((H - lh) / 2) - 2;
        ctx.drawImage(img, lx, ly, lw, lh);
        logoRight = lx + lw + 36;
      }
    } catch (_) { /* ignore */ }

    if (logoRight <= 28) {
      ctx.fillStyle = '#FFFFFF';
      ctx.font = 'bold 36px Segoe UI, Arial, sans-serif';
      ctx.fillText('BINHO', 28, 58);
      ctx.font = '600 18px Segoe UI, Arial, sans-serif';
      ctx.fillStyle = '#94A3B8';
      ctx.fillText('Transportes', 28, 84);
      logoRight = 220;
    }

    ctx.fillStyle = '#F8FAFC';
    ctx.font = 'bold 44px Segoe UI, Arial, sans-serif';
    ctx.fillText(String(head.setor || 'GESTÃO').toUpperCase(), logoRight, 58);

    if (head.relatorio) {
      ctx.fillStyle = '#38BDF8';
      ctx.font = '600 22px Segoe UI, Arial, sans-serif';
      ctx.fillText(String(head.relatorio), logoRight, 90);
    } else {
      ctx.fillStyle = '#94A3B8';
      ctx.font = '500 18px Segoe UI, Arial, sans-serif';
      ctx.fillText(String(head.title || 'Gestão'), logoRight, 90);
    }

    const dataUrl = canvas.toDataURL('image/png');
    return {
      dataUrl,
      base64: dataUrl.split(',')[1] || '',
      widthPx: W,
      heightPx: H,
    };
  }

  function exportFileName(ext) {
    const slug = [SETOR, SETOR === 'distribuicao' ? REL : ''].filter(Boolean).join('_');
    return `BINHO_Gestao_${slug}_${new Date().toISOString().slice(0, 10)}.${ext}`;
  }

  function getExcelJS() {
    return window.ExcelJS || window.exceljs || null;
  }

  async function exportExcel() {
    applyFilters();
    const head = sectorHeading();
    const banner = await buildReportBanner(head);
    const header = STATE.columns.map((c) => c.label);
    const keys = STATE.columns.map((c) => c.key);
    const meta = `Gerado em ${new Date().toLocaleString('pt-BR')} · ${activeFiltersSummary()} · ${STATE.filtered.length} linhas`;
    const ExcelJS = getExcelJS();

    if (!ExcelJS) {
      alert('Biblioteca Excel ainda carregando. Atualize a página e tente de novo.');
      return;
    }

    const wb = new ExcelJS.Workbook();
    wb.creator = 'BINHO Gestão';
    wb.created = new Date();
    const colCount = Math.max(header.length, 8);
    const ws = wb.addWorksheet('Relatorio', {
      views: [{ state: 'frozen', ySplit: 5 }],
      properties: { defaultRowHeight: 16 },
    });

    // Espaço do banner (sem merge estreito que corta a logo)
    ws.getRow(1).height = 18;
    ws.getRow(2).height = 18;
    ws.getRow(3).height = 18;
    ws.getRow(4).height = 12;
    for (let r = 1; r <= 4; r++) {
      for (let c = 1; c <= colCount; c++) {
        ws.getCell(r, c).fill = {
          type: 'pattern',
          pattern: 'solid',
          fgColor: { argb: 'FF0B1220' },
        };
      }
    }

    if (banner && banner.base64) {
      const imgId = wb.addImage({ base64: banner.base64, extension: 'png' });
      // Largura ~ colunas A–H em pontos Excel (~72pt por polegada); banner 1400x140
      ws.addImage(imgId, {
        tl: { col: 0, row: 0 },
        ext: { width: 720, height: 72 },
        editAs: 'oneCell',
      });
    }

    ws.getCell(5, 1).value = meta;
    ws.getCell(5, 1).font = { name: 'Calibri', size: 9, color: { argb: 'FF64748B' } };
    ws.getRow(5).height = 18;

    const headerRowIdx = 6;
    const headerRow = ws.getRow(headerRowIdx);
    header.forEach((label, i) => {
      const cell = headerRow.getCell(i + 1);
      cell.value = label;
      cell.font = { name: 'Calibri', bold: true, color: { argb: 'FFFFFFFF' }, size: 10 };
      cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF0369A1' } };
      cell.alignment = { vertical: 'middle', horizontal: 'left' };
      cell.border = {
        bottom: { style: 'thin', color: { argb: 'FF0EA5E9' } },
      };
    });
    headerRow.height = 20;

    STATE.filtered.forEach((row, ri) => {
      const r = ws.getRow(headerRowIdx + 1 + ri);
      keys.forEach((k, i) => {
        const cell = r.getCell(i + 1);
        cell.value = row[k] ?? '';
        cell.font = { name: 'Calibri', size: 9, color: { argb: 'FF0F172A' } };
        if (ri % 2 === 1) {
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFF1F5F9' } };
        }
        cell.border = {
          bottom: { style: 'hair', color: { argb: 'FFCBD5E1' } },
        };
      });
    });

    header.forEach((label, i) => {
      ws.getColumn(i + 1).width = Math.min(36, Math.max(12, String(label).length + 4));
    });

    if (header.length) {
      ws.autoFilter = {
        from: { row: headerRowIdx, column: 1 },
        to: { row: headerRowIdx + Math.max(STATE.filtered.length, 1), column: header.length },
      };
    }

    const buf = await wb.xlsx.writeBuffer();
    const blob = new Blob([buf], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = exportFileName('xlsx');
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
  }

  async function exportPdf() {
    const jspdf = window.jspdf;
    if (!jspdf || !jspdf.jsPDF) {
      alert('Biblioteca PDF ainda carregando. Atualize a página e tente de novo.');
      return;
    }
    applyFilters();
    const head = sectorHeading();
    const banner = await buildReportBanner(head);
    const { jsPDF } = jspdf;
    const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' });
    const pageW = doc.internal.pageSize.getWidth();
    const pageH = doc.internal.pageSize.getHeight();

    const bannerH = 78;
    if (banner && banner.dataUrl) {
      try {
        doc.addImage(banner.dataUrl, 'PNG', 0, 0, pageW, bannerH);
      } catch (_) {
        doc.setFillColor(11, 18, 32);
        doc.rect(0, 0, pageW, bannerH, 'F');
      }
    } else {
      doc.setFillColor(11, 18, 32);
      doc.rect(0, 0, pageW, bannerH, 'F');
      doc.setTextColor(255, 255, 255);
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(18);
      doc.text('BINHO', 28, 44);
    }

    doc.setTextColor(100, 116, 139);
    doc.setFontSize(9);
    doc.setFont('helvetica', 'normal');
    doc.text(
      `Gerado em ${new Date().toLocaleString('pt-BR')}  ·  ${activeFiltersSummary()}  ·  ${STATE.filtered.length} registros`,
      28,
      bannerH + 18
    );

    const tableHead = [STATE.columns.map((c) => c.label)];
    const body = STATE.filtered.map((row) => STATE.columns.map((c) => String(row[c.key] ?? '')));
    doc.autoTable({
      startY: bannerH + 28,
      head: tableHead,
      body,
      styles: {
        fontSize: 7.5,
        cellPadding: 3.5,
        lineColor: [203, 213, 225],
        lineWidth: 0.3,
        textColor: [15, 23, 42],
        font: 'helvetica',
      },
      headStyles: {
        fillColor: [3, 105, 161],
        textColor: 255,
        fontStyle: 'bold',
        halign: 'left',
      },
      alternateRowStyles: { fillColor: [241, 245, 249] },
      margin: { left: 24, right: 24, bottom: 36 },
      didDrawPage: () => {
        const page = doc.internal.getNumberOfPages();
        doc.setFontSize(8);
        doc.setTextColor(100, 116, 139);
        doc.text(
          `BINHO · ${head.title} · pág. ${page}`,
          pageW / 2,
          pageH - 16,
          { align: 'center' }
        );
      },
    });
    doc.save(exportFileName('pdf'));
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

  document.addEventListener('click', () => closeAllMultis());
  window.addEventListener('resize', () => {
    document.querySelectorAll('.ms.open').forEach((ms) => positionPanel(ms));
  });
  document.addEventListener('scroll', () => {
    document.querySelectorAll('.ms.open').forEach((ms) => positionPanel(ms));
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAllMultis();
  });

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
  });
  document.getElementById('btnExcel').addEventListener('click', () => {
    exportExcel().catch((err) => alert(String(err.message || err)));
  });
  document.getElementById('btnPdf').addEventListener('click', () => {
    exportPdf().catch((err) => alert(String(err.message || err)));
  });
  document.getElementById('btnReload').addEventListener('click', reload);
  document.getElementById('btnVoltarDash')?.addEventListener('click', goBackToDashboard);
  document.getElementById('btnVoltarDashTop')?.addEventListener('click', goBackToDashboard);

  reload();
})();

