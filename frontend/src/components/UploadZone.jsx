"use client";

import { useRef, useState } from "react";
import { uploadDocument } from "@/lib/api";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import Modal from "./Modal";

const SUPPORTED_EXTENSIONS = [".docx", ".xlsx", ".pdf"];

const extOf = (name) => {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i).toLowerCase();
};

export default function UploadZone({ tags = [], developmentId = null, canonicalLocale, onUploaded }) {
  const { t } = useI18n();
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [dragover, setDragover] = useState(false);
  const [duplicate, setDuplicate] = useState(null);
  const { showToast } = useToast();

  const handleFiles = async (files) => {
    if (!files || files.length === 0) return;
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

    setBusy(true);
    const failed = [];
    let duplicateInfo = null;
    let uploadedCount = 0;
    const trashTwins = [];
    try {
      for (const file of supported) {
        try {
          const doc = await uploadDocument(file, tags, { developmentId, canonicalLocale });
          uploadedCount += 1;
          // Близнец файла в корзине не блокирует загрузку — информационный тост.
          if (doc?.duplicate_in_trash) {
            trashTwins.push({ file: file.name, twin: doc.duplicate_in_trash });
          }
        } catch (err) {
          if (err.code === "duplicate") {
            duplicateInfo = { file: file.name, existing: err.data?.duplicate };
            break;
          }
          failed.push(file.name);
        }
      }
    } finally {
      setBusy(false);
    }

    if (duplicateInfo) {
      setDuplicate(duplicateInfo);
      if (uploadedCount > 0) {
        showToast(t("upload.uploadedUpToDuplicate", { count: uploadedCount }), { type: "success" });
        onUploaded?.();
      }
      return;
    }

    if (uploadedCount > 0) {
      showToast(t("upload.uploadedCount", { count: uploadedCount }), { type: "success" });
      onUploaded?.();
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
      showToast(t("upload.failed", { names: failed.join(", ") }), { type: "error" });
    }
  };

  return (
    <div
      className={`upload-zone${dragover ? " dragover" : ""}`}
      onClick={() => inputRef.current?.click()}
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
        accept=".docx,.xlsx,.pdf"
        multiple
        hidden
        onChange={(e) => {
          handleFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <p>{busy ? t("upload.busy") : t("upload.dropHint")}</p>

      {duplicate && (
        <Modal
          title={t("upload.dupTitle")}
          onClose={() => setDuplicate(null)}
          footer={
            <button className="modal-btn" onClick={() => setDuplicate(null)}>
              {t("upload.dupCancel")}
            </button>
          }
        >
          <p className="confirm-text">
            {t("upload.dupText", { file: duplicate.file, name: duplicate.existing?.filename })}
          </p>
          <p className="confirm-text muted">
            {duplicate.existing?.uploaded_by
              ? t("upload.dupUploadedBy", { name: duplicate.existing.uploaded_by })
              : ""}
            {t("upload.dupRejected")}
          </p>
        </Modal>
      )}
    </div>
  );
}
