/**
 * ACE · ARMAZÉM · Apps Script (planilha SEPARADA da distribuição)
 *
 * NÃO use a planilha do Coletas/Entregas aqui.
 * Abas deste projeto: Veiculos78 | Resumo78
 *
 * 1. Crie uma planilha NOVA no Google Sheets
 * 2. Copie o ID da URL: .../spreadsheets/d/ID_AQUI/edit
 * 3. Cole em SPREADSHEET_ID abaixo
 * 4. SECRET deve ser igual ao apps_script_token do ACE Armazém (armazem-ace)
 * 5. Salvar → Implantar → App da Web → Qualquer pessoa → copiar URL /exec
 */

var SPREADSHEET_ID = '1frqrIcyO-sN_Xd7m-lU0UF746sYBx5zUc7J9cLUoP9U';
var SECRET = 'armazem-ace';

function doGet(e) {
  try {
    var dados = extrairDados_(e);
    var action = String(dados.action || 'ping').toLowerCase();

    if (action === 'ping' || action === '') {
      return json_({
        ok: true,
        service: 'ACE Armazem Sheets Bridge',
        spreadsheet: SPREADSHEET_ID,
        hint: 'GET action=resumo|veiculos|ping | POST com token para gravar',
      });
    }

    if (action === 'resumo' || action === 'resumo78') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Resumo78'),
        report: '078',
      });
    }

    if (action === 'veiculos' || action === 'veiculos78' || action === '78') {
      return json_({
        ok: true,
        updated_at: new Date().toISOString(),
        rows: sheetToObjects_('Veiculos78'),
        total: sheetToObjects_('Veiculos78').length,
        report: '078',
      });
    }

    return json_({ ok: false, error: 'action invalida: ' + action });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
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
        hint: 'SECRET no Apps Script deve ser igual ao apps_script_token (armazem-ace). Publique Nova versao apos alterar.',
      });
    }

    var action = String(data.action || 'replace').toLowerCase();
    var sheetName = String(data.sheet || '');
    var headers = data.headers || [];
    var rows = data.rows || [];

    if (action === 'ping' || action === 'auth') {
      return json_({
        ok: true,
        service: 'ACE Armazem Sheets Bridge',
        spreadsheet: SPREADSHEET_ID,
        action: 'ping',
      });
    }

    // Só permite abas do Armazém — nunca Coletas/Entregas/etc.
    var allowed = { Veiculos78: true, Resumo78: true, _ping: true, _ace_ping: true };
    if (sheetName && !allowed[sheetName]) {
      return json_({
        ok: false,
        error: 'aba nao permitida neste bridge: ' + sheetName,
        hint: 'Use apenas Veiculos78 ou Resumo78',
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
    throw new Error('Configure SPREADSHEET_ID no Code.gs (ID da planilha NOVA do Armazem).');
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
  if (!sh) return [];
  var values = sh.getDataRange().getValues();
  if (!values || values.length < 2) return [];
  var headers = values[0].map(function (h) { return String(h || '').trim(); });
  var out = [];
  for (var r = 1; r < values.length; r++) {
    var row = values[r];
    var obj = {};
    var empty = true;
    for (var c = 0; c < headers.length; c++) {
      var key = headers[c];
      if (!key) continue;
      var text = cellToText_(row[c], key);
      if (text !== '') empty = false;
      obj[key] = text;
    }
    if (!empty) out.push(obj);
  }
  return out;
}

function cellToText_(val, key) {
  if (val === null || val === undefined || val === '') return '';
  var k = String(key || '').toLowerCase();
  if (Object.prototype.toString.call(val) === '[object Date]' && !isNaN(val.getTime())) {
    var year = val.getFullYear();
    if (year < 1900 || /hora|inicio|final|chegada|saida|prev/.test(k)) {
      return Utilities.formatDate(val, 'America/Sao_Paulo', 'dd/MM HH:mm');
    }
    return Utilities.formatDate(val, 'America/Sao_Paulo', 'dd/MM/yyyy HH:mm');
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
  if (!rows.length) return 0;
  var matrix = rowsToMatrix_(headers, rows);
  var startRow = sh.getLastRow() + 1;
  var body = sh.getRange(startRow, 1, matrix.length, headers.length);
  body.setNumberFormat('@');
  body.setValues(matrix);
  return matrix.length;
}

function replaceSheet_(name, headers, rows) {
  var ss = getSpreadsheet_();
  var tempName = String(name) + '__next';
  var oldTemp = ss.getSheetByName(tempName);
  if (oldTemp) ss.deleteSheet(oldTemp);
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
  if (current) ss.deleteSheet(current);
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
