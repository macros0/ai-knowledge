"use client";

import { useRef, useState } from "react";
import { useI18n } from "@/i18n/LocaleContext";

export default function UploadZone({ busy = false, onFiles }) {
  const { t } = useI18n();
  const inputRef = useRef(null);
  const [dragover, setDragover] = useState(false);
  return (
    <div>
      <div className={`upload-zone${dragover ? " dragover" : ""}`} role="button" tabIndex={busy ? -1 : 0}
        aria-disabled={busy} aria-label={t("upload.chooseFiles")}
        onClick={() => { if (!busy) inputRef.current?.click(); }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget || !["Enter", " "].includes(event.key)) return;
          event.preventDefault();
          if (!busy) inputRef.current?.click();
        }}
        onDragOver={(event) => { event.preventDefault(); if (!busy) setDragover(true); }}
        onDragLeave={() => setDragover(false)}
        onDrop={(event) => {
          event.preventDefault(); setDragover(false);
          if (!busy) onFiles?.(event.dataTransfer?.files);
        }}>
        <input ref={inputRef} type="file" accept=".docx,.xlsx,.pdf,.eml,.msg" multiple disabled={busy} hidden
          onChange={(event) => { onFiles?.(event.target.files); event.target.value = ""; }} />
        <p>{t(busy ? "upload.busy" : "upload.dropHint")}</p>
      </div>
      <p className="upload-formats">DOCX · XLSX · PDF · EML · MSG</p>
    </div>
  );
}
