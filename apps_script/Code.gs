/**
 * News Super Analyser transport.
 *
 * Required Script Properties:
 *   ANALYSER_SECRET        - random shared secret used by the Python client
 *   SOURCE_SPREADSHEET_ID  - spreadsheet containing the ИЖС-КОРП sheet
 *   TARGET_SPREADSHEET_ID  - spreadsheet containing the НОВОСТИ sheet
 *
 * Deploy as a web app executing as the script owner. The handler authenticates
 * every request before reading or writing either private spreadsheet.
 */

const SOURCE_SHEET = 'ИЖС-КОРП';
const TARGET_SHEET = 'НОВОСТИ';
const ANALYSIS_SHEET = 'АНАЛИЗ';
const OBJECTS_SHEET = 'ОБЪЕКТЫ';
const AI_RESPONSES_SHEET = 'ИИ_ОТВЕТЫ';
const SOURCE_HEADERS = [
  'record_key',
  'Дата сообщения',
  'Группа',
  'Отправитель',
  'Источник пересылки',
  'Текст новости',
  'Ссылки',
  'Хештеги',
  'ID сообщения',
  'ID группы',
  'Дата сбора'
];
const TARGET_HEADERS = SOURCE_HEADERS.concat(['Статус обработки']);
const FULL_TEXT_HEADERS = ['Полный текст', 'Статус загрузки', 'Дата загрузки'];
const FULL_TEXT_FIRST_COLUMN = 13;
const GEMINI_HEADER = 'Джемини';
const GEMINI_COLUMN = 16;
const GEMINI_PROMPT = 'Analyze the news text in the referenced cell. Return a structured plain-text report in Russian, not JSON. Include the incident type, a brief summary, and every affected object. For each object include its name or description, object type, owner or responsible organization, exact address or location, cadastral number, damage, and short confirming quotations from the source text. Clearly mark information that is not stated. Do not invent facts or use external information.';
const GIGA_HEADER = 'Гига';
const GIGA_COLUMN = 17;
const MAX_FULL_TEXT_BATCH_SIZE = 20;
const MAX_GEMINI_BATCH_SIZE = 20;
const MAX_GIGA_BATCH_SIZE = 20;
const MAX_AI_BATCH_SIZE = 20;
const NEWS_STATUS_COLUMN = 12;
const AI_LEASE_PREFIX = 'AI_LEASE_';
const AI_LEASE_MILLISECONDS = 2 * 60 * 60 * 1000;
const NEWS_STATUS_WAITING = 'Ожидает анализа';
const NEWS_STATUS_PROCESSING = 'В обработке';
const NEWS_STATUS_DONE = 'Обработано';
const NEWS_STATUS_ERROR = 'Ошибка анализа';
const NEWS_STATUS_SKIPPED = 'Пропущено';
const ANALYSIS_HEADERS = [
  'ID анализа', 'record_key', 'Дата и время новости', 'Ссылка на новость',
  'Модель', 'Статус анализа', 'Тип происшествия', 'Регион',
  'Населённый пункт', 'Краткое описание', 'Количество объектов',
  'Текст на английском', 'Дата и время анализа'
];
const OBJECT_BASE_HEADERS = [
  'ID анализа', 'Номер объекта', 'Модель', 'Название объекта', 'Тип объекта',
  'Организация', 'Регион', 'Населённый пункт', 'Адрес / ориентир',
  'Кадастровый номер', 'Повреждения', 'Подтверждающий фрагмент',
  'Примечание ИИ', 'Дата и время новости'
];
const OBJECT_ENRICHMENT_HEADERS = [
  'Кадастровый квартал', 'Все кадастровые', 'Джемини'
];
const OBJECT_HEADERS = OBJECT_BASE_HEADERS.concat(OBJECT_ENRICHMENT_HEADERS);
const MAX_OBJECT_BATCH_SIZE = 500;
const OBJECT_GEMINI_PROMPT = "Find one publicly available street address of the organization or property described in the provided row, located in Russia. Use the organization name from column F if available; otherwise use the property name from column D. Use the city from column H and the region from column G. Return only the address, preferably in Russian. If no reliable address can be found, return 'Not found'. Do not invent information.";
const NEWS_TIMEZONE_UTC_OFFSET_MINUTES = 5 * 60;
const NEWS_DATE_TIME_NUMBER_FORMAT = 'dd.MM.yyyy HH:mm:ss';
const AI_RESPONSE_HEADERS = [
  'ID анализа', 'Модель', 'Дата и время запроса', 'Входной текст',
  'Полный ответ модели', 'Ошибка'
];
const MAX_CELL_LENGTH = 50000;


