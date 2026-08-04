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
 * Escrita do ACE (com token): POST JSON action clear/append/replace
 * Abas 50: Coletas, Historico, ResumoDiario
 * Abas 103: Coletas103, Resumo103
 * Abas 36: Entregas36, Romaneios36, Resumo36
 * Abas 225: Agendamentos225, Resumo225, Alertas225
 * Abas 078: Veiculos78, Resumo78
 * Abas 177: Conferentes177, Resumo177
 */

var SPREADSHEET_ID = '1VOkCF1Hn-VUZC7aKu_pa0Hgo1VjjuEJOqFqNSAErCzU';
var SECRET = 'coletas-ace';

function doGet(e) {
  try {
    var dados = extrairDados_(e);
    var action = String(dados.action || 'ping').toLowerCase();

    if (action === 'ping' || action === '') {
      return json_({
        ok: true,
        service: 'ACE Sheets Bridge',
        spreadsheet: SPREADSHEET_ID,
        hint: 'GET action=resumo|coletas|historico|coletas103|resumo103|entregas36|romaneios36|resumo36|agendamentos225|resumo225|alertas225|veiculos78|resumo78 | POST com token para gravar',
      });
    }

    if (action === 'resumo') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('ResumoDiario'),
      });
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
        updated_at: new Date().toISOString(),
        rows: unique103,
        total_coletas: unique103.length,
        report: '103',
      });
    }

    if (action === 'resumo103') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Resumo103'),
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
        updated_at: new Date().toISOString(),
        rows: rows36,
        total: rows36.length,
        report: '36',
      });
    }

    if (action === 'romaneios36' || action === 'romaneios') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Romaneios36'),
        report: '36',
      });
    }

    if (action === 'resumo36') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Resumo36'),
        report: '36',
      });
    }

    if (action === 'agendamentos225' || action === 'agendamentos' || action === '225') {
      var rows225 = sheetToObjects_('Agendamentos225');
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: rows225,
        total: rows225.length,
        report: '225',
      });
    }

    if (action === 'resumo225') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Resumo225'),
        report: '225',
      });
    }

    if (action === 'alertas225') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Alertas225'),
        report: '225',
      });
    }

    // Armazém 078 — mesmas planilha/SECRET da distribuição
    if (action === 'resumo78') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Resumo78'),
        report: '078',
      });
    }

    if (action === 'veiculos78' || action === 'veiculos' || action === '78') {
      var rows78 = sheetToObjects_('Veiculos78');
      return json_({
        ok: true,
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
        updated_at: new Date().toISOString(),
        rows: rows177,
        total: rows177.length,
        report: '177',
      });
    }

    if (action === 'resumo177') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Resumo177'),
        report: '177',
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
      });
    }

    if (!sheetName) {
      return json_({ ok: false, error: 'sheet obrigatorio' });
    }
    if (!headers.length) {
      return json_({ ok: false, error: 'headers obrigatorios' });
    }

    if (action === 'replace') {
      return json_({ ok: true, sheet: sheetName, rows: replaceSheet_(sheetName, headers, rows) });
    }
    if (action === 'clear') {
      clearSheet_(sheetName, headers);
      return json_({ ok: true, sheet: sheetName, cleared: true });
    }
    if (action === 'append') {
      return json_({ ok: true, sheet: sheetName, rows: appendRows_(sheetName, headers, rows) });
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

function replaceSheet_(name, headers, rows) {
  // Grava em aba temporaria e troca o nome — evita GET ver aba vazia no meio do clear+write.
  var ss = getSpreadsheet_();
  var tempName = String(name) + '__next';
  var oldTemp = ss.getSheetByName(tempName);
  if (oldTemp) {
    ss.deleteSheet(oldTemp);
  }
  var tmp = ss.insertSheet(tempName);
  var matrix = [headers].concat(rowsToMatrix_(headers, rows));
  var chunk = 400;
  for (var i = 0; i < matrix.length; i += chunk) {
    var part = matrix.slice(i, i + chunk);
    var range = tmp.getRange(i + 1, 1, part.length, headers.length);
    range.setNumberFormat('@');
    range.setValues(part);
  }
  var current = ss.getSheetByName(name);
  if (current) {
    ss.deleteSheet(current);
  }
  tmp.setName(name);
  return Math.max(matrix.length - 1, 0);
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

function json_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
