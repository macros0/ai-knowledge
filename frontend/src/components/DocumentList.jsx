"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError, createBulkExport, deleteDocument, friendlyApiError, getDocumentStats, getSourceLocaleFacets, listActiveLocales, listAttributeValues, listDevelopments, listDocuments, listUploaders, regenerateDocument, resumeDocument, setDocumentDevelopment, setDocumentSourceLocale, updateDocumentTags } from "@/lib/api";
import { bumpTagVersion, useTagDictionary } from "@/lib/tagDictionary";
import { buildLocaleOptions, facetOptions } from "@/lib/sourceLocales.mjs";
import { buildCompactDocumentMeta, countActiveDocumentFilters, resetDocumentFilters } from "@/lib/documentLayout.mjs";
import { DownloadIcon, EyeIcon, LinkIcon, RefreshIcon, TrashIcon } from "./icons";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { useChat } from "@/context/ChatContext";
import { selectionLimit } from "@/lib/documentBulkLimits.mjs";
import SelectionBar from "./SelectionBar";
import PreviewModal from "./PreviewModal";
import DevelopmentFilter from "./DevelopmentFilter";
import DevelopmentPicker from "./DevelopmentPicker";
import DuplicateModal from "./DuplicateModal";
import TagPicker from "./TagPicker";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import TagManagerModal from "./TagManagerModal";
import SearchableSelect from "./SearchableSelect";

const BUSY_STATUSES = ["uploaded", "queued", "processing", "splitting", "indexing", "paused"];

// Фильтр по статусу OKF-генерации (Этап 5): на бэкенд уходит либо пустая строка
// (без фильтра), либо список статусов через запятую. Детальные пункты
// (Парсинг.../Генерация OKF.../...) — для диагностики зависшей стадии,
// агрегаты «В обработке»/«Ошибка» — для оперативного просмотра очереди.

const PAGE_SIZE = 50;
// Кап «Выделить все по фильтру» — совпадает с bulk_tags_max_docs. Бэкенд всё равно
// отклоняет превышение реального лимита понятным 400; кап защищает от лишнего набора.
const SEARCH_DEBOUNCE_MS = 300;

function progressText(doc, t) {
  if (doc.status === "queued") return t("docs.progressQueued");
  if (doc.status === "splitting" && doc.total_chunks > 0) {
    const active = doc.current_chunk ?? doc.processed_chunks;
    return t("docs.progressChunk", { active, total: doc.total_chunks });
  }
  if (doc.status === "paused" && doc.total_chunks > 0) {
    return t("docs.progressPaused", {
      processed: doc.processed_chunks ?? 0,
      total: doc.total_chunks,
    });
  }
  return null;
}

function initialParam(searchParams, key, fallback) {
  const v = searchParams.get(key);
  return v == null ? fallback : v;
}

// Группировка (Этап 5) выполняется на клиенте поверх полного отфильтрованного
// набора. SQL GROUP BY не подходит: в режиме «по тегам» документ с несколькими
// тегами входит в несколько групп (multi-membership), что JOIN размножает в
// дубликаты строк. Отсортировано по названию группы; «Без …» — всегда в конце.
function buildGroups(docs, groupBy, locale, t) {
  if (groupBy === "development") {
    const map = new Map();
    for (const d of docs) {
      const none = d.development_id == null;
      const key = none ? "__none__" : `dev:${d.development_id}`;
      const label = none
        ? t("docs.group.noDevelopment")
        : `${d.development_number || d.development_id}${d.development_name ? ` · ${d.development_name}` : ""}`;
      if (!map.has(key)) map.set(key, { key, label, docs: [] });
      map.get(key).docs.push(d);
    }
    return Array.from(map.values()).sort((a, b) => {
      if (a.key === "__none__") return 1;
      if (b.key === "__none__") return -1;
      return a.label.localeCompare(b.label, locale);
    });
  }
  const map = new Map();
  for (const d of docs) {
    const tags = d.tags && d.tags.length ? d.tags : ["__none__"];
    for (const tag of tags) {
      const none = tag === "__none__";
      const key = none ? "__none__" : tag;
      const label = none ? t("docs.group.noTag") : tag;
      if (!map.has(key)) map.set(key, { key, label, docs: [] });
      map.get(key).docs.push(d);
    }
  }
  return Array.from(map.values()).sort((a, b) => {
    if (a.key === "__none__") return 1;
    if (b.key === "__none__") return -1;
    return a.label.localeCompare(b.label, locale);
  });
}

