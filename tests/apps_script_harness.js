const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

class Range {
  constructor(sheet, row, col, rows, cols) {
    this.sheet = sheet; this.row = row; this.col = col; this.rows = rows; this.cols = cols;
  }
  getDisplayValues() {
    const values = [];
    for (let r = 0; r < this.rows; r++) {
      const row = [];
      for (let c = 0; c < this.cols; c++) {
        let value = (this.sheet.rows[this.row - 1 + r] || [])[this.col - 1 + c] || '';
        if (typeof value === 'string' && value.startsWith("'")) value = value.slice(1);
        row.push(String(value));
      }
      values.push(row);
    }
    return values;
  }
  getDisplayValue() { return this.getDisplayValues()[0][0]; }
  getFormulas() {
    return this.getDisplayValues().map(row => row.map(value =>
      typeof value === 'string' && value.startsWith('=') ? value : ''
    ));
  }
  getFormula() { return this.getFormulas()[0][0]; }
  setValues(values) {
    if (this.sheet.failAfterWrite) {
      this.sheet.failAfterWrite = false;
      this.write_(values);
      throw new Error('simulated_lost_response');
    }
    this.write_(values);
  }
  setFormula(formula) { this.write_([[formula]]); }
  setNumberFormat(format) {
    this.sheet.numberFormats.push({
      row: this.row, col: this.col, rows: this.rows, cols: this.cols, format
    });
    return this;
  }
  write_(values) {
    for (let r = 0; r < values.length; r++) {
      while (this.sheet.rows.length < this.row + r) this.sheet.rows.push([]);
      for (let c = 0; c < values[r].length; c++) {
        this.sheet.rows[this.row - 1 + r][this.col - 1 + c] = values[r][c];
      }
    }
  }
}

class Sheet {
  constructor(name, rows) {
    this.name = name; this.rows = rows; this.failAfterWrite = false;
    this.numberFormats = [];
  }
  getName() { return this.name; }
  getLastRow() { return this.rows.length; }
  getRange(row, col, rows, cols) { return new Range(this, row, col, rows, cols); }
}

const sourceHeaders = [
  'record_key','Дата сообщения','Группа','Отправитель','Источник пересылки',
  'Текст новости','Ссылки','Хештеги','ID сообщения','ID группы','Дата сбора'
];
const fullTextHeaders = ['Полный текст', 'Статус загрузки', 'Дата загрузки'];
const targetHeaders = sourceHeaders.concat(['Статус обработки']).concat(fullTextHeaders).concat(['Джемини', 'Гига']);
const analysisHeaders = [
  'ID анализа','record_key','Дата и время новости','Ссылка на новость','Модель',
  'Статус анализа','Тип происшествия','Регион','Населённый пункт',
  'Краткое описание','Количество объектов','Текст на английском',
  'Дата и время анализа'
];
const objectHeaders = [
  'ID анализа','Номер объекта','Модель','Название объекта','Тип объекта',
  'Организация','Регион','Населённый пункт','Адрес / ориентир',
  'Кадастровый номер','Повреждения','Подтверждающий фрагмент','Примечание ИИ',
  'Дата и время новости','Кадастровый квартал','Все кадастровые','Джемини'
];
const responseHeaders = [
  'ID анализа','Модель','Дата и время запроса','Входной текст',
  'Полный ответ модели','Ошибка'
];
const cadastralHeaders = [
  'ID объекта','Регион','Населённый пункт','Адрес','Кадастровый номер',
  'Кадастровый квартал','Все кадастровые номера','Статус','Ошибка','Дата проверки'
];
const sourceSheet = new Sheet('ИЖС-КОРП', [sourceHeaders, ['-1:1', '2026-01-01T00:00:00Z']]);
const targetSheet = new Sheet('НОВОСТИ', [targetHeaders]);
const analysisSheet = new Sheet('АНАЛИЗ', [analysisHeaders]);
const objectsSheet = new Sheet('ОБЪЕКТЫ', [objectHeaders]);
const responsesSheet = new Sheet('ИИ_ОТВЕТЫ', [responseHeaders]);
const cadastralSheet = new Sheet('КАДАСТРЫ', [cadastralHeaders]);
const properties = {
  ANALYSER_SECRET: 'secret',
  SOURCE_SPREADSHEET_ID: 'source-id',
  TARGET_SPREADSHEET_ID: 'target-id'
};
const scriptProperties = {
  getProperty: key => properties[key],
  setProperty: (key, value) => { properties[key] = String(value); },
  deleteProperty: key => { delete properties[key]; }
};
let uuidCounter = 1;

