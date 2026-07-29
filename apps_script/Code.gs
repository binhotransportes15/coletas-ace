/**
 * ACE · Apps Script receptor
 *
 * 1) Extensoes → Apps Script (nesta planilha)
 * 2) Cole este codigo no Code.gs
 * 3) Ajuste SECRET abaixo (o mesmo valor do config.json → apps_script_token)
 * 4) Implantar → Nova implantacao → Tipo: App da Web
 *    - Executar como: Eu
 *    - Quem tem acesso: Qualquer pessoa
 * 5) Copie a URL da implantacao para config.json → apps_script_url
 */

const SECRET = 'coletas-ace'; // deve ser igual a apps_script_token no config.json do ACE

function doGet() {
  return json_({
    ok: true,
    service: 'ACE Sheets Bridge',
    hint: 'Use POST com token para enviar coletas/historico/resumo.',
  });
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return json_({ ok: false, error: 'body vazio' });
    }
    const data = JSON.parse(e.postData.contents);
    const received = String((data && data.token) || '').trim();
    const expected = String(SECRET || '').trim();
    if (!data || received !== expected) {
      return json_({
        ok: false,
        error: 'nao autorizado',
        hint: 'SECRET no Apps Script precisa ser igual ao apps_script_token do ACE. Depois de alterar, faca Nova versao na implantacao.',
      });
    }

    const action = String(data.action || 'replace').toLowerCase();
    const sheetName = String(data.sheet || '');
    const headers = data.headers || [];
    const rows = data.rows || [];

    if (!sheetName) {
      return json_({ ok: false, error: 'sheet obrigatorio' });
    }
    if (!headers.length) {
      return json_({ ok: false, error: 'headers obrigatorios' });
    }

    if (action === 'replace') {
      const written = replaceSheet_(sheetName, headers, rows);
      return json_({ ok: true, sheet: sheetName, rows: written });
    }

    if (action === 'clear') {
      clearSheet_(sheetName, headers);
      return json_({ ok: true, sheet: sheetName, cleared: true });
    }

    if (action === 'append') {
      const written = appendRows_(sheetName, headers, rows);
      return json_({ ok: true, sheet: sheetName, rows: written });
    }

    return json_({ ok: false, error: 'action invalida: ' + action });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function getOrCreateSheet_(name) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
  }
  return sh;
}

function clearSheet_(name, headers) {
  const sh = getOrCreateSheet_(name);
  sh.clear();
  if (headers && headers.length) {
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
  }
}

function appendRows_(name, headers, rows) {
  const sh = getOrCreateSheet_(name);
  const values = sh.getDataRange().getValues();
  if (!values.length || !values[0] || !values[0].length) {
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
  }
  if (!rows.length) {
    return 0;
  }
  const matrix = rowsToMatrix_(headers, rows);
  const startRow = sh.getLastRow() + 1;
  sh.getRange(startRow, 1, matrix.length, headers.length).setValues(matrix);
  return matrix.length;
}

function replaceSheet_(name, headers, rows) {
  const sh = getOrCreateSheet_(name);
  sh.clear();
  const matrix = [headers].concat(rowsToMatrix_(headers, rows));
  // Escreve em blocos para planilhas grandes
  const chunk = 400;
  for (let i = 0; i < matrix.length; i += chunk) {
    const part = matrix.slice(i, i + chunk);
    sh.getRange(i + 1, 1, part.length, headers.length).setValues(part);
  }
  return Math.max(matrix.length - 1, 0);
}

function rowsToMatrix_(headers, rows) {
  return (rows || []).map(function (row) {
    return headers.map(function (h) {
      const v = row[h];
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
