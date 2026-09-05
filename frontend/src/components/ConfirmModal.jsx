"use client";

import { useState } from "react";
import Modal from "./Modal";
import { useI18n } from "@/i18n/LocaleContext";

/**
 * Typed confirmation для деструктивных операций.
 * Требует ввести точное значение (matchValue), пока не совпадёт — кнопка
 * подтверждения заблокирована.
 *
 * По умолчанию совпадение — по числу документов (String(count)); для удаления
 * разработки передаётся matchValue = номер разработки, а description/hint
 * переопределяются под контекст.
 */
export default function ConfirmModal({
  count,
  actionLabel,
  onConfirm,
  onCancel,
  matchValue,
  title,
  description,
  hint,
}) {
  const { t, tc } = useI18n();
  const [value, setValue] = useState("");

  const effectiveTitle = title ?? t("confirm.bulkDeleteTitle");
  const target = matchValue ?? String(count);
  const match = value.trim() === target;

  const desc =
    description ?? (
      <>
        {t("confirm.bulkDeleteDescPre")}{" "}
        <strong>{tc("confirm.bulkDeleteDocs", count)}</strong>{" "}
        {t("confirm.bulkDeleteDescPost")}
      </>
    );

  const hintText = hint ?? (
    <>
      {t("confirm.bulkDeleteHintPre")} <code>{target}</code>:
    </>
  );

  return (
    <Modal
      title={effectiveTitle}
      onClose={onCancel}
      footer={
        <>
          <button className="modal-btn" onClick={onCancel}>
            {t("common.cancel")}
          </button>
          <button className="modal-btn danger" disabled={!match} onClick={onConfirm}>
            {actionLabel}
          </button>
        </>
      }
    >
      <p className="confirm-text">{desc}</p>
      <p className="confirm-text muted">{hintText}</p>
      <input
        className="confirm-input"
        type="text"
        value={value}
        autoFocus
        placeholder={target}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && match) onConfirm();
        }}
      />
    </Modal>
  );
}
