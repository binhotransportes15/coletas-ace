/**
 * ACE · Apps Script (mesmo modelo do Vale Pallet)
 *
 * IMPORTANTE: App da Web muitas vezes NAO acha a planilha com getActiveSpreadsheet().
 * Por isso e obrigatorio colar o ID abaixo.
 *
 * Como pegar o ID:
 * https://docs.google.com/spreadsheets/d/COLE_ESTE_PEDACO_AQUI/edit
 *
 * Passos:
 * 1. Cole o ID em SPREADSHEET_ID
 * 2. Confirme SECRET = 'coletas-ace' (igual ao apps_script_token do ACE)
 * 3. Salvar
 * 4. Implantar → Gerenciar implantações → lápis → Nova versão → Implantar
 *
 * Leitura do site (sem token):
 *   GET ?action=resumo | ?action=coletas | ?action=historico&coleta_id=SPO071651
 *       | ?action=coletas103 | ?action=resumo103
 *       | ?action=entregas36 | ?action=romaneios36 | ?action=resumo36
 *       | ?action=agendamentos225 | ?action=resumo225 | ?action=alertas225
 *       | ?action=veiculos78 | ?action=resumo78 | ?action=ping
 * Escrita do ACE (com token): POST JSON action clear/append/replace|replace_many|ping|bump
 * replace_many: várias abas num POST só (ciclo automático rápido)
 * replace: sobrescreve no lugar (sem clear total) + content_hash (pula se igual)
 *         → TV/dashboard não vê aba vazia no meio do sync
 * GET action=version → {version} para o site não reler a planilha se nada mudou
 * CacheService nos resumos leves (TTL curto, invalidado no bump)
 * LockService evita POST em paralelo travar
 * Abas 50: Coletas, Historico, ResumoDiario
 * Abas 103: Coletas103, Resumo103
 * Abas 36: Entregas36, Romaneios36, Resumo36
 * Abas 225: Agendamentos225, Resumo225, Alertas225
 * Abas 078: Veiculos78, Resumo78
 * Abas 177: Conferentes177, Resumo177
 * Abas 031: Pendencias31, Resumo31, Ofensores31
 */

var SPREADSHEET_ID = '1VOkCF1Hn-VUZC7aKu_pa0Hgo1VjjuEJOqFqNSAErCzU';
var SECRET = 'coletas-ace';
var CACHE_TTL_SEC = 45; // só resumos / payloads leves
var PROP_VERSION = 'ace_data_version';
var PROP_HASHES = 'ace_sheet_hashes';

