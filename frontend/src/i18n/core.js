// Чистая логика i18n (node-тестируемая, без обращения к window на верхнем уровне).
//
// Единый источник переключателя языка по образцу темы (`lib/theme.js`):
// выбор живёт в localStorage (`okf.locale`), по умолчанию — детект браузера
// (первый поддержанный язык из списка предпочтений, иначе "ru"). Фактический
// язык всегда резолвится через `normalizeLocale`/`getMessages` — невалидные
// значения клампятся в "ru" (фолбэк-локаль, словарь которой гарантированно полный).

import locales from "./locales/index.js";

export const DEFAULT_LOCALE = "ru";
export const SUPPORTED_LOCALES = Object.keys(locales);
export const STORAGE_KEY = "okf.locale";

// "en-US" / "en_US" / "EN" → код поддерживаемой локали или null. В отличие от
// normalizeLocale не клампит незнакомое в дефолт — перебор кандидатов должен
// уметь пропустить неподдержанный язык и посмотреть следующий.
function supportedCode(input) {
  if (!input) return null;
  const code = String(input).toLowerCase().split(/[-_]/)[0];
  return SUPPORTED_LOCALES.includes(code) ? code : null;
}

// "en-US" / "en_US" / "EN" → код поддерживаемой локали (по префиксу до -/_).
export function normalizeLocale(input) {
  return supportedCode(input) || DEFAULT_LOCALE;
}

// Первый ПОДДЕРЖАННЫЙ из кандидатов (navigator.languages, Accept-Language) —
// включая дефолтную локаль: порядок в списке и есть предпочтение пользователя,
// и "ru" первым кандидатом должен побеждать "en" вторым (стандартный Chrome на
// русской системе шлёт ["ru-RU","ru","en-US","en"]). Тот же перебор, что в
// boot.js — иначе <html lang> и язык UI расходятся. Ни одного поддержанного /
// пустой список → DEFAULT_LOCALE.
export function detectLocale(candidates) {
  for (const c of candidates || []) {
    const code = supportedCode(c);
    if (code) return code;
  }
  return DEFAULT_LOCALE;
}

// Резолв локали для SSR из cookie (сохранённый выбор) + Accept-Language
// (первый заход). Cookie имеет приоритет — явный выбор пользователя побеждает
// заголовок браузера.
export function resolveServerLocale(cookieLocale, acceptLanguage) {
  if (cookieLocale) return normalizeLocale(cookieLocale);
  if (acceptLanguage) {
    const parts = String(acceptLanguage)
      .split(",")
      .map((p) => p.split(";")[0].trim());
    return detectLocale(parts);
  }
  return DEFAULT_LOCALE;
}

export function readStored(win) {
  try {
    const v = win.localStorage.getItem(STORAGE_KEY);
    return v && SUPPORTED_LOCALES.includes(v) ? v : null;
  } catch {
    return null;
  }
}

// Локаль, которую СЕЙЧАС видит сервер, или null (cookie нет/значение чужое).
export function readLocaleCookie(win) {
  try {
    for (const entry of String(win.document.cookie || "").split(";")) {
      const eq = entry.indexOf("=");
      if (eq < 0 || entry.slice(0, eq).trim() !== STORAGE_KEY) continue;
      const value = decodeURIComponent(entry.slice(eq + 1).trim());
      return SUPPORTED_LOCALES.includes(value) ? value : null;
    }
  } catch {
    // cookie недоступен — считаем, что сервер языка не знает.
  }
  return null;
}

// Cookie — единственный канал языка к серверу: по нему резолвятся SSR
// force-dynamic страницы (okf/fulltext/chunks) и `display`-имена тегов
// (backend `locale_service.request_locale`). Статичные маршруты cookie не
// читают — их язык определяет клиентский boot-скрипт.
export function setLocaleCookie(locale, win) {
  try {
    win.document.cookie = `${STORAGE_KEY}=${encodeURIComponent(locale)}; path=/; max-age=31536000; samesite=lax`;
  } catch {
    // cookie недоступен — не критично (RSC-страницы отрендерятся на дефолтном языке)
  }
}

export function setStored(locale, win) {
  try {
    win.localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // localStorage недоступен (приватный режим и т.п.) — локаль живёт до перезагрузки.
  }
  setLocaleCookie(locale, win);
}

// Словарь локали поверх дефолтного (ru): отсутствующие в языке ключи
// подставляются из фолбэка, неполный перевод не ломает интерфейс.
// `overrides` — runtime-override UI-словарей (Этап 7 фаза C): { locale: dict },
// самый приоритетный слой поверх versioned-словаря релиза.
export function getMessages(locale, overrides) {
  const base = locales[DEFAULT_LOCALE].messages;
  const code = normalizeLocale(locale);
  const loc = locales[code];
  const versioned = loc && loc.messages ? { ...base, ...loc.messages } : base;
  const override = overrides && overrides[code];
  return override ? { ...versioned, ...override } : versioned;
}

function lookup(messages, key) {
  return key in messages ? messages[key] : null;
}

function interpolate(template, params) {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (m, name) =>
    name in params ? String(params[name]) : m
  );
}

export function translate(messages, key, params) {
  const value = lookup(messages, key);
  if (typeof value !== "string") return key;
  return interpolate(value, params);
}

// Выбор грамматической формы (CLDR-срез, покрывает ru/uk и en-модель: en/de).
export function pluralForm(locale, count) {
  const abs = Math.abs(Number(count) || 0);
  const code = normalizeLocale(locale);
  if (code === "ru" || code === "uk") {
    const d10 = abs % 10;
    const d100 = abs % 100;
    if (d10 === 1 && d100 !== 11) return "one";
    if (d10 >= 2 && d10 <= 4 && (d100 < 12 || d100 > 14)) return "few";
    return "many";
  }
  // en/de и прочие: one/other (zero не используется).
  return abs === 1 ? "one" : "other";
}

// Перевод со склонениями: запись в словаре — либо строка (тогда {count}
// интерполируется среди прочих параметров), либо объект форм
// { zero, one, few, many, other } — выбирается по `count` и локали.
export function translatePlural(messages, locale, key, count, params) {
  const value = lookup(messages, key);
  if (value == null) return key;
  if (typeof value === "string") {
    return interpolate(value, { ...(params || {}), count });
  }
  if (typeof value === "object") {
    const form = pluralForm(locale, count);
    const template = value[form] ?? value.other;
    if (typeof template !== "string") return key;
    return interpolate(template, { ...(params || {}), count });
  }
  return key;
}

export function createTranslator(locale, overrides) {
  const code = normalizeLocale(locale);
  const messages = getMessages(code, overrides);
  return {
    locale: code,
    t: (key, params) => translate(messages, key, params),
    tc: (key, count, params) => translatePlural(messages, code, key, count, params),
  };
}

export function formatDate(value, locale) {
  const d = typeof value === "string" ? new Date(value) : value;
  if (!d || Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat(normalizeLocale(locale)).format(d);
}

export function formatDateTime(value, locale) {
  const d = typeof value === "string" ? new Date(value) : value;
  if (!d || Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat(normalizeLocale(locale), {
    dateStyle: "short",
    timeStyle: "short",
  }).format(d);
}

export function formatNumber(value, locale) {
  return new Intl.NumberFormat(normalizeLocale(locale)).format(Number(value) || 0);
}