function doPost(e) {
  let request;
  try {
    request = JSON.parse((e && e.postData && e.postData.contents) || '');
  } catch (error) {
    return jsonResponse_({ok: false, error: 'invalid_json'});
  }

  const properties = PropertiesService.getScriptProperties();
  const expectedSecret = properties.getProperty('ANALYSER_SECRET');
  if (!expectedSecret || !request || request.secret !== expectedSecret) {
    return jsonResponse_({ok: false, error: 'unauthorized'});
  }
  if (request.version !== 1) {
    return jsonResponse_({ok: false, error: 'unsupported_version'});
  }

  try {
    if (request.action === 'read_source') {
      return jsonResponse_(readSource_(properties));
    }
    if (request.action === 'read_target_keys') {
      return jsonResponse_(readTargetKeys_(properties));
    }
    if (request.action === 'append_target') {
      return jsonResponse_(appendTarget_(properties, request.row));
    }
    if (request.action === 'read_full_text_candidates') {
      return jsonResponse_(readFullTextCandidates_(properties, request.limit));
    }
    if (request.action === 'update_full_text') {
      return jsonResponse_(updateFullText_(properties, request));
    }
    if (request.action === 'read_full_text_backfill_candidates') {
      return jsonResponse_(readFullTextBackfillCandidates_(properties, request.limit));
    }
    if (request.action === 'backfill_full_text') {
      return jsonResponse_(backfillFullText_(properties, request));
    }
    if (request.action === 'read_gemini_formula_candidates') {
      return jsonResponse_(readGeminiFormulaCandidates_(properties, request));
    }
    if (request.action === 'prepare_gemini_formula') {
      return jsonResponse_(prepareGeminiFormula_(properties, request));
    }
    if (request.action === 'read_giga_text_candidates') {
      return jsonResponse_(readGigaTextCandidates_(properties, request));
    }
    if (request.action === 'write_giga_text_result') {
      return jsonResponse_(writeGigaTextResult_(properties, request));
    }
    if (request.action === 'read_ai_candidates') {
      return jsonResponse_(readAiCandidates_(properties, request));
    }
    if (request.action === 'finalize_ai_candidate') {
      return jsonResponse_(finalizeAiCandidate_(properties, request));
    }
    if (request.action === 'save_ai_response') {
      return jsonResponse_(saveAiResponse_(properties, request.response_row));
    }
    if (request.action === 'update_ai_response_error') {
      return jsonResponse_(updateAiResponseError_(properties, request));
    }
    if (request.action === 'save_ai_analysis') {
      return jsonResponse_(
        saveAiAnalysis_(properties, request.analysis_row, request.object_rows)
      );
    }
    if (request.action === 'read_object_enrichment_candidates') {
      return jsonResponse_(readObjectEnrichmentCandidates_(properties, request));
    }
    if (request.action === 'update_object_enrichment') {
      return jsonResponse_(updateObjectEnrichment_(properties, request));
    }
    return jsonResponse_({ok: false, error: 'unsupported_action'});
  } catch (error) {
    return jsonResponse_({ok: false, status: 'error', error: publicError_(error)});
  }
}


function readSource_(properties) {
  const sheet = openSheet_(properties, 'SOURCE_SPREADSHEET_ID', SOURCE_SHEET);
  assertHeaders_(sheet, SOURCE_HEADERS);
  const lastRow = sheet.getLastRow();
  const values = lastRow < 1
    ? []
    : sheet.getRange(1, 1, lastRow, SOURCE_HEADERS.length).getDisplayValues();
  return {ok: true, values: values};
}


function readTargetKeys_(properties) {
  const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
  assertHeaders_(sheet, TARGET_HEADERS);
  const lastRow = sheet.getLastRow();
  const keys = lastRow < 2
    ? []
    : sheet.getRange(2, 1, lastRow - 1, 1).getDisplayValues()
        .map(function(row) { return normalizedKey_(row[0]); })
        .filter(Boolean);
  return {ok: true, headers: TARGET_HEADERS, keys: keys};
}


function appendTarget_(properties, row) {
  const validated = validateTargetRow_(row);
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) {
    return {ok: false, error: 'busy'};
  }
  try {
    const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
    assertHeaders_(sheet, TARGET_HEADERS);
    const key = validated[0];
    if (existingKeys_(sheet).has(key)) {
      return {ok: true, status: 'exists', record_key: key};
    }
    const firstRow = Math.max(2, sheet.getLastRow() + 1);
    sheet.getRange(firstRow, 1, 1, TARGET_HEADERS.length)
      .setValues([validated.map(safeCell_)]);
    return {ok: true, status: 'inserted', record_key: key};
  } finally {
    lock.releaseLock();
  }
}


function readFullTextCandidates_(properties, rawLimit) {
  const limit = Number(rawLimit);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_FULL_TEXT_BATCH_SIZE) {
    throw new Error('invalid_candidate_limit');
  }
  const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
  assertHeaders_(sheet, TARGET_HEADERS);
  assertHeadersAt_(sheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return {ok: true, candidates: []};

  // Read A:O once. F is the approved fallback for M when loading fails.
  const rows = sheet.getRange(2, 1, lastRow - 1, 15).getDisplayValues();
  const candidates = [];
  for (let index = 0; index < rows.length && candidates.length < limit; index++) {
    const row = rows[index];
    const key = normalizedKey_(row[0]);
    const fullText = String(row[12] || '');
    const loadStatus = String(row[13] || '');
    const loadedAt = String(row[14] || '');
    // Any value in M:O may be a user edit. Only entirely blank cells are eligible.
    if (key && !fullText && !loadStatus && !loadedAt) {
      candidates.push({
        record_key: key,
        links: String(row[6] || ''),
        source_text: String(row[5] || '')
      });
    }
  }
  return {ok: true, candidates: candidates};
}


function updateFullText_(properties, request) {
  const key = normalizedKey_(request.record_key);
  if (!/^-?\d+:\d+$/.test(key)) throw new Error('invalid_record_key');
  const fullText = validatedCell_(request.full_text, 'invalid_full_text');
  const loadStatus = validatedCell_(request.load_status, 'invalid_load_status');
  const loadedAt = validatedCell_(request.loaded_at, 'invalid_loaded_at');
  const allowedStatuses = new Set([
    'Загружено',
    'Недоступно',
    'Неподдерживаемая ссылка',
    'Ошибка'
  ]);
  if (!allowedStatuses.has(loadStatus)) throw new Error('invalid_load_status');
  if (loadStatus === 'Загружено' && !fullText) throw new Error('full_text_required');

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
    assertHeaders_(sheet, TARGET_HEADERS);
    assertHeadersAt_(sheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
    const matches = matchingRows_(sheet, key);
    if (!matches.length) return {ok: true, status: 'not_found', record_key: key};
    if (matches.length > 1) throw new Error('duplicate_record_key_target');

    const rowNumber = matches[0];
    const current = sheet
      .getRange(rowNumber, FULL_TEXT_FIRST_COLUMN, 1, FULL_TEXT_HEADERS.length)
      .getDisplayValues()[0];
    if (current.some(function(value) { return String(value || '') !== ''; })) {
      return {ok: true, status: 'already_processed', record_key: key};
    }
    sheet
      .getRange(rowNumber, FULL_TEXT_FIRST_COLUMN, 1, FULL_TEXT_HEADERS.length)
      .setValues([[safeCell_(fullText), safeCell_(loadStatus), safeCell_(loadedAt)]]);
    return {ok: true, status: 'updated', record_key: key};
  } finally {
    lock.releaseLock();
  }
}