const context = {
  console, Set,
  PropertiesService: {getScriptProperties: () => scriptProperties},
  Utilities: {getUuid: () => `123e4567-e89b-42d3-a456-${String(uuidCounter++).padStart(12, '0')}`},
  SpreadsheetApp: {openById: id => ({
    getSheetByName: name => id === 'source-id' && name === 'ИЖС-КОРП' ? sourceSheet
      : id === 'target-id' && name === 'НОВОСТИ' ? targetSheet
      : id === 'target-id' && name === 'АНАЛИЗ' ? analysisSheet
      : id === 'target-id' && name === 'ОБЪЕКТЫ' ? objectsSheet
      : id === 'target-id' && name === 'ИИ_ОТВЕТЫ' ? responsesSheet
      : id === 'target-id' && name === 'КАДАСТРЫ' ? cadastralSheet : null
  })},
  LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {}})},
  ContentService: {
    MimeType: {JSON: 'json'},
    createTextOutput: text => ({text, setMimeType() { return this; }})
  }
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('apps_script/Code.gs', 'utf8'), context);

assert.equal(context.readyGeneratedText_('Готовый ответ', '=AI("prompt";M2)'), 'Готовый ответ');
assert.equal(context.readyGeneratedText_('=AI("prompt";M2)', '=AI("prompt";M2)'), '');
assert.equal(context.readyGeneratedText_('#N/A', '=AI("prompt";M2)'), '');
assert.equal(context.readyGeneratedText_('-', ''), '');
assert.equal(context.sheetDateTimeSerial_(''), '');
assert.throws(
  () => context.sheetDateTimeSerial_('31.02.2026 12:00:00'),
  /invalid_news_datetime/
);

function request(body) {
  const output = context.doPost({postData: {contents: JSON.stringify(body)}});
  return JSON.parse(output.text);
}

assert.equal(request({version: 1, action: 'read_source', secret: 'wrong'}).error, 'unauthorized');
const source = request({version: 1, action: 'read_source', secret: 'secret'});
assert.equal(source.ok, true);
assert.equal(source.values[1][0], '-1:1');

const row = ['-1:1', '01.01.2026 05:00:00', '', '', '', '=формула', '', '', '1', '-1', '', 'Ожидает анализа'];
const inserted = request({version: 1, action: 'append_target', secret: 'secret', row});
assert.equal(inserted.status, 'inserted');
assert.equal(targetSheet.rows[1][5], "'=формула");

targetSheet.rows[1][11] = 'Исправлено вручную';
const repeated = request({version: 1, action: 'append_target', secret: 'secret', row});
assert.equal(repeated.status, 'exists');
assert.equal(targetSheet.rows.length, 2);
assert.equal(targetSheet.rows[1][11], 'Исправлено вручную');

targetSheet.failAfterWrite = true;
const row2 = ['-1:2', '', '', '', '', 'Текст', '', '', '2', '-1', '', 'Ожидает анализа'];
const ambiguousAppend = request({version: 1, action: 'append_target', secret: 'secret', row: row2});
assert.equal(ambiguousAppend.status, 'error');
assert.equal(ambiguousAppend.error, 'internal_error');
assert.equal(request({version: 1, action: 'append_target', secret: 'secret', row: row2}).status, 'exists');
assert.equal(targetSheet.rows.length, 3);

const keys = request({version: 1, action: 'read_target_keys', secret: 'secret'});
assert.deepEqual(keys.keys, ['-1:1', '-1:2']);

targetSheet.rows[1][6] = 'https://t.me/example_channel/1';
const candidates = request({
  version: 1, action: 'read_full_text_candidates', secret: 'secret', limit: 1
});
assert.equal(candidates.ok, true);
assert.deepEqual(candidates.candidates, [
  {
    record_key: '-1:1',
    links: 'https://t.me/example_channel/1',
    source_text: '=формула'
  }
]);

