"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { deleteDocument, listAttributeValues, listDevelopments, listDocuments, listUploaders, regenerateDocument, resumeDocument, setDocumentDevelopment, updateDocumentTags } from "@/lib/api";
import { bumpTagVersion, useTagDictionary } from "@/lib/tagDictionary";
import { DownloadIcon, EyeIcon, LinkIcon, RefreshIcon, TrashIcon } from "./icons";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "./Toast";
import SelectionBar from "./SelectionBar";
import PreviewModal from "./PreviewModal";
import DevelopmentFilter from "./DevelopmentFilter";
import DevelopmentPicker from "./DevelopmentPicker";
import DuplicateModal from "./DuplicateModal";
import TagPicker from "./TagPicker";
import TagManagerModal from "./TagManagerModal";

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

const PAGE_SIZE = 50;
// Кап «Выделить все по фильтру» — совпадает с bulk_tags_max_docs. Бэкенд всё равно
// отклоняет превышение реального лимита понятным 400; кап защищает от лишнего набора.
const MAX_SELECT = 50;
const SEARCH_DEBOUNCE_MS = 300;

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

export default function DocumentList({ refreshKey = 0, onOpenTrash }) {
  const router = useRouter();
  const { mode, hasRole, loading, user } = useAuth();
  const { showToast } = useToast();
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [regenerating, setRegenerating] = useState({});
  const [selected, setSelected] = useState({});
  // Id документов, которые выделил toggle «Выделить все» (по фильтру). Нужны,
  // чтобы отличать «включено» (✓) от частично снятого вручную (◐).
  const [filterSelectedIds, setFilterSelectedIds] = useState([]);
  const [showPreview, setShowPreview] = useState(false);
  // Поиск теперь серверный: searchInput — что ввёл пользователь (без задержки),
  // search — дебаунснутое значение, уходящее в запрос.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  // Выбор в дропдауне: null — ещё не выбирал (действует дефолт по роли),
  // "" — все, "__me__" — мои, иначе — конкретный username.
  const [chosenUploader, setChosenUploader] = useState(null);
  const [uploaders, setUploaders] = useState([]);
  const [sortKey, setSortKey] = useState("date_desc");
  const [developments, setDevelopments] = useState([]);
  const [modules, setModules] = useState([]);
  const [problemOnly, setProblemOnly] = useState(false);
  const [moduleFilter, setModuleFilter] = useState("");
  const [devFilter, setDevFilter] = useState(null);
  const [tagFilter, setTagFilter] = useState("");
  const [dupDoc, setDupDoc] = useState(null);
  const [showTags, setShowTags] = useState(false);
  // Спойлер редактора тегов в карточке: по умолчанию свёрнут (чипы + «✎»),
  // раскрытие — только когда нужно редактировать (не частая операция).
  const [editingTags, setEditingTags] = useState({});
  // Спойлер панели массовых действий: свёрнут по умолчанию, раскрывается по клику
  // или автоматически при появлении выделения.
  const [bulkOpen, setBulkOpen] = useState(false);
  const mounted = useRef(true);
  const timer = useRef(null);
  const loadSeq = useRef(0);

  // Словарь тегов для селекта фильтра (общий с пикерами, обновляется по bumpTagVersion).
  const tagDictionary = useTagDictionary();
  const filterTags = tagDictionary.filter((t) => t.count > 0);

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
    const result = await listDocuments({
      uploader: resolvedUploader || undefined,
      problem: problemOnly ? true : undefined,
      module: moduleFilter || undefined,
      developmentId: devFilter ? Number(devFilter) : undefined,
      tag: tagFilter || undefined,
      search: search || undefined,
      sort: sortKey,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    });
    if (seq !== loadSeq.current || !mounted.current) return;
    setDocs(result.documents);
    setTotal(result.total);
    const busy = result.documents.some((d) => BUSY_STATUSES.includes(d.status));
    if (busy && mounted.current) {
      timer.current = setTimeout(load, 1500);
    }
  }, [resolvedUploader, problemOnly, moduleFilter, devFilter, tagFilter, search, sortKey, page]);

  const loadUploaders = useCallback(async () => {
    try {
      setUploaders(await listUploaders());
    } catch {
      // не критично — дропдаун просто останется без реальных username
    }
  }, []);

  const loadModules = useCallback(async () => {
    try {
      setModules((await listAttributeValues("module")).map((v) => v.value));
    } catch {
      setModules([]);
    }
  }, []);

  const loadDevelopments = useCallback(async () => {
    try {
      setDevelopments(await listDevelopments());
    } catch {
      setDevelopments([]);
    }
  }, []);

  // Debounce серверного поиска: не слать запрос на каждое нажатие клавиши.
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput.trim());
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchInput]);

  // Смена поиска/сортировки/фильтра сбрасывает страницу к началу (иначе
  // окажемся на середине старой страницы с новым набором результатов).
  useEffect(() => {
    setPage(0);
  }, [search, sortKey, resolvedUploader, problemOnly, moduleFilter, devFilter, tagFilter]);

  useEffect(() => {
    if (loading) return;
    mounted.current = true;
    load();
    loadUploaders();
    loadModules();
    loadDevelopments();
    return () => {
      mounted.current = false;
      clearTimeout(timer.current);
    };
  }, [loading, refreshKey, load, loadUploaders, loadModules, loadDevelopments]);

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
    showToast(`Документ «${doc.filename}» перемещён в корзину`, {
      type: "success",
      action: onOpenTrash ? { label: "Открыть корзину", onClick: onOpenTrash } : null,
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

  const changeTags = async (doc, tags) => {
    try {
      const res = await updateDocumentTags(doc.id, tags);
      if (res?.dev_tags_sync_pending) {
        showToast("Теги сохранены, индексация разработки обновится в фоне", { type: "warning" });
      }
      bumpTagVersion();
      load();
    } catch (err) {
      showToast(`Не удалось изменить теги: ${err.message}`, { type: "error" });
      load();
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

  const toggleEditTags = (id) => {
    setEditingTags((s) => ({ ...s, [id]: !s[id] }));
  };

  const selectedIds = Object.keys(selected);

  // При появлении выделения панель массовых действий раскрывается автоматически
  // (чтобы можно было сразу применить операцию); ручное сворачивание сохраняется.
  useEffect(() => {
    if (selectedIds.length > 0) setBulkOpen(true);
  }, [selectedIds.length]);

  const pageDocIds = docs.map((d) => d.id);

  const allByFilterOn =
    filterSelectedIds.length > 0 && filterSelectedIds.every((id) => selected[id]);
  const allByFilterPartial =
    filterSelectedIds.length > 0 &&
    !allByFilterOn &&
    filterSelectedIds.some((id) => selected[id]);

  const selectPage = () => {
    setSelected((s) => {
      const next = { ...s };
      pageDocIds.forEach((id) => {
        next[id] = true;
      });
      return next;
    });
  };

  const selectAllFiltered = async () => {
    try {
      const result = await listDocuments({
        uploader: resolvedUploader || undefined,
        problem: problemOnly ? true : undefined,
        module: moduleFilter || undefined,
        developmentId: devFilter ? Number(devFilter) : undefined,
        tag: tagFilter || undefined,
        search: search || undefined,
        sort: sortKey,
        limit: MAX_SELECT,
        offset: 0,
      });
      const ids = result.documents.map((d) => d.id);
      const next = {};
      ids.forEach((id) => {
        next[id] = true;
      });
      setSelected(next);
      setFilterSelectedIds(ids);
      if (result.total > ids.length) {
        showToast(
          `Выделено ${ids.length} из ${result.total} (лимит массовой операции). Уточните фильтр, чтобы обработать остальные`,
          { type: "warning" }
        );
      }
    } catch (err) {
      showToast(`Не удалось выделить документы: ${err.message}`, { type: "error" });
    }
  };

  const toggleAllByFilter = async () => {
    if (allByFilterOn) {
      setSelected({});
      setFilterSelectedIds([]);
      return;
    }
    await selectAllFiltered();
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <>
      {canEdit && (
        <SelectionBar
          selectedIds={selectedIds}
          total={total}
          allByFilterOn={allByFilterOn}
          allByFilterPartial={allByFilterPartial}
          canDelete={isAdmin}
          open={bulkOpen}
          onToggle={() => setBulkOpen((v) => !v)}
          onToggleAllByFilter={toggleAllByFilter}
          onSelectPage={selectPage}
          onOpenPreview={() => setShowPreview(true)}
          onDone={(result) => {
            const n = result?.updated?.length ?? 0;
            showToast(
              n > 0 ? `Теги обновлены у ${n} документ(ов)` : "Состав тегов не изменился"
            );
            load();
          }}
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
      {showTags && <TagManagerModal onClose={() => setShowTags(false)} />}
      <div className="doc-filter-bar">
        <input
          type="text"
          className="doc-filter-input"
          placeholder="Поиск: название, тег, разработка, загрузчик"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          aria-label="Поиск по документам"
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
        <label className="doc-filter-problem">
          <input
            type="checkbox"
            checked={problemOnly}
            onChange={(e) => setProblemOnly(e.target.checked)}
            aria-label="Только проблемные документы"
          />
          Проблемные
        </label>
        <select
          className="doc-filter-select"
          value={moduleFilter}
          onChange={(e) => setModuleFilter(e.target.value)}
          aria-label="Фильтр по модулю"
        >
          <option value="">Все модули</option>
          {modules.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <select
          className="doc-filter-select"
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
          aria-label="Фильтр по тегу"
        >
          <option value="">Все теги</option>
          {filterTags.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name} ({t.count})
            </option>
          ))}
        </select>
        <DevelopmentFilter
          developments={developments}
          value={devFilter}
          onChange={setDevFilter}
        />
        {canEdit && (
          <button className="doc-filter-btn" onClick={() => setShowTags(true)}>
            Справочник тегов
          </button>
        )}
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
        {docs.map((doc) => (
          <li key={doc.id} className={`document-item ${selected[doc.id] ? "selected" : ""}`}>
            {canEdit && (
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
                {canEdit ? (
                  <>
                    <br />
                    {editingTags[doc.id] ? (
                      <>
                        <TagPicker
                          label=""
                          className="tag-picker-inline"
                          selected={doc.tags || []}
                          onChange={(tags) => changeTags(doc, tags)}
                          placeholder="Добавить тег..."
                        />
                        <button
                          className="tag-edit-toggle"
                          onClick={() => toggleEditTags(doc.id)}
                          aria-label="Свернуть редактирование тегов"
                        >
                          −
                        </button>
                      </>
                    ) : (
                      <div className="doc-tags-row">
                        {doc.tags && doc.tags.length > 0 ? (
                          doc.tags.map((t) => (
                            <span key={t} className="tag-chip tag-chip-readonly">
                              {t}
                            </span>
                          ))
                        ) : (
                          <span className="doc-tags-muted">Теги: —</span>
                        )}
                        <button
                          className="tag-edit-toggle"
                          onClick={() => toggleEditTags(doc.id)}
                          aria-label="Редактировать теги"
                        >
                          ✎
                        </button>
                      </div>
                    )}
                  </>
                ) : (
                  doc.tags &&
                  doc.tags.length > 0 && (
                    <>
                      <br />
                      <span className="doc-tags">Теги: {doc.tags.join(", ")}</span>
                    </>
                  )
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
        {docs.length === 0 &&
          (total > 0 ||
            search ||
            problemOnly ||
            moduleFilter ||
            devFilter) && (
            <li className="document-empty">Ничего не найдено</li>
          )}
      </ul>
      {totalPages > 1 && (
        <div className="doc-pagination">
          <button
            className="page-btn"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
          >
            ← Назад
          </button>
          <span className="page-indicator">
            {page + 1} из {totalPages}
          </span>
          <button
            className="page-btn"
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
          >
            Вперёд →
          </button>
        </div>
      )}
    </>
  );
}