function readFullTextBackfillCandidates_(properties, rawLimit) {
  const limit = Number(rawLimit);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_FULL_TEXT_BATCH_SIZE) {
    throw new Error('invalid_candidate_limit');
  }
  const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
  assertHeaders_(sheet, TARGET_HEADERS);
  assertHeadersAt_(sheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return {ok: true, candidates: []};
  const rows = sheet.getRange(2, 1, lastRow - 1, 15).getDisplayValues();
  const candidates = [];
  for (let index = 0; index < rows.length && candidates.length < limit; index++) {
    const row = rows[index];
    const key = normalizedKey_(row[0]);
    const sourceText = String(row[5] || '');
    const fullText = String(row[12] || '');
    if (key && !fullText && sourceText.trim()) {
      candidates.push({record_key: key});
    }
  }
  return {ok: true, candidates: candidates};
}


function backfillFullText_(properties, request) {
  const key = normalizedKey_(request.record_key);
  if (!/^-?\d+:\d+$/.test(key)) throw new Error('invalid_record_key');
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
    assertHeaders_(sheet, TARGET_HEADERS);
    assertHeadersAt_(sheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
    const matches = matchingRows_(sheet, key);
    if (!matches.length) return {ok: true, status: 'not_found', record_key: key};
    if (matches.length > 1) throw new Error('duplicate_record_key_target');
    const rowNumber = matches[0];
    const sourceText = String(sheet.getRange(rowNumber, 6, 1, 1)
      .getDisplayValues()[0][0] || '');
    const fullTextCell = sheet.getRange(rowNumber, FULL_TEXT_FIRST_COLUMN, 1, 1);
    const currentFullText = String(fullTextCell.getDisplayValues()[0][0] || '');
    if (currentFullText) {
      return {ok: true, status: 'already_processed', record_key: key};
    }
    if (!sourceText.trim()) {
      return {ok: true, status: 'no_source_text', record_key: key};
    }
    // Intentionally write only M. L and N:O remain byte-for-byte untouched.
    fullTextCell.setValues([[safeCell_(sourceText)]]);
    return {ok: true, status: 'updated', record_key: key};
  } finally {
    lock.releaseLock();
  }
}