const protectedColumns = targetSheet.rows[1].slice(0, 12);
const updated = request({
  version: 1,
  action: 'update_full_text',
  secret: 'secret',
  record_key: '-1:1',
  full_text: 'Полный текст',
  load_status: 'Загружено',
  loaded_at: '27.10.2026 12:00:00'
});
assert.equal(updated.status, 'updated');
assert.deepEqual(targetSheet.rows[1].slice(0, 12), protectedColumns);
assert.deepEqual(targetSheet.rows[1].slice(12, 15), [
  'Полный текст', 'Загружено', '27.10.2026 12:00:00'
]);

// TG-05 prepares only P, references M on the same row, and never overwrites P.
const geminiCandidates = request({
  version: 1, action: 'read_gemini_formula_candidates', secret: 'secret',
  limit: 1, record_key: '-1:1'
});
assert.deepEqual(geminiCandidates.candidates, [{
  record_key: '-1:1', source_cell: 'M2', target_cell: 'P2'
}]);
const protectedBeforeGemini = targetSheet.rows[1].slice(0, 15);
const analysisRowsBeforeGemini = analysisSheet.rows.length;
const objectRowsBeforeGemini = objectsSheet.rows.length;
const responseRowsBeforeGemini = responsesSheet.rows.length;
assert.equal(request({
  version: 1, action: 'prepare_gemini_formula', secret: 'secret', record_key: '-1:1'
}).status, 'updated');
assert.deepEqual(targetSheet.rows[1].slice(0, 15), protectedBeforeGemini);
assert.equal(targetSheet.rows[1][15], context.buildGeminiFormula_(2));
assert.ok(targetSheet.rows[1][15].startsWith('=AI("Analyze the news text'));
assert.ok(targetSheet.rows[1][15].endsWith(';M2)'));
assert.equal(analysisSheet.rows.length, analysisRowsBeforeGemini);
assert.equal(objectsSheet.rows.length, objectRowsBeforeGemini);
assert.equal(responsesSheet.rows.length, responseRowsBeforeGemini);
assert.equal(request({
  version: 1, action: 'prepare_gemini_formula', secret: 'secret', record_key: '-1:1'
}).status, 'already_processed');
targetSheet.rows[1][15] = 'Готовый ручной результат';
assert.equal(request({
  version: 1, action: 'prepare_gemini_formula', secret: 'secret', record_key: '-1:1'
}).status, 'already_processed');
assert.equal(targetSheet.rows[1][15], 'Готовый ручной результат');

// TG-06 writes plain text only to Q and preserves M:P and all GigaChat journals.
const gigaCandidates = request({
  version: 1, action: 'read_giga_text_candidates', secret: 'secret',
  limit: 1, record_key: '-1:1'
});
assert.deepEqual(gigaCandidates.candidates, [{
  record_key: '-1:1', full_text: 'Полный текст',
  source_cell: 'M2', target_cell: 'Q2'
}]);
const protectedBeforeGiga = targetSheet.rows[1].slice(0, 16);
const analysisRowsBeforeGiga = analysisSheet.rows.length;
const objectRowsBeforeGiga = objectsSheet.rows.length;
const responseRowsBeforeGiga = responsesSheet.rows.length;
const gigaResult = 'Тип инцидента: Пожар\n\nОбъект 1:\n- Название: Дом';
assert.equal(request({
  version: 1, action: 'write_giga_text_result', secret: 'secret',
  record_key: '-1:1', result: gigaResult
}).status, 'updated');
assert.deepEqual(targetSheet.rows[1].slice(0, 16), protectedBeforeGiga);
assert.equal(targetSheet.rows[1][16], gigaResult);
assert.equal(analysisSheet.rows.length, analysisRowsBeforeGiga);
assert.equal(objectsSheet.rows.length, objectRowsBeforeGiga);
assert.equal(responsesSheet.rows.length, responseRowsBeforeGiga);
assert.equal(request({
  version: 1, action: 'write_giga_text_result', secret: 'secret',
  record_key: '-1:1', result: 'Не перезаписывать'
}).status, 'already_processed');
assert.equal(targetSheet.rows[1][16], gigaResult);
targetSheet.rows[1][16] = 'Ручной результат Q';
assert.equal(request({
  version: 1, action: 'write_giga_text_result', secret: 'secret',
  record_key: '-1:1', result: 'Не перезаписывать'
}).status, 'already_processed');
assert.equal(targetSheet.rows[1][16], 'Ручной результат Q');
assert.deepEqual(request({
  version: 1, action: 'read_giga_text_candidates', secret: 'secret',
  limit: 1, record_key: '-1:1'
}).candidates, []);