function doGet(e) {
  try {
    var dados = extrairDados_(e);
    var action = String(dados.action || 'ping').toLowerCase();

    if (action === 'ping' || action === '') {
      return json_({
        ok: true,
        service: 'ACE Sheets Bridge',
        spreadsheet: SPREADSHEET_ID,
        version: getDataVersion_(),
        hint: 'GET action=version|resumo|coletas|… | POST token replace/bump',
      });
    }

    if (action === 'version' || action === 'ver') {
      return json_({
        ok: true,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
      });
    }

    if (action === 'tv_layout' || action === 'tvlayout') {
      var rawLay = PropertiesService.getScriptProperties().getProperty('ace_tv_layout');
      var layout = null;
      if (rawLay) {
        try {
          layout = JSON.parse(rawLay);
        } catch (errParse) {
          layout = null;
        }
      }
      return json_({
        ok: true,
        layout: layout,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
      });
    }

    // Resumos leves: cache por versão (site atualiza rápido sem reler aba)
    var cachedActions = {
      resumo: 'ResumoDiario',
      resumo103: 'Resumo103',
      resumo36: 'Resumo36',
      resumo225: 'Resumo225',
      alertas225: 'Alertas225',
      resumo78: 'Resumo78',
      resumo177: 'Resumo177',
      resumo31: 'Resumo31',
      ofensores31: 'Ofensores31',
    };
    if (cachedActions[action]) {
      return cachedSheetJson_(action, cachedActions[action]);
    }

    if (action === 'coletas') {
      // So linhas da aba Coletas (1 SPO = 1 coleta). Nunca misturar Historico.
      var rows = sheetToObjects_('Coletas').filter(function (r) {
        if (r.event_key || r.seq_evento) return false;
        // Precisa do cabecalho SPO + SITUACAO ATUAL para contar
        if (!(r.coleta_id || r.coleta || (r.unidade && r.numero))) return false;
        var sit = String(r.situacao_atual || '').toUpperCase();
        return /CADASTRADA|COMANDADA|COLETADA|CANCELADA/.test(sit);
      });
      // Dedupe por coleta_id
      var seen = {};
      var unique = [];
      for (var i = 0; i < rows.length; i++) {
        var key = String(rows[i].coleta_id || rows[i].coleta || '').trim();
        if (!key || seen[key]) continue;
        seen[key] = true;
        unique.push(rows[i]);
      }
      return json_({
        ok: true,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
        rows: unique,
        total_coletas: unique.length,
      });
    }

    if (action === 'historico') {
      var filtroId = normalizarColetaId_(dados.coleta_id || dados.coleta || '');
      if (!filtroId) {
        return json_({ ok: false, error: 'informe coleta_id (ex.: SPO071651 ou SPO 071651)' });
      }
      var hist = sheetToObjects_('Historico').filter(function (r) {
        var cid = normalizarColetaId_(r.coleta_id || r.coleta || '');
        return cid === filtroId;
      });
      hist.sort(function (a, b) {
        var da = String(a.data || '') + ' ' + String(a.hora || '');
        var db = String(b.data || '') + ' ' + String(b.hora || '');
        return da < db ? -1 : da > db ? 1 : 0;
      });
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        coleta_id: filtroId,
        rows: hist,
        total_eventos: hist.length,
      });
    }

    if (action === 'coletas103' || action === '103') {
      var rows103 = sheetToObjects_('Coletas103').filter(function (r) {
        if (!(r.coleta_id || r.coleta)) return false;
        var st = String(r.status_ace || r.situacao_atual || '').toUpperCase();
        return /PARADO|EM_ROTA|EM ROTA|REALIZADA|CANCELADA|CADASTRADA|COMANDADA|COLETADA/.test(st)
          || String(r.coleta_id || '').trim() !== '';
      });
      var seen103 = {};
      var unique103 = [];
      for (var j = 0; j < rows103.length; j++) {
        var key103 = normalizarColetaId_(rows103[j].coleta_id || rows103[j].coleta || '');
        if (!key103 || seen103[key103]) continue;
        seen103[key103] = true;
        unique103.push(rows103[j]);
      }
      return json_({
        ok: true,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
        rows: unique103,
        total_coletas: unique103.length,
        report: '103',
      });
    }

    if (action === 'entregas36' || action === 'entregas' || action === '36') {
      var rows36 = sheetToObjects_('Entregas36').filter(function (r) {
        if (!(r.ctrc_id || r.romaneio)) return false;
        if (String(r.excluido || '') === '1') return false;
        var st = String(r.status_ace || '').toLowerCase();
        return st !== 'excluido';
      });
      return json_({
        ok: true,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
        rows: rows36,
        total: rows36.length,
        report: '36',
      });
    }

    if (action === 'romaneios36' || action === 'romaneios') {
      return json_({
        ok: true,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Romaneios36'),
        report: '36',
      });
    }

    if (action === 'agendamentos225' || action === 'agendamentos' || action === '225') {
      var rows225 = sheetToObjects_('Agendamentos225');
      return json_({
        ok: true,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
        rows: rows225,
        total: rows225.length,
        report: '225',
      });
    }

    if (action === 'veiculos78' || action === 'veiculos' || action === '78') {
      var rows78 = sheetToObjects_('Veiculos78');
      return json_({
        ok: true,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
        rows: rows78,
        total: rows78.length,
        report: '078',
      });
    }

    if (action === 'conferentes177' || action === 'conferentes' || action === '177') {
      var rows177 = sheetToObjects_('Conferentes177');
      return json_({
        ok: true,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
        rows: rows177,
        total: rows177.length,
        report: '177',
      });
    }

    if (action === 'pendencias31' || action === 'pendencias' || action === '31') {
      var rows31 = sheetToObjects_('Pendencias31');
      return json_({
        ok: true,
        version: getDataVersion_(),
        updated_at: new Date().toISOString(),
        rows: rows31,
        total: rows31.length,
        report: '031',
      });
    }

    return json_({ ok: false, error: 'action invalida: ' + action });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function normalizarColetaId_(valor) {
  return String(valor || '').toUpperCase().replace(/\s+/g, '').trim();
}
function doPost(e) {
  try {
    var data = extrairDados_(e);
    var received = String(data.token || '').trim();
    var expected = String(SECRET || '').trim();
    if (received !== expected) {
      return json_({
        ok: false,
        error: 'nao autorizado',
        hint: 'SECRET no Apps Script deve ser igual ao apps_script_token do ACE. Publique Nova versao apos alterar.',
      });
    }

    var action = String(data.action || 'replace').toLowerCase();
    var sheetName = String(data.sheet || '');
    var headers = data.headers || [];
    var rows = data.rows || [];

    if (action === 'ping' || action === 'auth') {
      // Saude do bridge (nao mexe em abas). Usado pelo ACE antes de gravar.
      return json_({
        ok: true,
        service: 'ACE Sheets Bridge',
        spreadsheet: SPREADSHEET_ID,
        action: 'ping',
        version: getDataVersion_(),
      });
    }

    if (action === 'bump' || action === 'invalidate') {
      var verBump = bumpDataVersion_();
      clearReadCache_();
      return json_({ ok: true, action: 'bump', version: verBump });
    }

    if (action === 'tv_layout_set' || action === 'tvlayout_set') {
      if (!data.layout || typeof data.layout !== 'object') {
        return json_({ ok: false, error: 'layout obrigatorio' });
      }
      PropertiesService.getScriptProperties().setProperty(
        'ace_tv_layout',
        JSON.stringify(data.layout)
      );
      var verLay = bumpDataVersion_();
      clearReadCache_();
      return json_({
        ok: true,
        action: 'tv_layout_set',
        version: verLay,
      });
    }

    // Vários abas num POST só — bem mais rápido que 1 POST por aba
    if (action === 'replace_many' || action === 'batch' || action === 'replace_batch') {
      var items = data.sheets || data.items || [];
      if (!items || !items.length) {
        return json_({ ok: false, error: 'sheets[] obrigatorio' });
      }
      var lock = LockService.getScriptLock();
      lock.waitLock(30000);
      try {
        var results = [];
        var anyWrote = false;
        for (var i = 0; i < items.length; i++) {
          var it = items[i] || {};
          var nm = String(it.sheet || it.name || '').trim();
          var hd = it.headers || [];
          var rw = it.rows || [];
          if (typeof rw === 'string') {
            try { rw = JSON.parse(rw); } catch (e1) { rw = []; }
          }
          if (typeof hd === 'string') {
            try { hd = JSON.parse(hd); } catch (e2) { hd = []; }
          }
          if (!nm || !hd.length) {
            results.push({ sheet: nm, ok: false, error: 'sheet/headers' });
            continue;
          }
          var rep = replaceSheetUnlocked_(nm, hd, rw, String(it.content_hash || '').trim());
          results.push({
            sheet: nm,
            ok: true,
            rows: rep.rows,
            skipped: !!rep.skipped,
          });
          if (!rep.skipped) anyWrote = true;
        }
        var ver = getDataVersion_();
        if (anyWrote && data.bump_version !== false && data.bump_version !== 'false') {
          ver = bumpDataVersion_();
          clearReadCache_();
        }
        return json_({
          ok: true,
          action: 'replace_many',
          results: results,
          wrote: anyWrote,
          version: ver,
        });
      } finally {
        lock.releaseLock();
      }
    }

    if (!sheetName) {
      return json_({ ok: false, error: 'sheet obrigatorio' });
    }
    if (!headers.length) {
      return json_({ ok: false, error: 'headers obrigatorios' });
    }

    if (action === 'replace') {
      var hash = String(data.content_hash || '').trim();
      var doBump = data.bump_version !== false && data.bump_version !== 'false';
      var rep = replaceSheet_(sheetName, headers, rows, hash, doBump);
      return json_({
        ok: true,
        sheet: sheetName,
        rows: rep.rows,
        skipped: !!rep.skipped,
        version: getDataVersion_(),
      });
    }
    if (action === 'clear') {
      clearSheet_(sheetName, headers);
      bumpDataVersion_();
      clearReadCache_();
      return json_({ ok: true, sheet: sheetName, cleared: true, version: getDataVersion_() });
    }
    if (action === 'append') {
      var n = appendRows_(sheetName, headers, rows);
      bumpDataVersion_();
      clearReadCache_();
      return json_({ ok: true, sheet: sheetName, rows: n, version: getDataVersion_() });
    }

    return json_({ ok: false, error: 'action invalida: ' + action });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function extrairDados_(e) {
  var dados = {};
  try {
    if (e && e.parameter) {
      for (var k in e.parameter) {
        if (Object.prototype.hasOwnProperty.call(e.parameter, k)) {
          dados[k] = e.parameter[k];
        }
      }
    }
    if (dados.payload) {
      var parsedPayload = JSON.parse(dados.payload);
      for (var pk in parsedPayload) {
        if (Object.prototype.hasOwnProperty.call(parsedPayload, pk)) {
          dados[pk] = parsedPayload[pk];
        }
      }
      delete dados.payload;
    }
    if (e && e.postData && e.postData.contents) {
      var raw = String(e.postData.contents).trim();
      if (raw.charAt(0) === '{') {
        var parsedBody = JSON.parse(raw);
        for (var bk in parsedBody) {
          if (Object.prototype.hasOwnProperty.call(parsedBody, bk)) {
            dados[bk] = parsedBody[bk];
          }
        }
      }
    }
  } catch (ignore) {}
  return dados;
}

function getSpreadsheet_() {
  if (!SPREADSHEET_ID || SPREADSHEET_ID.indexOf('COLE_O_ID') === 0) {
    throw new Error('Configure SPREADSHEET_ID no Code.gs (ID da planilha na URL).');
  }
  return SpreadsheetApp.openById(SPREADSHEET_ID);
}

function getOrCreateSheet_(name) {
  var ss = getSpreadsheet_();
  var sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
  }
  return sh;
}

function sheetToObjects_(name) {
  var sh = getSpreadsheet_().getSheetByName(name);
  if (!sh) {
    return [];
  }
  var values = sh.getDataRange().getValues();
  if (!values || values.length < 2) {
    return [];
  }
  var headers = values[0].map(function (h) { return String(h || '').trim(); });
  var out = [];
  for (var r = 1; r < values.length; r++) {
    var row = values[r];
    var obj = {};
    var empty = true;
    for (var c = 0; c < headers.length; c++) {
      var key = headers[c];
      if (!key) continue;
      var val = row[c];
      var text = cellToText_(val, key);
      if (text !== '') empty = false;
      obj[key] = text;
    }
    if (!empty) out.push(obj);
  }
  return out;
}

/**
 * Converte Date do Sheets para texto curto.
 * Evita "Thu Jul 02 2026 00:00:00 GMT-0300 (Horário Padrão de Brasília)".
 * Hora pura no Sheets vira 30/12/1899 — exibimos só HH:mm.
 */
function cellToText_(val, key) {
  if (val === null || val === undefined || val === '') return '';
  var k = String(key || '').toLowerCase();
  if (Object.prototype.toString.call(val) === '[object Date]' && !isNaN(val.getTime())) {
    var year = val.getFullYear();
    var isHoraCol = /(^hora$|_hora$|hora_)/.test(k);
    var isDataCol = /(^data$|_data$|data_)/.test(k) || k === 'data_cadastro' || k === 'data_limite_inicial';
    var isChegadaCol = /(chegada|saida|prev_|inicio_descarga|final_descarga)/.test(k);
    // Serial de hora no Sheets (epoch 1899)
    if (year < 1900 || isHoraCol) {
      return Utilities.formatDate(val, 'America/Sao_Paulo', 'HH:mm');
    }
    if (isDataCol && !isChegadaCol) {
      return Utilities.formatDate(val, 'America/Sao_Paulo', 'dd/MM');
    }
    // Date com hora relevante (inclui colunas 078 de chegada/descarga)
    var h = val.getHours();
    var m = val.getMinutes();
    if (h === 0 && m === 0 && !isChegadaCol) {
      return Utilities.formatDate(val, 'America/Sao_Paulo', 'dd/MM');
    }
    return Utilities.formatDate(val, 'America/Sao_Paulo', 'dd/MM HH:mm');
  }
  return String(val);
}

function clearSheet_(name, headers) {
  var sh = getOrCreateSheet_(name);
  sh.clear();
  if (headers && headers.length) {
    var range = sh.getRange(1, 1, 1, headers.length);
    range.setNumberFormat('@');
    range.setValues([headers]);
  }
}

function appendRows_(name, headers, rows) {
  var sh = getOrCreateSheet_(name);
  var values = sh.getDataRange().getValues();
  if (!values.length || !values[0] || !values[0].length) {
    var head = sh.getRange(1, 1, 1, headers.length);
    head.setNumberFormat('@');
    head.setValues([headers]);
  }
  if (!rows.length) {
    return 0;
  }
  var matrix = rowsToMatrix_(headers, rows);
  var startRow = sh.getLastRow() + 1;
  var body = sh.getRange(startRow, 1, matrix.length, headers.length);
  body.setNumberFormat('@'); // texto: nao transforma 29/07 em Date
  body.setValues(matrix);
  return matrix.length;
}

function replaceSheet_(name, headers, rows, contentHash, doBump) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var rep = replaceSheetUnlocked_(name, headers, rows, contentHash);
    if (doBump !== false && !rep.skipped) {
      bumpDataVersion_();
      clearReadCache_();
    }
    return rep;
  } finally {
    lock.releaseLock();
  }
}