function readGeminiFormulaCandidates_(properties, request) {
  const limit = Number(request.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_GEMINI_BATCH_SIZE) {
    throw new Error('invalid_gemini_candidate_limit');
  }
  const requestedKey = normalizedKey_(request.record_key);
  if (requestedKey && !/^-?\d+:\d+$/.test(requestedKey)) {
    throw new Error('invalid_record_key');
  }
  const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
  assertHeaders_(sheet, TARGET_HEADERS);
  assertHeadersAt_(sheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
  assertHeadersAt_(sheet, GEMINI_COLUMN, [GEMINI_HEADER]);
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return {ok: true, candidates: []};

  const rows = sheet.getRange(2, 1, lastRow - 1, GEMINI_COLUMN).getDisplayValues();
  const formulas = sheet.getRange(2, GEMINI_COLUMN, lastRow - 1, 1).getFormulas();
  const candidates = [];
  for (let index = 0; index < rows.length && candidates.length < limit; index++) {
    const row = rows[index];
    const key = normalizedKey_(row[0]);
    if (requestedKey && key !== requestedKey) continue;
    const fullText = String(row[FULL_TEXT_FIRST_COLUMN - 1] || '');
    const geminiValue = String(row[GEMINI_COLUMN - 1] || '');
    const geminiFormula = String((formulas[index] || [])[0] || '');
    if (key && fullText.trim() && !geminiValue && !geminiFormula) {
      const rowNumber = index + 2;
      candidates.push({
        record_key: key,
        source_cell: 'M' + rowNumber,
        target_cell: 'P' + rowNumber
      });
    }
  }
  return {ok: true, candidates: candidates};
}


function prepareGeminiFormula_(properties, request) {
  const key = normalizedKey_(request.record_key);
  if (!/^-?\d+:\d+$/.test(key)) throw new Error('invalid_record_key');
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
    assertHeaders_(sheet, TARGET_HEADERS);
    assertHeadersAt_(sheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
    assertHeadersAt_(sheet, GEMINI_COLUMN, [GEMINI_HEADER]);
    const matches = matchingRows_(sheet, key);
    if (!matches.length) return {ok: true, status: 'not_found', record_key: key};
    if (matches.length > 1) throw new Error('duplicate_record_key_target');

    const rowNumber = matches[0];
    const fullText = String(sheet.getRange(rowNumber, FULL_TEXT_FIRST_COLUMN, 1, 1)
      .getDisplayValues()[0][0] || '');
    if (!fullText.trim()) {
      return {ok: true, status: 'no_full_text', record_key: key};
    }
    const target = sheet.getRange(rowNumber, GEMINI_COLUMN, 1, 1);
    const currentFormula = String(target.getFormula() || '');
    const currentValue = String(target.getDisplayValue() || '');
    if (currentFormula || currentValue) {
      return {ok: true, status: 'already_processed', record_key: key};
    }

    target.setFormula(buildGeminiFormula_(rowNumber));
    return {
      ok: true,
      status: 'updated',
      record_key: key,
      source_cell: 'M' + rowNumber,
      target_cell: 'P' + rowNumber
    };
  } finally {
    lock.releaseLock();
  }
}


function buildGeminiFormula_(rowNumber) {
  const escapedPrompt = GEMINI_PROMPT.replace(/"/g, '""');
  return '=AI("' + escapedPrompt + '";M' + rowNumber + ')';
}


function readGigaTextCandidates_(properties, request) {
  const limit = Number(request.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_GIGA_BATCH_SIZE) {
    throw new Error('invalid_giga_candidate_limit');
  }
  const requestedKey = normalizedKey_(request.record_key);
  if (requestedKey && !/^-?\d+:\d+$/.test(requestedKey)) {
    throw new Error('invalid_record_key');
  }
  const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
  assertHeaders_(sheet, TARGET_HEADERS);
  assertHeadersAt_(sheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
  assertHeadersAt_(sheet, GEMINI_COLUMN, [GEMINI_HEADER]);
  assertHeadersAt_(sheet, GIGA_COLUMN, [GIGA_HEADER]);
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return {ok: true, candidates: []};

  const rows = sheet.getRange(2, 1, lastRow - 1, GIGA_COLUMN).getDisplayValues();
  const formulas = sheet.getRange(2, GIGA_COLUMN, lastRow - 1, 1).getFormulas();
  const candidates = [];
  for (let index = 0; index < rows.length && candidates.length < limit; index++) {
    const row = rows[index];
    const key = normalizedKey_(row[0]);
    if (requestedKey && key !== requestedKey) continue;
    const fullText = String(row[FULL_TEXT_FIRST_COLUMN - 1] || '');
    const gigaValue = String(row[GIGA_COLUMN - 1] || '');
    const gigaFormula = String((formulas[index] || [])[0] || '');
    if (key && fullText.trim() && !gigaValue && !gigaFormula) {
      const rowNumber = index + 2;
      candidates.push({
        record_key: key,
        full_text: fullText,
        source_cell: 'M' + rowNumber,
        target_cell: 'Q' + rowNumber
      });
    }
  }
  return {ok: true, candidates: candidates};
}


function writeGigaTextResult_(properties, request) {
  const key = normalizedKey_(request.record_key);
  if (!/^-?\d+:\d+$/.test(key)) throw new Error('invalid_record_key');
  const result = validatedCell_(request.result, 'invalid_giga_result');
  if (!result.trim()) throw new Error('invalid_giga_result');
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
    assertHeaders_(sheet, TARGET_HEADERS);
    assertHeadersAt_(sheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
    assertHeadersAt_(sheet, GEMINI_COLUMN, [GEMINI_HEADER]);
    assertHeadersAt_(sheet, GIGA_COLUMN, [GIGA_HEADER]);
    const matches = matchingRows_(sheet, key);
    if (!matches.length) return {ok: true, status: 'not_found', record_key: key};
    if (matches.length > 1) throw new Error('duplicate_record_key_target');

    const rowNumber = matches[0];
    const fullText = String(sheet.getRange(rowNumber, FULL_TEXT_FIRST_COLUMN, 1, 1)
      .getDisplayValues()[0][0] || '');
    if (!fullText.trim()) {
      return {ok: true, status: 'no_full_text', record_key: key};
    }
    const target = sheet.getRange(rowNumber, GIGA_COLUMN, 1, 1);
    const currentFormula = String(target.getFormula() || '');
    const currentValue = String(target.getDisplayValue() || '');
    if (currentFormula || currentValue) {
      return {ok: true, status: 'already_processed', record_key: key};
    }

    target.setValues([[safeCell_(result)]]);
    return {
      ok: true,
      status: 'updated',
      record_key: key,
      source_cell: 'M' + rowNumber,
      target_cell: 'Q' + rowNumber
    };
  } finally {
    lock.releaseLock();
  }
}


function readAiCandidates_(properties, request) {
  const limit = Number(request.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_AI_BATCH_SIZE) {
    throw new Error('invalid_ai_candidate_limit');
  }
  const requestedKey = normalizedKey_(request.record_key);
  if (requestedKey && !/^-?\d+:\d+$/.test(requestedKey)) {
    throw new Error('invalid_record_key');
  }
  const claim = request.claim === true;

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    return readAiCandidatesLocked_(properties, limit, requestedKey, claim);
  } finally {
    lock.releaseLock();
  }
}


function readAiCandidatesLocked_(properties, limit, requestedKey, claim) {

  const newsSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
  const analysisSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', ANALYSIS_SHEET);
  const objectsSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', OBJECTS_SHEET);
  const responsesSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', AI_RESPONSES_SHEET);
  assertHeaders_(newsSheet, TARGET_HEADERS);
  assertHeadersAt_(newsSheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
  assertHeadersAt_(newsSheet, GEMINI_COLUMN, [GEMINI_HEADER]);
  assertHeadersAt_(newsSheet, GIGA_COLUMN, [GIGA_HEADER]);
  assertHeaders_(analysisSheet, ANALYSIS_HEADERS);
  assertHeaders_(objectsSheet, OBJECT_HEADERS);
  assertHeaders_(responsesSheet, AI_RESPONSE_HEADERS);

  const successfulKeys = successfulGigaChatKeys_(analysisSheet, objectsSheet);
  const lastRow = newsSheet.getLastRow();
  if (lastRow < 2) return {ok: true, candidates: []};
  const rows = newsSheet.getRange(2, 1, lastRow - 1, GIGA_COLUMN).getDisplayValues();
  const generatedFormulas = newsSheet
    .getRange(2, GEMINI_COLUMN, lastRow - 1, 2).getFormulas();
  const candidates = [];
  let requestedMatches = 0;
  const now = Date.now();
  for (let index = 0; index < rows.length; index++) {
    const row = rows[index];
    const rowNumber = index + 2;
    const key = normalizedKey_(row[0]);
    if (!key) continue;
    if (requestedKey && key !== requestedKey) continue;
    if (requestedKey) requestedMatches += 1;
    if (requestedMatches > 1) throw new Error('duplicate_record_key_target');
    let newsStatus = String(row[11] || '');
    if (successfulKeys.has(key)) {
      if (claim && newsStatus !== NEWS_STATUS_SKIPPED && newsStatus !== NEWS_STATUS_DONE) {
        newsSheet.getRange(rowNumber, NEWS_STATUS_COLUMN, 1, 1)
          .setValues([[NEWS_STATUS_DONE]]);
        deleteAiLease_(properties, key);
      }
      continue;
    }
    if (newsStatus === NEWS_STATUS_SKIPPED) continue;
    if (newsStatus === NEWS_STATUS_PROCESSING) {
      const lease = readAiLease_(properties, key);
      if (lease && lease.expires_at > now) continue;
      newsStatus = NEWS_STATUS_WAITING;
      if (claim) deleteAiLease_(properties, key);
    }
    if (newsStatus !== NEWS_STATUS_WAITING) continue;
    const formulas = generatedFormulas[index] || [];
    const geminiText = readyGeneratedText_(row[15], formulas[0]);
    const gigaText = readyGeneratedText_(row[16], formulas[1]);
    if (!geminiText) continue;
    const sourceModels = geminiText && gigaText
      ? 'Gemini + GigaChat'
      : (geminiText ? 'Gemini' : 'GigaChat');
    let claimToken = '';
    if (claim) {
      claimToken = Utilities.getUuid();
      writeAiLease_(properties, key, claimToken, now + AI_LEASE_MILLISECONDS);
      newsSheet.getRange(rowNumber, NEWS_STATUS_COLUMN, 1, 1)
        .setValues([[NEWS_STATUS_PROCESSING]]);
    }
    candidates.push({
      record_key: key,
      news_datetime: String(row[1] || ''),
      links: String(row[6] || ''),
      gemini_text: geminiText,
      giga_text: gigaText,
      source_models: sourceModels,
      news_status: newsStatus,
      claim_token: claimToken
    });
    if (!requestedKey && candidates.length >= limit) break;
  }
  return {ok: true, candidates: candidates};
}


function readyGeneratedText_(displayValue, formula) {
  const display = String(displayValue || '').trim();
  const formulaText = String(formula || '').trim();
  if (!display || /^=/.test(display)) return '';
  const unavailable = new Set([
    '#error!', '#n/a', '#name?', '#ref!', '#value!', '#num!', '#null!',
    'loading...', 'загрузка...', 'generating...', 'создание...',
    'сгенерировать и заполнить', '-'
  ]);
  if (unavailable.has(display.toLocaleLowerCase())) return '';
  if (formulaText && display === formulaText) return '';
  return display;
}


function finalizeAiCandidate_(properties, request) {
  const key = normalizedKey_(request.record_key);
  if (!/^-?\d+:\d+$/.test(key)) throw new Error('invalid_record_key');
  const claimToken = validatedAnalysisId_(request.claim_token);
  const requestedStatus = String(request.final_status || '');
  if (![NEWS_STATUS_DONE, NEWS_STATUS_ERROR].includes(requestedStatus)) {
    throw new Error('invalid_news_status');
  }
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    const newsSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', TARGET_SHEET);
    const analysisSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', ANALYSIS_SHEET);
    const objectsSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', OBJECTS_SHEET);
    assertHeaders_(newsSheet, TARGET_HEADERS);
    assertHeadersAt_(newsSheet, FULL_TEXT_FIRST_COLUMN, FULL_TEXT_HEADERS);
    assertHeadersAt_(newsSheet, GEMINI_COLUMN, [GEMINI_HEADER]);
    assertHeadersAt_(newsSheet, GIGA_COLUMN, [GIGA_HEADER]);
    assertHeaders_(analysisSheet, ANALYSIS_HEADERS);
    assertHeaders_(objectsSheet, OBJECT_HEADERS);
    const matches = matchingRows_(newsSheet, key);
    if (!matches.length) return {ok: true, status: 'not_found'};
    if (matches.length > 1) throw new Error('duplicate_record_key_target');
    const statusCell = newsSheet.getRange(matches[0], NEWS_STATUS_COLUMN, 1, 1);
    const current = String(statusCell.getDisplayValues()[0][0] || '');
    const successful = successfulGigaChatKeys_(analysisSheet, objectsSheet).has(key);
    const finalStatus = successful ? NEWS_STATUS_DONE : requestedStatus;
    if (current === finalStatus && !readAiLease_(properties, key)) {
      return {ok: true, status: 'already_finalized', final_status: finalStatus};
    }
    const lease = readAiLease_(properties, key);
    if (!lease || lease.token !== claimToken) throw new Error('ai_lease_conflict');
    if (requestedStatus === NEWS_STATUS_DONE && !successful) {
      throw new Error('successful_analysis_missing');
    }
    statusCell.setValues([[finalStatus]]);
    deleteAiLease_(properties, key);
    return {ok: true, status: 'updated', final_status: finalStatus};
  } finally {
    lock.releaseLock();
  }
}


function saveAiResponse_(properties, rawRow) {
  const row = validateFixedRow_(rawRow, AI_RESPONSE_HEADERS.length, 'invalid_ai_response_row');
  const analysisId = validatedAnalysisId_(row[0]);
  if (row[1] !== 'GigaChat') throw new Error('invalid_ai_model');
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', AI_RESPONSES_SHEET);
    assertHeaders_(sheet, AI_RESPONSE_HEADERS);
    if (matchingRows_(sheet, analysisId).length) {
      return {ok: true, status: 'exists', analysis_id: analysisId};
    }
    appendRows_(sheet, [row]);
    return {ok: true, status: 'inserted', analysis_id: analysisId};
  } finally {
    lock.releaseLock();
  }
}


function updateAiResponseError_(properties, request) {
  const analysisId = validatedAnalysisId_(request.analysis_id);
  const expected = validatedCell_(request.expected_error, 'invalid_ai_response_error');
  const next = validatedCell_(request.error, 'invalid_ai_response_error');
  if (!next) throw new Error('invalid_ai_response_error');
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', AI_RESPONSES_SHEET);
    assertHeaders_(sheet, AI_RESPONSE_HEADERS);
    const matches = matchingRows_(sheet, analysisId);
    if (!matches.length) return {ok: true, status: 'not_found'};
    if (matches.length > 1) throw new Error('duplicate_analysis_id');
    const cell = sheet.getRange(matches[0], 6, 1, 1);
    const current = String(cell.getDisplayValues()[0][0] || '');
    if (current === next) return {ok: true, status: 'exists'};
    if (current !== expected) throw new Error('ai_response_error_conflict');
    cell.setValues([[safeCell_(next)]]);
    return {ok: true, status: 'updated'};
  } finally {
    lock.releaseLock();
  }
}


