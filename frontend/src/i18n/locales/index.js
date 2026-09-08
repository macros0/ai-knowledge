// Манифест локалей. Новый язык = новый файл словаря + одна запись здесь.
// de (08.09.2026) — ЗАГЛУШКА: строки ссылаются на en-объект (без отдельного
// файла de.js). Немецкий режим показывает английский интерфейс + немецкие
// форматы дат/чисел (Intl "de"), пока реальный перевод не появится в runtime
// override (admin «Перевод интерфейса») или не будет заменён на отдельный
// de.js-словарь.
import ru from "./ru.js";
import en from "./en.js";

const locales = {
  ru: { label: "Русский", short: "RU", messages: ru },
  en: { label: "English", short: "EN", messages: en },
  de: { label: "Deutsch", short: "DE", messages: en },
};

export default locales;