/** Sem lock — usar dentro de replace_many (já com lock). */
function replaceSheetUnlocked_(name, headers, rows, contentHash) {
  var hash = String(contentHash || '').trim();
  if (hash) {
    var prev = getSheetHash_(name);
    if (prev && prev === hash) {
      return { rows: (rows || []).length, skipped: true };
    }
  }

  var sh = getOrCreateSheet_(name);
  var matrix = [headers].concat(rowsToMatrix_(headers, rows));
  var height = Math.max(matrix.length, 1);
  var width = Math.max(headers.length, 1);

  // Garante espaço antes de escrever (sem apagar a aba inteira)
  var maxRows = sh.getMaxRows();
  var maxCols = sh.getMaxColumns();
  if (maxRows < height) sh.insertRowsAfter(maxRows, height - maxRows);
  if (maxCols < width) sh.insertColumnsAfter(maxCols, width - maxCols);

  // Antes: sh.clear() + setValues → janela com dashboard zerado.
  // Agora: sobrescreve A1 e só limpa o excedente (TV sempre vê dados velhos ou novos).
  var prevLastRow = Math.max(sh.getLastRow(), 1);
  var prevLastCol = Math.max(sh.getLastColumn(), 1);

  var rangeAll = sh.getRange(1, 1, height, width);
  rangeAll.setNumberFormat('@');
  rangeAll.setValues(matrix);

  if (prevLastRow > height) {
    sh.getRange(height + 1, 1, prevLastRow - height, Math.max(prevLastCol, width)).clearContent();
  }
  if (prevLastCol > width) {
    sh.getRange(1, width + 1, height, prevLastCol - width).clearContent();
  }

  if (hash) {
    setSheetHash_(name, hash);
  }

  // Limpa rascunhos antigos de double-buffer (se existirem)
  var ss = getSpreadsheet_();
  var oldTemp = ss.getSheetByName(String(name) + '__next');
  if (oldTemp) {
    ss.deleteSheet(oldTemp);
  }
  return { rows: Math.max(matrix.length - 1, 0), skipped: false };
}