function readObjectEnrichmentCandidates_(properties, request) {
  const limit = Number(request.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_OBJECT_BATCH_SIZE) {
    throw new Error('invalid_object_candidate_limit');
  }
  let requestedRow = null;
  if (request.row_number !== undefined && request.row_number !== null) {
    requestedRow = Number(request.row_number);
    if (!Number.isInteger(requestedRow) || requestedRow < 2) {
      throw new Error('invalid_object_row_number');
    }
  }
  const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', OBJECTS_SHEET);
  assertHeaders_(sheet, OBJECT_HEADERS);
  const lastRow = sheet.getLastRow();
  if (lastRow < 2 || (requestedRow !== null && requestedRow > lastRow)) {
    return {ok: true, candidates: []};
  }
  const firstRow = requestedRow === null ? 2 : requestedRow;
  const rowCount = requestedRow === null ? lastRow - 1 : 1;
  const range = sheet.getRange(firstRow, 1, rowCount, OBJECT_HEADERS.length);
  const displays = range.getDisplayValues();
  const formulas = range.getFormulas();
  const candidates = [];
  displays.forEach(function(row, index) {
    if (candidates.length >= limit) return;
    const formulaRow = formulas[index];
    const targetValues = [9, 14, 15, 16].map(function(column) {
      return cellContent_(row[column], formulaRow[column]);
    });
    if (targetValues.every(function(value) { return value !== ''; })) return;
    candidates.push({
      row_number: firstRow + index,
      analysis_id: String(row[0] || ''),
      object_number: String(row[1] || ''),
      name: String(row[3] || ''),
      organization: String(row[5] || ''),
      region: String(row[6] || ''),
      locality: String(row[7] || ''),
      address: String(row[8] || ''),
      cadastral_number: targetValues[0],
      cadastral_quarter: targetValues[1],
      all_cadastral: targetValues[2],
      gemini: targetValues[3]
    });
  });
  return {ok: true, candidates: candidates};
}


