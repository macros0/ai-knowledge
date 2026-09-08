const BASE = "/api";
const CHAT_TIMEOUT_MS = 90_000;

// Тексты ошибок живут в словарях (i18n/locales), а не здесь: интерфейс
// двуязычный, и литерал в api.js пришёл бы к англоязычному пользователю
// по-русски. Здесь — только выбор КЛЮЧА по ответу бэкенда.
const KNOWN_SERVICES = new Set(["llm", "ollama", "qdrant"]);

export function serviceMessageKey(service) {
  return KNOWN_SERVICES.has(service) ? `apiError.service.${service}` : null;
}

export class ApiError extends Error {
  constructor(message, { status, code, service, data } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.service = service;
    this.data = data;
  }

  get isDependencyUnavailable() {
    return this.code === "dependency_unavailable";
  }
}

// Ключ словаря и параметры для ошибки API — чистая функция, тестируется без DOM.
//
// Приоритет: стабильный code из тела ответа -> сервис недоступной зависимости
// -> статус. Если код неизвестен клиенту (бэкенд новее фронтенда), возвращаем
// null и вызывающий показывает detail — диагностику как есть, лучше чем ничего.
export function apiErrorKey(err) {
  if (!(err instanceof ApiError)) return null;
  if (err.isDependencyUnavailable) {
    const svcKey = serviceMessageKey(err.service);
    if (svcKey) return { key: svcKey, params: {} };
  }
  if (err.code) return { key: `apiError.${err.code}`, params: {} };
  if (err.status === 401 || err.status === 403) {
    return { key: "apiError.sessionExpired", params: {} };
  }
  if (err.status === 404) return { key: "apiError.endpointMissing", params: {} };
  if (err.status === 0) return { key: "apiError.backendUnreachable", params: {} };
  // Кода нет — общая формулировка по статусу потеряла бы смысл сообщения
  // ("Тег используется документами" информативнее, чем "Ошибка (HTTP 409)").
  return null;
}

// Человекочитаемое сообщение об ошибке API на языке интерфейса: тосты в админ-UI
// не должны показывать «Not Found» / «Internal Server Error» без контекста.
// t — переводчик из useI18n(); detail с бэкенда остаётся фолбэком для кодов,
// которых нет в словаре клиента.
export function friendlyApiError(err, t) {
  const tr = t || ((k) => k);
  const picked = apiErrorKey(err);
  if (picked) {
    const text = tr(picked.key, picked.params);
    // text === key означает, что ключа нет в словаре клиента (бэкенд новее) —
    // тогда лучше показать detail, чем сам ключ.
    if (text !== picked.key) return text;
  }
  if (err && err.message) return err.message;
  const status = err && err.status;
  return status ? tr("apiError.http", { status }) : tr("apiError.unknown");
}

async function request(path, init, timeoutMs) {
  const controller = new AbortController();
  const timer =
    timeoutMs != null
      ? setTimeout(() => controller.abort(), timeoutMs)
      : null;
  try {
    const resp = await fetch(`${BASE}${path}`, { ...init, signal: controller.signal });
    if (!resp.ok) {
      const detail = await resp.text();
      let message = detail || `HTTP ${resp.status}`;
      let code = null;
      let service = null;
      let data = null;
      try {
        const parsed = JSON.parse(detail);
        if (parsed && typeof parsed.detail === "string") message = parsed.detail;
        if (parsed && typeof parsed.code === "string") code = parsed.code;
        if (parsed && typeof parsed.service === "string") service = parsed.service;
        data = parsed;
      } catch {
        // not JSON — use raw text
      }
      throw new ApiError(message, { status: resp.status, code, service, data });
    }
    return resp.json();
  } catch (err) {
    if (err.name === "AbortError") {
      // message — фолбэк-диагностика; текст для пользователя берётся по коду.
      throw new ApiError("request timeout", { status: 0, code: "timeout" });
    }
    throw err;
  } finally {
    if (timer != null) clearTimeout(timer);
  }
}

export function uploadDocument(file, tags = [], { developmentId = null } = {}) {
  const form = new FormData();
  form.append("file", file);
  for (const tag of tags) {
    form.append("tags", tag);
  }
  if (developmentId != null) {
    form.append("development_id", String(developmentId));
  }
  return request("/documents", { method: "POST", body: form });
}

