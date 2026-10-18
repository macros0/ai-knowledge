"use client";

import { apiToast } from "@/lib/apiToast.mjs";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  bulkReviewTags,
  cleanupTags,
  deleteTag,
  listTags,
  withReferenceEditLock,
} from "@/lib/api";
import { bumpTagVersion } from "@/lib/tagDictionary";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { PencilIcon, TrashIcon } from "./icons";
import TagTranslationEditor from "./TagTranslationEditor";
import Modal from "./Modal";
import ReferenceBatchResult from "./ReferenceBatchResult";
import { chunkReferenceCards } from "@/lib/referenceEditing.mjs";

/**
 * Модалка управления справочником тегов: список с поиском, счётчики использования,
 * удаление неиспользуемых, а также (Этап 7 фаза B) переводы тегов: фильтр
 * «требует проверки», правка перевода для текущего языка и подтверждение
 * машинных переводов.
 */
export default function TagManagerModal({ onClose }) {
  const { showToast } = useToast();
  const { t, tc, locale } = useI18n();
  const [tags, setTags] = useState([]);
  const [query, setQuery] = useState("");
  const [reviewOnly, setReviewOnly] = useState(false);
  const [expanded, setExpanded] = useState({});
  const [busy, setBusy] = useState(false);
  const dirtyDrafts = useRef({});
  const loadedTags = useRef(tags);
  const [retainedDrafts, setRetainedDrafts] = useState({});
  const [reviewPreview, setReviewPreview] = useState(null);
  const [batchResults, setBatchResults] = useState([]);
  useEffect(() => { loadedTags.current = tags; }, [tags]);
  const onDirtyChange = useCallback((id, value) => {
    dirtyDrafts.current[id] = value;
    const snapshot = value ? loadedTags.current.find(tag => tag.id === id) : null;
    setRetainedDrafts(previous => {
      if (value) return previous[id] || !snapshot ? previous : { ...previous, [id]: snapshot };
      if (!previous[id]) return previous;
      const next = { ...previous }; delete next[id]; return next;
    });
  }, []);
  const handleClose = () => {
    if (busy) return;
    if (Object.values(dirtyDrafts.current).some(Boolean) && !window.confirm(t("admin.glossary.unsavedChangesConfirm"))) return;
    onClose();
  };
  const toggleExpanded = id => {
    if (expanded[id] && dirtyDrafts.current[id] && !window.confirm(t("admin.glossary.unsavedChangesConfirm"))) return;
    setExpanded(previous => ({ ...previous, [id]: !previous[id] }));
  };

  const load = async () => {
    try {
      setTags(await listTags());
    } catch (error) {
      showToast(apiToast(error, t, { type: "error" }));
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let list = q
      ? tags.filter(
          (t) =>
            t.name.toLowerCase().includes(q) ||
            t.display.toLowerCase().includes(q)
        )
      : tags;
    if (reviewOnly) list = list.filter((t) => t.needs_review);
    return [...list].sort(
      (a, b) => a.count - b.count || a.name.localeCompare(b.name, "ru")
    );
  }, [tags, query, reviewOnly]);

  const unused = useMemo(() => tags.filter((t) => t.count === 0), [tags]);
  const reviewCount = filtered.filter(tag => tag.needs_review).length;
  const retained = Object.values(retainedDrafts).filter(tag => !filtered.some(item => item.id === tag.id));
  const displayedTags = [...filtered, ...retained];

  const afterChange = async () => {
    bumpTagVersion();
    await load();
  };

  const removeOne = async tag => {
    if (busy) return;
    setBusy(true);
    try {
      await withReferenceEditLock({ resource_type: "tag", resource_id: String(tag.id) }, ({ headers }) =>
        deleteTag(tag.name, { expected_reference_revision: tag.reference_revision, headers }));
      showToast(t("tags.manager.deleted", { name: tag.name }), { type: "success" });
      await afterChange();
    } catch (error) { showToast(apiToast(error, t, { type: "error" })); }
    finally { setBusy(false); }
  };

  const removeAllUnused = async () => {
    if (busy) return;
    setBusy(true); let applied = false;
    const results = [];
    try {
      const preview = await cleanupTags({ confirm: false });
      if (!preview.total || !window.confirm(t("reference.cleanupConfirm", { count: preview.total }))) return;
      for (const batch of preview.batches) {
        applied = true;
        const response = await cleanupTags({ confirm: true, ...batch });
        results.push(...response.results); setBatchResults([...results]);
      }
    } catch (error) { showToast(apiToast(error, t, { type: "error" })); }
    finally { if (applied) await afterChange(); setBusy(false); }
  };

  const approveIds = (ids, targetLocale) => {
    if (busy) return;
    const items = tags.filter(tag => ids.includes(tag.id)).flatMap(tag => tag.translations
      .filter(row => row.is_machine_translated && !row.reviewed_by && (!targetLocale || row.locale === targetLocale))
      .map(row => ({ tag_id: tag.id, name: tag.name, locale: row.locale, text: row.text,
        expected_reference_revision: tag.reference_revision })));
    if (items.length) setReviewPreview(items);
  };
  const applyReview = async () => {
    if (busy || !reviewPreview) return;
    setBusy(true); const results = [];
    try {
      for (const batch of chunkReferenceCards(reviewPreview)) {
        const response = await bulkReviewTags(batch.map(({ tag_id, locale, expected_reference_revision }) =>
          ({ tag_id, locale, expected_reference_revision })));
        results.push(...response.results); setBatchResults([...results]);
      }
      setReviewPreview(null);
    } catch (error) { showToast(apiToast(error, t, { type: "error" })); }
    finally { await afterChange(); setBusy(false); }
  };

  // Справочная скобка: любой en-перевод (машинный или проверенный — здесь это
  // только подсказка, авторитет остаётся в name/canonical). reviewed/machine
  // намеренно не различаем: выбор из двух не нужен (одна запись на локаль),
  // а откат на русский для непроверенного en делал бы чужую локаль русской чаще.
  const referenceText = (tag) => {
    const en = (tag.translations || []).find((tr) => tr.locale === "en");
    return (en && en.text) || tag.name;
  };

  return (
    <div className="modal-overlay" onClick={handleClose}>
      <div className="modal-card tag-manager" onClick={(e) => e.stopPropagation()}>
        <header className="tag-manager-header">
          <h3 className="modal-title">{t("tags.manager.title")}</h3>
          <button className="tag-manager-close" onClick={handleClose} aria-label={t("tags.manager.closeAria")}>
            ×
          </button>
        </header>
        <input
          className="tag-manager-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("tags.manager.searchPlaceholder")}
          aria-label={t("tags.manager.searchAria")}
        />
        <div className="tag-manager-summary">
          <span>
            {t("tags.manager.total", { total: tags.length, unused: unused.length })}
          </span>
          <button
            className={`bulk-tag-btn${reviewOnly ? " active" : ""}`}
            onClick={() => setReviewOnly(!reviewOnly)}
          >
            {t("tags.manager.reviewOnly")}
          </button>
          <button
            className="bulk-tag-btn"
            onClick={() => approveIds(filtered.filter((t) => t.needs_review).map((t) => t.id))}
            disabled={busy || reviewCount === 0}
          >
            {t("tags.manager.reviewAll", { count: reviewCount })}
          </button>
          <button
            className="bulk-tag-btn"
            onClick={removeAllUnused}
            disabled={busy || unused.length === 0}
          >
            {t("tags.manager.deleteUnused", { count: unused.length })}
          </button>
        </div>
        {retained.length > 0 && <p>{t("reference.retainedDrafts")}</p>}
        <ReferenceBatchResult results={batchResults} />
        {reviewPreview && <Modal title={t("reference.reviewPreview")} onClose={() => { if (!busy) setReviewPreview(null); }}
          footer={<>
            <button type="button" className="modal-btn" disabled={busy} onClick={() => setReviewPreview(null)}>{t("common.cancel")}</button>
            <button type="button" className="modal-btn" disabled={busy} onClick={applyReview}>{t("tags.manager.approve")}</button>
          </>}>
          <ul>{reviewPreview.map(item => <li key={`${item.tag_id}:${item.locale}`}>
            <strong>{item.name} ({item.locale})</strong>: {item.text}
          </li>)}</ul>
        </Modal>}
        <ul className="tag-manager-list">
          {displayedTags.map((tag) => (
            <li
              key={tag.id}
              className={`tag-manager-item ${tag.count === 0 ? "unused" : ""}`}
            >
              <div className="tag-manager-row">
                <button
                  className="tag-manager-expand"
                  onClick={() =>
                    toggleExpanded(tag.id)
                  }
                  aria-label={t("tags.manager.expand")}
                >
                  {expanded[tag.id] ? "▾" : "▸"}
                </button>
                <span className="tag-manager-name" title={tag.name}>
                  {tag.display}
                  {/* Только сравнение со СПРАВОЧНЫМ текстом: `display !== name`
                      прятало скобку у тега с каноническим английским name, когда
                      перевода на текущий язык нет (display = name), а en-перевод
                      есть и отличается. */}
                  {referenceText(tag) !== tag.display && (
                    <span className="tag-manager-canonical"> ({referenceText(tag)})</span>
                  )}
                </span>
                {locale !== tag.canonical_locale && (
                  <button
                    type="button"
                    className="tag-edit-toggle"
                    onClick={() =>
                      setExpanded((e) => ({ ...e, [tag.id]: true }))
                    }
                    aria-label={t("tags.manager.edit")}
                    title={t("tags.manager.edit")}
                    aria-expanded={Boolean(expanded[tag.id])}
                  >
                    <PencilIcon size={13} />
                  </button>
                )}
                {tag.needs_review && (
                  <span className="tag-manager-machine" title={t("tags.manager.reviewOnly")}>
                    {t("tags.manager.machineBadge")}
                  </span>
                )}
                <span
                  className="tag-manager-count"
                  title={tc("tags.picker.usedIn", tag.count)}
                >
                  {tag.count}
                </span>
                {tag.count === 0 && (
                  <button
                    className="tag-manager-delete"
                    onClick={() => removeOne(tag)}
                    disabled={busy}
                    aria-label={t("tags.manager.deleteAria", { name: tag.name })}
                  >
                    <TrashIcon size={14} />
                  </button>
                )}
              </div>
              {expanded[tag.id] && (
                <div className="tag-manager-translations">
                  <div>{t("reference.original", { locale: tag.canonical_locale })}: {tag.name}</div>
                  {tag.translations.length === 0 ? (
                    <span className="tag-manager-empty">
                      {t("tags.manager.noTranslations")}
                    </span>
                  ) : (
                    <ul className="tag-manager-tr-list">
                      {tag.translations.map((tr) => (
                        <li key={tr.locale} className="tag-manager-tr">
                          <span className="tag-manager-tr-locale">{tr.locale}</span>
                          <span className="tag-manager-tr-text">{tr.text}</span>
                          {tr.is_machine_translated && (
                            <span className="tag-manager-machine">
                              {t("tags.manager.machineBadge")}
                            </span>
                          )}
                          {tr.is_machine_translated && !tr.reviewed_by && (
                            <button
                              className="bulk-tag-btn"
                              onClick={() => approveIds([tag.id], tr.locale)}
                              disabled={busy}
                            >
                              {t("tags.manager.approve")}
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  <TagTranslationEditor tag={tag} locale={locale}
                    onSaved={afterChange} onDirtyChange={onDirtyChange} />
                </div>
              )}
            </li>
          ))}
          {displayedTags.length === 0 && (
            <li className="tag-manager-empty">{t("tags.manager.noMatch")}</li>
          )}
        </ul>
      </div>
    </div>
  );
}
