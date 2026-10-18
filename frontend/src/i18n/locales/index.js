// Манифест локалей. Новый язык = новый файл словаря + одна запись здесь.
// de/fr содержат переводы совместного редактирования; остальные строки
// сохраняют английский fallback. Runtime override может дополнить перевод.
// Фактическая видимость
// языка в переключателе управляется бэкендом (активация); запись здесь — лишь
// признание языка UI-языком релиза фронтенда.
import ru from "./ru.js";
import en from "./en.js";
import de from "./de.js";
import fr from "./fr.js";

const locales = {
  ru: { label: "Русский", short: "RU", messages: ru },
  en: { label: "English", short: "EN", messages: en },
  de: { label: "Deutsch", short: "DE", messages: de },
  fr: { label: "Français", short: "FR", messages: fr },
};

export default locales;