export function listTags() {
  return request("/tags").then((data) => data.tags ?? []);
}

export function deleteTag(name) {
  return request(`/tags/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export function cleanupTags() {
  return request("/tags/cleanup", { method: "POST" });
}

export function updateTagTranslation(tagId, locale, text) {
  return request(`/tags/${tagId}/translations/${encodeURIComponent(locale)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locale, text }),
  });
}

export function bulkReviewTags(tagIds) {
  return request("/tags/bulk-review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag_ids: tagIds }),
  });
}

export function updateDocumentTags(docId, tags) {
  return request(`/documents/${docId}/tags`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags }),
  });
}

export function bulkUpdateTags(docIds, { add = [], remove = [] } = {}) {
  return request("/documents/bulk-tags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_ids: docIds, add, remove }),
  });
}

export function listDocuments(params = {}) {
  const qs = new URLSearchParams();
  if (params.uploader) qs.set("uploader", params.uploader);
  if (params.status) qs.set("status", params.status);
  if (params.hasDuplicates !== undefined && params.hasDuplicates !== null) {
    qs.set("has_duplicates", String(params.hasDuplicates));
  }
  if (params.developmentId) qs.set("development_id", String(params.developmentId));
  if (params.developmentNumber) qs.set("development_number", params.developmentNumber);
  if (params.module) qs.set("module", params.module);
  if (params.tag) qs.set("tag", params.tag);
  if (params.problem) qs.set("problem", "true");
  if (params.dateFrom) qs.set("date_from", params.dateFrom);
  if (params.dateTo) qs.set("date_to", params.dateTo);
  if (params.search) qs.set("search", params.search);
  if (params.sort) qs.set("sort", params.sort);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const s = qs.toString();
  return request(`/documents${s ? `?${s}` : ""}`).then((data) => ({
    documents: data.documents ?? [],
    total: data.total ?? 0,
    limit: data.limit ?? null,
    offset: data.offset ?? 0,
  }));
}

export function getDocumentStats() {
  return request("/documents/stats");
}

export function listUploaders() {
  return request("/documents/uploaders").then((data) => data.uploaders ?? []);
}

export function listTrashDocuments(params = {}) {
  const qs = new URLSearchParams();
  if (params.uploader) qs.set("uploader", params.uploader);
  if (params.search) qs.set("search", params.search);
  if (params.sort) qs.set("sort", params.sort);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const s = qs.toString();
  return request(`/documents/trash${s ? `?${s}` : ""}`).then((data) => ({
    documents: data.documents ?? [],
    total: data.total ?? 0,
    limit: data.limit ?? null,
    offset: data.offset ?? 0,
    retention_days: data.retention_days ?? 14,
  }));
}

export function restoreDocument(docId, { force = false } = {}) {
  const qs = force ? "?force=true" : "";
  return request(`/documents/${docId}/restore${qs}`, { method: "POST" });
}

export function bulkRestoreDocuments(docIds) {
  return request("/documents/bulk-restore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_ids: docIds }),
  });
}

export function deleteDocument(docId) {
  return request(`/documents/${docId}`, { method: "DELETE" });
}

export function resumeDocument(docId) {
  return request(`/documents/${docId}/resume`, { method: "POST" });
}

export function regenerateDocument(docId) {
  return request(`/documents/${docId}/regenerate`, { method: "POST" });
}

export function bulkPreview(docIds) {
  return request("/documents/bulk-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_ids: docIds }),
  });
}

export function bulkDelete(docIds) {
  return request("/documents/bulk-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_ids: docIds }),
  });
}

export function bulkRegenerate(docIds) {
  return request("/documents/bulk-regenerate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_ids: docIds }),
  });
}

export function listJobs() {
  return request("/jobs").then((data) => data.jobs ?? []);
}

export function getJob(jobId) {
  return request(`/jobs/${jobId}`);
}

export function approveJob(jobId) {
  return request(`/jobs/${jobId}/approve`, { method: "POST" });
}

export function cancelJob(jobId) {
  return request(`/jobs/${jobId}/cancel`, { method: "POST" });
}

export function listAudit(params = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    qs.set(k, v);
  }
  const q = qs.toString();
  return request(`/audit${q ? `?${q}` : ""}`).then((data) => data.entries ?? []);
}

export function listAuditActionTypes() {
  return request("/audit/action-types").then((data) => data.action_types ?? []);
}

