// «Последние использованные» модули чата (Этап 5.1).
//
// Персональная (не глобальная) метрика рекуррентности: ряд чипов «последних
// модулей» текущего пользователя хранится в localStorage и масштабируется на
// любое число модулей в справочнике. Чистая рекуррентность по порядку — выбор
// модуля двигает его в начало, самый старый вытесняется (без таймстемпов).
//
// Хелпер чистый: storage инжектируется ({ getItem, setItem }) — в проде это
// window.localStorage, в node --test — stub. Ключ — по username (персонально),
// fallback «anonymous» для disabled-режима/без авторизации. Осиротевший ключ
// после переименования пользователя — осознанно принимаем (некритичные UI-данные).

export const RECENT_MODULES_MAX = 6;

const KEY_PREFIX = "okf.recentModules.";

export function recentModulesKey(username) {
  return `${KEY_PREFIX}${username || "anonymous"}`;
}

function normalize(list) {
  if (!Array.isArray(list)) return [];
  return list.filter((item) => typeof item === "string" && item.length > 0);
}

export function readRecentModules(storage, username) {
  let raw = null;
  try {
    raw = storage.getItem(recentModulesKey(username));
  } catch {
    return [];
  }
  if (!raw) return [];
  try {
    return normalize(JSON.parse(raw)).slice(0, RECENT_MODULES_MAX);
  } catch {
    // Невалидный JSON (старая версия формата, ручное вмешательство) — тихо
    // откатываемся к пустому списку, не роняя рендер ModulePicker.
    return [];
  }
}

export function recordRecentModule(storage, username, moduleName) {
  const current = readRecentModules(storage, username).filter((m) => m !== moduleName);
  const next = [moduleName, ...current].slice(0, RECENT_MODULES_MAX);
  try {
    storage.setItem(recentModulesKey(username), JSON.stringify(next));
  } catch {
    // Переполнение квоты localStorage / недоступен storage — не критично.
  }
  return next;
}

export function pruneRecentModules(storage, username, validModules) {
  const valid = new Set(validModules || []);
  const next = readRecentModules(storage, username).filter((m) => valid.has(m));
  try {
    storage.setItem(recentModulesKey(username), JSON.stringify(next));
  } catch {
    // ignore
  }
  return next;
}
