// Опции выбора языка исходного документа (source_locale) в карточке документа.
// Node-тестируемый (без DOM): Intl.DisplayNames доступен в Node >= 14.

// Синхронизировать с backend/app/services/source_locale.py::KNOWN_SOURCE_LOCALES.
export const KNOWN_SOURCE_LOCALES = [
  "ar", "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr",
  "he", "hr", "hu", "is", "it", "ja", "kk", "ko", "lt", "lv", "nl",
  "no", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sr", "sv", "tr",
  "uk", "zh",
];

// Локализованное имя языка (Intl.DisplayNames); null — если код не распознан.
export function languageLabel(code, uiLocale) {
  try {
    return new Intl.DisplayNames([uiLocale], { type: "language" }).of(code);
  } catch {
    return null;
  }
}

// Список опций {code, label}. Текущее значение включается всегда (даже вне
// списков — честный показ), затем активные locales (имена приходят от бэкенда),
// затем KNOWN_SOURCE_LOCALES (имена через Intl.DisplayNames, fallback — код).
export function buildLocaleOptions(currentValue, activeLocales = [], uiLocale = "en") {
  const options = new Map();
  const push = (code, label) => {
    if (!code) return;
    const norm = String(code).toLowerCase();
    if (options.has(norm)) return;
    options.set(norm, { code: norm, label: label || norm });
  };

  if (currentValue) {
    push(currentValue, languageLabel(currentValue, uiLocale) || currentValue);
  }
  for (const loc of activeLocales || []) {
    push(loc && loc.code, loc && loc.name);
  }
  for (const code of KNOWN_SOURCE_LOCALES) {
    push(code, languageLabel(code, uiLocale) || code);
  }
  return [...options.values()];
}

// Опции фасет-фильтра из ответа GET /documents/source-locale-facets
// [{code, count}]. Каждая опция — {code, label, count}; label через
// Intl.DisplayNames (fallback — код), null-код → sentinel "unknown" («не
// определён»). Порядок сохраняется как пришёл (уже по count desc).
export function facetOptions(facets, uiLocale = "en") {
  const out = [];
  for (const item of facets || []) {
    if (item.code == null) {
      out.push({ code: "unknown", label: "unknown", count: item.count });
      continue;
    }
    const code = String(item.code).toLowerCase();
    out.push({
      code,
      label: languageLabel(code, uiLocale) || code,
      count: item.count,
    });
  }
  return out;
}