export function listAuditUsers() {
  return request("/audit/users").then((data) => data.users ?? []);
}

export function listBlocks() {
  return request("/users/blocks").then((data) => data.blocks ?? []);
}

export function blockUser(externalId, { reason, expires_at } = {}) {
  return request(`/users/${externalId}/block`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason, expires_at }),
  });
}

export function unblockUser(externalId) {
  return request(`/users/${externalId}/unblock`, { method: "POST" });
}

export function listOkfFiles(docId) {
  return request(`/documents/${docId}/okf`);
}

export function getOkfContent(docId, filename) {
  return fetch(`${BASE}/documents/${docId}/okf/${filename}`).then((r) => r.text());
}

export function search(query, tags = [], topK = 5, mode = "hybrid") {
  return request("/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, tags, top_k: topK, mode }),
  });
}

export function chat(query, tags = [], topK = 5, mode = "hybrid", sessionId = null) {
  const body = { query, tags, top_k: topK, mode };
  if (sessionId) body.session_id = sessionId;
  return request(
    "/chat",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    CHAT_TIMEOUT_MS
  );
}

export function listChatSessions(params = {}) {
  const qs = new URLSearchParams();
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const s = qs.toString();
  return request(`/chat/history${s ? `?${s}` : ""}`).then((data) => ({
    sessions: data.sessions ?? [],
    total: data.total ?? 0,
  }));
}

export function getChatThread(sessionId) {
  return request(`/chat/history/${encodeURIComponent(sessionId)}`);
}

export function deleteChatSession(sessionId) {
  return request(`/chat/history/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

export function listAdminChatUsers() {
  return request("/chat/admin/history/users").then((data) => data.users ?? []);
}

export function listAdminChatSessions(userId, params = {}) {
  const qs = new URLSearchParams();
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const s = qs.toString();
  return request(
    `/chat/admin/history/${encodeURIComponent(userId)}${s ? `?${s}` : ""}`
  ).then((data) => ({
    sessions: data.sessions ?? [],
    total: data.total ?? 0,
  }));
}

export function getAdminChatThread(userId, sessionId) {
  return request(
    `/chat/admin/history/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}`
  );
}

export function getChatSettings() {
  return request("/settings");
}

export function getMe() {
  return request("/auth/me");
}

export function simulateAuth(username) {
  return request("/auth/simulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
}

export function getHealth() {
  return fetch("/health").then((r) => r.json());
}

// --- Справочник разработок (Этап 4) ---

export function listDevelopments() {
  return request("/developments").then((data) => data.developments ?? []);
}

export function listDevelopmentsPage(params = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, v);
  }
  const s = qs.toString();
  return request(`/developments${s ? `?${s}` : ""}`).then((data) => ({
    developments: data.developments ?? [],
    total: data.total ?? 0,
    limit: data.limit ?? null,
    offset: data.offset ?? 0,
  }));
}

export function getDevelopment(devId) {
  return request(`/developments/${devId}`);
}

export function createDevelopment({ number, name, module }) {
  return request("/developments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number, name, module }),
  });
}

export function updateDevelopment(devId, { number, name, module, version }) {
  return request(`/developments/${devId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number, name, module, version }),
  });
}

export function deleteDevelopment(devId, version) {
  return request(`/developments/${devId}?version=${encodeURIComponent(version)}`, {
    method: "DELETE",
  });
}

export function listDevelopmentDocuments(devId, params = {}) {
  const qs = new URLSearchParams();
  if (params.search) qs.set("search", params.search);
  if (params.sort) qs.set("sort", params.sort);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  const s = qs.toString();
  return request(`/developments/${devId}/documents${s ? `?${s}` : ""}`).then((d) => ({
    documents: d.documents ?? [],
    total: d.total ?? 0,
    limit: d.limit ?? null,
    offset: d.offset ?? 0,
  }));
}

// --- Generic атрибуты (module и т.п.) ---

export function listAttributeValues(key) {
  return request(`/attributes/${key}`).then((data) => data.values ?? []);
}

