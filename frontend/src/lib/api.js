const BASE = "/api";
const CHAT_TIMEOUT_MS = 90_000;

const SERVICE_MESSAGES = {
  llm: "Сервис генерации ответа недоступен. Проверьте подключение к провайдеру LLM.",
  ollama: "Сервис эмбеддингов недоступен. Проверьте, что embedding-сервис запущен.",
  qdrant: "База знаний недоступна. Проверьте, что Qdrant запущен.",
};

export function getServiceMessage(service) {
  return SERVICE_MESSAGES[service] || null;
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
      throw new ApiError(
        "Не удалось получить ответ вовремя: сервис генерации временно недоступен, попробуйте позже.",
        { status: 0 }
      );
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

export function listUploaders() {
  return request("/documents/uploaders").then((data) => data.uploaders ?? []);
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

export function chat(query, tags = [], topK = 5, mode = "hybrid") {
  return request(
    "/chat",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, tags, top_k: topK, mode }),
    },
    CHAT_TIMEOUT_MS
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

export function updateDevelopment(devId, { number, name, module }) {
  return request(`/developments/${devId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number, name, module }),
  });
}

export function deleteDevelopment(devId) {
  return request(`/developments/${devId}`, { method: "DELETE" });
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
