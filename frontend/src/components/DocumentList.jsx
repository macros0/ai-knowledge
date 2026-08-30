"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { deleteDocument, listDevelopments, listDocuments, listUploaders, regenerateDocument, resumeDocument, setDocumentDevelopment } from "@/lib/api";
import { filterDocuments, sortDocuments, SORT_OPTIONS } from "@/lib/docFilter.mjs";
import { DownloadIcon, EyeIcon, LinkIcon, RefreshIcon, TrashIcon } from "./icons";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "./Toast";
import BulkActionsBar from "./BulkActionsBar";
import PreviewModal from "./PreviewModal";
import DevelopmentPicker from "./DevelopmentPicker";
import DuplicateModal from "./DuplicateModal";

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
  const { mode, hasRole, loading, user } = useAuth();
  const { showToast } = useToast();
  const [docs, setDocs] = useState([]);
  const [regenerating, setRegenerating] = useState({});
  const [selected, setSelected] = useState({});
  const [showPreview, setShowPreview] = useState(false);
  const [filenameMask, setFilenameMask] = useState("");
  // Выбор в дропдауне: null — ещё не выбирал (действует дефолт по роли),
  // "" — все, "__me__" — мои, иначе — конкретный username.
  const [chosenUploader, setChosenUploader] = useState(null);
  const [uploaders, setUploaders] = useState([]);
  const [sortKey, setSortKey] = useState("date_desc");
  const [developments, setDevelopments] = useState([]);
  const [dupDoc, setDupDoc] = useState(null);
  const mounted = useRef(true);
  const timer = useRef(null);
  const loadSeq = useRef(0);

  // В disabled-режиме (всё открыто) действия доступны, как и на бэкенде.
  const canEdit = mode === "disabled" || hasRole("editor", "admin");
  const isAdmin = mode === "disabled" || hasRole("admin");

  // Единый фильтр по загрузчику: дефолт — «мои» для editor/admin, иначе «все».
  // "__me__" — спец-значение только для UI; на бэкенд уходит либо ничего (все),
  // либо резолвленный username текущего пользователя.
  const selectedUploader =
    chosenUploader ?? (hasRole("editor", "admin") ? "__me__" : "");

  const chooseUploader = (next) => setChosenUploader(next);

  const resolvedUploader =
    selectedUploader === "__me__" ? user?.username ?? "" : selectedUploader;

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    const list = await listDocuments(resolvedUploader);
    if (seq !== loadSeq.current || !mounted.current) return;
    setDocs(list);
    const busy = list.some((d) => BUSY_STATUSES.includes(d.status));
    if (busy && mounted.current) {
      timer.current = setTimeout(load, 1500);
    }
  }, [resolvedUploader]);

  const loadUploaders = useCallback(async () => {
    try {
      setUploaders(await listUploaders());
    } catch {
      // не критично — дропдаун просто останется без реальных username
    }
  }, []);

  const loadDevelopments = useCallback(async () => {
    try {
      setDevelopments(await listDevelopments());
    } catch {
      setDevelopments([]);
    }
  }, []);

  useEffect(() => {
    if (loading) return;
    mounted.current = true;
    load();
    loadUploaders();
    loadDevelopments();
    return () => {
      mounted.current = false;
      clearTimeout(timer.current);
    };
  }, [loading, refreshKey, load, loadUploaders, loadDevelopments]);

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

  const changeDevelopment = async (doc, value) => {
    const devId = value === null || value === "" ? null : Number(value);
    try {
      const res = await setDocumentDevelopment(doc.id, devId, devId !== null);
      if (res?.dev_tags_sync_pending) {
        showToast("Документ привязан, индексация обновится в фоне", { type: "warning" });
      }
      load();
    } catch (err) {
      showToast(`Не удалось изменить разработку: ${err.message}`, { type: "error" });
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

  const filteredDocs = useMemo(() => {
    const sort = SORT_OPTIONS[sortKey];
    return sortDocuments(filterDocuments(docs, { mask: filenameMask }), sort);
  }, [docs, filenameMask, sortKey]);

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
      {dupDoc && (
        <DuplicateModal
          doc={dupDoc}
          canDelete={canEdit}
          onClose={() => setDupDoc(null)}
          onDeleted={() => load()}
        />
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
        <select
          className="doc-filter-select"
          value={selectedUploader}
          onChange={(e) => chooseUploader(e.target.value)}
          aria-label="Фильтр по загрузчику"
        >
          <optgroup label="Быстрый выбор">
            <option value="__me__">Мои документы</option>
            <option value="">Все загрузчики</option>
          </optgroup>
          <optgroup label="Загрузчики">
            {uploaders.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </optgroup>
        </select>
        <select
          className="doc-filter-select"
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value)}
          aria-label="Сортировка"
        >
          <optgroup label="Дата">
            <option value="date_desc">Новые сначала</option>
            <option value="date_asc">Старые сначала</option>
          </optgroup>
          <optgroup label="Название">
            <option value="name_asc">А–Я</option>
            <option value="name_desc">Я–А</option>
          </optgroup>
          <optgroup label="Загрузчик">
            <option value="uploader_asc">А–Я</option>
            <option value="uploader_desc">Я–А</option>
          </optgroup>
        </select>
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
                {doc.development_number && (!canEdit || developments.length === 0) && (
                  <>
                    <br />
                    <Link
                      className="dev-tag-link"
                      href={`/developments/${doc.development_id}`}
                      title={`Разработка: ${doc.development_name || ""}`}
                    >
                      <LinkIcon size={12} />
                      {doc.development_number}
                      {doc.development_name ? ` · ${doc.development_name}` : ""}
                    </Link>
                  </>
                )}
                {doc.development_suggestion && !doc.development_id && (
                  <>
                    <br />
                    <span className="dev-suggestion">
                      Требует уточнения: {doc.development_suggestion.number || doc.development_suggestion.name || "—"}
                    </span>
                  </>
                )}
                {doc.has_duplicates && (
                  <>
                    <br />
                    <button
                      type="button"
                      className="dup-badge"
                      onClick={() => setDupDoc(doc)}
                      title="Показать дубликаты"
                    >
                      Дубликат
                    </button>
                  </>
                )}
                {canEdit && developments.length > 0 && (
                  <>
                    <br />
                    <DevelopmentPicker
                      developments={developments}
                      value={doc.development_id ?? null}
                      onChange={(devId) => changeDevelopment(doc, devId)}
                    />
                  </>
                )}
                {doc.uploaded_by && (
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
