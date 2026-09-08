// Манифест локалей. Новый язык = новый файл словаря + одна запись здесь.
// de (08.09.2026) и fr (08.09.2026) — ЗАГЛУШКИ: строки ссылаются на en-объект
// (без отдельного файла de.js/fr.js). Немецкий/французский режим показывает
// английский интерфейс + форматы дат/чисел целевой локали (Intl "de"/"fr"), пока
// реальный перевод не появится в runtime override (admin «Перевод интерфейса»)
// или не будет заменён на отдельный de.js/fr.js-словарь. Фактическая видимость
// языка в переключателе управляется бэкендом (активация); запись здесь — лишь
// признание языка UI-языком релиза фронтенда.
import ru from "./ru.js";
import en from "./en.js";

const locales = {
  ru: { label: "Русский", short: "RU", messages: ru },
  en: { label: "English", short: "EN", messages: en },
  de: { label: "Deutsch", short: "DE", messages: en },
  fr: { label: "Français", short: "FR", messages: en },
};

export default locales;