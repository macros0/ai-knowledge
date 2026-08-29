"use client";

import { useRef, useState } from "react";
import { uploadDocument } from "@/lib/api";
import { useToast } from "./Toast";

const SUPPORTED_EXTENSIONS = [".docx", ".xlsx", ".pdf"];

const extOf = (name) => {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i).toLowerCase();
};

export default function UploadZone({ tags = [], onUploaded }) {
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [dragover, setDragover] = useState(false);
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
      showToast(
        `Нет поддерживаемых файлов. Допустимы: ${SUPPORTED_EXTENSIONS.join(", ")}`,
        { type: "warning" }
      );
      return;
    }

    setBusy(true);
    const failed = [];
    try {
      for (const file of supported) {
        try {
          await uploadDocument(file, tags);
        } catch (err) {
          failed.push(file.name);
        }
      }
    } finally {
      setBusy(false);
    }

    const uploadedCount = supported.length - failed.length;
    if (uploadedCount > 0) {
      showToast(`Загружено файлов: ${uploadedCount}`, { type: "success" });
      onUploaded?.();
    }
    if (skipped.length > 0) {
      showToast(`Пропущено (неподдерживаемый формат): ${skipped.join(", ")}`, {
        type: "warning",
        duration: 8000,
      });
    }
    if (failed.length > 0) {
      showToast(`Ошибка загрузки: ${failed.join(", ")}`, { type: "error" });
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