const repeatedFullText = request({
  version: 1,
  action: 'update_full_text',
  secret: 'secret',
  record_key: '-1:1',
  full_text: 'Не перезаписывать',
  load_status: 'Загружено',
  loaded_at: '28.10.2026 12:00:00'
});
assert.equal(repeatedFullText.status, 'already_processed');
assert.deepEqual(targetSheet.rows[1].slice(12, 15), [
  'Полный текст', 'Загружено', '27.10.2026 12:00:00'
]);

const missing = request({
  version: 1,
  action: 'update_full_text',
  secret: 'secret',
  record_key: '-1:999',
  full_text: 'Текст',
  load_status: 'Загружено',
  loaded_at: '27.10.2026 12:00:00'
});
assert.equal(missing.status, 'not_found');

const fallbackUpdate = request({
  version: 1, action: 'update_full_text', secret: 'secret',
  record_key: '-1:2', full_text: 'Текст из F',
  load_status: 'Неподдерживаемая ссылка', loaded_at: ''
});
assert.equal(fallbackUpdate.status, 'updated');
assert.deepEqual(targetSheet.rows[2].slice(12, 15), [
  'Текст из F', 'Неподдерживаемая ссылка', ''
]);
targetSheet.rows[2][12] = '';
targetSheet.rows[2][13] = '';
targetSheet.rows[2][14] = '';

// TG-04 backfill writes only an empty M from F and preserves L and N:O.
targetSheet.rows[2][11] = 'Обработано';
targetSheet.rows[2][12] = '';
targetSheet.rows[2][13] = 'Неподдерживаемая ссылка';
targetSheet.rows[2][14] = '26.10.2026 12:00:00';
const backfillCandidates = request({
  version: 1, action: 'read_full_text_backfill_candidates',
  secret: 'secret', limit: 20
});
assert.deepEqual(backfillCandidates.candidates, [{record_key: '-1:2'}]);
const beforeBackfillProtected = targetSheet.rows[2].slice();
const analysisRowsBeforeBackfill = analysisSheet.rows.length;
const responseRowsBeforeBackfill = responsesSheet.rows.length;
assert.equal(request({
  version: 1, action: 'backfill_full_text', secret: 'secret', record_key: '-1:2'
}).status, 'updated');
assert.equal(targetSheet.rows[2][12], 'Текст');
assert.equal(targetSheet.rows[2][11], beforeBackfillProtected[11]);
assert.deepEqual(targetSheet.rows[2].slice(13, 15), beforeBackfillProtected.slice(13, 15));
assert.equal(analysisSheet.rows.length, analysisRowsBeforeBackfill);
assert.equal(responsesSheet.rows.length, responseRowsBeforeBackfill);
assert.equal(request({
  version: 1, action: 'backfill_full_text', secret: 'secret', record_key: '-1:2'
}).status, 'already_processed');
targetSheet.rows[2][12] = '';
targetSheet.rows[2][5] = '   ';
assert.equal(request({
  version: 1, action: 'backfill_full_text', secret: 'secret', record_key: '-1:2'
}).status, 'no_source_text');
assert.equal(request({
  version: 1, action: 'prepare_gemini_formula', secret: 'secret', record_key: '-1:2'
}).status, 'no_full_text');
assert.equal(request({
  version: 1, action: 'write_giga_text_result', secret: 'secret',
  record_key: '-1:2', result: 'Результат'
}).status, 'no_full_text');
targetSheet.rows[2][15] = '=AI("prompt";M3)';
assert.deepEqual(request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2'
}).candidates, []);
targetSheet.rows[2][15] = '';
targetSheet.rows[2][5] = 'Текст';
targetSheet.rows[2][11] = 'Ожидает анализа';
targetSheet.rows[2][13] = '';
targetSheet.rows[2][14] = '';

// TG-07.2 requires both P and Q. Incomplete rows remain waiting for later runs.
targetSheet.rows[2][16] = 'Готовый Q';
assert.equal(request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2'
}).candidates.length, 0);
assert.equal(targetSheet.rows[2][11], 'Ожидает анализа');
assert.equal(request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2', claim: true
}).candidates.length, 0);
assert.equal(targetSheet.rows[2][11], 'Ожидает анализа');
assert.equal(properties['AI_LEASE_-1:2'], undefined);

