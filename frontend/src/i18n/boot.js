// Inline-скрипт установки <html lang> до первой гидратации (по образцу
// boot-скрипта темы в lib/theme.js): читает localStorage «okf.locale», при
// отсутствии — детект navigator.languages/navigator.language, иначе фолбэк "en".

// Литералы продублированы: bootstrap() сериализуется через .toString() и
// исполняется браузером без доступа к модулю. Держать в синхроне с
// STORAGE_KEY/SUPPORTED_LOCALES из core.js.
function bootstrap() {
  try {
    var supported = ["ru", "en", "de", "fr"];
    var stored = null;
    try {
      stored = window.localStorage.getItem("okf.locale");
    } catch (e) {
      stored = null;
    }
    var lang = supported.indexOf(stored) !== -1 ? stored : null;
    if (!lang) {
      var langs = (window.navigator && window.navigator.languages) || [];
      if (window.navigator && window.navigator.language) {
        langs = langs.concat([window.navigator.language]);
      }
      for (var i = 0; i < langs.length; i++) {
        var code = String(langs[i]).toLowerCase().split("-")[0].split("_")[0];
        if (supported.indexOf(code) !== -1) {
          lang = code;
          break;
        }
      }
    }
    document.documentElement.lang = lang || "en";
  } catch (e) {
    document.documentElement.lang = "en";
  }
}

export function bootScript() {
  return `(${bootstrap.toString()})();`;
}
