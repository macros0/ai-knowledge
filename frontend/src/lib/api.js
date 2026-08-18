const BASE = "/api";

async function request(path, init) {
  const resp = await fetch(`${BASE}${path}`, init);
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return resp.json();
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

export function listDocuments() {
  return request("/documents").then((data) => data.documents ?? []);
}

export function deleteDocument(docId) {
  return request(`/documents/${docId}`, { method: "DELETE" });
}

export function resumeDocument(docId) {
  return request(`/documents/${docId}/resume`, { method: "POST" });
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
  return request("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, tags, top_k: topK, mode }),
  });
}

export function getChatSettings() {
  return request("/settings");
}