targetSheet.rows[2][15] = '=AI("prompt";M3)';
assert.equal(request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2'
}).candidates.length, 0);
targetSheet.rows[2][15] = '-';
assert.equal(request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2'
}).candidates.length, 0);
targetSheet.rows[2][15] = 'Готовый P';
targetSheet.rows[2][16] = '';
const geminiOnlyPreview = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2'
});
assert.equal(geminiOnlyPreview.candidates[0].record_key, '-1:2');
assert.equal(geminiOnlyPreview.candidates[0].source_models, 'Gemini');
assert.equal(geminiOnlyPreview.candidates[0].giga_text, '');
assert.equal(geminiOnlyPreview.candidates[0].claim_token, '');
assert.equal(targetSheet.rows[2][11], 'Ожидает анализа');
const geminiOnlyClaim = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2', claim: true
});
assert.ok(geminiOnlyClaim.candidates[0].claim_token);
assert.equal(geminiOnlyClaim.candidates[0].source_models, 'Gemini');
assert.equal(targetSheet.rows[2][11], 'В обработке');
delete properties['AI_LEASE_-1:2'];
targetSheet.rows[2][11] = 'Ожидает анализа';

targetSheet.rows[2][16] = 'Готовый Q';
const bothReadyPreview = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2'
});
assert.equal(bothReadyPreview.candidates[0].record_key, '-1:2');
assert.equal(bothReadyPreview.candidates[0].source_models, 'Gemini + GigaChat');
assert.equal(bothReadyPreview.candidates[0].claim_token, '');
assert.equal(targetSheet.rows[2][11], 'Ожидает анализа');
const bothReadyClaim = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2', claim: true
});
assert.ok(bothReadyClaim.candidates[0].claim_token);
assert.equal(targetSheet.rows[2][11], 'В обработке');
delete properties['AI_LEASE_-1:2'];
targetSheet.rows[2][11] = 'Ожидает анализа';

// One waiting row does not prevent a later fully ready row from being selected.
targetSheet.rows[1][11] = 'Ожидает анализа';
targetSheet.rows[1][15] = '';
targetSheet.rows[1][16] = 'Ручной результат Q';
const laterReady = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1
});
assert.equal(laterReady.candidates[0].record_key, '-1:2');
assert.equal(targetSheet.rows[1][11], 'Ожидает анализа');
assert.equal(targetSheet.rows[2][11], 'Ожидает анализа');

// TG-07 preview is read-only; apply claims L when both P/Q results are ready.
targetSheet.rows[1][11] = 'Ожидает анализа';
targetSheet.rows[1][15] = 'Готовый ручной результат';
targetSheet.rows[1][16] = 'Ручной результат Q';
targetSheet.rows[2][15] = '';
targetSheet.rows[2][16] = '';
const newsBeforeAi = JSON.stringify(targetSheet.rows);
const aiCandidates = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1
});
assert.equal(aiCandidates.ok, true);
assert.equal(aiCandidates.candidates[0].record_key, '-1:1');
assert.equal(aiCandidates.candidates[0].gemini_text, 'Готовый ручной результат');
assert.equal(aiCandidates.candidates[0].giga_text, 'Ручной результат Q');
assert.equal(aiCandidates.candidates[0].source_models, 'Gemini + GigaChat');
assert.equal(aiCandidates.candidates[0].claim_token, '');
assert.equal(JSON.stringify(targetSheet.rows), newsBeforeAi);
const claimed = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  claim: true
});
assert.equal(claimed.candidates[0].record_key, '-1:1');
assert.ok(claimed.candidates[0].claim_token);
assert.equal(targetSheet.rows[1][11], 'В обработке');
const claimToken = claimed.candidates[0].claim_token;

