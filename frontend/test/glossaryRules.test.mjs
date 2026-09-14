import test from "node:test";
import assert from "node:assert/strict";

import { formatGlossaryRulePrefixes, parseGlossaryRulePrefixes } from "../src/lib/glossaryRules.mjs";

test("quoted prefixes preserve intentional trailing spaces", () => {
  const value = '"IT", "IT ", "ИТ", "ИТ ", "инфо тип ", "инфо-тип "';
  assert.deepEqual(parseGlossaryRulePrefixes(value), ["IT", "IT ", "ИТ", "ИТ ", "инфо тип ", "инфо-тип "]);
  assert.equal(formatGlossaryRulePrefixes(["IT", "IT ", "ИТ", "ИТ "]), '"IT", "IT ", "ИТ", "ИТ "');
});

test("quoted prefix parser rejects unquoted and duplicate literals", () => {
  assert.throws(() => parseGlossaryRulePrefixes("IT, ИТ"), /кавыч/iu);
  assert.throws(() => parseGlossaryRulePrefixes('"ИТ", "ИТ"'), /дублир/iu);
  assert.throws(() => parseGlossaryRulePrefixes('"IT", "it"'), /дублир/iu);
});
