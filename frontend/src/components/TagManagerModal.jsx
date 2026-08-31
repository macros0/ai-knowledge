"use client";

import { useEffect, useMemo, useState } from "react";
import { cleanupTags, deleteTag, listTags } from "@/lib/api";
import { bumpTagVersion } from "@/lib/tagDictionary";
import { useToast } from "./Toast";
import { TrashIcon } from "./icons";

/**
 * Модалка управления справочником тегов: список с поиском, счётчики использования,
 * удаление неиспользуемых (count=0) по одному или всех сразу («мусор»).
 * Удаляются только имена из пула автодополнения — данные документов не трогаются.
 */
export default function TagManagerModal({ onClose }) {
  const { showToast } = useToast();
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
      showToast(`Тег «${name}» удалён`, { type: "success" });
      await afterChange();
    } catch (err) {
      showToast(`Не удалось удалить тег: ${err.message}`, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const removeAllUnused = async () => {
    setBusy(true);
    try {
      const res = await cleanupTags();
      showToast(`Удалено неиспользуемых тегов: ${res?.total ?? 0}`, { type: "success" });
      await afterChange();
    } catch (err) {
      showToast(`Не удалось очистить теги: ${err.message}`, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card tag-manager" onClick={(e) => e.stopPropagation()}>
        <header className="tag-manager-header">
          <h3 className="modal-title">Справочник тегов</h3>
          <button className="tag-manager-close" onClick={onClose} aria-label="Закрыть">
            ×
          </button>
        </header>
        <input
          className="tag-manager-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по тегам..."
          aria-label="Поиск по тегам"
        />
        <div className="tag-manager-summary">
          <span>
            Всего: <strong>{tags.length}</strong> · Неиспользуемых:{" "}
            <strong>{unused.length}</strong>
          </span>
          <button
            className="bulk-tag-btn"
            onClick={removeAllUnused}
            disabled={busy || unused.length === 0}
          >
            Удалить все неиспользуемые ({unused.length})
          </button>
        </div>
        <ul className="tag-manager-list">
          {filtered.map((t) => (
            <li
              key={t.name}
              className={`tag-manager-item ${t.count === 0 ? "unused" : ""}`}
            >
              <span className="tag-manager-name" title={t.name}>
                {t.name}
              </span>
              <span
                className="tag-manager-count"
                title={`Используется в ${t.count} документ(ах)`}
              >
                {t.count}
              </span>
              {t.count === 0 && (
                <button
                  className="tag-manager-delete"
                  onClick={() => removeOne(t.name)}
                  disabled={busy}
                  aria-label={`Удалить тег ${t.name}`}
                >
                  <TrashIcon size={14} />
                </button>
              )}
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="tag-manager-empty">Нет совпадений</li>
          )}
        </ul>
      </div>
    </div>
  );
}