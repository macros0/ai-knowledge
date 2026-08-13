import { chat, deleteDocument, listDocuments, listOkfFiles, listTags, uploadDocument } from "./api.js";
import "./style.css";

const STATUS_LABELS = {
  uploaded: "Загружен",
  processing: "Парсинг...",
  splitting: "Генерация OKF...",
  indexing: "Индексация...",
  done: "Готов",
  error: "Ошибка",
};

const $ = (sel) => document.querySelector(sel);

const tabDocuments = $("#tab-documents");
const tabChat = $("#tab-chat");
const panelDocuments = $("#panel-documents");
const panelChat = $("#panel-chat");
const uploadZone = $("#upload-zone");
const fileInput = $("#file-input");
const documentList = $("#document-list");
const chatLog = $("#chat-log");
const chatForm = $("#chat-form");
const chatInput = $("#chat-input");
const submitBtn = chatForm.querySelector("button");

function switchTab(name) {
  tabDocuments.classList.toggle("active", name === "documents");
  tabChat.classList.toggle("active", name === "chat");
  panelDocuments.classList.toggle("hidden", name !== "documents");
  panelChat.classList.toggle("hidden", name !== "chat");
  if (name === "documents") refreshDocuments();
}

tabDocuments.addEventListener("click", () => switchTab("documents"));
tabChat.addEventListener("click", () => switchTab("chat"));

// ---------------- tag pickers ----------------
const datalist = $("#tag-datalist");
let tagDictionary = [];

async function refreshTagDictionary() {
  try {
    tagDictionary = await listTags();
  } catch {
    tagDictionary = [];
  }
  datalist.innerHTML = "";
  for (const t of tagDictionary) {
    const opt = document.createElement("option");
    opt.value = t.name;
    datalist.appendChild(opt);
  }
}

function createTagPicker({ chipsEl, inputEl, onChange }) {
  const selected = new Set();

  function render() {
    chipsEl.innerHTML = "";
    for (const tag of [...selected]) {
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      chip.textContent = tag;
      chip.addEventListener("click", () => {
        selected.delete(tag);
        render();
        onChange([...selected]);
      });
      chipsEl.appendChild(chip);
    }
  }

  inputEl.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const value = inputEl.value.trim();
    if (!value) return;
    inputEl.value = "";
    if (!selected.has(value)) {
      selected.add(value);
      render();
      onChange([...selected]);
    }
  });

  inputEl.addEventListener("blur", () => {
    const value = inputEl.value.trim();
    if (value && !selected.has(value)) {
      selected.add(value);
      inputEl.value = "";
      render();
      onChange([...selected]);
    }
  });

  return {
    selected() {
      return [...selected];
    },
    set(tags) {
      selected.clear();
      for (const t of tags) selected.add(t);
      render();
    },
    clear() {
      selected.clear();
      render();
    },
  };
}

const uploadTags = createTagPicker({
  chipsEl: $("#upload-tags-chips"),
  inputEl: $("#upload-tags-input"),
  onChange: () => {},
});
const chatTags = createTagPicker({
  chipsEl: $("#chat-tags-chips"),
  inputEl: $("#chat-tags-input"),
  onChange: () => {},
});

// ---------------- upload ----------------
uploadZone.addEventListener("click", () => fileInput.click());
uploadZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  uploadZone.classList.add("dragover");
});
uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("dragover"));
uploadZone.addEventListener("drop", (e) => {
  e.preventDefault();
  uploadZone.classList.remove("dragover");
  const file = e.dataTransfer?.files?.[0];
  if (file) handleUpload(file);
});
fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  if (file) handleUpload(file);
  fileInput.value = "";
});

async function handleUpload(file) {
  const tags = uploadTags.selected();
  try {
    await uploadDocument(file, tags);
    uploadTags.clear();
    await refreshTagDictionary();
    await refreshDocuments();
    pollStatus();
  } catch (err) {
    alert(`Не удалось загрузить файл: ${err.message}`);
  }
}

// ---------------- document list ----------------
async function refreshDocuments() {
  const docs = await listDocuments();
  documentList.innerHTML = "";
  for (const doc of docs) {
    documentList.appendChild(renderDocumentItem(doc));
  }
}

