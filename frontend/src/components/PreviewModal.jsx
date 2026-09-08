"use client";

import { useEffect, useState } from "react";
import { bulkDelete, bulkPreview, bulkRegenerate, friendlyApiError } from "@/lib/api";
import { TYPED_CONFIRM_THRESHOLD } from "@/lib/constants";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import Modal from "./Modal";
import ConfirmModal from "./ConfirmModal";

/**
 * Предпросмотр масштаба массовой операции (bulk-preview) + запуск.
 * Кнопки запуска активны после просмотра; удаление при count > порога требует
 * typed confirmation.
 */
export default function PreviewModal({ docIds, onClose, onDone }) {
  const { showToast } = useToast();
  const { t } = useI18n();
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
        if (!cancelled) showToast(t("preview.loadError", { message: friendlyApiError(err, t) }));
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
          ? t("preview.awaiting")
          : t("preview.queue", { id: job.id });
      showToast(t("preview.opToast", { status: statusText }), { type: "success" });
      onClose();
      onDone?.();
    } catch (err) {
      showToast(friendlyApiError(err, t), { type: "error", duration: 8000 });
      setBusy(false);
    }
  };

  const requestDelete = () => {
    if (count > TYPED_CONFIRM_THRESHOLD) {
      setConfirming(true);
      return;
    }
    if (window.confirm(t("preview.confirmDelete", { count }))) {
      run("delete");
    }
  };

  const requestRegenerate = () => {
    if (window.confirm(t("preview.confirmRegenerate", { count }))) {
      run("regenerate");
    }
  };

  if (confirming) {
    return (
      <ConfirmModal
        count={count}
        actionLabel={t("preview.deleteAction")}
        onCancel={() => setConfirming(false)}
        onConfirm={() => run("delete")}
      />
    );
  }

  return (
    <Modal
      title={t("preview.title")}
      onClose={onClose}
      footer={
        <>
          <button className="modal-btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className="modal-btn" disabled={loading || count === 0} onClick={requestDelete}>
            {t("preview.deleteBtn", { count })}
          </button>
          <button
            className="modal-btn"
            disabled={loading || count === 0 || busy}
            onClick={requestRegenerate}
          >
            {t("preview.regenerateBtn", { count })}
          </button>
        </>
      }
    >
      {loading ? (
        <p className="muted">{t("preview.loading")}</p>
      ) : (
        <>
          <p className="confirm-text">
            {t("preview.affected", { count })}
            {estimated > 0 && (
              <>
                {" · "}
                {t("preview.estRegen", { minutes: Math.max(1, Math.round(estimated)) })}
              </>
            )}
          </p>
          {missing.length > 0 && (
            <p className="confirm-text muted">
              {t("preview.missing", { count: missing.length, names: missing.slice(0, 10).join(", ") })}
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
                <li className="muted">{t("preview.more", { count: preview.documents.length - 20 })}</li>
              )}
            </ul>
          )}
        </>
      )}
    </Modal>
  );
}