const analysisId = '123e4567-e89b-42d3-a456-426614174000';
const responseRow = [
  analysisId, 'GigaChat', '2026-10-27T13:00:00+05:00',
  '[Gemini]\nГотовый ручной результат\n\n[GigaChat]\nРучной результат Q',
  '{"objects":[]}', ''
];
assert.equal(request({
  version: 1, action: 'save_ai_response', secret: 'secret', response_row: responseRow
}).status, 'inserted');
assert.equal(request({
  version: 1, action: 'save_ai_response', secret: 'secret', response_row: responseRow
}).status, 'exists');
assert.equal(responsesSheet.rows.length, 2);
assert.equal(request({
  version: 1, action: 'update_ai_response_error', secret: 'secret',
  analysis_id: analysisId, expected_error: '', error: 'invalid JSON'
}).status, 'updated');
assert.equal(request({
  version: 1, action: 'update_ai_response_error', secret: 'secret',
  analysis_id: analysisId, expected_error: '', error: 'invalid JSON'
}).status, 'exists');
assert.equal(responsesSheet.rows[1][5], 'invalid JSON');

const analysisRow = [
  analysisId, '-1:1', '01.01.2026 05:00:00', 'https://t.me/example_channel/1',
  'GigaChat', 'Успешно', 'Обстрел', 'Белгородская область', 'Белгород',
  'Повреждены два объекта', '2', '', '2026-10-27T13:00:01+05:00'
];
const objectRows = [
  [analysisId, '1', 'Gemini + GigaChat', 'Дом', 'жилой дом', '', 'Белгородская область',
    'Белгород', 'ул. Первая', '', 'повреждена кровля', 'фрагмент 1', '',
    '01.01.2026 05:00:00'],
  [analysisId, '2', 'Gemini + GigaChat', 'Автомобиль', 'транспорт', '', 'Белгородская область',
    'Белгород', '', '', 'повреждён', 'фрагмент 2', '', '01.01.2026 05:00:00']
];
analysisSheet.failAfterWrite = true;
const partialWrite = request({
  version: 1, action: 'save_ai_analysis', secret: 'secret',
  analysis_row: analysisRow, object_rows: objectRows
});
assert.equal(partialWrite.status, 'error');
assert.equal(context.successfulGigaChatKeys_(analysisSheet, objectsSheet).has('-1:1'), false);
const resumedWrite = request({
  version: 1, action: 'save_ai_analysis', secret: 'secret',
  analysis_row: analysisRow, object_rows: objectRows
});
assert.equal(resumedWrite.status, 'completed');
assert.equal(resumedWrite.analysis_inserted, 0);
assert.equal(resumedWrite.objects_inserted, 2);
assert.equal(analysisSheet.rows.length, 2);
assert.equal(objectsSheet.rows.length, 3);
assert.equal(objectsSheet.rows[1].length, 17);
assert.equal(typeof objectsSheet.rows[1][13], 'number');
assert.equal(objectsSheet.rows[1][13], objectsSheet.rows[2][13]);
assert.equal(objectsSheet.rows[1][13], context.sheetDateTimeSerial_('01.01.2026 05:00:00'));
assert.deepEqual(objectsSheet.rows[1].slice(0, 13), objectRows[0].slice(0, 13));
assert.equal(objectsSheet.numberFormats.at(-1).format, 'dd.MM.yyyy HH:mm:ss');
assert.equal(context.successfulGigaChatKeys_(analysisSheet, objectsSheet).has('-1:1'), true);
const repeatedAnalysis = request({
  version: 1, action: 'save_ai_analysis', secret: 'secret',
  analysis_row: analysisRow, object_rows: objectRows
});
assert.equal(repeatedAnalysis.status, 'exists');
assert.equal(analysisSheet.rows.length, 2);
assert.equal(objectsSheet.rows.length, 3);

const enrichmentCandidates = request({
  version: 1, action: 'read_object_enrichment_candidates', secret: 'secret',
  limit: 1, row_number: 2
});
assert.equal(enrichmentCandidates.candidates.length, 1);
assert.equal(enrichmentCandidates.candidates[0].row_number, 2);
assert.equal(enrichmentCandidates.candidates[0].cadastral_number, '');
const enriched = request({
  version: 1, action: 'update_object_enrichment', secret: 'secret',
  analysis_id: analysisId, object_number: '1',
  values: {
    cadastral_number: '31:16:0101001:25',
    cadastral_quarter: '31:16:0101001',
    all_cadastral: '31:16:0101001:25'
  },
  prepare_gemini: true
});
assert.equal(enriched.status, 'updated');
assert.deepEqual(enriched.updated_fields, [
  'cadastral_number', 'cadastral_quarter', 'all_cadastral', 'gemini'
]);
assert.equal(objectsSheet.rows[1][9], '31:16:0101001:25');
assert.equal(objectsSheet.rows[1][14], '31:16:0101001');
assert.equal(objectsSheet.rows[1][15], '31:16:0101001:25');
assert.ok(objectsSheet.rows[1][16].startsWith('=AI("'));
assert.ok(objectsSheet.rows[1][16].endsWith(';D2:I2)'));
assert.ok(objectsSheet.rows[1][16].includes('organization name from column F'));
assert.ok(objectsSheet.rows[1][16].includes("return 'Not found'"));

