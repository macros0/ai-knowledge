import { detectLocale, readStored } from "../i18n/core.js";
import { readChatStream } from "./chatStream.mjs";
import { errorReference, validRequestId } from "./diagnosticIdentifiers.mjs";

const BASE = "/api";
const CHAT_TIMEOUT_MS = 210_000;

function currentUiLocale() {
  if (typeof window === "undefined") return "ru";
  return readStored(window) || detectLocale(window.navigator?.languages);
}

// Тексты ошибок живут в словарях (i18n/locales), а не здесь: интерфейс
// двуязычный, и литерал в api.js пришёл бы к англоязычному пользователю
// по-русски. Здесь — только выбор КЛЮЧА по ответу бэкенда.
const KNOWN_SERVICES = new Set(["llm", "ollama", "qdrant"]);

export function serviceMessageKey(service) {
  return KNOWN_SERVICES.has(service) ? `apiError.service.${service}` : null;
}

export class ApiError extends Error {
  constructor(message, { status, code, service, data, requestId, localReportId } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.service = service;
    this.data = data;
    this.requestId = validRequestId(requestId) ? requestId : null;
    this.localReportId = !this.requestId && status === 0
      ? validRequestId(localReportId) ? localReportId : globalThis.crypto?.randomUUID?.() || null : null;
  }

  get isDependencyUnavailable() {
    return this.code === "dependency_unavailable";
  }
}

// Ключ словаря и параметры для ошибки API — чистая функция, тестируется без DOM.
//
// Приоритет: стабильный code из тела ответа -> сервис недоступной зависимости
// -> статус. Неизвестные ошибки получают безопасное общее сообщение.
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
  const statusCode = {
    400: "invalid_request", 408: "timeout", 409: "conflict",
    413: "file_too_large", 422: "invalid_request", 429: "rate_limited",
    502: "dependency_unavailable", 503: "dependency_unavailable", 504: "timeout",
  }[err.status];
  if (statusCode) return { key: `apiError.${statusCode}`, params: {} };
  return null;
}

// Человекочитаемое сообщение об ошибке API на языке интерфейса: тосты в админ-UI
// не должны показывать «Not Found» / «Internal Server Error» без контекста.
// В UX допускаются только тексты из словаря: detail/message могут содержать
// ответы провайдера, идентификаторы, SQL и трассировки исключений.
export function friendlyApiError(err, t) {
  const tr = t || ((k) => k);
  const picked = apiErrorKey(err);
  if (picked) {
    const text = tr(picked.key, picked.params);
    if (text && text !== picked.key) return text;
  }
  const fallback = tr("apiError.internal_error");
  return fallback && fallback !== "apiError.internal_error"
    ? fallback
    : "Внутренняя ошибка. Обратитесь в техническую поддержку.";
}

// Document errors have a stable ID and timestamp even when the underlying
// exception is intentionally hidden. Only a narrow legacy parser message is
// inspected for its numeric limit; arbitrary doc.error is never rendered.
export function friendlyDocumentError(doc, t) {
  const legacyMemory = /^Разбор файла превысил лимит (\d{1,5}) MiB$/.exec(doc?.error || "");
  const code = doc?.error_code === "internal_error" && legacyMemory
    ? "parser_resource_limit" : doc?.error_code;
  const message = friendlyApiError(new ApiError("", { code }), t);
  const details = [];
  if (/^[a-z][a-z0-9_]{0,63}$/.test(code || "")) {
    details.push(t("apiError.supportCode", { code }));
  }
  if (legacyMemory && Number(legacyMemory[1]) > 0) {
    details.push(t("apiError.supportMemory", { limit: Number(legacyMemory[1]) }));
  }
  if (/^[0-9a-f]{16}$/.test(doc?.id || "")) {
    details.push(t("apiError.supportDocument", { id: doc.id }));
  }
  if (/^\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d/.test(doc?.updated_at || "")) {
    details.push(t("apiError.supportTime", { time: doc.updated_at }));
  }
  return details.length ? `${message} ${details.join("; ")}` : message;
}

