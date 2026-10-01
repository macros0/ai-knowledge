"use client";

import { useEffect, useRef, useState } from "react";
import { uploadDocument } from "@/lib/api";
import { createDocumentUploadRunner, SUPPORTED_DOCUMENT_EXTENSIONS } from "@/lib/documentUploadBatch.mjs";
import { uploadFailureMessage } from "@/lib/uploadFailures.mjs";
import { useToast } from "@/components/Toast";
import { useI18n } from "@/i18n/LocaleContext";

export default function useDocumentUpload({ onUploaded }) {
  const { t } = useI18n();
  const { showToast } = useToast();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState({ completed: 0, total: 0 });
  const [review, setReview] = useState(null);
  const mounted = useRef(true);
  const decision = useRef(null);
  const callbacks = useRef({ onUploaded, t, showToast });
  useEffect(() => { callbacks.current = { onUploaded, t, showToast }; }, [onUploaded, t, showToast]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; decision.current?.(false); decision.current = null; };
  }, []);
  const runner = useRef(null);

  const resolveReview = (accepted) => {
    const resolve = decision.current;
    decision.current = null;
    setReview(null);
    resolve?.(accepted);
  };
  const start = async (files, parameters) => {
    if (!runner.current) runner.current = createDocumentUploadRunner({
    uploadDocument,
    isActive: () => mounted.current,
    onBusy: (value) => { if (mounted.current) setBusy(value); },
    onProgress: (value) => { if (mounted.current) setProgress(value); },
    onUploaded: (doc) => callbacks.current.onUploaded?.(doc),
    reviewUpload: (info) => {
      if (!mounted.current) return Promise.resolve(false);
      return new Promise((resolve) => { decision.current = resolve; setReview(info); });
    },
    });
    const summary = await runner.current.start(files, parameters);
    if (!summary || !mounted.current) return;
    const { t, showToast } = callbacks.current;
    if (!summary.supportedCount) {
      showToast(t("upload.noSupported", { exts: SUPPORTED_DOCUMENT_EXTENSIONS.join(", ") }), { type: "warning" });
      return;
    }
    if (summary.uploadedCount) showToast(t("upload.uploadedCount", { count: summary.uploadedCount }), { type: "success" });
    for (const { file, twin } of summary.trashTwins) showToast(t("upload.trashTwin", { file, name: twin.filename }), { type: "warning", duration: 8000 });
    if (summary.skipped.length) showToast(t("upload.skipped", { names: summary.skipped.join(", ") }), { type: "warning", duration: 8000 });
    if (summary.failed.length) showToast(summary.storageFull
      ? `${t("apiError.storage_full")} (${summary.failed.map((item) => item.file).join(", ")})`
      : summary.failed.map(({ file, error }) => uploadFailureMessage(file, error, t)).join(" "), { type: "error" });
  };
  return { busy, progress, review, start, resolveReview };
}