function rowsToMatrix_(headers, rows) {
  return (rows || []).map(function (row) {
    return headers.map(function (h) {
      var v = row[h];
      if (v === null || v === undefined) return '';
      return String(v);
    });
  });
}

function getDataVersion_() {
  var props = PropertiesService.getScriptProperties();
  var v = parseInt(props.getProperty(PROP_VERSION) || '0', 10);
  return isNaN(v) ? 0 : v;
}

function bumpDataVersion_() {
  var props = PropertiesService.getScriptProperties();
  var next = getDataVersion_() + 1;
  props.setProperty(PROP_VERSION, String(next));
  return next;
}

function getSheetHash_(name) {
  try {
    var props = PropertiesService.getScriptProperties();
    var raw = props.getProperty(PROP_HASHES) || '{}';
    var map = JSON.parse(raw);
    return String(map[name] || '');
  } catch (e) {
    return '';
  }
}

function setSheetHash_(name, hash) {
  var props = PropertiesService.getScriptProperties();
  var map = {};
  try {
    map = JSON.parse(props.getProperty(PROP_HASHES) || '{}');
  } catch (e) {
    map = {};
  }
  map[name] = String(hash || '');
  props.setProperty(PROP_HASHES, JSON.stringify(map));
}

function clearReadCache_() {
  try {
    CacheService.getScriptCache().removeAll([
      'ace:resumo:' + (getDataVersion_() - 1),
      'ace:resumo103:' + (getDataVersion_() - 1),
      'ace:resumo36:' + (getDataVersion_() - 1),
      'ace:resumo225:' + (getDataVersion_() - 1),
      'ace:alertas225:' + (getDataVersion_() - 1),
      'ace:resumo78:' + (getDataVersion_() - 1),
      'ace:resumo177:' + (getDataVersion_() - 1),
    ]);
  } catch (e) {}
}

function cachedSheetJson_(action, sheetName) {
  var ver = getDataVersion_();
  var key = 'ace:' + action + ':' + ver;
  var cache = CacheService.getScriptCache();
  try {
    var hit = cache.get(key);
    if (hit) {
      return ContentService
        .createTextOutput(hit)
        .setMimeType(ContentService.MimeType.JSON);
    }
  } catch (e) {}

  var payload = {
    ok: true,
    version: ver,
    updated_at: new Date().toISOString(),
    rows: sheetToObjects_(sheetName),
    cached: false,
  };
  var text = JSON.stringify(payload);
  // CacheService ~100KB — só guarda se couber
  if (text.length < 90000) {
    try {
      cache.put(key, text, CACHE_TTL_SEC);
    } catch (e2) {}
  }
  return ContentService
    .createTextOutput(text)
    .setMimeType(ContentService.MimeType.JSON);
}

function json_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
