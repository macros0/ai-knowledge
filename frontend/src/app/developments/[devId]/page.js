"use client";

import { useCallback, useEffect, useState } from "react";
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

export default function DevelopmentCardPage() {
  const { devId } = useParams();
  const { showToast } = useToast();
  const [dev, setDev] = useState(null);
  const [docs, setDocs] = useState([]);

  const load = useCallback(async () => {
    try {
      const d = await getDevelopment(devId);
      setDev(d);
      const documents = await listDevelopmentDocuments(devId);
      setDocs(documents);
    } catch (err) {
      showToast(`Не удалось загрузить разработку: ${err.message}`, { type: "error" });
    }
  }, [devId, showToast]);

  useEffect(() => {
    load();
  }, [load]);

  if (!dev) {
    return (
      <section className="panel">
        <p>Загрузка разработки...</p>
      </section>
    );
  }

  return (
    <section className="panel">
      <p className="breadcrumbs">
        <Link href="/developments">← Справочник разработок</Link>
      </p>
      <h2>
        {dev.number} — {dev.name}
      </h2>
      <p className="meta">
        Модуль: <strong>{dev.module || "—"}</strong> · Документов: {dev.documents_count}
      </p>

      <h3>Документы разработки</h3>
      {docs.length === 0 ? (
        <p className="muted">Нет привязанных документов.</p>
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
    </section>
  );
}