// Re-read and write both treat formulas and manual text as occupied.
const repeatedEnrichment = request({
  version: 1, action: 'update_object_enrichment', secret: 'secret',
  analysis_id: analysisId, object_number: '1',
  values: {
    cadastral_number: '99:99:9999999:99',
    cadastral_quarter: '99:99:9999999',
    all_cadastral: '99:99:9999999:99'
  },
  prepare_gemini: true
});
assert.equal(repeatedEnrichment.status, 'already_processed');
assert.equal(objectsSheet.rows[1][9], '31:16:0101001:25');
assert.equal(request({
  version: 1, action: 'read_object_enrichment_candidates', secret: 'secret',
  limit: 1, row_number: 2
}).candidates.length, 0);

objectsSheet.rows[2][9] = '-';
const independentFields = request({
  version: 1, action: 'update_object_enrichment', secret: 'secret',
  analysis_id: analysisId, object_number: '2',
  values: {cadastral_number: '31:16:0101001:77', cadastral_quarter: '31:16:0101001'},
  prepare_gemini: false
});
assert.deepEqual(independentFields.updated_fields, ['cadastral_quarter']);
assert.equal(objectsSheet.rows[2][9], '-');
assert.equal(objectsSheet.rows[2][14], '31:16:0101001');
assert.deepEqual(targetSheet.rows[1].slice(0, 11), JSON.parse(newsBeforeAi)[1].slice(0, 11));
assert.deepEqual(targetSheet.rows[1].slice(12, 17), JSON.parse(newsBeforeAi)[1].slice(12, 17));

const finalized = request({
  version: 1, action: 'finalize_ai_candidate', secret: 'secret',
  record_key: '-1:1', claim_token: claimToken, final_status: 'Обработано'
});
assert.equal(finalized.status, 'updated');
assert.equal(targetSheet.rows[1][11], 'Обработано');
assert.equal(request({
  version: 1, action: 'finalize_ai_candidate', secret: 'secret',
  record_key: '-1:1', claim_token: claimToken, final_status: 'Обработано'
}).status, 'already_finalized');

// A successful final analysis is always skipped, even if a second source appears later.
assert.equal(request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:1'
}).candidates.length, 0);
targetSheet.rows.push([...targetSheet.rows[1]]);
const duplicateTarget = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:1'
});
assert.equal(duplicateTarget.status, 'error');
assert.equal(duplicateTarget.error, 'duplicate_record_key_target');
targetSheet.rows.pop();

// An error is skipped until the user explicitly returns it to the queue.
const errorId = '223e4567-e89b-42d3-a456-426614174001';
responsesSheet.rows.push([errorId, 'GigaChat', '', 'Текст', '', 'invalid JSON']);
analysisSheet.rows.push([errorId, '-1:2', '', '', 'GigaChat', 'Ошибка', '', '', '', '', '0', '', '']);
targetSheet.rows[2][12] = 'Другой полный текст';
targetSheet.rows[2][15] = 'Ответ Gemini для повторной попытки';
targetSheet.rows[2][16] = 'Ответ GigaChat для повторной попытки';
targetSheet.rows[2][11] = 'Ошибка анализа';
assert.equal(request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 20
}).candidates.length, 0);
targetSheet.rows[2][11] = 'Ожидает анализа';
const retryable = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1
});
assert.equal(retryable.candidates[0].record_key, '-1:2');

// Manual skip always wins.
targetSheet.rows[2][11] = 'Пропущено';
assert.equal(request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2', claim: true
}).candidates.length, 0);

