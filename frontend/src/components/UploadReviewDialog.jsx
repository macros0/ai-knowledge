"use client";

import Modal from "./Modal";
import { useI18n } from "@/i18n/LocaleContext";

export default function UploadReviewDialog({ review, onDecision }) {
  const { t, fmtDateTime } = useI18n();
  if (!review) return null;
  return (
        <Modal
          title={t(review.exact ? "upload.dupTitle" : "upload.similarTitle")}
          onClose={() => onDecision(false)}
          footer={
            <>
              <button className="modal-btn" onClick={() => onDecision(false)}>
                {t("upload.dupCancel")}
              </button>
              {!review.exact && <button className="modal-btn primary" onClick={() => onDecision(true)}>
                {t("upload.confirmSimilar")}
              </button>}
            </>
          }
        >
          <p className="confirm-text">
            {review.exact
              ? t("upload.dupText", { file: review.file, name: review.existing?.filename })
              : t("upload.similarText", { file: review.file })}
          </p>
          {review.exact ? <p className="confirm-text muted">
            {review.existing?.uploaded_by
              ? t("upload.dupUploadedBy", { name: review.existing.uploaded_by })
              : ""}
            {t("upload.dupRejected")}
          </p> : <p className="confirm-text muted">{t("upload.similarHint")}</p>}
          <ul>
            {(review.exact
              ? (review.existing ? [{ doc: review.existing, jaccard: 1 }] : [])
              : [...(review.duplicates?.level2 ?? []), ...(review.duplicates?.level3 ?? [])]
            ).map(({ doc, jaccard }) => <li key={doc.id}>
              <strong>{doc.filename}</strong>
              <p className="meta">{fmtDateTime(doc.created_at)} · {t("upload.similarity", { percent: Math.round(jaccard * 100) })}</p>
              <a href={`/documents/${encodeURIComponent(doc.id)}/okf`} target="_blank" rel="noopener noreferrer">
                {t("upload.openExisting")}
              </a>
            </li>)}
          </ul>
        </Modal>
  );
}