function renderDocumentItem(doc) {
  const li = document.createElement("li");
  li.className = "document-item";

  const info = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = doc.filename;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = doc.error
    ? `Ошибка: ${doc.error}`
    : `${(doc.size / 1024).toFixed(1)} КБ · OKF-файлов: ${doc.okf_file_count}`;
  if (doc.tags && doc.tags.length) {
    const tagsSpan = document.createElement("span");
    tagsSpan.className = "doc-tags";
    tagsSpan.textContent = `Теги: ${doc.tags.join(", ")}`;
    meta.appendChild(document.createElement("br"));
    meta.appendChild(tagsSpan);
  }
  info.append(title, meta);

  const right = document.createElement("div");
  right.style.display = "flex";
  right.style.alignItems = "center";
  right.style.gap = "8px";

  const status = document.createElement("span");
  status.className = `status ${doc.status}`;
  status.textContent = STATUS_LABELS[doc.status] ?? doc.status;
  right.appendChild(status);

  const details = document.createElement("button");
  details.className = "delete-btn";
  details.textContent = "OKF";
  details.addEventListener("click", () => showOkf(doc.id));
  right.appendChild(details);

  if (doc.status === "done") {
    const del = document.createElement("button");
    del.className = "delete-btn";
    del.textContent = "Удалить";
    del.addEventListener("click", async () => {
      await deleteDocument(doc.id);
      refreshDocuments();
    });
    right.appendChild(del);
  }

  li.append(info, right);
  return li;
}

let pollTimer;

function pollStatus() {
  window.clearTimeout(pollTimer);
  pollTimer = window.setTimeout(async () => {
    const docs = await listDocuments();
    const busy = docs.some(
      (d) => d.status === "uploaded" || d.status === "processing" || d.status === "splitting" || d.status === "indexing",
    );
    await refreshDocuments();
    if (busy) pollStatus();
  }, 1500);
}

// ---------------- OKF viewer ----------------
async function showOkf(docId) {
  const files = await listOkfFiles(docId);
  if (!files.length) return;
  const text = await fetch(`/api/documents/${docId}/okf/${encodeURIComponent(files[0].filename)}`).then((r) => r.text());
  const win = window.open("", "_blank", "width=720,height=600");
  if (!win) return;
  win.document.write(`<pre style="font-family:monospace;white-space:pre-wrap;padding:16px">${escapeHtml(text)}</pre>`);
  win.document.close();
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ---------------- chat ----------------
chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = chatInput.value.trim();
  if (!query) return;

  appendMessage("user", query);
  chatInput.value = "";
  submitBtn.disabled = true;

  const loading = appendMessage("assistant", "Думаю...");
  try {
    const resp = await chat(query, chatTags.selected());
    loading.querySelector(".bubble").textContent = resp.answer;
    renderSources(loading, resp.sources);
  } catch (err) {
    loading.querySelector(".bubble").textContent = `Ошибка: ${err.message}`;
  } finally {
    submitBtn.disabled = false;
    chatLog.scrollTop = chatLog.scrollHeight;
  }
});

function appendMessage(role, text) {
  const wrapper = document.createElement("div");
  wrapper.className = `msg ${role}`;
  const roleLabel = document.createElement("div");
  roleLabel.className = "role";
  roleLabel.textContent = role === "user" ? "Вы" : "Ассистент";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrapper.append(roleLabel, bubble);
  chatLog.appendChild(wrapper);
  chatLog.scrollTop = chatLog.scrollHeight;
  return wrapper;
}

function renderSources(wrapper, sources) {
  if (!sources.length) return;
  const box = document.createElement("details");
  box.className = "sources";
  const summary = document.createElement("summary");
  summary.textContent = "Источники";
  const ol = document.createElement("ol");
  for (const s of sources) {
    const li = document.createElement("li");
    li.textContent = `${s.title} (релевантность ${(s.score * 100).toFixed(0)}%)`;
    ol.appendChild(li);
  }
  box.append(summary, ol);
  wrapper.appendChild(box);
}

// ---------------- init ----------------
switchTab("documents");
pollStatus();
refreshTagDictionary();
