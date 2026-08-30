"use client";

import { useState } from "react";
import Modal from "./Modal";

/**
 * Typed confirmation для массового удаления.
 * Требует ввести точное число документов (или слово «удалить»), пока не совпадёт —
 * кнопка подтверждения заблокирована.
 */
export default function ConfirmModal({ count, actionLabel, onConfirm, onCancel }) {
  const [value, setValue] = useState("");

  const match = value.trim() === String(count);

  return (
    <Modal
      title="Подтвердите массовое удаление"
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
      <p className="confirm-text">
        Вы собираетесь <strong>безвозвратно удалить {count} документов</strong> и все
        связанные с ними концепты, чанки и векторы.
      </p>
      <p className="confirm-text muted">
        Для подтверждения введите число <code>{count}</code>:
      </p>
      <input
        className="confirm-input"
        type="text"
        value={value}
        autoFocus
        placeholder={String(count)}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && match) onConfirm();
        }}
      />
    </Modal>
  );
}
