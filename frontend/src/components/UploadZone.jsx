"use client";

import { useEffect, useRef, useState } from "react";
import { uploadDocument } from "@/lib/api";
import { uploadWithReview } from "@/lib/uploadReview.mjs";
import { uploadFailureMessage } from "@/lib/uploadFailures.mjs";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import Modal from "./Modal";

const SUPPORTED_EXTENSIONS = [".docx", ".xlsx", ".pdf", ".eml", ".msg"];

const extOf = (name) => {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i).toLowerCase();
};

export default function UploadZone({ tags = [], developmentId = null, canonicalLocale, onUploaded }) {
  const { t, fmtDateTime } = useI18n();
  const inputRef = useRef(null);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const decisionRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [dragover, setDragover] = useState(false);
  const [duplicate, setDuplicate] = useState(null);
  const { showToast } = useToast();

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      decisionRef.current?.(false);
      decisionRef.current = null;
    };
  }, []);

  const resolveReview = (accepted) => {
    const resolve = decisionRef.current;
    decisionRef.current = null;
    setDuplicate(null);
    resolve?.(accepted);
  };

  const reviewUpload = (info) => {
    if (!mountedRef.current) return Promise.resolve(false);
    return new Promise((resolve) => {
      decisionRef.current = resolve;
      setDuplicate(info);
    });
  };

  const handleFiles = async (files) => {
    if (busyRef.current || !files || files.length === 0) return;
    const fileList = Array.from(files);

    const supported = [];
    const skipped = [];
    for (const file of fileList) {
      if (SUPPORTED_EXTENSIONS.includes(extOf(file.name))) {
        supported.push(file);
      } else {
        skipped.push(file.name);
      }
    }

    if (supported.length === 0) {
      showToast(t("upload.noSupported", { exts: SUPPORTED_EXTENSIONS.join(", ") }), {
        type: "warning",
      });
      return;
    }

    busyRef.current = true;
    setBusy(true);
    const failed = [];
    const failureMessages = [];
    let storageFull = false;
    let uploadedCount = 0;
    const trashTwins = [];
    try {
      for (const file of supported) {
        if (!mountedRef.current) break;
        try {
          const doc = await uploadWithReview(file,
            (allowSimilar) => uploadDocument(file, tags, { developmentId, canonicalLocale, allowSimilar }),
            reviewUpload);
          if (!doc) continue;
          uploadedCount += 1;
          onUploaded?.();
          // Близнец файла в корзине не блокирует загрузку — информационный тост.
          if (doc?.duplicate_in_trash) {
            trashTwins.push({ file: file.name, twin: doc.duplicate_in_trash });
          }
        } catch (err) {
          failed.push(file.name);
          failureMessages.push(uploadFailureMessage(file.name, err, t));
          if (err.code === "storage_full") {
            storageFull = true;
            break;
          }
        }
      }
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setBusy(false);
    }

    if (!mountedRef.current) return;

    if (uploadedCount > 0) {
      showToast(t("upload.uploadedCount", { count: uploadedCount }), { type: "success" });
    }
    for (const { file, twin } of trashTwins) {
      showToast(t("upload.trashTwin", { file, name: twin.filename }), {
        type: "warning",
        duration: 8000,
      });
    }
    if (skipped.length > 0) {
      showToast(t("upload.skipped", { names: skipped.join(", ") }), {
        type: "warning",
        duration: 8000,
      });
    }
    if (failed.length > 0) {
      showToast(
        storageFull
          ? `${t("apiError.storage_full")} (${failed.join(", ")})`
          : failureMessages.join(" "),
        { type: "error" },
      );
    }
  };

  return (
    <>
    <div
      className={`upload-zone${dragover ? " dragover" : ""}`}
      role="button"
      tabIndex={busy ? -1 : 0}
      aria-disabled={busy}
      onClick={() => { if (!busyRef.current) inputRef.current?.click(); }}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget || !["Enter", " "].includes(e.key)) return;
        e.preventDefault();
        if (!busyRef.current) inputRef.current?.click();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragover(true);
      }}
      onDragLeave={() => setDragover(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragover(false);
        handleFiles(e.dataTransfer?.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".docx,.xlsx,.pdf,.eml,.msg"
        multiple
        disabled={busy}
        hidden
        onChange={(e) => {
          handleFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <p>{busy ? t("upload.busy") : t("upload.dropHint")}</p>
    </div>

      {duplicate && (
        <Modal
          title={t(duplicate.exact ? "upload.dupTitle" : "upload.similarTitle")}
          onClose={() => resolveReview(false)}
          footer={
            <>
              <button className="modal-btn" onClick={() => resolveReview(false)}>
                {t("upload.dupCancel")}
              </button>
              {!duplicate.exact && <button className="modal-btn primary" onClick={() => resolveReview(true)}>
                {t("upload.confirmSimilar")}
              </button>}
            </>
          }
        >
          <p className="confirm-text">
            {duplicate.exact
              ? t("upload.dupText", { file: duplicate.file, name: duplicate.existing?.filename })
              : t("upload.similarText", { file: duplicate.file })}
          </p>
          {duplicate.exact ? <p className="confirm-text muted">
            {duplicate.existing?.uploaded_by
              ? t("upload.dupUploadedBy", { name: duplicate.existing.uploaded_by })
              : ""}
            {t("upload.dupRejected")}
          </p> : <p className="confirm-text muted">{t("upload.similarHint")}</p>}
          <ul>
            {(duplicate.exact
              ? (duplicate.existing ? [{ doc: duplicate.existing, jaccard: 1 }] : [])
              : [...(duplicate.duplicates?.level2 ?? []), ...(duplicate.duplicates?.level3 ?? [])]
            ).map(({ doc, jaccard }) => <li key={doc.id}>
              <strong>{doc.filename}</strong>
              <p className="meta">{fmtDateTime(doc.created_at)} · {t("upload.similarity", { percent: Math.round(jaccard * 100) })}</p>
              <a href={`/documents/${encodeURIComponent(doc.id)}/okf`} target="_blank" rel="noopener noreferrer">
                {t("upload.openExisting")}
              </a>
            </li>)}
          </ul>
        </Modal>
      )}
    </>
  );
}
