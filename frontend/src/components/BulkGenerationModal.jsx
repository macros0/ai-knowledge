"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, bulkPreview, bulkRegenerate, bulkResume, friendlyApiError, previewInterruptedDocuments } from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";
import Modal from "./Modal";

export default function BulkGenerationModal({ operation, docIds, onClose, onDone }) {
  const { t } = useI18n();
  const { showToast } = useToast();
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const regenerate = operation === "regenerate";

  useEffect(() => {
    let cancelled = false;
    const pending = operation === "interrupted"
      ? previewInterruptedDocuments()
      : bulkPreview(docIds, operation);
    pending.then((data) => { if (!cancelled) setPreview(data); })
      .catch((err) => { if (!cancelled) setError(err); });
    return () => { cancelled = true; };
  }, [docIds, operation]);

  const ids = preview?.eligible_doc_ids ?? [];
  const overLimit = preview && ids.length > preview.max_docs;
  const close = () => { if (!submitting.current) onClose(); };
  const run = async () => {
    if (submitting.current || !ids.length || overLimit) return;
    submitting.current = true;
    setBusy(true);
    try {
      const job = await (regenerate ? bulkRegenerate : bulkResume)(ids);
      showToast(job.status === "awaiting_approval" ? t("preview.awaiting") : t("preview.queue", { id: job.id }), { type: "success" });
      onDone(job);
    } catch (err) {
      setError(err);
      submitting.current = false;
      setBusy(false);
    }
  };

  return (
    <Modal title={t(`bulkGeneration.${operation}`)} onClose={close} footer={<>
      <button className="modal-btn" onClick={close} disabled={busy}>{t("common.cancel")}</button>
      <button className="modal-btn" onClick={run} disabled={busy || !preview || !ids.length || overLimit}>
        {t("bulkGeneration.start", { count: ids.length })}
      </button>
    </>}>
      <p>{t(regenerate ? "bulkGeneration.regenerateHelp" : "bulkGeneration.resumeHelp")}</p>
      {operation === "interrupted" && <p>{t("bulkGeneration.interruptedHelp")}</p>}
      {error && <p role="alert">{error.status === 404 ? t("bulkGeneration.backendUpdate") : friendlyApiError(error, t)}</p>}
      {!preview && !error && <p>{t("preview.loading")}</p>}
      {preview && <>
        <p>{t("bulkGeneration.counts", { eligible: ids.length, skipped: preview.skipped.length })}</p>
        {overLimit && <p role="alert">{t("bulkGeneration.limit", { max: preview.max_docs })}</p>}
        {operation === "interrupted" && preview.requested > preview.matched && <p>{t("bulkGeneration.batch", { total: preview.requested, count: preview.matched })}</p>}
        <ul className="preview-doc-list">
          {preview.documents.slice(0, 20).map((doc) => <li key={doc.id} className="preview-doc-item">
            <span className="preview-doc-name">{doc.filename}</span>
            <span>{t(`status.${doc.status}`)}</span>
          </li>)}
        </ul>
        {preview.documents.length > 20 && <p>{t("preview.more", { count: preview.documents.length - 20 })}</p>}
        {preview.skipped.length > 0 && <details>
          <summary>{t("bulkGeneration.skipped")}</summary>
          <ul>{preview.skipped.map((item) => <li key={item.doc_id}>
            {preview.documents.find((doc) => doc.id === item.doc_id)?.filename ?? item.doc_id}: {friendlyApiError(new ApiError("", { code: item.error_code }), t)}
          </li>)}</ul>
        </details>}
      </>}
    </Modal>
  );
}
