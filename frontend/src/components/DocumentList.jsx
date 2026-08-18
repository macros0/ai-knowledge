"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { deleteDocument, listDocuments, resumeDocument } from "@/lib/api";
import { DownloadIcon, EyeIcon } from "./icons";

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
  const [docs, setDocs] = useState([]);
  const mounted = useRef(true);
  const timer = useRef(null);

  const load = useCallback(async () => {
    const list = await listDocuments();
    if (!mounted.current) return;
    setDocs(list);
    const busy = list.some((d) => BUSY_STATUSES.includes(d.status));
    if (busy && mounted.current) {
      timer.current = setTimeout(load, 1500);
    }
  }, []);

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
    await deleteDocument(doc.id);
    load();
  };

  const resume = async (doc) => {
    await resumeDocument(doc.id);
    load();
  };

  return (
    <ul className="document-list">
      {docs.map((doc) => (
        <li key={doc.id} className="document-item">
          <div>
            <strong>{doc.filename}</strong>
            <div className="meta">
              {doc.error
                ? `Ошибка: ${doc.error}`
                : (progressText(doc) ?? `${(doc.size / 1024).toFixed(1)} КБ · OKF-файлов: ${doc.okf_file_count}`)}
              {doc.tags && doc.tags.length > 0 && (
                <>
                  <br />
                  <span className="doc-tags">Теги: {doc.tags.join(", ")}</span>
                </>
              )}
            </div>
          </div>
          <div className="doc-actions">
            <span className={`status ${doc.status}`}>{STATUS_LABELS[doc.status] ?? doc.status}</span>
            <button className="icon-btn" onClick={() => openOkf(doc)} title="Список чанков">
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
            {(doc.status === "paused" || doc.status === "failed") && (
              <button className="delete-btn" onClick={() => resume(doc)}>
                Возобновить
              </button>
            )}
            <button className="delete-btn" onClick={() => remove(doc)}>
              Удалить
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}
