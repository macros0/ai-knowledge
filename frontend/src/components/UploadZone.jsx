"use client";

import { useRef, useState } from "react";
import { uploadDocument } from "@/lib/api";

export default function UploadZone({ tags = [], onUploaded }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [dragover, setDragover] = useState(false);

  const handleFiles = async (files) => {
    if (!files || files.length === 0) return;
    const fileList = Array.from(files);
    setBusy(true);
    try {
      for (const file of fileList) {
        try {
          await uploadDocument(file, tags);
        } catch (err) {
          alert(`Не удалось загрузить "${file.name}": ${err.message}`);
        }
      }
      onUploaded?.();
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
      <p>{busy ? "Загрузка..." : "Перетащите файлы (.docx, .xlsx, .pdf) или нажмите для выбора"}</p>
    </div>
  );
}