// An active lease prevents a concurrent claim; an expired lease is recoverable.
targetSheet.rows[2][11] = 'В обработке';
properties['AI_LEASE_-1:2'] = JSON.stringify({
  token: '323e4567-e89b-42d3-a456-426614174001',
  expires_at: Date.now() + 60_000
});
assert.equal(request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2', claim: true
}).candidates.length, 0);
properties['AI_LEASE_-1:2'] = JSON.stringify({
  token: '323e4567-e89b-42d3-a456-426614174001',
  expires_at: Date.now() - 1
});
const recovered = request({
  version: 1, action: 'read_ai_candidates', secret: 'secret', limit: 1,
  record_key: '-1:2', claim: true
});
assert.equal(recovered.candidates[0].record_key, '-1:2');
assert.notEqual(recovered.candidates[0].claim_token, '323e4567-e89b-42d3-a456-426614174001');
assert.equal(targetSheet.rows[2][11], 'В обработке');

// The cadastral queue is idempotent, imports only into empty J/O/P, and keeps Q.
const firstQueueSync = request({
  version: 1, action: 'sync_cadastral_queue', secret: 'secret'
});
assert.equal(firstQueueSync.records_added, objectsSheet.rows.length - 1);
assert.equal(cadastralSheet.rows.length, objectsSheet.rows.length);
const queueTasks = request({
  version: 1, action: 'read_cadastral_tasks', secret: 'secret', limit: 500
});
assert.equal(queueTasks.tasks.length, objectsSheet.rows.length - 1);
const secondObjectTask = queueTasks.tasks.find(task => task.object_id.endsWith('|2'));
assert.ok(secondObjectTask);
assert.equal(request({
  version: 1, action: 'update_cadastral_result', secret: 'secret',
  object_id: secondObjectTask.object_id,
  region: secondObjectTask.region,
  locality: secondObjectTask.locality,
  address: secondObjectTask.address,
  expected_status: 'ОЖИДАЕТ',
  cadastral_number: '',
  cadastral_quarter: '31:16:0101001',
  all_cadastral: '31:16:0101001:77',
  status: 'НАЙДЕНО', error: '', checked_at: '30.10.2026 10:00:00'
}).status, 'updated');
const importedQueue = request({
  version: 1, action: 'sync_cadastral_queue', secret: 'secret'
});
assert.equal(importedQueue.results_received, 1);
assert.equal(objectsSheet.rows[2][9], '-');
assert.equal(objectsSheet.rows[2][15], '31:16:0101001:77');
assert.equal(request({
  version: 1, action: 'sync_cadastral_queue', secret: 'secret'
}).results_received, 0);

const thirdAnalysisId = '423e4567-e89b-42d3-a456-426614174003';
objectsSheet.rows.push([
  thirdAnalysisId, '1', 'GigaChat', 'Объект', '', 'Организация',
  'Регион', 'Город', 'ул. Новая, д. 1', '', '', '', '', '', '', '', ''
]);
const syncWithNewObject = request({
  version: 1, action: 'sync_cadastral_queue', secret: 'secret'
});
assert.equal(syncWithNewObject.records_added, 1);
assert.equal(syncWithNewObject.formulas_prepared, 1);
assert.ok(objectsSheet.rows[objectsSheet.rows.length - 1][16].startsWith('=AI('));
const newTask = request({
  version: 1, action: 'read_cadastral_tasks', secret: 'secret', limit: 500
}).tasks.find(task => task.object_id === thirdAnalysisId + '|1');
objectsSheet.rows[objectsSheet.rows.length - 1][8] = 'ул. Новая, д. 2';
assert.equal(request({
  version: 1, action: 'sync_cadastral_queue', secret: 'secret'
}).address_resets, 1);
assert.equal(request({
  version: 1, action: 'update_cadastral_result', secret: 'secret',
  object_id: newTask.object_id, region: newTask.region,
  locality: newTask.locality, address: newTask.address,
  expected_status: 'ОЖИДАЕТ', cadastral_number: '31:16:1:1',
  cadastral_quarter: '31:16:1', all_cadastral: '31:16:1:1',
  status: 'НАЙДЕНО', error: '', checked_at: '30.10.2026 10:01:00'
}).status, 'stale');

objectsSheet.rows.push([...objectsSheet.rows[objectsSheet.rows.length - 1]]);
const duplicateQueueSource = request({
  version: 1, action: 'sync_cadastral_queue', secret: 'secret'
});
assert.equal(duplicateQueueSource.status, 'error');
assert.equal(duplicateQueueSource.error, 'duplicate_object_identity');
objectsSheet.rows.pop();
console.log('Apps Script offline tests: OK');

