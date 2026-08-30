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
  constructor(message, { status, code, service } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.service = service;
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
      try {
        const parsed = JSON.parse(detail);
        if (parsed && typeof parsed.detail === "string") message = parsed.detail;
        if (parsed && typeof parsed.code === "string") code = parsed.code;
        if (parsed && typeof parsed.service === "string") service = parsed.service;
      } catch {
        // not JSON — use raw text
      }
      throw new ApiError(message, { status: resp.status, code, service });
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

export function uploadDocument(file, tags = []) {
  const form = new FormData();
  form.append("file", file);
  for (const tag of tags) {
    form.append("tags", tag);
  }
  return request("/documents", { method: "POST", body: form });
}

export function listTags() {
  return request("/tags").then((data) => data.tags ?? []);
}

export function listDocuments(scope = "mine") {
  const qs = scope ? `?scope=${encodeURIComponent(scope)}` : "";
  return request(`/documents${qs}`).then((data) => data.documents ?? []);
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
