"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { deleteDocument, listDocuments, regenerateDocument, resumeDocument } from "@/lib/api";
import { filterDocuments } from "@/lib/docFilter.mjs";
import { DownloadIcon, EyeIcon, RefreshIcon, TrashIcon } from "./icons";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "./Toast";
import BulkActionsBar from "./BulkActionsBar";
import PreviewModal from "./PreviewModal";

const STATUS_LABELS = {
  uploaded: "Загружен",
  processing: "Парсинг...",
  splitting: "Генерация OKF...",
  indexing: "Индексация...",
  done: "Готов",
  paused: "Приостановлен",
  failed: "Ошибка",
  error: "Ошибка",
};

const BUSY_STATUSES = ["uploaded", "processing", "splitting", "indexing", "paused"];

function progressText(doc) {
  if (doc.status === "splitting" && doc.total_chunks > 0) {
    const active = doc.current_chunk ?? doc.processed_chunks;
    return `Генерация чанка ${active} из ${doc.total_chunks}`;
  }
  if (doc.status === "paused" && doc.total_chunks > 0) {
    return `Приостановлено: сохранено ${doc.processed_chunks ?? 0} из ${doc.total_chunks} чанков`;
  }
  return null;
}

export default function DocumentList({ refreshKey = 0 }) {
  const router = useRouter();
  const { mode, hasRole, loading } = useAuth();
  const { showToast } = useToast();
  const [docs, setDocs] = useState([]);
  const [regenerating, setRegenerating] = useState({});
  const [selected, setSelected] = useState({});
  const [showPreview, setShowPreview] = useState(false);
  const [filenameMask, setFilenameMask] = useState("");
  const [uploaderFilter, setUploaderFilter] = useState("");
  const mounted = useRef(true);
  const timer = useRef(null);

  // В disabled-режиме (всё открыто) действия доступны, как и на бэкенде.
  const canEdit = mode === "disabled" || hasRole("editor", "admin");
  const isAdmin = mode === "disabled" || hasRole("admin");

  // Вкладки «Мои документы»/«Все документы» — только для editor/admin (у них
  // есть «свои» загрузки). viewer/security/аноним своих не заводят — всегда
  // видят общий список (scope=all).
  const canSeeTabs = mode !== "disabled" && hasRole("editor", "admin");
  const [scope, setScope] = useState(canSeeTabs ? "mine" : "all");
  const scopeChosen = useRef(false);

  // После загрузки профиля выставляем корректный дефолт (editor/admin → «mine»,
  // остальные → «all»), но не перебиваем уже сделанный пользователем выбор.
  useEffect(() => {
    if (loading) return;
    if (scopeChosen.current) return;
    setScope(canSeeTabs ? "mine" : "all");
  }, [loading, canSeeTabs]);

  const chooseScope = (next) => {
    scopeChosen.current = true;
    setScope(next);
  };

  const load = useCallback(async () => {
    const list = await listDocuments(scope);
    if (!mounted.current) return;
    setDocs(list);
    const busy = list.some((d) => BUSY_STATUSES.includes(d.status));
    if (busy && mounted.current) {
      timer.current = setTimeout(load, 1500);
    }
  }, [scope]);

  useEffect(() => {
    mounted.current = true;
    load();
    return () => {
      mounted.current = false;
      clearTimeout(timer.current);
    };
  }, [refreshKey, load]);

  const openOkf = (doc) => {
    router.push(`/documents/${doc.id}/okf`);
  };

  const remove = async (doc) => {
    const isActive = doc.status === "splitting" || doc.status === "processing" || doc.status === "indexing";
    if (isActive && !window.confirm(`Документ "${doc.filename}" в процессе обработки. Удалить?`)) {
      return;
    }
    try {
      await deleteDocument(doc.id);
    } catch (err) {
      showToast(`Не удалось удалить: ${err.message}`, { type: "error" });
      load();
      return;
    }
    setSelected((s) => {
      const next = { ...s };
      delete next[doc.id];
      return next;
    });
    load();
  };

  const resume = async (doc) => {
    try {
      await resumeDocument(doc.id);
    } catch (err) {
      showToast(`Не удалось возобновить: ${err.message}`, { type: "error" });
      load();
      return;
    }
    load();
  };

  const regenerate = async (doc) => {
    if (regenerating[doc.id]) return;
    if (
      !window.confirm(
        `Перегенерировать концепты документа "${doc.filename}"?\nТекущие концепты и индекс будут удалены и созданы заново.`
      )
    ) {
      return;
    }
    setRegenerating((s) => ({ ...s, [doc.id]: true }));
    try {
      await regenerateDocument(doc.id);
      load();
    } catch (err) {
      showToast(`Не удалось перегенерировать: ${err.message}`, { type: "error" });
    } finally {
      setRegenerating((s) => ({ ...s, [doc.id]: false }));
    }
  };

  const toggleSelect = (id) => {
    setSelected((s) => {
      const next = { ...s };
      if (next[id]) delete next[id];
      else next[id] = true;
      return next;
    });
  };

  const selectedIds = Object.keys(selected);

  const uploaders = useMemo(() => {
    const set = new Set();
    for (const d of docs) {
      if (d.uploaded_by) set.add(d.uploaded_by);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [docs]);

  const filteredDocs = useMemo(
    () => filterDocuments(docs, { mask: filenameMask, uploaderFilter, scope }),
    [docs, filenameMask, uploaderFilter, scope]
  );

  return (
    <>
      {isAdmin && (
        <BulkActionsBar
          selectedIds={selectedIds}
          onClear={() => setSelected({})}
          onOpenPreview={() => setShowPreview(true)}
        />
      )}
      {showPreview && (
        <PreviewModal
          docIds={selectedIds}
          onClose={() => setShowPreview(false)}
          onDone={() => {
            setSelected({});
            setShowPreview(false);
            load();
          }}
        />
      )}
      {canSeeTabs && (
        <div className="scope-tabs" role="tablist" aria-label="Фильтр документов">
          <button
            type="button"
            role="tab"
            aria-selected={scope === "mine"}
            className={scope === "mine" ? "scope-tab active" : "scope-tab"}
            onClick={() => chooseScope("mine")}
          >
            Мои документы
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={scope === "all"}
            className={scope === "all" ? "scope-tab active" : "scope-tab"}
            onClick={() => chooseScope("all")}
          >
            Все документы
          </button>
        </div>
      )}
      <div className="doc-filter-bar">
        <input
          type="text"
          className="doc-filter-input"
          placeholder="Поиск по названию (* ? — маска)"
          value={filenameMask}
          onChange={(e) => setFilenameMask(e.target.value)}
          aria-label="Поиск по названию"
        />
        {scope === "all" && (
          <select
            className="doc-filter-select"
            value={uploaderFilter}
            onChange={(e) => setUploaderFilter(e.target.value)}
            aria-label="Фильтр по загрузчику"
          >
            <option value="">Все загрузчики</option>
            {uploaders.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        )}
      </div>
      <ul className="document-list">
        {filteredDocs.map((doc) => (
          <li key={doc.id} className="document-item">
            {isAdmin && (
              <input
                type="checkbox"
                className="doc-checkbox"
                aria-label={`Выбрать ${doc.filename}`}
                checked={!!selected[doc.id]}
                onChange={() => toggleSelect(doc.id)}
              />
            )}
            <div>
              <strong>{doc.filename}</strong>
              <div className="meta">
                {doc.error
                  ? `Ошибка: ${doc.error}`
                  : (progressText(doc) ?? `${(doc.size / 1024).toFixed(1)} КБ · Концептов: ${doc.okf_concept_count}`)}
                {doc.tags && doc.tags.length > 0 && (
                  <>
                    <br />
                    <span className="doc-tags">Теги: {doc.tags.join(", ")}</span>
                  </>
                )}
                {scope === "all" && doc.uploaded_by && (
                  <>
                    <br />
                    <span className="doc-uploader">Загрузил: {doc.uploaded_by}</span>
                  </>
                )}
              </div>
            </div>
            <div className="doc-actions">
              <span className={`status ${doc.status}`}>{STATUS_LABELS[doc.status] ?? doc.status}</span>
              <button className="icon-btn" onClick={() => openOkf(doc)} title="Концепты и чанки">
                <EyeIcon />
              </button>
              <a
                className="icon-btn"
                href={`/api/documents/${doc.id}/download`}
                download
                title="Скачать исходный файл"
              >
                <DownloadIcon />
              </a>
              {canEdit && (
                <span className="danger-group">
                  {!["uploaded", "processing", "splitting", "indexing"].includes(doc.status) && (
                    <button
                      className="icon-btn"
                      onClick={() => regenerate(doc)}
                      title="Перегенерировать концепты (LLM)"
                      disabled={regenerating[doc.id]}
                    >
                      <RefreshIcon className={regenerating[doc.id] ? "spin" : undefined} />
                    </button>
                  )}
                  {(doc.status === "paused" || doc.status === "failed") && (
                    <button className="delete-btn" onClick={() => resume(doc)}>
                      Возобновить
                    </button>
                  )}
                  <button className="delete-btn" onClick={() => remove(doc)} title="Удалить">
                    <TrashIcon />
                  </button>
                </span>
              )}
            </div>
          </li>
        ))}
        {docs.length > 0 && filteredDocs.length === 0 && (
          <li className="document-empty">Ничего не найдено</li>
        )}
      </ul>
    </>
  );
}