export default function DocumentList({ refreshKey = 0, onOpenTrash }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { mode, hasRole, loading, user } = useAuth();
  const { settings } = useChat();
  const { showToast } = useToast();
  const { t, tc, locale, fmtDate, fmtDateTime } = useI18n();
  const [tagLocales, setTagLocales] = useState({});
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
  const [searchInput, setSearchInput] = useState(() => initialParam(searchParams, "q", ""));
  const [search, setSearch] = useState(() => initialParam(searchParams, "q", ""));
  const [page, setPage] = useState(() => {
    const n = parseInt(initialParam(searchParams, "page", "0"), 10);
    return Number.isFinite(n) && n > 0 ? n : 0;
  });
  // Выбор в дропдауне: null — ещё не выбирал (действует дефолт по роли),
  // "" — все, "__me__" — мои, иначе — конкретный username.
  const [chosenUploader, setChosenUploader] = useState(() => initialParam(searchParams, "uploader", null));
  const [uploaders, setUploaders] = useState([]);
  const [sortKey, setSortKey] = useState(() => initialParam(searchParams, "sort", "date_desc"));
  const [developments, setDevelopments] = useState([]);
  const [modules, setModules] = useState([]);
  const [problemOnly, setProblemOnly] = useState(() => initialParam(searchParams, "problem", "0") === "1");
  const [moduleFilter, setModuleFilter] = useState(() => initialParam(searchParams, "module", ""));
  const [devFilter, setDevFilter] = useState(() => {
    const v = initialParam(searchParams, "dev", null);
    return v == null ? null : v;
  });
  const [tagFilter, setTagFilter] = useState(() => initialParam(searchParams, "tag", ""));
  const [statusFilter, setStatusFilter] = useState(() => initialParam(searchParams, "status", ""));
  const [dateFrom, setDateFrom] = useState(() => initialParam(searchParams, "from", ""));
  const [dateTo, setDateTo] = useState(() => initialParam(searchParams, "to", ""));
  // localeFilter — фильтр по языку ДОКУМЕНТА (query-параметр `locale`), НЕ язык
  // интерфейса (cookie okf.locale): "" = все, "unknown" = «не определён», иначе код.
  const [localeFilter, setLocaleFilter] = useState(() => initialParam(searchParams, "locale", ""));
  const [groupBy, setGroupBy] = useState(() => initialParam(searchParams, "group", ""));
  const [stats, setStats] = useState(null);
  const [dupDoc, setDupDoc] = useState(null);
  const [showTags, setShowTags] = useState(false);
  // Спойлер редактора тегов в карточке: по умолчанию свёрнут (чипы + «✎»),
  // раскрытие — только когда нужно редактировать (не частая операция).
  const [editingTags, setEditingTags] = useState({});
  // Активные локали (GET /api/locales) для селекта языка документа + карта
  // «doc_id -> редактируется ли язык сейчас».
  const [activeLocales, setActiveLocales] = useState([]);
  const [editingLocale, setEditingLocale] = useState({});
  // Фасеты языков документа (GET /documents/source-locale-facets) для фильтр-селекта.
  const [localeFacets, setLocaleFacets] = useState([]);
  // Спойлер панели массовых действий: свёрнут по умолчанию, раскрывается по клику
  // или автоматически при появлении выделения.
  const [bulkOpen, setBulkOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const mounted = useRef(true);
  const timer = useRef(null);
  const loadSeq = useRef(0);

  const STATUS_LABELS = {
    uploaded: t("status.uploaded"),
    queued: t("status.queued"),
    processing: t("status.processing"),
    splitting: t("status.splitting"),
    indexing: t("status.indexing"),
    done: t("status.done"),
    paused: t("status.paused"),
    failed: t("status.failed"),
    error: t("status.error"),
  };

  const STATUS_FILTER_OPTIONS = [
    { value: "", label: t("docs.statusFilterAll") },
    { value: "done", label: t("status.done") },
    { value: "processing", label: t("status.processing") },
    { value: "splitting", label: t("status.splitting") },
    { value: "indexing", label: t("status.indexing") },
    { value: "uploaded", label: t("status.uploaded") },
    { value: "queued", label: t("status.queued") },
    { value: "paused", label: t("status.paused") },
    { value: "uploaded,queued,processing,splitting,indexing,paused", label: t("docs.statusFilterProcessing") },
    { value: "failed,error", label: t("status.failed") },
  ];

  // Словарь тегов для селекта фильтра (общий с пикерами, обновляется по bumpTagVersion).
  const tagDictionary = useTagDictionary();
  const filterTags = tagDictionary.filter((t) => t.count > 0);
  // Локализованное имя тега по каноническому (fallback — сам канонический текст).
  const tagDisplay = (name) =>
    tagDictionary.find((t) => t.name === name)?.display ?? name;

  // В disabled-режиме (всё открыто) действия доступны, как и на бэкенде.
  const canEdit = mode === "disabled" || hasRole("editor", "admin");
  const isAdmin = mode === "disabled" || hasRole("admin");
  const exportEnabled = isAdmin && settings.bulk_export_enabled;
  const selectLimit = selectionLimit({ isAdmin, exportEnabled, exportMaxDocs: settings.bulk_export_max_docs });

  // Единый фильтр по загрузчику: дефолт — «мои» для editor/admin, иначе «все».
  // "__me__" — спец-значение только для UI; на бэкенд уходит либо ничего (все),
  // либо резолвленный username текущего пользователя.
  const selectedUploader =
    chosenUploader ?? (hasRole("editor", "admin") ? "__me__" : "");

  const chooseUploader = (next) => setChosenUploader(next);

  const clearDocumentFilters = () => {
    const defaults = resetDocumentFilters();
    setSearchInput(defaults.searchInput);
    setSearch(defaults.search);
    setChosenUploader(defaults.chosenUploader);
    setProblemOnly(defaults.problemOnly);
    setModuleFilter(defaults.moduleFilter);
    setDevFilter(defaults.devFilter);
    setTagFilter(defaults.tagFilter);
    setStatusFilter(defaults.statusFilter);
    setDateFrom(defaults.dateFrom);
    setDateTo(defaults.dateTo);
    setLocaleFilter(defaults.localeFilter);
    setPage(defaults.page);
  };

  const resolvedUploader =
    selectedUploader === "__me__" ? user?.username ?? "" : selectedUploader;

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    try {
      const result = await listDocuments({
        uploader: resolvedUploader || undefined,
        problem: problemOnly ? true : undefined,
        module: moduleFilter || undefined,
        developmentId: devFilter ? Number(devFilter) : undefined,
        tag: tagFilter || undefined,
        status: statusFilter || undefined,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        sourceLocales: localeFilter && localeFilter !== "unknown" ? localeFilter : undefined,
        sourceLocaleUnknown: localeFilter === "unknown" ? true : undefined,
        search: search || undefined,
        sort: sortKey,
        // В grouped-режиме пагинация отключена — нужен весь набор для секций.
        limit: groupBy ? undefined : PAGE_SIZE,
        offset: groupBy ? 0 : page * PAGE_SIZE,
      });
      if (seq !== loadSeq.current || !mounted.current) return;
      setDocs(result.documents);
      setTotal(result.total);
      const busy = result.documents.some((d) => BUSY_STATUSES.includes(d.status));
      if (busy && mounted.current) {
        timer.current = setTimeout(load, 1500);
      }
    } catch (err) {
      // Бэкенд недоступен/ошибка сети: не оставляем список «молча пустым» —
      // показываем ошибку и не ломаем поллинг (следующий эффект перезапустит load).
      if (seq !== loadSeq.current || !mounted.current) return;
      showToast(t("docs.loadError", { message: friendlyApiError(err, t) }), { type: "error" });
    }
  }, [resolvedUploader, problemOnly, moduleFilter, devFilter, tagFilter, statusFilter, dateFrom, dateTo, localeFilter, search, sortKey, page, groupBy, t]);

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

  const loadActiveLocales = useCallback(async () => {
    try {
      setActiveLocales(await listActiveLocales());
    } catch {
      setActiveLocales([]);
    }
  }, []);

  const loadFacets = useCallback(async () => {
    try {
      setLocaleFacets(await getSourceLocaleFacets(resolvedUploader || undefined));
    } catch {
      setLocaleFacets([]);
    }
  }, [resolvedUploader]);

  const loadStats = useCallback(async () => {
    try {
      setStats(await getDocumentStats());
    } catch {
      setStats(null);
    }
  }, []);

  // URL-синхронизация фильтров (Этап 5): состояние → query-параметры (shareable
  // view). Чтение — только при инициализации стейта, поэтому цикл не возникает.
  useEffect(() => {
    const params = new URLSearchParams();
    if (search) params.set("q", search);
    if (chosenUploader != null) params.set("uploader", chosenUploader);
    if (moduleFilter) params.set("module", moduleFilter);
    if (tagFilter) params.set("tag", tagFilter);
    if (devFilter) params.set("dev", devFilter);
    if (problemOnly) params.set("problem", "1");
    if (statusFilter) params.set("status", statusFilter);
    if (dateFrom) params.set("from", dateFrom);
    if (dateTo) params.set("to", dateTo);
    if (localeFilter) params.set("locale", localeFilter);
    if (sortKey !== "date_desc") params.set("sort", sortKey);
    if (page > 0) params.set("page", String(page));
    if (groupBy) params.set("group", groupBy);
    const qs = params.toString();
    router.replace(qs ? `/?${qs}` : "/", { scroll: false });
  }, [search, chosenUploader, moduleFilter, tagFilter, devFilter, problemOnly, statusFilter, dateFrom, dateTo, localeFilter, sortKey, page, groupBy, router]);

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
  }, [search, sortKey, resolvedUploader, problemOnly, moduleFilter, devFilter, tagFilter, statusFilter, dateFrom, dateTo, localeFilter, groupBy]);

  useEffect(() => {
    if (loading) return;
    mounted.current = true;
    load();
    loadUploaders();
    loadModules();
    loadDevelopments();
    loadActiveLocales();
    loadFacets();
    loadStats();
    return () => {
      mounted.current = false;
      clearTimeout(timer.current);
    };
  }, [loading, refreshKey, load, loadUploaders, loadModules, loadDevelopments, loadActiveLocales, loadFacets, loadStats]);

  const openOkf = (doc) => {
    router.push(`/documents/${doc.id}/okf`);
  };

  const remove = async (doc) => {
    const isActive = doc.status === "queued" || doc.status === "splitting" || doc.status === "processing" || doc.status === "indexing";
    if (isActive && !window.confirm(t("docs.confirmRemoveActive", { name: doc.filename }))) {
      return;
    }
    try {
      await deleteDocument(doc.id);
    } catch (err) {
      showToast(t("docs.deleteError", { message: friendlyApiError(err, t) }), { type: "error" });
      load();
      return;
    }
    setSelected((s) => {
      const next = { ...s };
      delete next[doc.id];
      return next;
    });
    showToast(t("docs.removedToTrash", { name: doc.filename }), {
      type: "success",
      action: onOpenTrash ? { label: t("docs.openTrash"), onClick: onOpenTrash } : null,
    });
    load();
  };

  const resume = async (doc) => {
    try {
      await resumeDocument(doc.id);
    } catch (err) {
      showToast(t("docs.resumeError", { message: friendlyApiError(err, t) }), { type: "error" });
      load();
      return;
    }
    load();
  };

  const regenerate = async (doc) => {
    if (regenerating[doc.id]) return;
    if (
      !window.confirm(
        t("docs.confirmRegenerate", { name: doc.filename })
      )
    ) {
      return;
    }
    setRegenerating((s) => ({ ...s, [doc.id]: true }));
    try {
      await regenerateDocument(doc.id);
      load();
    } catch (err) {
      showToast(t("docs.regenerateError", { message: friendlyApiError(err, t) }), { type: "error" });
    } finally {
      setRegenerating((s) => ({ ...s, [doc.id]: false }));
    }
  };

  const changeDevelopment = async (doc, value) => {
    const devId = value === null || value === "" ? null : Number(value);
    try {
      const res = await setDocumentDevelopment(doc.id, devId, devId !== null);
      if (res?.dev_tags_sync_pending) {
        showToast(t("docs.devSyncPending"), { type: "warning" });
      }
      load();
      loadStats();
    } catch (err) {
      showToast(t("docs.changeDevError", { message: friendlyApiError(err, t) }), { type: "error" });
    }
  };

  const changeTags = async (doc, tags) => {
    try {
        const res = await updateDocumentTags(doc.id, tags, tagLocales[doc.id] || locale);
      if (res?.dev_tags_sync_pending) {
        showToast(t("docs.tagsSyncPending"), { type: "warning" });
      }
      bumpTagVersion();
      load();
    } catch (err) {
      showToast(t("docs.changeTagsError", { message: friendlyApiError(err, t) }), { type: "error" });
      load();
    }
  };

  const toggleSelect = (id) => {
    setSelected((s) => {
      const next = { ...s };
      if (next[id]) delete next[id];
      else if (Object.keys(next).length < selectLimit) next[id] = true;
      else showToast(t("docs.selectionCapToast", { max: selectLimit }), { type: "warning" });
      return next;
    });
  };

  const toggleEditTags = (id) => {
    setEditingTags((s) => ({ ...s, [id]: !s[id] }));
  };

  const exportSelected = async () => {
    if (!selectedIds.length || !exportEnabled) return;
    if (!window.confirm(t("selection.exportConfirm", { selected: selectedIds.length }))) return;
    try {
      const job = await createBulkExport(selectedIds);
      showToast(t("selection.exportQueued", { id: job.id }), { type: "success" });
    } catch (err) {
      showToast(t("selection.exportError", { message: friendlyApiError(err, t) }), { type: "error" });
    }
  };

  const toggleEditLocale = (id) => {
    setEditingLocale((s) => ({ ...s, [id]: !s[id] }));
  };

  const changeLocale = async (doc, value) => {
    setEditingLocale((s) => ({ ...s, [doc.id]: false }));
    try {
      await setDocumentSourceLocale(doc.id, value || null);
      load();
    } catch (err) {
      showToast(t("docs.changeLocaleError", { message: friendlyApiError(err, t) }), { type: "error" });
      load();
    }
  };

  const selectedIds = Object.keys(selected);

  useEffect(() => {
    setSelected((current) => {
      const ids = Object.keys(current);
      if (ids.length <= selectLimit) return current;
      return Object.fromEntries(ids.slice(0, selectLimit).map((id) => [id, true]));
    });
    setFilterSelectedIds((ids) => ids.slice(0, selectLimit));
  }, [selectLimit]);

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
      // Кап MAX_SELECT: в grouped-режиме docs — весь отфильтрованный набор
      // (limit=None), «страница» фактически равна ему; выделяем не больше
      // лимита массовой операции, иначе bulk-tags молча упадёт на 400.
      for (const id of pageDocIds) {
        if (Object.keys(next).length >= selectLimit) {
          showToast(
            t("docs.selectionCapToast", { max: selectLimit }),
            { type: "warning" }
          );
          break;
        }
        next[id] = true;
      }
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
        status: statusFilter || undefined,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        sourceLocales: localeFilter && localeFilter !== "unknown" ? localeFilter : undefined,
        sourceLocaleUnknown: localeFilter === "unknown" ? true : undefined,
        search: search || undefined,
        sort: sortKey,
        limit: selectLimit,
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
          t("docs.selectedLimitToast", { selected: ids.length, total: result.total }),
          { type: "warning" }
        );
      }
    } catch (err) {
      showToast(t("docs.selectError", { message: friendlyApiError(err, t) }), { type: "error" });
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

  const groups = groupBy ? buildGroups(docs, groupBy, locale, t) : [];
  const activeFilterCount = countActiveDocumentFilters({
    search: searchInput.trim() || search,
    uploader: selectedUploader,
    status: statusFilter,
    problemOnly,
    module: moduleFilter,
    development: devFilter,
    tag: tagFilter,
    locale: localeFilter,
    dateFrom,
    dateTo,
  });
  const uploaderOptions = [
    { value: "__me__", label: t("docs.myDocuments") },
    { value: "", label: t("docs.allUploaders") },
    ...uploaders.map((uploader) => ({ value: uploader, label: uploader })),
  ];
  const tagOptions = [
    { value: "", label: t("docs.allTags") },
    ...filterTags.map((tag) => ({
      value: tag.name,
      label: `${tag.display || tag.name} (${tag.count})`,
      searchText: `${tag.name} ${tag.display || ""}`,
    })),
  ];
  const moduleOptions = [
    { value: "", label: t("docs.allModules") },
    ...modules.map((module) => ({ value: module, label: module, searchText: module })),
  ];
  const localeOptions = [
    { value: "", label: t("docs.allLocales") },
    ...facetOptions(localeFacets, locale).map((option) => ({
      value: option.code,
      label: option.code === "unknown" ? `${t("docs.localeUnknown")} (${option.count})` : `${option.label} (${option.count})`,
      searchText: `${option.code} ${option.label}`,
    })),
  ];
  if (localeFilter && !localeOptions.some((option) => option.value === localeFilter)) {
    localeOptions.push({ value: localeFilter, label: localeFilter, searchText: localeFilter });
  }
  const markupPct =
    stats && stats.total > 0
      ? Math.round((stats.with_development / stats.total) * 100)
      : 0;

  const renderDoc = (doc) => {
    const needsMarkup =
      !BUSY_STATUSES.includes(doc.status) &&
      !doc.development_id &&
      !doc.development_suggestion;
    const progress = progressText(doc, t);
    const fileMeta = t("docs.metaFile", {
      size: (doc.size / 1024).toFixed(1),
      count: doc.okf_concept_count,
    });
    const fallbackMeta = doc.concepts_generated_at
      ? `${fileMeta} · ${t("docs.conceptsGeneratedAt", { date: fmtDateTime(doc.concepts_generated_at) })}`
      : fileMeta;
    const displayTags = (doc.tags || []).map(tagDisplay);
    const compactMeta = buildCompactDocumentMeta({
      tags: displayTags,
      development:
        doc.development_number && (!canEdit || developments.length === 0)
          ? `${doc.development_number}${doc.development_name ? ` · ${doc.development_name}` : ""}`
          : null,
      locale: doc.source_locale || null,
      uploader: doc.uploaded_by ? t("docs.uploadedBy", { name: doc.uploaded_by }) : null,
      date: doc.created_at ? fmtDate(doc.created_at) : null,
    });
    const metaValue = (key) => compactMeta.find((item) => item.key === key)?.value;
    return (
      <li key={doc.id} className={`document-item ${selected[doc.id] ? "selected" : ""}`}>
        {canEdit && (
          <input
            type="checkbox"
            className="doc-checkbox"
            aria-label={t("docs.selectAria", { name: doc.filename })}
            checked={!!selected[doc.id]}
            onChange={() => toggleSelect(doc.id)}
          />
        )}
        <div className="doc-main">
          <div className="doc-primary">
            <strong title={doc.filename}>{doc.filename}</strong>
            <span className="meta doc-primary-meta">
              {doc.error_code || doc.error ? friendlyApiError(new ApiError("", { code: doc.error_code }), t) : (progress || fallbackMeta)}
            </span>
          </div>
          <div className="doc-meta-line">
            {canEdit ? (
              editingTags[doc.id] ? (
                <>
                  <ReferenceLocaleSelect value={tagLocales[doc.id]}
                    onChange={(value) => setTagLocales((prev) => ({ ...prev, [doc.id]: value }))} />
                  <TagPicker
                    collapsible
                    defaultExpanded
                    onCollapse={() => toggleEditTags(doc.id)}
                    label=""
                    className="tag-picker-inline"
                    selected={doc.tags || []}
                    onChange={(tags) => changeTags(doc, tags)}
                    placeholder={t("docs.addTagPlaceholder")}
                  />
                </>
              ) : (
                <div className="doc-tags-row">
                  {displayTags.map((tag) => (
                    <span key={tag} className="tag-chip tag-chip-readonly">{tag}</span>
                  ))}
                  <button
                    className="tag-edit-toggle"
                    onClick={() => toggleEditTags(doc.id)}
                    aria-label={t("docs.editTagsAria")}
                  >
                    ✎
                  </button>
                </div>
              )
            ) : (
              metaValue("tags") && <span className="doc-tags">{metaValue("tags")}</span>
            )}
            {doc.development_number && (!canEdit || developments.length === 0) && (
              <Link
                className="dev-tag-link"
                href={`/developments/${doc.development_id}`}
                title={t("docs.developmentTitle", { name: doc.development_name || "" })}
              >
                <LinkIcon size={12} />
                {metaValue("development")}
              </Link>
            )}
            {doc.development_suggestion && !doc.development_id && (
              <span className="dev-suggestion">
                {t("docs.devSuggestion", {
                  name: doc.development_suggestion.number || doc.development_suggestion.name || "—",
                })}
              </span>
            )}
            {(doc.source_locale || canEdit) && (
              editingLocale[doc.id] ? (
                <>
                  <SearchableSelect
                    className="document-locale-searchable"
                    triggerClassName="locale-select-inline"
                    options={[
                      { value: "", label: t("docs.localeNone") },
                      ...buildLocaleOptions(doc.source_locale, activeLocales, locale).map((o) => ({
                        value: o.code,
                        label: o.label,
                        searchText: `${o.code} ${o.label}`,
                      })),
                    ]}
                    value={doc.source_locale || ""}
                    onChange={(next) => changeLocale(doc, next)}
                    searchPlaceholder={t("docs.localeSearchPlaceholder")}
                    emptyLabel={t("docs.empty")}
                    ariaLabel={t("docs.localeEditAria")}
                  />
                  <button
                    className="tag-edit-toggle"
                    onClick={() => toggleEditLocale(doc.id)}
                    aria-label={t("docs.collapseLocaleAria")}
                  >
                    −
                  </button>
                </>
              ) : (
                <>
                  <span
                    className={`doc-locale-badge${doc.source_locale_source === "manual" ? " doc-locale-badge-manual" : ""}`}
                    title={doc.source_locale_source === "manual" ? t("docs.localeManualTitle") : undefined}
                  >
                    🌐 {metaValue("locale") || t("docs.localeNone")}
                  </span>
                  {canEdit && (
                    <button
                      className="tag-edit-toggle"
                      onClick={() => toggleEditLocale(doc.id)}
                      aria-label={t("docs.localeEditAria")}
                    >
                      ✎
                    </button>
                  )}
                </>
              )
            )}
            {doc.problem && (
              <span className="doc-problem-badge" title={doc.problem_message || doc.problem}>
                ⚠ {doc.problem_message || doc.problem}
              </span>
            )}
            {needsMarkup && <span className="dev-draft-badge">{t("docs.draftBadge")}</span>}
            {doc.has_duplicates && (
              <button type="button" className="dup-badge" onClick={() => setDupDoc(doc)} title={t("docs.duplicateTitle")}>
                {t("docs.duplicateBadge")}
              </button>
            )}
            {canEdit && developments.length > 0 && (
              <DevelopmentPicker
                developments={developments}
                value={doc.development_id ?? null}
                onChange={(devId) => changeDevelopment(doc, devId)}
              />
            )}
            {(doc.uploaded_by || doc.created_at) && (
              <span className="doc-uploader">
                {metaValue("uploader") || t("docs.uploaded")}
                {doc.uploaded_by && doc.created_at ? " · " : ""}
                {metaValue("date") || ""}
              </span>
            )}
          </div>
        </div>
        <div className="doc-actions">
          <span className={`status ${doc.status}`}>{STATUS_LABELS[doc.status] ?? doc.status}</span>
          <button className="icon-btn" onClick={() => openOkf(doc)} title={t("docs.viewConceptsTitle")}>
            <EyeIcon />
          </button>
          <a
            className="icon-btn"
            href={`/api/documents/${doc.id}/download`}
            download
            title={t("docs.downloadTitle")}
          >
            <DownloadIcon />
          </a>
          {canEdit && (
            <span className="danger-group">
              {!["uploaded", "queued", "processing", "splitting", "indexing"].includes(doc.status) && (
                <button
                  className="icon-btn"
                  onClick={() => regenerate(doc)}
                  title={t("docs.regenerateTitle")}
                  disabled={regenerating[doc.id]}
                >
                  <RefreshIcon className={regenerating[doc.id] ? "spin" : undefined} />
                </button>
              )}
              {(doc.status === "paused" || doc.status === "failed" || (doc.status === "done" && doc.partial_chunks?.length > 0)) && (
                <button className="delete-btn" onClick={() => resume(doc)}>
                  {t(doc.status === "done" ? "docs.repairPartialBtn" : "docs.resumeBtn")}
                </button>
              )}
              <button className="delete-btn" onClick={() => remove(doc)} title={t("docs.deleteTitle")}>
                <TrashIcon />
              </button>
            </span>
          )}
        </div>
      </li>
    );
  };

  return (
    <>
      {canEdit && (
        <SelectionBar
          selectedIds={selectedIds}
          total={total}
          allByFilterOn={allByFilterOn}
          allByFilterPartial={allByFilterPartial}
          canDelete={isAdmin}
          canExport={exportEnabled && selectedIds.length <= settings.bulk_export_max_docs}
          open={bulkOpen}
          onToggle={() => setBulkOpen((v) => !v)}
          onToggleAllByFilter={toggleAllByFilter}
          onSelectPage={selectPage}
          onOpenPreview={() => setShowPreview(true)}
          onExport={exportSelected}
          onDone={(result) => {
            const n = result?.updated?.length ?? 0;
            showToast(n > 0 ? tc("docs.tagsUpdated", n) : t("docs.tagsUnchanged"));
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
      {stats && stats.total > 0 && (
        <div
          className="markup-progress"
          title={t("docs.markupProgressTitle", { done: stats.with_development, total: stats.total })}
        >
          <span className="markup-progress-label">
            {t("docs.markupProgress", {
              done: stats.with_development,
              total: stats.total,
              pct: markupPct,
            })}
          </span>
          <div className="markup-progress-bar">
            <div className="markup-progress-fill" style={{ width: `${markupPct}%` }} />
          </div>
        </div>
      )}
      <div className="doc-view-mode" role="radiogroup" aria-label={t("docs.viewModeLabel")}>
        <button
          type="button"
          className={`view-btn${groupBy === "" ? " active" : ""}`}
          onClick={() => setGroupBy("")}
        >
          {t("docs.view.list")}
        </button>
        <button
          type="button"
          className={`view-btn${groupBy === "tag" ? " active" : ""}`}
          onClick={() => setGroupBy("tag")}
        >
          {t("docs.view.tag")}
        </button>
        <button
          type="button"
          className={`view-btn${groupBy === "development" ? " active" : ""}`}
          onClick={() => setGroupBy("development")}
        >
          {t("docs.view.development")}
        </button>
      </div>
      <div className="doc-filter-bar">
        <div className="doc-filter-primary">
          <input
            type="text"
            className="doc-filter-input"
            placeholder={t("docs.searchPlaceholder")}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            aria-label={t("docs.searchAria")}
          />
          <SearchableSelect
            options={uploaderOptions}
            value={selectedUploader}
            onChange={chooseUploader}
            searchPlaceholder={t("docs.optionSearchPlaceholder")}
            emptyLabel={t("docs.empty")}
            ariaLabel={t("docs.uploaderFilterAria")}
          />
          <select
            className="doc-filter-select"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label={t("docs.statusFilterAria")}
          >
            {STATUS_FILTER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <select
            className="doc-filter-select"
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value)}
            aria-label={t("sort.label")}
          >
            <optgroup label={t("sort.groupDate")}>
              <option value="date_desc">{t("sort.newFirst")}</option>
              <option value="date_asc">{t("sort.oldFirst")}</option>
            </optgroup>
            <optgroup label={t("sort.groupName")}>
              <option value="name_asc">{t("sort.alphaAsc")}</option>
              <option value="name_desc">{t("sort.alphaDesc")}</option>
            </optgroup>
            <optgroup label={t("sort.groupUploader")}>
              <option value="uploader_asc">{t("sort.alphaAsc")}</option>
              <option value="uploader_desc">{t("sort.alphaDesc")}</option>
            </optgroup>
          </select>
          <button
            type="button"
            className={`doc-filter-btn${filtersOpen ? " active" : ""}`}
            onClick={() => setFiltersOpen((value) => !value)}
            aria-expanded={filtersOpen}
          >
            {t("docs.filtersButton", { count: activeFilterCount })}
          </button>
        </div>
        {filtersOpen && (
          <div className="doc-filter-secondary">
            <label className="doc-filter-problem">
              <input
                type="checkbox"
                checked={problemOnly}
                onChange={(e) => setProblemOnly(e.target.checked)}
                aria-label={t("docs.problemOnlyAria")}
              />
              {t("docs.problemOnly")}
            </label>
            <SearchableSelect
              options={moduleOptions}
              value={moduleFilter}
              onChange={setModuleFilter}
              searchPlaceholder={t("docs.optionSearchPlaceholder")}
              emptyLabel={t("docs.empty")}
              ariaLabel={t("docs.moduleFilterAria")}
            />
            {groupBy !== "tag" && (
              <SearchableSelect
                options={tagOptions}
                value={tagFilter}
                onChange={setTagFilter}
                searchPlaceholder={t("docs.optionSearchPlaceholder")}
                emptyLabel={t("docs.empty")}
                ariaLabel={t("docs.tagFilterAria")}
              />
            )}
            <SearchableSelect
              options={localeOptions}
              value={localeFilter}
              onChange={setLocaleFilter}
              searchPlaceholder={t("docs.localeFilterAria")}
              emptyLabel={t("docs.empty")}
              ariaLabel={t("docs.localeFilterAria")}
            />
            <DevelopmentFilter developments={developments} value={devFilter} onChange={setDevFilter} />
            <label className="doc-filter-date">
              <span>{t("docs.dateFrom")}</span>
              <input type="date" className="doc-filter-date-input" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} aria-label={t("docs.dateFromAria")} />
              <span>{t("docs.dateTo")}</span>
              <input type="date" className="doc-filter-date-input" value={dateTo} onChange={(e) => setDateTo(e.target.value)} aria-label={t("docs.dateToAria")} />
            </label>
            {canEdit && <button className="doc-filter-btn" onClick={() => setShowTags(true)}>{t("docs.tagDictionary")}</button>}
            <button
              type="button"
              className="doc-filter-btn"
              onClick={clearDocumentFilters}
              disabled={activeFilterCount === 0 && chosenUploader !== null}
            >
              {t("docs.resetFilters")}
            </button>
          </div>
        )}
      </div>
      {groupBy ? (
        <div className="document-groups">
          {groups.map((g) => (
            <section key={g.key} className="document-group">
              <h3 className="document-group-header">
                {g.label} <span className="group-count">({g.docs.length})</span>
              </h3>
              <ul className="document-list">{g.docs.map((doc) => renderDoc(doc))}</ul>
            </section>
          ))}
          {groups.length === 0 && <li className="document-empty">{t("docs.empty")}</li>}
        </div>
      ) : (
        <ul className="document-list">
          {docs.map((doc) => renderDoc(doc))}
          {docs.length === 0 &&
            (total > 0 ||
              search ||
              problemOnly ||
              statusFilter ||
              moduleFilter ||
              devFilter ||
              tagFilter ||
              localeFilter ||
              dateFrom ||
              dateTo) && (
              <li className="document-empty">{t("docs.empty")}</li>
            )}
        </ul>
      )}
      {!groupBy && totalPages > 1 && (
        <div className="doc-pagination">
          <button
            className="page-btn"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
          >
            {t("pagination.back")}
          </button>
          <span className="page-indicator">
            {t("pagination.page", { page: page + 1, total: totalPages })}
          </span>
          <button
            className="page-btn"
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
          >
            {t("pagination.forward")}
          </button>
        </div>
      )}
    </>
  );
}
