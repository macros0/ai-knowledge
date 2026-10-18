import test from "node:test";
import assert from "node:assert/strict";
import locales from "../src/i18n/locales/index.js";

test("reference comparison has localized actions in all supported languages", () => {
  for (const locale of ["ru", "en", "de", "fr"]) {
    assert.equal(typeof locales[locale].messages["reference.conflictTitle"], "string", locale);
    assert.ok(locales[locale].messages["reference.acceptComparison"], locale);
    assert.doesNotMatch(locales[locale].messages["devpanel.versionConflict"], /again|ещё раз/);
  }
  assert.notEqual(locales.de.messages["reference.conflictTitle"], locales.en.messages["reference.conflictTitle"]);
  assert.notEqual(locales.fr.messages["reference.conflictTitle"], locales.en.messages["reference.conflictTitle"]);
});
