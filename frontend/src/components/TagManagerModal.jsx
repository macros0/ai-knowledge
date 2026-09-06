"use client";

import { useEffect, useMemo, useState } from "react";
import {
  bulkReviewTags,
  cleanupTags,
  deleteTag,
  listTags,
  updateTagTranslation,
} from "@/lib/api";
import { bumpTagVersion } from "@/lib/tagDictionary";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { TrashIcon } from "./icons";

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

  const load = async () => {
    try {
      setTags(await listTags());
    } catch {
      setTags([]);
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
  const reviewCount = useMemo(
    () => tags.filter((t) => t.needs_review).length,
    [tags]
  );

  const afterChange = async () => {
    bumpTagVersion();
    await load();
  };

  const removeOne = async (name) => {
    setBusy(true);
    try {
      await deleteTag(name);
      showToast(t("tags.manager.deleted", { name }), { type: "success" });
      await afterChange();
    } catch (err) {
      showToast(t("tags.manager.deleteError", { message: err.message }), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const removeAllUnused = async () => {
    setBusy(true);
    try {
      const res = await cleanupTags();
      showToast(t("tags.manager.cleaned", { count: res?.total ?? 0 }), { type: "success" });
      await afterChange();
    } catch (err) {
      showToast(t("tags.manager.cleanupError", { message: err.message }), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const approveIds = async (ids) => {
    setBusy(true);
    try {
      await bulkReviewTags(ids);
      showToast(t("tags.manager.reviewed"), { type: "success" });
      await afterChange();
    } catch (err) {
      showToast(t("tags.manager.translationError", { message: err.message }), {
        type: "error",
      });
    } finally {
      setBusy(false);
    }
  };

  const saveTranslation = async (tag) => {
    const el = document.getElementById(`tag-tr-${tag.id}`);
    const text = (el?.value ?? "").trim();
    if (!text) return;
    setBusy(true);
    try {
      await updateTagTranslation(tag.id, locale, text);
      showToast(t("tags.manager.translationSaved"), { type: "success" });
      await afterChange();
    } catch (err) {
      showToast(t("tags.manager.translationError", { message: err.message }), {
        type: "error",
      });
    } finally {
      setBusy(false);
    }
  };

  const currentText = (tag) =>
    tag.translations.find((tr) => tr.locale === locale)?.text ?? "";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card tag-manager" onClick={(e) => e.stopPropagation()}>
        <header className="tag-manager-header">
          <h3 className="modal-title">{t("tags.manager.title")}</h3>
          <button className="tag-manager-close" onClick={onClose} aria-label={t("tags.manager.closeAria")}>
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
            onClick={() => approveIds(tags.filter((t) => t.needs_review).map((t) => t.id))}
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
        <ul className="tag-manager-list">
          {filtered.map((tag) => (
            <li
              key={tag.id}
              className={`tag-manager-item ${tag.count === 0 ? "unused" : ""}`}
            >
              <div className="tag-manager-row">
                <button
                  className="tag-manager-expand"
                  onClick={() =>
                    setExpanded((e) => ({ ...e, [tag.id]: !e[tag.id] }))
                  }
                  aria-label={t("tags.manager.expand")}
                >
                  {expanded[tag.id] ? "▾" : "▸"}
                </button>
                <span className="tag-manager-name" title={tag.name}>
                  {tag.display}
                  {tag.display !== tag.name && (
                    <span className="tag-manager-canonical"> ({tag.name})</span>
                  )}
                </span>
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
                    onClick={() => removeOne(tag.name)}
                    disabled={busy}
                    aria-label={t("tags.manager.deleteAria", { name: tag.name })}
                  >
                    <TrashIcon size={14} />
                  </button>
                )}
              </div>
              {expanded[tag.id] && (
                <div className="tag-manager-translations">
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
                              onClick={() => approveIds([tag.id])}
                              disabled={busy}
                            >
                              {t("tags.manager.approve")}
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="tag-manager-edit">
                    <input
                      id={`tag-tr-${tag.id}`}
                      defaultValue={currentText(tag)}
                      placeholder={t("tags.manager.editPlaceholder", { locale })}
                    />
                    <button
                      className="bulk-tag-btn"
                      onClick={() => saveTranslation(tag)}
                      disabled={busy}
                    >
                      {t("tags.manager.save")}
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="tag-manager-empty">{t("tags.manager.noMatch")}</li>
          )}
        </ul>
      </div>
    </div>
  );
}