export function addAttributeValue(key, value) {
  return request(`/attributes/${key}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
}

export function deleteAttributeValue(key, value) {
  return request(`/attributes/${key}/${encodeURIComponent(value)}`, {
    method: "DELETE",
  });
}

// --- Связь документа с разработкой ---

export function setDocumentDevelopment(docId, developmentId, confirmed = false) {
  return request(`/documents/${docId}/development`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ development_id: developmentId, confirmed }),
  });
}

export function detectDocumentDevelopment(docId) {
  return request(`/documents/${docId}/detect-development`, { method: "POST" });
}

export function listDocumentDuplicates(docId) {
  return request(`/documents/${docId}/duplicates`);
}

// --- Языки и стоп-слова (Этап 7) ---

export function listActiveLocales() {
  return request("/locales").then((data) => data.locales ?? []);
}

export async function getUiDictionary(locale) {
  try {
    const r = await request(`/i18n/${encodeURIComponent(locale)}`);
    return r.data ?? null;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export function listLocales() {
  return request("/admin/locales").then((data) => data.locales ?? []);
}

export function createLocale(code, name) {
  return request("/admin/locales", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, name }),
  });
}

export function updateLocale(code, { name, status } = {}) {
  return request(`/admin/locales/${encodeURIComponent(code)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, status }),
  });
}

export function activateLocale(code) {
  return request(`/admin/locales/${encodeURIComponent(code)}/activate`, { method: "POST" });
}

export function disableLocale(code) {
  return request(`/admin/locales/${encodeURIComponent(code)}/disable`, { method: "POST" });
}

export function listStopwords(code, kind) {
  const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return request(`/admin/locales/${encodeURIComponent(code)}/stopwords${qs}`).then(
    (data) => data.words ?? []
  );
}

export function importStopwords(code, { words, kind = "bm25", mode = "merge", confirm = false, confirm_empty_replace = false } = {}) {
  const qs = `?kind=${encodeURIComponent(kind)}&mode=${encodeURIComponent(mode)}`;
  return request(`/admin/locales/${encodeURIComponent(code)}/stopwords/import${qs}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ words, confirm, confirm_empty_replace }),
  });
}

export function addStopword(code, { word, kind = "bm25" } = {}) {
  return request(`/admin/locales/${encodeURIComponent(code)}/stopwords`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ word, kind }),
  });
}

export function renameStopword(code, word, newWord, kind = "bm25") {
  return request(
    `/admin/locales/${encodeURIComponent(code)}/stopwords/${encodeURIComponent(word)}?kind=${encodeURIComponent(kind)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word: newWord }),
    }
  );
}

export function deleteStopword(code, word, kind = "bm25") {
  return request(
    `/admin/locales/${encodeURIComponent(code)}/stopwords/${encodeURIComponent(word)}?kind=${encodeURIComponent(kind)}`,
    { method: "DELETE" }
  );
}

export function stopwordsHistory(code) {
  return request(`/admin/locales/${encodeURIComponent(code)}/stopwords/history`).then(
    (data) => data.entries ?? []
  );
}

export function rollbackStopwords(code, entryId) {
  return request(`/admin/locales/${encodeURIComponent(code)}/stopwords/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entry_id: entryId }),
  });
}

export function probeStopwords(code, queries) {
  return request(`/admin/locales/${encodeURIComponent(code)}/stopwords/probe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ queries }),
  }).then((data) => data.results ?? []);
}

// --- Runtime-override UI-словарей (фаза C) + перевод справочников (фаза B) ---

export function getUiDictionaryAdmin(code) {
  return request(`/admin/locales/${encodeURIComponent(code)}/ui-dictionary`);
}

export function importUiDictionary(code, { data, note = "", confirm = false } = {}) {
  return request(`/admin/locales/${encodeURIComponent(code)}/ui-dictionary/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data, note, confirm }),
  });
}

export function uiDictionaryHistory(code) {
  return request(`/admin/locales/${encodeURIComponent(code)}/ui-dictionary/history`).then(
    (data) => data.entries ?? []
  );
}

export function rollbackUiDictionary(code, entryId) {
  return request(`/admin/locales/${encodeURIComponent(code)}/ui-dictionary/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entry_id: entryId }),
  });
}

export function backfillTranslations(locale, entities) {
  return request("/tags/translations/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locale, entities }),
  });
}

export function translationPending(locale, entities) {
  return request(
    `/tags/translations/pending?locale=${encodeURIComponent(locale)}&entities=${encodeURIComponent(entities.join(","))}`
  ).then((data) => data.pending ?? {});
}