function updateObjectEnrichment_(properties, request) {
  const analysisId = validatedAnalysisId_(request.analysis_id);
  const objectNumber = String(request.object_number || '');
  if (!/^\d+$/.test(objectNumber) || Number(objectNumber) < 1) {
    throw new Error('invalid_object_number');
  }
  if (!request.values || typeof request.values !== 'object' || Array.isArray(request.values)) {
    throw new Error('invalid_object_enrichment_values');
  }
  const mapping = {
    cadastral_number: 10,
    cadastral_quarter: 15,
    all_cadastral: 16
  };
  const keys = Object.keys(request.values);
  if (keys.some(function(key) { return !mapping[key]; })) {
    throw new Error('invalid_object_enrichment_values');
  }
  const values = {};
  keys.forEach(function(key) {
    values[key] = validatedCell_(request.values[key], 'invalid_object_enrichment_values');
    if (!values[key]) throw new Error('invalid_object_enrichment_values');
  });
  if (typeof request.prepare_gemini !== 'boolean') {
    throw new Error('invalid_object_gemini_flag');
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    const sheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', OBJECTS_SHEET);
    assertHeaders_(sheet, OBJECT_HEADERS);
    const matches = matchingObjectRows_(sheet, analysisId, objectNumber);
    if (!matches.length) {
      return {ok: true, status: 'not_found', updated_fields: []};
    }
    if (matches.length > 1) throw new Error('duplicate_object_identity');
    const rowNumber = matches[0];
    const rowRange = sheet.getRange(rowNumber, 1, 1, OBJECT_HEADERS.length);
    const row = rowRange.getDisplayValues()[0];
    const formulas = rowRange.getFormulas()[0];
    const updated = [];
    keys.forEach(function(key) {
      const column = mapping[key];
      if (cellContent_(row[column - 1], formulas[column - 1]) !== '') return;
      sheet.getRange(rowNumber, column, 1, 1).setValues([[safeCell_(values[key])]]);
      updated.push(key);
    });
    if (
      request.prepare_gemini &&
      cellContent_(row[16], formulas[16]) === '' &&
      String(row[7] || '').trim() &&
      (String(row[5] || '').trim() || String(row[3] || '').trim())
    ) {
      sheet.getRange(rowNumber, 17, 1, 1).setFormula(
        objectGeminiFormula_(rowNumber)
      );
      updated.push('gemini');
    }
    return {
      ok: true,
      status: updated.length ? 'updated' : 'already_processed',
      updated_fields: updated
    };
  } finally {
    lock.releaseLock();
  }
}


function matchingObjectRows_(sheet, analysisId, objectNumber) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];
  const rows = sheet.getRange(2, 1, lastRow - 1, 2).getDisplayValues();
  const matches = [];
  rows.forEach(function(row, index) {
    if (String(row[0] || '') === analysisId && String(row[1] || '') === objectNumber) {
      matches.push(index + 2);
    }
  });
  return matches;
}


function cellContent_(displayValue, formula) {
  return String(formula || displayValue || '');
}


