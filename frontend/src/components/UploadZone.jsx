"use client";

import { useRef, useState } from "react";
import { uploadDocument } from "@/lib/api";

export default function UploadZone({ tags = [], onUploaded }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [dragover, setDragover] = useState(false);

  const handleFile = async (file) => {
    if (!file) return;
    setBusy(true);
    try {
      await uploadDocument(file, tags);
      onUploaded?.();
    } catch (err) {
      alert(`Не удалось загрузить файл: ${err.message}`);
    } finally {
      setBusy(false);
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
        handleFile(e.dataTransfer?.files?.[0]);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".docx,.xlsx,.pdf"
        hidden
        onChange={(e) => {
          handleFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <p>{busy ? "Загрузка..." : "Перетащите файл (.docx, .xlsx, .pdf) или нажмите для выбора"}</p>
    </div>
  );
}
