"use client";

import { useCallback, useEffect, useState } from "react";
import { bulkRestoreDocuments, listTrashDocuments, restoreDocument } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "./Toast";
import Modal from "./Modal";

const PAGE_SIZE = 50;

const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU");
};

export default function TrashPanel() {
  const { mode, hasRole } = useAuth();
  const { showToast } = useToast();
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [retentionDays, setRetentionDays] = useState(14);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState({});
  const [busy, setBusy] = useState({});
  const [conflict, setConflict] = useState(null);
  // Пока идёт первый запрос — показываем индикатор, а не «Корзина пуста»
  // (иначе при холодном бэкенде виден flash и пауза без индикации).
  const [loaded, setLoaded] = useState(false);

  const canEdit = mode === "disabled" || hasRole("editor", "admin");

  const load = useCallback(async () => {
    const result = await listTrashDocuments({ limit: PAGE_SIZE, offset: page * PAGE_SIZE });
    setDocs(result.documents);
    setTotal(result.total);
    setRetentionDays(result.retention_days ?? 14);
    setLoaded(true);
  }, [page]);

  useEffect(() => {
    load().catch((err) => showToast(`Не удалось загрузить корзину: ${err.message}`, { type: "error" }));
  }, [load, showToast]);

  const restoreOne = async (doc, { force = false } = {}) => {
    if (busy[doc.id]) return;
    setBusy((s) => ({ ...s, [doc.id]: true }));
    try {
      await restoreDocument(doc.id, { force });
      showToast(`Документ «${doc.filename}» восстановлен`, { type: "success" });
      setSelected((s) => {
        const next = { ...s };
        delete next[doc.id];
        return next;
      });
      load();
    } catch (err) {
      if (err.code === "duplicate") {
        setConflict({ doc, duplicates: err.data?.duplicates ?? {} });
      } else {
        showToast(`Не удалось восстановить: ${err.message}`, { type: "error" });
      }
    } finally {
      setBusy((s) => ({ ...s, [doc.id]: false }));
    }
  };

  const toggleSelect = (id) => {
    setSelected((s) => {
      const next = { ...s };
      if (next[id]) delete next[id];
      else next[id] = true;
      return next;
    });
  };

  const selectedIds = Object.keys(selected);

  const bulkRestore = async () => {
    try {
      const res = await bulkRestoreDocuments(selectedIds);
      const n = res?.restored?.length ?? 0;
      const conflicts = res?.conflicts ?? [];
      showToast(n > 0 ? `Восстановлено документов: ${n}` : "Нет документов для восстановления", {
        type: n > 0 ? "success" : "warning",
      });
      if (conflicts.length > 0) {
        showToast(
          `Конфликт дедупликации: ${conflicts.length} — уже есть похожий активный документ. ` +
            "Восстановите его по одному и подтвердите с пропуском проверки.",
          { type: "warning", duration: 10000 }
        );
      }
      setSelected({});
      load();
    } catch (err) {
      showToast(`Не удалось восстановить: ${err.message}`, { type: "error" });
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="trash-panel">
      {canEdit && selectedIds.length > 0 && (
        <div className="trash-bar">
          <span>Выбрано: {selectedIds.length}</span>
          <button className="btn" onClick={bulkRestore}>
            Восстановить выбранные
          </button>
          <button className="btn ghost" onClick={() => setSelected({})}>
            Снять выделение
          </button>
        </div>
      )}
      <p className="trash-hint">
        Документы хранятся в корзине {retentionDays} дн. и затем удаляются окончательно.
      </p>
      {!loaded ? (
        <p className="trash-empty">Загрузка корзины…</p>
      ) : docs.length === 0 ? (
        <p className="trash-empty">Корзина пуста</p>
      ) : (
        <ul className="document-list">
          {docs.map((doc) => (
            <li key={doc.id} className={`document-item${selected[doc.id] ? " selected" : ""}`}>
              {canEdit && (
                <input
                  type="checkbox"
                  className="doc-checkbox"
                  aria-label={`Выбрать ${doc.filename}`}
                  checked={!!selected[doc.id]}
                  onChange={() => toggleSelect(doc.id)}
                />
              )}
              <div>
                <strong>{doc.filename}</strong>
                <div className="meta">
                  Удалён: {fmtDate(doc.deleted_at)}
                  {doc.deleted_by ? ` · ${doc.deleted_by}` : ""}
                  <br />
                  <span className="trash-days">
                    До окончательного удаления: {doc.days_left} дн.
                  </span>
                </div>
              </div>
              <div className="doc-actions">
                {canEdit && (
                  <button
                    className="btn"
                    onClick={() => restoreOne(doc)}
                    disabled={busy[doc.id]}
                  >
                    Восстановить
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {totalPages > 1 && (
        <div className="doc-pagination">
          <button className="page-btn" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
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
      {conflict && (
        <Modal
          title="Конфликт при восстановлении"
          onClose={() => setConflict(null)}
          footer={
            <>
              <button className="modal-btn" onClick={() => setConflict(null)}>
                Отмена
              </button>
              <button
                className="modal-btn"
                onClick={() => {
                  const doc = conflict.doc;
                  setConflict(null);
                  restoreOne(doc, { force: true });
                }}
              >
                Восстановить как отдельный
              </button>
            </>
          }
        >
          <p className="dup-hint">
            Пока документ был в корзине, в систему загрузили похожий документ
            «{conflict.duplicates?.level2?.[0]?.doc?.filename ?? conflict.duplicates?.level3?.[0]?.doc?.filename ?? "—"}».
            Вы можете восстановить документ как отдельный.
          </p>
        </Modal>
      )}
    </div>
  );
}