function objectGeminiFormula_(rowNumber) {
  const prompt = OBJECT_GEMINI_PROMPT.replace(/"/g, '""');
  return '=AI("' + prompt + '";D' + rowNumber + ':I' + rowNumber + ')';
}


function saveAiAnalysis_(properties, rawAnalysisRow, rawObjectRows) {
  const analysisRow = validateFixedRow_(
    rawAnalysisRow, ANALYSIS_HEADERS.length, 'invalid_analysis_row'
  );
  const analysisId = validatedAnalysisId_(analysisRow[0]);
  if (analysisRow[4] !== 'GigaChat') throw new Error('invalid_ai_model');
  if (!['Успешно', 'Ошибка'].includes(analysisRow[5])) {
    throw new Error('invalid_analysis_status');
  }
  if (!Array.isArray(rawObjectRows)) throw new Error('invalid_object_rows');
  const objectRows = rawObjectRows.map(function(rawRow) {
    const row = validateFixedRow_(rawRow, OBJECT_BASE_HEADERS.length, 'invalid_object_row');
    if (validatedAnalysisId_(row[0]) !== analysisId) {
      throw new Error('object_analysis_id_mismatch');
    }
    if (!['Gemini', 'GigaChat', 'Gemini + GigaChat'].includes(row[2])) {
      throw new Error('invalid_ai_model');
    }
    if (!/^\d+$/.test(row[1]) || Number(row[1]) < 1) {
      throw new Error('invalid_object_number');
    }
    if (row[13] !== analysisRow[2]) throw new Error('object_news_datetime_mismatch');
    return row;
  });
  const newsDateTime = objectRows.length ? sheetDateTimeSerial_(analysisRow[2]) : '';
  const requestNumbers = new Set();
  objectRows.forEach(function(row) {
    if (requestNumbers.has(row[1])) throw new Error('duplicate_object_number');
    requestNumbers.add(row[1]);
  });
  if (Number(analysisRow[10]) !== objectRows.length) {
    throw new Error('object_count_mismatch');
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return {ok: false, status: 'error', error: 'busy'};
  try {
    const analysisSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', ANALYSIS_SHEET);
    const objectsSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', OBJECTS_SHEET);
    const responsesSheet = openSheet_(properties, 'TARGET_SPREADSHEET_ID', AI_RESPONSES_SHEET);
    assertHeaders_(analysisSheet, ANALYSIS_HEADERS);
    assertHeaders_(objectsSheet, OBJECT_HEADERS);
    assertHeaders_(responsesSheet, AI_RESPONSE_HEADERS);
    if (!matchingRows_(responsesSheet, analysisId).length) {
      throw new Error('ai_response_missing');
    }

    let analysisInserted = 0;
    const analysisMatches = matchingRows_(analysisSheet, analysisId);
    if (analysisMatches.length > 1) throw new Error('duplicate_analysis_id');
    if (!analysisMatches.length) {
      appendRows_(analysisSheet, [analysisRow]);
      analysisInserted = 1;
    } else {
      const existing = analysisSheet
        .getRange(analysisMatches[0], 1, 1, ANALYSIS_HEADERS.length)
        .getDisplayValues()[0];
      if (String(existing[1]) !== analysisRow[1] || String(existing[4]) !== 'GigaChat') {
        throw new Error('analysis_id_conflict');
      }
    }

    const existingNumbers = existingObjectNumbers_(objectsSheet, analysisId);
    const missingObjects = objectRows.filter(function(row) {
      return !existingNumbers.has(row[1]);
    });
    appendObjectRows_(objectsSheet, missingObjects, newsDateTime);
    return {
      ok: true,
      status: analysisInserted || missingObjects.length ? 'completed' : 'exists',
      analysis_inserted: analysisInserted,
      objects_inserted: missingObjects.length,
      analysis_id: analysisId
    };
  } finally {
    lock.releaseLock();
  }
}


function openSheet_(properties, propertyName, sheetName) {
  const spreadsheetId = properties.getProperty(propertyName);
  if (!spreadsheetId) throw new Error('missing_' + propertyName.toLowerCase());
  const sheet = SpreadsheetApp.openById(spreadsheetId).getSheetByName(sheetName);
  if (!sheet) throw new Error('required_sheet_missing_' + sheetName);
  return sheet;
}


function assertHeaders_(sheet, expected) {
  if (sheet.getLastRow() < 1) throw new Error('missing_headers_' + sheet.getName());
  const actual = sheet.getRange(1, 1, 1, expected.length).getDisplayValues()[0];
  expected.forEach(function(header, index) {
    if (actual[index] !== header) {
      throw new Error('invalid_headers_' + sheet.getName());
    }
  });
}


function assertHeadersAt_(sheet, firstColumn, expected) {
  if (sheet.getLastRow() < 1) throw new Error('missing_headers_' + sheet.getName());
  const actual = sheet.getRange(1, firstColumn, 1, expected.length).getDisplayValues()[0];
  expected.forEach(function(header, index) {
    if (actual[index] !== header) throw new Error('invalid_full_text_headers');
  });
}


function existingKeys_(sheet) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return new Set();
  return new Set(
    sheet.getRange(2, 1, lastRow - 1, 1).getDisplayValues()
      .map(function(row) { return normalizedKey_(row[0]); })
      .filter(Boolean)
  );
}


function matchingRows_(sheet, key) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];
  const values = sheet.getRange(2, 1, lastRow - 1, 1).getDisplayValues();
  const matches = [];
  values.forEach(function(row, index) {
    if (normalizedKey_(row[0]) === key) matches.push(index + 2);
  });
  return matches;
}


function successfulGigaChatKeys_(analysisSheet, objectsSheet) {
  const lastRow = analysisSheet.getLastRow();
  if (lastRow < 2) return new Set();
  const objectCounts = new Map();
  const objectsLastRow = objectsSheet.getLastRow();
  if (objectsLastRow >= 2) {
    objectsSheet.getRange(2, 1, objectsLastRow - 1, 1).getDisplayValues()
      .forEach(function(row) {
        const analysisId = String(row[0] || '');
        if (analysisId) objectCounts.set(
          analysisId, (objectCounts.get(analysisId) || 0) + 1
        );
      });
  }
  const rows = analysisSheet
    .getRange(2, 1, lastRow - 1, ANALYSIS_HEADERS.length).getDisplayValues();
  return new Set(rows.filter(function(row) {
    if (String(row[4] || '') !== 'GigaChat' || String(row[5] || '') !== 'Успешно') {
      return false;
    }
    const expectedObjects = Number(row[10]);
    if (!Number.isInteger(expectedObjects) || expectedObjects < 0) return false;
    return (objectCounts.get(String(row[0] || '')) || 0) === expectedObjects;
  }).map(function(row) { return normalizedKey_(row[1]); }).filter(Boolean));
}


function readAiLease_(properties, key) {
  const raw = properties.getProperty(AI_LEASE_PREFIX + key);
  if (!raw) return null;
  try {
    const lease = JSON.parse(raw);
    if (typeof lease.token !== 'string' || typeof lease.expires_at !== 'number') {
      return null;
    }
    return lease;
  } catch (error) {
    return null;
  }
}


