"use client";

import { useEffect, useState } from "react";
import { deleteDocument, listDocumentDuplicates } from "@/lib/api";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import Modal from "./Modal";

export default function DuplicateModal({ doc, canDelete, onClose, onDeleted }) {
  const { showToast } = useToast();
  const { t, fmtDateTime } = useI18n();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState({});
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    listDocumentDuplicates(doc.id)
      .then(setData)
      .catch((err) => {
        showToast(t("dup.loadError", { message: err.message }), { type: "error" });
        onClose();
      });
  }, [doc.id, showToast, onClose, t]);

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
          ? t("dup.deletedWithErrors", { count: ok, errors: failed.join("; ") })
          : t("dup.deletedCount", { count: ok }),
        { type: failed.length > 0 ? "warning" : "success", duration: 6000 }
      );
      onDeleted?.();
      onClose();
    } else {
      showToast(t("dup.deleteError", { message: failed[0] ?? "unknown" }), { type: "error" });
    }
  };

  return (
    <>
      <Modal
        title={t("dup.title")}
        onClose={onClose}
        footer={
          <>
            <button className="modal-btn" onClick={onClose}>
              {t("common.close")}
            </button>
            {canDelete && (
              <button
                className="modal-btn danger"
                onClick={() => setConfirmOpen(true)}
                disabled={busy || selectedCount === 0}
              >
                {selectedCount > 0
                  ? t("dup.deleteSelectedCount", { count: selectedCount })
                  : t("dup.deleteSelected")}
              </button>
            )}
          </>
        }
      >
        <p className="dup-hint">
          {t("dup.hint")}
          {canDelete && t("dup.hintDelete")}
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
                  aria-label={t("dup.markAria", { name: r.filename })}
                  checked={!!selected[r.key]}
                  disabled={r.key === lastKeptKey}
                  title={
                    r.key === lastKeptKey ? t("dup.mustKeep") : undefined
                  }
                  onChange={() => toggleSelect(r.key)}
                />
              )}
              <div className="dup-body">
                <div className="dup-title">
                  <span className="dup-filename">{r.filename}</span>
                  {r.key === newestKey && <span className="dup-newest">{t("dup.newest")}</span>}
                  {r.current && <span className="dup-current-label">{t("dup.current")}</span>}
                </div>
                <div className="dup-meta">
                  {t("dup.uploaded", { date: fmtDateTime(r.created_at) })}
                  {r.uploaded_by ? ` · ${r.uploaded_by}` : ""}
                  {r.jaccard != null && <> · {t("dup.similarity", { pct: (r.jaccard * 100).toFixed(1) })}</>}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </Modal>
      {confirmOpen && (
        <Modal
          title={t("dup.confirmTitle")}
          onClose={() => {
            if (!busy) setConfirmOpen(false);
          }}
          footer={
            <>
              <button className="modal-btn" onClick={() => setConfirmOpen(false)} disabled={busy}>
                {t("common.cancel")}
              </button>
              <button className="modal-btn danger" onClick={doDelete} disabled={busy}>
                {busy ? t("dup.deleting") : t("dup.deleteSelectedCount", { count: selectedCount })}
              </button>
            </>
          }
        >
          <p className="confirm-text">
            {t("dup.confirmTextPre")} <strong>{t("dup.trashWord")}</strong> {t("dup.confirmTextPost")}
          </p>
          <ul className="dup-confirm-list">
            {selectedRows.map((r) => (
              <li key={r.key}>
                {r.filename}
                {r.current && <span className="dup-current-label">{t("dup.current")}</span>}
              </li>
            ))}
          </ul>
        </Modal>
      )}
    </>
  );
}
