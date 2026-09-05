"use client";

import { useEffect, useMemo, useState } from "react";
import { cleanupTags, deleteTag, listTags } from "@/lib/api";
import { bumpTagVersion } from "@/lib/tagDictionary";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { TrashIcon } from "./icons";

/**
 * Модалка управления справочником тегов: список с поиском, счётчики использования,
 * удаление неиспользуемых (count=0) по одному или всех сразу («мусор»).
 * Удаляются только имена из пула автодополнения — данные документов не трогаются.
 */
export default function TagManagerModal({ onClose }) {
  const { showToast } = useToast();
  const { t, tc } = useI18n();
  const [tags, setTags] = useState([]);
  const [query, setQuery] = useState("");
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
    const list = q ? tags.filter((t) => t.name.toLowerCase().includes(q)) : tags;
    return [...list].sort(
      (a, b) => a.count - b.count || a.name.localeCompare(b.name, "ru")
    );
  }, [tags, query]);

  const unused = useMemo(() => tags.filter((t) => t.count === 0), [tags]);

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
              key={tag.name}
              className={`tag-manager-item ${tag.count === 0 ? "unused" : ""}`}
            >
              <span className="tag-manager-name" title={tag.name}>
                {tag.name}
              </span>
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