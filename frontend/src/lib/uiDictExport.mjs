// Чистый модуль экспорта UI-словарей (Этап 7 фаза C, node-тестируемый).
// Значения словарей живут только на фронте (ru.js/en.js), поэтому экспорт
// строится здесь через getMessages() — тот же порядок разрешения строк, что и
// в рантайме (override → versioned locale → fallback ru), без второго merge.

import { getMessages } from "../i18n/core.js";

// Эффективный словарь локали: runtime override → встроенный locale → ru fallback.
// `override` — активный runtime-словарь (или null/{} — без override).
export function effectiveDictionary(locale, override) {
  const code = String(locale || "ru").trim() || "ru";
  return getMessages(code, { [code]: override || {} });
}

// Полный ru-словарь — исходник для перевода/терминологического review.
export function ruSourceDictionary() {
  return getMessages("ru");
}

export function exportFilename(locale, kind) {
  const date = new Date().toISOString().slice(0, 10);
  if (kind === "effective") return `ui-dictionary-${locale}-effective-${date}.json`;
  if (kind === "ru-source") return `ui-dictionary-ru-source-for-${locale}-${date}.json`;
  return `ui-dictionary-${locale}-override-${date}.json`;
}

// Скачивание JSON через Blob+anchor (паттерн SecurityPanel).
export function downloadDict(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
