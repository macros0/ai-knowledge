"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { getDevelopment, listDevelopmentDocuments } from "@/lib/api";
import { useToast } from "@/components/Toast";

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

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

export default function DevelopmentCardPage() {
  const { devId } = useParams();
  const { showToast } = useToast();
  const [dev, setDev] = useState(null);
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState("date_desc");
  const [page, setPage] = useState(0);
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    try {
      const d = await getDevelopment(devId);
      const result = await listDevelopmentDocuments(devId, {
        search: search || undefined,
        sort: sortKey,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      if (seq !== loadSeq.current) return;
      setDev(d);
      setDocs(result.documents);
      setTotal(result.total);
    } catch (err) {
      showToast(`Не удалось загрузить разработку: ${err.message}`, { type: "error" });
    }
  }, [devId, search, sortKey, page, showToast]);

  // Debounce серверного поиска.
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchInput]);

  // Смена поиска/сортировки сбрасывает страницу к началу.
  useEffect(() => {
    setPage(0);
  }, [search, sortKey]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="panel">
      <p className="breadcrumbs">
        <Link href="/developments">← Справочник разработок</Link>
      </p>
      <h2>
        {dev?.number} — {dev?.name}
      </h2>
      <p className="meta">
        Модуль: <strong>{dev?.module || "—"}</strong> · Документов: {dev?.documents_count}
      </p>

      <h3>Документы разработки</h3>
      <div className="doc-filter-bar">
        <input
          type="text"
          className="doc-filter-input"
          placeholder="Поиск: название, тег, загрузчик"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          aria-label="Поиск по документам разработки"
        />
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
      {docs.length === 0 ? (
        <p className="muted">
          {search ? "Ничего не найдено" : "Нет привязанных документов."}
        </p>
      ) : (
        <ul className="document-list">
          {docs.map((doc) => (
            <li key={doc.id} className="document-item">
              <div>
                <strong>{doc.filename}</strong>
                <div className="meta">
                  {doc.error ? `Ошибка: ${doc.error}` : `${(doc.size / 1024).toFixed(1)} КБ · Концептов: ${doc.okf_concept_count}`}
                  {doc.uploaded_by && (
                    <>
                      <br />
                      <span className="doc-uploader">Загрузил: {doc.uploaded_by}</span>
                    </>
                  )}
                </div>
              </div>
              <div className="doc-actions">
                <span className={`status ${doc.status}`}>
                  {STATUS_LABELS[doc.status] ?? doc.status}
                </span>
                <Link className="icon-btn" href={`/documents/${doc.id}/okf`} title="Концепты и чанки">
                  →
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
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
    </section>
  );
}
