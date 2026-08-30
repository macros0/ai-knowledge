"use client";

import { useEffect, useState } from "react";
import { bulkDelete, bulkPreview, bulkRegenerate } from "@/lib/api";
import { useToast } from "./Toast";
import Modal from "./Modal";
import ConfirmModal from "./ConfirmModal";

// Порог typed confirmation (ровно 5 документов, Этап 2а).
export const TYPED_CONFIRM_THRESHOLD = 5;

/**
 * Предпросмотр масштаба массовой операции (bulk-preview) + запуск.
 * Кнопки запуска активны после просмотра; удаление при count > порога требует
 * typed confirmation.
 */
export default function PreviewModal({ docIds, onClose, onDone }) {
  const { showToast } = useToast();
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await bulkPreview(docIds);
        if (!cancelled) setPreview(data);
      } catch (err) {
        if (!cancelled) showToast(`Не удалось получить предпросмотр: ${err.message}`);
        if (!cancelled) onClose();
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const count = preview?.matched ?? 0;
  const missing = preview?.missing ?? [];
  const estimated = preview?.estimated_minutes ?? 0;

  const run = async (op) => {
    setBusy(true);
    try {
      const fn = op === "delete" ? bulkDelete : bulkRegenerate;
      const job = await fn(docIds);
      const statusText =
        job.status === "awaiting_approval"
          ? "требует одобрения второго администратора"
          : `поставлена в очередь (#${job.id})`;
      showToast(`Массовая операция ${statusText}`, { type: "success" });
      onClose();
      onDone?.();
    } catch (err) {
      showToast(err.message, { type: "error", duration: 8000 });
      setBusy(false);
    }
  };

  const requestDelete = () => {
    if (count > TYPED_CONFIRM_THRESHOLD) {
      setConfirming(true);
      return;
    }
    if (window.confirm(`Удалить ${count} документов?`)) {
      run("delete");
    }
  };

  const requestRegenerate = () => {
    if (window.confirm(`Перегенерировать концепты ${count} документов?`)) {
      run("regenerate");
    }
  };

  if (confirming) {
    return (
      <ConfirmModal
        count={count}
        actionLabel="Удалить"
        onCancel={() => setConfirming(false)}
        onConfirm={() => run("delete")}
      />
    );
  }

  return (
    <Modal
      title="Предпросмотр массовой операции"
      onClose={onClose}
      footer={
        <>
          <button className="modal-btn" onClick={onClose}>
            Отмена
          </button>
          <button className="modal-btn" disabled={loading || count === 0} onClick={requestDelete}>
            Удалить {count}
          </button>
          <button
            className="modal-btn"
            disabled={loading || count === 0 || busy}
            onClick={requestRegenerate}
          >
            Перегенерировать {count}
          </button>
        </>
      }
    >
      {loading ? (
        <p className="muted">Загрузка предпросмотра…</p>
      ) : (
        <>
          <p className="confirm-text">
            Затронуто документов: <strong>{count}</strong>
            {estimated > 0 && (
              <>
                {" · "}оценка перегенерации:{" "}
                <strong>~{Math.max(1, Math.round(estimated))} мин</strong>
              </>
            )}
          </p>
          {missing.length > 0 && (
            <p className="confirm-text muted">
              Не найдены ({missing.length}): {missing.slice(0, 10).join(", ")}
              {missing.length > 10 ? "…" : ""}
            </p>
          )}
          {preview.documents.length > 0 && (
            <ul className="preview-doc-list">
              {preview.documents.slice(0, 20).map((d) => (
                <li key={d.id} className="preview-doc-item">
                  <span className="preview-doc-name">{d.filename}</span>
                  <span className={`status ${d.status}`}>{d.status}</span>
                </li>
              ))}
              {preview.documents.length > 20 && (
                <li className="muted">…и ещё {preview.documents.length - 20}</li>
              )}
            </ul>
          )}
        </>
      )}
    </Modal>
  );
}
