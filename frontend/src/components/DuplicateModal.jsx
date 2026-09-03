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
  const [selected, setSelected] = useState({});
  const [confirmOpen, setConfirmOpen] = useState(false);

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
      docId: doc.id,
      current: true,
      filename: doc.filename,
      created_at: doc.created_at,
      uploaded_by: doc.uploaded_by,
      jaccard: null,
    },
    ...candidates.map((c) => ({
      key: c.doc.id,
      docId: c.doc.id,
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

  const selectedRows = rows.filter((r) => selected[r.key]);
  const selectedCount = selectedRows.length;
  // «Один документ всегда должен остаться»: последняя неотмеченная строка
  // недоступна для отметки (блокируется чекбокс, а не кнопка).
  const lastKeptKey =
    rows.length - selectedCount === 1 ? rows.find((r) => !selected[r.key])?.key : null;

  const toggleSelect = (key) => {
    setSelected((s) => {
      const remaining = rows.filter((r) => !s[r.key]).length;
      if (!s[key] && remaining <= 1) return s;
      const next = { ...s };
      if (next[key]) delete next[key];
      else next[key] = true;
      return next;
    });
  };

  const doDelete = async () => {
    setBusy(true);
    let ok = 0;
    const failed = [];
    for (const r of selectedRows) {
      try {
        await deleteDocument(r.docId);
        ok++;
      } catch (err) {
        failed.push(`${r.filename} — ${err.message}`);
      }
    }
    setBusy(false);
    setConfirmOpen(false);
    if (ok > 0) {
      showToast(
        failed.length > 0
          ? `Удалено документов: ${ok}. Ошибки: ${failed.join("; ")}`
          : `Удалено документов: ${ok}`,
        { type: failed.length > 0 ? "warning" : "success", duration: 6000 }
      );
      onDeleted?.();
      onClose();
    } else {
      showToast(`Не удалось удалить: ${failed[0] ?? "неизвестная ошибка"}`, { type: "error" });
    }
  };

  return (
    <>
      <Modal
        title="Возможные дубликаты"
        onClose={onClose}
        footer={
          <>
            <button className="modal-btn" onClick={onClose}>
              Закрыть
            </button>
            {canDelete && (
              <button
                className="modal-btn danger"
                onClick={() => setConfirmOpen(true)}
                disabled={busy || selectedCount === 0}
              >
                Удалить выбранные{selectedCount > 0 ? ` (${selectedCount})` : ""}
              </button>
            )}
          </>
        }
      >
        <p className="dup-hint">
          Документ похож на уже загруженные. Сверьте даты загрузки, чтобы не удалить
          актуальную ревизию.
          {canDelete &&
            " Отметьте документы для удаления — один документ из группы всегда должен остаться в системе."}
        </p>
        <ul className="dup-list">
          {rows.map((r) => (
            <li
              key={r.key}
              className={`dup-item${r.current ? " current" : ""}${
                selected[r.key] ? " selected" : ""
              }`}
            >
              {canDelete && (
                <input
                  type="checkbox"
                  className="doc-checkbox dup-checkbox"
                  aria-label={`Отметить для удаления: ${r.filename}`}
                  checked={!!selected[r.key]}
                  disabled={r.key === lastKeptKey}
                  title={
                    r.key === lastKeptKey
                      ? "Хотя бы один документ из группы должен остаться"
                      : undefined
                  }
                  onChange={() => toggleSelect(r.key)}
                />
              )}
              <div className="dup-body">
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
              </div>
            </li>
          ))}
        </ul>
      </Modal>
      {confirmOpen && (
        <Modal
          title="Удалить выбранные документы?"
          onClose={() => {
            if (!busy) setConfirmOpen(false);
          }}
          footer={
            <>
              <button className="modal-btn" onClick={() => setConfirmOpen(false)} disabled={busy}>
                Отмена
              </button>
              <button className="modal-btn danger" onClick={doDelete} disabled={busy}>
                {busy ? "Удаление…" : `Удалить (${selectedCount})`}
              </button>
            </>
          }
        >
          <p className="confirm-text">
            Выбранные документы будут перемещены в <strong>корзину</strong> и со временем
            удалены окончательно. До этого их можно восстановить в разделе «Корзина».
          </p>
          <ul className="dup-confirm-list">
            {selectedRows.map((r) => (
              <li key={r.key}>
                {r.filename}
                {r.current && <span className="dup-current-label">текущий</span>}
              </li>
            ))}
          </ul>
        </Modal>
      )}
    </>
  );
}
