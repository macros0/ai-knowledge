"use client";

import { useEffect, useState } from "react";
import { deleteDocument, listDocumentDuplicates } from "@/lib/api";
import { useToast } from "./Toast";
import Modal from "./Modal";

const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU");
};

export default function DuplicateModal({ doc, canDelete, onClose, onDeleted }) {
  const { showToast } = useToast();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listDocumentDuplicates(doc.id)
      .then(setData)
      .catch((err) => {
        showToast(`Не удалось загрузить дубликаты: ${err.message}`, { type: "error" });
        onClose();
      });
  }, [doc.id, showToast, onClose]);

  const candidates = [
    ...(data?.level2 ?? []).map((c) => ({ ...c, level: 2 })),
    ...(data?.level3 ?? []).map((c) => ({ ...c, level: 3 })),
  ];

  const rows = [
    {
      key: `cur-${doc.id}`,
      current: true,
      filename: doc.filename,
      created_at: doc.created_at,
      uploaded_by: doc.uploaded_by,
      jaccard: null,
    },
    ...candidates.map((c) => ({
      key: c.doc.id,
      current: false,
      filename: c.doc.filename,
      created_at: c.doc.created_at,
      uploaded_by: c.doc.uploaded_by,
      jaccard: c.jaccard,
      level: c.level,
    })),
  ];

  const newestKey = rows.reduce((a, b) => {
    const ta = new Date(a.created_at ?? 0).getTime();
    const tb = new Date(b.created_at ?? 0).getTime();
    return tb > ta ? b : a;
  }, rows[0])?.key;

  const remove = async () => {
    setBusy(true);
    try {
      await deleteDocument(doc.id);
      showToast("Документ удалён", { type: "success" });
      onDeleted?.();
      onClose();
    } catch (err) {
      showToast(`Не удалось удалить: ${err.message}`, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Возможные дубликаты"
      onClose={onClose}
      footer={
        <>
          <button className="modal-btn" onClick={onClose}>
            Закрыть
          </button>
          {canDelete && (
            <button className="modal-btn danger" onClick={remove} disabled={busy}>
              Удалить этот документ
            </button>
          )}
        </>
      }
    >
      <p className="dup-hint">
        Документ похож на уже загруженные. Сверьте даты загрузки, чтобы не удалить
        актуальную ревизию.
      </p>
      <ul className="dup-list">
        {rows.map((r) => (
          <li key={r.key} className={`dup-item${r.current ? " current" : ""}`}>
            <div className="dup-title">
              <span className="dup-filename">{r.filename}</span>
              {r.key === newestKey && <span className="dup-newest">новее</span>}
              {r.current && <span className="dup-current-label">текущий</span>}
            </div>
            <div className="dup-meta">
              Загружен: {fmtDate(r.created_at)}
              {r.uploaded_by ? ` · ${r.uploaded_by}` : ""}
              {r.jaccard != null && <> · Совпадение: {(r.jaccard * 100).toFixed(1)}%</>}
            </div>
          </li>
        ))}
      </ul>
    </Modal>
  );
}
