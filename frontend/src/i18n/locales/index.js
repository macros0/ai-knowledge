// Манифест локалей. Новый язык = новый файл словаря + одна запись здесь.
import ru from "./ru.js";
import en from "./en.js";

const locales = {
  ru: { label: "Русский", short: "RU", messages: ru },
  en: { label: "English", short: "EN", messages: en },
};

export default locales;