function writeAiLease_(properties, key, token, expiresAt) {
  properties.setProperty(
    AI_LEASE_PREFIX + key,
    JSON.stringify({token: token, expires_at: expiresAt})
  );
}


function deleteAiLease_(properties, key) {
  properties.deleteProperty(AI_LEASE_PREFIX + key);
}


function existingObjectNumbers_(sheet, analysisId) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return new Set();
  const rows = sheet.getRange(2, 1, lastRow - 1, 2).getDisplayValues();
  return new Set(rows.filter(function(row) {
    return String(row[0] || '') === analysisId;
  }).map(function(row) { return String(row[1] || ''); }).filter(Boolean));
}


function appendRows_(sheet, rows) {
  if (!rows.length) return;
  const firstRow = Math.max(2, sheet.getLastRow() + 1);
  sheet.getRange(firstRow, 1, rows.length, rows[0].length)
    .setValues(rows.map(function(row) { return row.map(safeCell_); }));
}


function appendObjectRows_(sheet, rows, newsDateTime) {
  if (!rows.length) return;
  const firstRow = Math.max(2, sheet.getLastRow() + 1);
  const values = rows.map(function(row) {
    return row.slice(0, 13).map(safeCell_).concat(
      [newsDateTime], OBJECT_ENRICHMENT_HEADERS.map(function() { return ''; })
    );
  });
  sheet.getRange(firstRow, 1, values.length, OBJECT_HEADERS.length).setValues(values);
  sheet.getRange(firstRow, 14, values.length, 1)
    .setNumberFormat(NEWS_DATE_TIME_NUMBER_FORMAT);
}


function sheetDateTimeSerial_(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  const match = text.match(
    /^(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2}):(\d{2})$/
  );
  if (!match) throw new Error('invalid_news_datetime');
  const year = Number(match[3]);
  const month = Number(match[2]);
  const day = Number(match[1]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const wallClockMilliseconds = Date.UTC(year, month - 1, day, hour, minute, second);
  const check = new Date(wallClockMilliseconds);
  if (
    check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 ||
    check.getUTCDate() !== day || check.getUTCHours() !== hour ||
    check.getUTCMinutes() !== minute || check.getUTCSeconds() !== second
  ) throw new Error('invalid_news_datetime');

  // Upstream normalizes news time to UTC+5. Store its wall-clock value as a
  // Sheets serial so sorting/filtering is numeric and independent of text locale.
  const instantMilliseconds = wallClockMilliseconds -
    NEWS_TIMEZONE_UTC_OFFSET_MINUTES * 60 * 1000;
  const normalizedWallClockMilliseconds = instantMilliseconds +
    NEWS_TIMEZONE_UTC_OFFSET_MINUTES * 60 * 1000;
  return normalizedWallClockMilliseconds / 86400000 + 25569;
}


function validateFixedRow_(row, width, errorName) {
  if (!Array.isArray(row) || row.length !== width) throw new Error(errorName);
  return row.map(function(value) { return validatedCell_(value, errorName); });
}


function validatedAnalysisId_(value) {
  const analysisId = String(value || '');
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(analysisId)) {
    throw new Error('invalid_analysis_id');
  }
  return analysisId;
}


function validateTargetRow_(row) {
  if (!Array.isArray(row) || row.length !== TARGET_HEADERS.length) {
    throw new Error('invalid_target_row');
  }
  const normalized = row.map(function(value) {
    if (value === null || value === undefined) return '';
    if (typeof value !== 'string') throw new Error('target_values_must_be_strings');
    if (value.length > MAX_CELL_LENGTH) throw new Error('cell_too_long');
    return value;
  });
  if (!/^-?\d+:\d+$/.test(normalized[0])) throw new Error('invalid_record_key');
  return normalized;
}


function validatedCell_(value, errorName) {
  if (value === null || value === undefined) return '';
  if (typeof value !== 'string') throw new Error(errorName);
  if (value.length > MAX_CELL_LENGTH) throw new Error('cell_too_long');
  return value;
}


function safeCell_(value) {
  return /^[=+\-@]/.test(value) ? "'" + value : value;
}


function normalizedKey_(value) {
  return String(value || '').replace(/^'/, '');
}


function publicError_(error) {
  const message = error && error.message ? String(error.message) : '';
  const exact = new Set([
    'invalid_target_row',
    'target_values_must_be_strings',
    'cell_too_long',
    'invalid_record_key',
    'invalid_candidate_limit',
    'invalid_full_text',
    'invalid_load_status',
    'invalid_loaded_at',
    'full_text_required',
    'full_text_not_allowed',
    'duplicate_record_key_target',
    'invalid_full_text_headers',
    'invalid_gemini_candidate_limit',
    'invalid_giga_candidate_limit',
    'invalid_giga_result',
    'invalid_ai_candidate_limit',
    'invalid_news_status',
    'ai_lease_conflict',
    'successful_analysis_missing',
    'invalid_ai_response_row',
    'invalid_ai_response_error',
    'ai_response_error_conflict',
    'invalid_analysis_row',
    'invalid_object_rows',
    'invalid_object_row',
    'invalid_analysis_id',
    'invalid_ai_model',
    'invalid_analysis_status',
    'invalid_object_number',
    'duplicate_object_number',
    'object_analysis_id_mismatch',
    'object_news_datetime_mismatch',
    'object_count_mismatch',
    'invalid_news_datetime',
    'ai_response_missing',
    'duplicate_analysis_id',
    'analysis_id_conflict',
    'invalid_object_candidate_limit',
    'invalid_object_row_number',
    'invalid_object_enrichment_values',
    'invalid_object_gemini_flag',
    'duplicate_object_identity'
  ]);
  if (exact.has(message)) return message;
  if (/^(missing_|required_sheet_missing_|invalid_headers_)/.test(message)) {
    return message.slice(0, 200);
  }
  return 'internal_error';
}


function jsonResponse_(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

