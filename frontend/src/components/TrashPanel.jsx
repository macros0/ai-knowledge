"use client";

import { useCallback, useEffect, useState } from "react";
import { bulkRestoreDocuments, friendlyApiError, listTrashDocuments, restoreDocument } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import Modal from "./Modal";

const PAGE_SIZE = 50;

export default function TrashPanel() {
  const { mode, hasRole } = useAuth();
  const { showToast } = useToast();
  const { t, fmtDateTime } = useI18n();
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
    load().catch((err) => showToast(t("trash.loadError", { message: friendlyApiError(err, t) }), { type: "error" }));
  }, [load, showToast, t]);

  const restoreOne = async (doc, { force = false } = {}) => {
    if (busy[doc.id]) return;
    setBusy((s) => ({ ...s, [doc.id]: true }));
    try {
      await restoreDocument(doc.id, { force });
      showToast(t("trash.restored", { name: doc.filename }), { type: "success" });
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
        showToast(t("trash.restoreError", { message: friendlyApiError(err, t) }), { type: "error" });
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
      showToast(n > 0 ? t("trash.restoredCount", { count: n }) : t("trash.noneToRestore"), {
        type: n > 0 ? "success" : "warning",
      });
      if (conflicts.length > 0) {
        showToast(t("trash.conflictBulk", { count: conflicts.length }), {
          type: "warning",
          duration: 10000,
        });
      }
      setSelected({});
      load();
    } catch (err) {
      showToast(t("trash.restoreError", { message: friendlyApiError(err, t) }), { type: "error" });
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="trash-panel">
      {canEdit && selectedIds.length > 0 && (
        <div className="trash-bar">
          <span>{t("trash.selected", { count: selectedIds.length })}</span>
          <button className="btn" onClick={bulkRestore}>
            {t("trash.restoreSelected")}
          </button>
          <button className="btn ghost" onClick={() => setSelected({})}>
            {t("trash.clearSelection")}
          </button>
        </div>
      )}
      <p className="trash-hint">
        {t("trash.hint", { days: retentionDays })}
      </p>
      {!loaded ? (
        <p className="trash-empty">{t("trash.loading")}</p>
      ) : docs.length === 0 ? (
        <p className="trash-empty">{t("trash.empty")}</p>
      ) : (
        <ul className="document-list">
          {docs.map((doc) => (
            <li key={doc.id} className={`document-item${selected[doc.id] ? " selected" : ""}`}>
              {canEdit && (
                <input
                  type="checkbox"
                  className="doc-checkbox"
                  aria-label={t("trash.selectAria", { name: doc.filename })}
                  checked={!!selected[doc.id]}
                  onChange={() => toggleSelect(doc.id)}
                />
              )}
              <div>
                <strong>{doc.filename}</strong>
                <div className="meta">
                  {t("trash.deletedAt", { date: fmtDateTime(doc.deleted_at) })}
                  {doc.deleted_by ? ` · ${doc.deleted_by}` : ""}
                  <br />
                  <span className="trash-days">
                    {t("trash.daysLeft", { days: doc.days_left })}
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
                    {t("trash.restore")}
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
            {t("pagination.back")}
          </button>
          <span className="page-indicator">
            {t("pagination.page", { page: page + 1, total: totalPages })}
          </span>
          <button
            className="page-btn"
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
          >
            {t("pagination.forward")}
          </button>
        </div>
      )}
      {conflict && (
        <Modal
          title={t("trash.conflictTitle")}
          onClose={() => setConflict(null)}
          footer={
            <>
              <button className="modal-btn" onClick={() => setConflict(null)}>
                {t("common.cancel")}
              </button>
              <button
                className="modal-btn"
                onClick={() => {
                  const doc = conflict.doc;
                  setConflict(null);
                  restoreOne(doc, { force: true });
                }}
              >
                {t("trash.restoreSeparate")}
              </button>
            </>
          }
        >
          <p className="dup-hint">
            {t("trash.conflictHint", {
              name:
                conflict.duplicates?.level2?.[0]?.doc?.filename ??
                conflict.duplicates?.level3?.[0]?.doc?.filename ??
                "—",
            })}
          </p>
        </Modal>
      )}
    </div>
  );
}
