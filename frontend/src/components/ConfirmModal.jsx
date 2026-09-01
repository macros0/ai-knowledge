"use client";

import { useState } from "react";
import Modal from "./Modal";

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
  title = "Подтвердите массовое удаление",
  description,
  hint,
}) {
  const [value, setValue] = useState("");

  const target = matchValue ?? String(count);
  const match = value.trim() === target;

  const desc =
    description ?? (
      <>
        Вы собираетесь <strong>безвозвратно удалить {count} документов</strong> и все
        связанные с ними концепты, чанки и векторы.
      </>
    );

  const hintText = hint ?? (
    <>
      Для подтверждения введите число <code>{target}</code>:
    </>
  );

  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button className="modal-btn" onClick={onCancel}>
            Отмена
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
