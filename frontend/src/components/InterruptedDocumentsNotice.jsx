"use client";

import { useEffect, useState } from "react";
import { previewInterruptedDocuments } from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";
import BulkGenerationModal from "./BulkGenerationModal";

export default function InterruptedDocumentsNotice({ refreshKey, onRestored }) {
  const { t } = useI18n();
  const [available, setAvailable] = useState(false);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let active = true, pending = false;
    const refresh = async () => {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      try {
        const preview = await previewInterruptedDocuments();
        if (active) { setAvailable(preview.eligible_doc_ids.length > 0); setError(false); }
      } catch {
        if (active) setError(true);
      } finally { pending = false; }
    };
    refresh();
    document.addEventListener("visibilitychange", refresh);
    return () => { active = false; document.removeEventListener("visibilitychange", refresh); };
  }, [refreshKey, retry]);
  return <>
    {(available || error) && <div className="interrupted-documents-notice" role="status">
      <span>{t(error ? "bulkGeneration.checkFailed" : "bulkGeneration.notice")}</span>
      {error ? <button type="button" className="bulk-tag-btn" onClick={() => setRetry((value) => value + 1)}>{t("bulkGeneration.retryCheck")}</button>
        : <button type="button" className="bulk-tag-btn" onClick={() => setOpen(true)}>{t("bulkGeneration.reviewInterrupted")}</button>}
    </div>}
    {open && <BulkGenerationModal operation="interrupted" onClose={() => setOpen(false)} onDone={() => {
      setOpen(false); setRetry((value) => value + 1); onRestored();
    }} />}
  </>;
}