// Единственная точка вызова fetch в модуле: URL берётся как есть, разбор
// успешного ответа задаёт вызывающий. Нормализация ошибок (таймаут, сетевой
// сбой, не-2xx с кодом из тела) живёт только здесь — сырой fetch мимо этой
// обёртки отдал бы наружу браузерное «Failed to fetch» вместо ключа словаря.
async function fetchApi(url, { init, timeoutMs, parse = (resp) => resp.json() } = {}) {
  const controller = new AbortController();
  const externalSignal = init?.signal;
  const abortFromCaller = () => controller.abort();
  if (externalSignal?.aborted) controller.abort();
  externalSignal?.addEventListener("abort", abortFromCaller, { once: true });
  const timer =
    timeoutMs != null
      ? setTimeout(() => controller.abort(), timeoutMs)
      : null;
  try {
    const resp = await fetch(url, { ...init, signal: controller.signal });
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
      throw new ApiError(message, { status: resp.status, code, service, data,
        requestId: errorReference(resp.headers.get("x-request-id"), data?.request_id) });
    }
    return await parse(resp);
  } catch (err) {
    if (err.name === "AbortError") {
      // message — фолбэк-диагностика; текст для пользователя берётся по коду.
      throw new ApiError("request timeout", { status: 0, code: "timeout" });
    }
    // Сетевой сбой (backend не поднят, обрыв связи, CORS) — fetch кидает
    // TypeError. Без обёртки он уходил наружу как есть, и friendlyApiError
    // показывал браузерное «Failed to fetch» по-английски мимо словарей.
    // status 0 без кода → apiError.backendUnreachable.
    if (err instanceof TypeError) {
      throw new ApiError(err.message, { status: 0 });
    }
    throw err;
  } finally {
    if (timer != null) clearTimeout(timer);
    externalSignal?.removeEventListener("abort", abortFromCaller);
  }
}

// JSON-эндпоинт под /api — частный (и почти всегда нужный) случай fetchApi.
export function csrfTokenFromDocument(doc = typeof document !== "undefined" ? document : null) {
  if (!doc || typeof doc.cookie !== "string") return null;
  const raw = doc.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("csrf_token="))
    ?.slice("csrf_token=".length);
  if (!raw) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

function request(path, init, timeoutMs, parse) {
  const method = (init?.method || "GET").toUpperCase();
  const headers = new Headers(init?.headers || {});
  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    const csrf = csrfTokenFromDocument();
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  return fetchApi(`${BASE}${path}`, {
    init: { ...init, headers },
    timeoutMs,
    parse,
  });
}

function jsonRequest(path, method, data, timeoutMs) {
  return request(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }, timeoutMs);
}

export const getDiagnosticBrowserStatus = () => request("/diagnostic-client/status", {}, 5000);
export const joinDiagnosticBrowser = (code = null) => jsonRequest("/diagnostic-client/join", "POST", code === null ? {} : { code });
export const leaveDiagnosticBrowser = (participationId) => jsonRequest("/diagnostic-client/leave", "POST", { participation_id: participationId });
export const sendDiagnosticBrowserEvent = (event) => request("/diagnostic-client/events", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(event),
}, 5000, () => null);

const diagnosticBase = "/admin/diagnostics";
export const getDiagnosticsStatus = () => request(`${diagnosticBase}/status`);
export const queryDiagnosticEvents = (filters) => jsonRequest(`${diagnosticBase}/events/query`, "POST", filters);
export const startDiagnosticSession = (settings) => jsonRequest(`${diagnosticBase}/sessions`, "POST", settings);
export const stopDiagnosticSession = (id) => jsonRequest(`${diagnosticBase}/sessions/${encodeURIComponent(id)}/stop`, "POST", {});
export const inviteDiagnosticBrowser = (id) => jsonRequest(`${diagnosticBase}/sessions/${encodeURIComponent(id)}/invite`, "POST", {});
export const createDiagnosticBundle = (filters) => jsonRequest(`${diagnosticBase}/bundles`, "POST", filters);
export const listDiagnosticBundles = (offset = 0) => request(`${diagnosticBase}/bundles?offset=${offset}&limit=20`);
export const previewDiagnosticBundle = (id) => jsonRequest(`${diagnosticBase}/bundles/${encodeURIComponent(id)}/preview`, "POST", {});
export const deleteDiagnosticBundle = (id) => request(`${diagnosticBase}/bundles/${encodeURIComponent(id)}`, { method: "DELETE" });
export const diagnosticDownloadUrl = (id) => `${BASE}${diagnosticBase}/bundles/${encodeURIComponent(id)}/download`;

export function uploadDocument(file, tags = [], { developmentId = null, canonicalLocale, allowSimilar = false } = {}) {
  const form = new FormData();
  form.append("file", file);
  if (allowSimilar) form.append("allow_similar", "true");
  if (canonicalLocale) form.append("canonical_locale", canonicalLocale);
  for (const tag of tags) {
    form.append("tags", tag);
  }
  if (developmentId != null) {
    form.append("development_id", String(developmentId));
  }
  return request("/documents", { method: "POST", body: form });
}

