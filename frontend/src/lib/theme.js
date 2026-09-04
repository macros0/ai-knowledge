// Единый источник логики темы (Авто/Светлая/Тёмная).
//
// Значение выбора живёт в localStorage (`okf.theme`): "auto" | "light" | "dark",
// по умолчанию "auto" (следует системной теме через prefers-color-scheme).
// Резолв в конкретную тему всегда происходит в `applyTheme`/`resolveTheme` —
// промежуточного состояния «без data-theme» в рантайме нет: bootScript ставит
// атрибут до первой отрисовки, чтобы не было мигания.
//
// Модуль чистый (без обращения к window на верхнем уровне) — bootScript()
// импортируется в корневом layout (RSC) и генерирует инлайн-скрипт.

export const THEME_KEY = "okf.theme";

const STORED_MODES = ["light", "dark", "auto"];

export function readStored(win) {
  try {
    const v = win.localStorage.getItem(THEME_KEY);
    return v && STORED_MODES.includes(v) ? v : null;
  } catch {
    return null;
  }
}

export function setStored(mode, win) {
  try {
    win.localStorage.setItem(THEME_KEY, mode);
  } catch {
    // localStorage недоступен (приватный режим и т.п.) — тема живёт до перезагрузки.
  }
}

export function systemPrefersLight(win) {
  try {
    return !!win.matchMedia && win.matchMedia("(prefers-color-scheme: light)").matches;
  } catch {
    return false;
  }
}

export function resolveTheme(mode, win) {
  if (mode === "light" || mode === "dark") return mode;
  return systemPrefersLight(win) ? "light" : "dark";
}

export function applyTheme(mode, win) {
  const w = win || window;
  w.document.documentElement.dataset.theme = resolveTheme(mode, w);
}

// Ключ продублирован литералом: bootstrap() сериализуется через .toString()
// и исполняется браузером без доступа к модулю. Держать в синхроне с THEME_KEY.
function bootstrap() {
  try {
    var stored = null;
    try {
      stored = window.localStorage.getItem("okf.theme");
    } catch (e) {
      stored = null;
    }
    var mode =
      stored === "light" || stored === "dark" || stored === "auto" ? stored : "auto";
    var theme;
    if (mode === "light") {
      theme = "light";
    } else if (mode === "dark") {
      theme = "dark";
    } else {
      theme =
        window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: light)").matches
          ? "light"
          : "dark";
    }
    document.documentElement.dataset.theme = theme;
  } catch (e) {
    document.documentElement.dataset.theme = "dark";
  }
}

export function bootScript() {
  return `(${bootstrap.toString()})();`;
}
