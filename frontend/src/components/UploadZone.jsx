"use client";

import { useRef, useState } from "react";
import { uploadDocument } from "@/lib/api";
import { useToast } from "./Toast";
import Modal from "./Modal";

const SUPPORTED_EXTENSIONS = [".docx", ".xlsx", ".pdf"];

const extOf = (name) => {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i).toLowerCase();
};

export default function UploadZone({ tags = [], developmentId = null, onUploaded }) {
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
      showToast(
        `Нет поддерживаемых файлов. Допустимы: ${SUPPORTED_EXTENSIONS.join(", ")}`,
        { type: "warning" }
      );
      return;
    }

    setBusy(true);
    const failed = [];
    let duplicateInfo = null;
    try {
      for (const file of supported) {
        try {
          await uploadDocument(file, tags, { developmentId });
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
      return;
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

      {duplicate && (
        <Modal
          title="Файл уже загружен"
          onClose={() => setDuplicate(null)}
          footer={
            <button className="modal-btn" onClick={() => setDuplicate(null)}>
              Отменить загрузку
            </button>
          }
        >
          <p className="confirm-text">
            Файл <strong>{duplicate.file}</strong> идентичен уже загруженному документу
            «{duplicate.existing?.filename}».
          </p>
          <p className="confirm-text muted">
            {duplicate.existing?.uploaded_by
              ? `Загружен: ${duplicate.existing.uploaded_by}. `
              : ""}
            Повторная загрузка отклонена.
          </p>
        </Modal>
      )}
    </div>
  );
}