export async function logoutAuth() {
  const result = await request("/auth/logout", { method: "POST" });
  window.location.assign(result.redirect_url || "/");
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

export function getDocument(docId) {
  return request(`/documents/${encodeURIComponent(docId)}`);
}

export function updateDocumentTags(docId, tags, canonicalLocale) {
  return request(`/documents/${docId}/tags`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags, canonical_locale: canonicalLocale }),
  });
}

export function bulkUpdateTags(docIds, { add = [], remove = [], canonicalLocale } = {}) {
  return request("/documents/bulk-tags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_ids: docIds, add, remove, canonical_locale: canonicalLocale }),
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
  if (params.sourceLocales) qs.set("source_locales", params.sourceLocales);
  if (params.sourceLocaleUnknown) qs.set("source_locale_unknown", "true");
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

export function getDocumentQueueStatus() {
  return request("/documents/queue-status");
}

export function getSourceLocaleFacets(uploader) {
  const qs = uploader ? `?uploader=${encodeURIComponent(uploader)}` : "";
  return request(`/documents/source-locale-facets${qs}`).then((data) => data.items ?? []);
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

export function cancelDocumentUpdate(docId, updateId) {
  return request(`/documents/${docId}/cancel-update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ update_id: updateId }),
  });
}

export function bulkPreview(docIds, operation) {
  return request(`/documents/bulk-preview${operation ? `?operation=${encodeURIComponent(operation)}` : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_ids: docIds }),
  }).then((data) => operation ? generationPreview(data) : data);
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

export function getDocumentSources(docId) {
  return request(`/documents/${encodeURIComponent(docId)}/sources`);
}

export function bulkResume(docIds) {
  return request("/documents/bulk-resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_ids: docIds }),
  });
}

function generationPreview(data) {
  if (!Array.isArray(data?.eligible_doc_ids) || !Array.isArray(data?.skipped) || !Array.isArray(data?.documents) || !Number.isInteger(data?.max_docs)) {
    throw new ApiError("Generation preview requires an updated backend", { status: 404 });
  }
  return data;
}

export function previewInterruptedDocuments() {
  return request("/documents/bulk-resume/interrupted").then(generationPreview);
}

export function getDocumentChunks(docId) {
  return request(`/documents/${docId}/chunks`);
}

export function getOkfContent(docId, filename) {
  // Ответ — не JSON, а текст OKF-файла; всё остальное (коды, сетевой сбой) —
  // как у прочих вызовов.
  return fetchApi(`${BASE}/documents/${docId}/okf/${filename}`, {
    parse: (resp) => resp.text(),
  });
}

export function search(query, tags = [], topK = 5, mode = "hybrid", useGlossary = true) {
  return request("/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, tags, top_k: topK, mode, use_glossary: useGlossary }),
  });
}

export function chat(query, tags = [], topK = 5, mode = "hybrid", sessionId = null, sourceLocale = "", useGlossary = true, mailMode = "all", onText = null, onSources = null, options = {}) {
  const body = { query, locale: currentUiLocale(), tags, top_k: topK, mode, use_glossary: useGlossary, mail_mode: mailMode };
  if (options.responseMode) body.response_mode = options.responseMode;
  if (options.searchDepth != null) body.search_depth = options.searchDepth;
  if (options.attemptId) body.attempt_id = options.attemptId;
  if (options.sourceSelection) body.source_selection = options.sourceSelection;
  if (options.searchDocIds != null) body.search_doc_ids = options.searchDocIds;
  if (sessionId) body.session_id = sessionId;
  // Фильтр по языку документа (Этап 7 фаза D): не отправляем поле при «Все языки».
  if (sourceLocale === "unknown") {
    body.include_unknown_source_locale = true;
  } else if (sourceLocale) {
    body.source_locales = [sourceLocale];
    body.include_unknown_source_locale = false;
  }
  return request(
    onText ? "/chat/stream" : "/chat",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: options.signal,
    },
    options.responseMode ? undefined : CHAT_TIMEOUT_MS,
    onText ? async (response) => {
      try {
        return await readChatStream(response, onText, onSources || (() => {}), {
          onProgress: options.onProgress,
          idleTimeoutMs: options.responseMode ? CHAT_TIMEOUT_MS : 0,
        });
      } catch (err) {
        if (err.name === "AbortError") throw err;
        throw new ApiError("Chat stream failed", { code: err.code || "dependency_unavailable", status: err.status || 503,
          requestId: err.requestId });
      }
    } : undefined
  );
}

export function getChatSearchScope(docIds) {
  return request('/chat/search-scope', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_ids: docIds }),
  });
}

export function cancelChatAttempt(attemptId, sessionId) {
  return request(`/chat/attempts/${encodeURIComponent(attemptId)}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
    keepalive: true,
  });
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

export function listRecentChatTurns({limit = 10, beforeId, excludeSessionId} = {}) {
  const qs = new URLSearchParams({limit: String(limit)});
  if (beforeId != null) qs.set("before_id", String(beforeId));
  if (excludeSessionId) qs.set("exclude_session_id", excludeSessionId);
  return request(`/chat/history/recent?${qs}`);
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
  // /health живёт в корне, а не под /api, — отсюда fetchApi с полным URL.
  // Деградация приходит как 200 со статусом в теле, так что проверка resp.ok
  // баннер не ломает: не-2xx здесь означает именно недоступный сервис.
  return fetchApi("/health");
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

export function createDevelopment({ number, name, module, canonical_locale }) {
  return request("/developments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number, name, module, canonical_locale }),
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

export function addAttributeValue(key, value, { label, canonicalLocale } = {}) {
  return request(`/attributes/${key}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value, label, canonical_locale: canonicalLocale }),
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

export function setDocumentSourceLocale(docId, locale) {
  return request(`/documents/${docId}/source-locale`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_locale: locale }),
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

export function createBulkExport(docIds) {
  return request("/documents/bulk-export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_ids: docIds }),
  });
}

export function deleteExportArtifacts(jobId) {
  return request(`/jobs/${jobId}/export`, { method: "DELETE" });
}

// --- Доменный глоссарий ---

export function listGlossary(params = {}) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") qs.set(key, value);
  }
  const query = qs.toString();
  return request(`/admin/glossary${query ? `?${query}` : ""}`);
}

export function getGlossaryTerm(termId) {
  return request(`/admin/glossary/${termId}`);
}

export function checkGlossaryAliases(aliases, termId, draftContext) {
  return request("/admin/glossary/aliases/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ aliases, term_id: termId ?? null, ...draftContext }),
  });
}

export function checkGlossaryConflicts(draft, termId) {
  return request("/admin/glossary/conflicts/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({...draft, term_id: termId ?? null}),
  });
}

export function createGlossaryTerm(data) {
  return request("/admin/glossary", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function updateGlossaryTerm(termId, data) {
  return request(`/admin/glossary/${termId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function updateGlossarySource(termId, data) {
  return request(`/admin/glossary/${termId}/source`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function addGlossaryAlias(termId, data) {
  return request(`/admin/glossary/${termId}/aliases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function updateGlossaryAlias(termId, aliasId, data) {
  return request(`/admin/glossary/${termId}/aliases/${aliasId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function deleteGlossaryAlias(termId, aliasId, version) {
  return request(`/admin/glossary/${termId}/aliases/${aliasId}?version=${encodeURIComponent(version)}`, { method: "DELETE" });
}

export function previewGlossaryQuery(query, locale) {
  return request("/admin/glossary/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, locale }),
  });
}

export function glossaryTranslation(termId, locale, data) {
  return request(`/admin/glossary/${termId}/translations/${encodeURIComponent(locale)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function reviewGlossaryTranslation(termId, locale, data) {
  return request(`/admin/glossary/${termId}/translations/${encodeURIComponent(locale)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function pendingGlossaryTranslations(locale) {
  return request(`/admin/glossary/translations/pending?locale=${encodeURIComponent(locale)}`);
}

export function backfillGlossaryTranslations(locale, termIds, expectedTranslationVersions = {}) {
  return request("/admin/glossary/translations/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locale, term_ids: termIds, expected_translation_versions: expectedTranslationVersions }),
  });
}

export function listGlossaryRules() {
  return request("/admin/glossary/rules");
}

export function createGlossaryRule(data) {
  return jsonRequest("/admin/glossary/rules", "POST", data);
}

export function updateGlossaryRule(ruleId, data) {
  return jsonRequest(`/admin/glossary/rules/${ruleId}`, "PATCH", data);
}

export function deleteGlossaryRule(ruleId, version) {
  return request(`/admin/glossary/rules/${ruleId}?version=${encodeURIComponent(version)}`, { method: "DELETE" });
}

export function previewGlossaryMerge(data) {
  return jsonRequest("/admin/glossary/merge/preview", "POST", data);
}

export function commitGlossaryMerge(data) {
  return jsonRequest("/admin/glossary/merge", "POST", data);
}

export function previewGlossaryRuleMerge(data) {
  return jsonRequest("/admin/glossary/rules/merge/preview", "POST", data);
}

export function previewGlossaryRule(data) {
  return jsonRequest("/admin/glossary/rules/preview", "POST", data);
}

export function commitGlossaryRuleMerge(data) {
  return jsonRequest("/admin/glossary/rules/merge", "POST", data);
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
