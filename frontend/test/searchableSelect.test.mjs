import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { filterSearchableOptions } from "../src/lib/searchableSelect.mjs";

const options = [
  { value: "sap", label: "SAP" },
  { value: "isap", label: "Internal SAP" },
  { value: "ru", label: "Русский" },
  { value: "en", label: "English" },
];

test("searchable selector uses a shared option filtering helper", async () => {
  const source = await readFile(new URL("../src/components/DocumentList.jsx", import.meta.url), "utf8");
  assert.match(source, /import SearchableSelect from "\.\/SearchableSelect"/);
});

test("searchable selector exposes search and keyboard controls", async () => {
  const source = await readFile(new URL("../src/components/SearchableSelect.jsx", import.meta.url), "utf8");
  assert.match(source, /ArrowDown/);
  assert.match(source, /Escape/);
  assert.match(source, /document\.addEventListener\("mousedown"/);
});

test("all language selectors use the shared searchable control", async () => {
  const files = [
    "ReferenceLocaleSelect.jsx",
    "LocaleToggle.jsx",
    "GlossaryTranslations.jsx",
    "DocumentList.jsx",
    "ChatPanel.jsx",
  ];
  for (const file of files) {
    const source = await readFile(new URL(`../src/components/${file}`, import.meta.url), "utf8");
    assert.match(source, /SearchableSelect/, file);
  }
});

test("searchable popup keeps a readable width and single-line options", async () => {
  const component = await readFile(new URL("../src/components/SearchableSelect.jsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../src/app/globals.css", import.meta.url), "utf8");
  assert.match(component, /popupMinWidth/);
  assert.match(component, /Math\.max\(triggerWidth, popupMinWidth\)/);
  assert.match(styles, /\.dev-picker-option[\s\S]*?white-space:\s*nowrap/);
  assert.match(styles, /\.dev-picker-name[\s\S]*?flex:\s*1 1 auto/);
});

test("document module filter uses the shared searchable selector", async () => {
  const source = await readFile(new URL("../src/components/DocumentList.jsx", import.meta.url), "utf8");
  assert.match(source, /const moduleOptions =/);
  assert.match(source, /options=\{moduleOptions\}/);
});

test("development module filter uses the same searchable selector", async () => {
  const source = await readFile(new URL("../src/components/DevelopmentPanel.jsx", import.meta.url), "utf8");
  assert.match(source, /SearchableSelect/);
  assert.match(source, /moduleFilter/);
});

test("bulk tag combobox keeps its popup readable", async () => {
  const source = await readFile(new URL("../src/components/TagCombobox.jsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../src/app/globals.css", import.meta.url), "utf8");
  assert.match(source, /tag-combobox-list/);
  assert.match(source, /createPortal/);
  assert.match(source, /positionPopup/);
  assert.match(styles, /\.tag-combobox-list\s*\{[\s\S]*?min-width:\s*260px/);
  assert.match(styles, /\.tag-combobox-item\s*\{[\s\S]*?white-space:\s*nowrap/);
});

test("development detail sorting uses the shared searchable selector", async () => {
  const source = await readFile(new URL("../src/app/developments/[devId]/page.js", import.meta.url), "utf8");
  assert.match(source, /SearchableSelect/);
  assert.match(source, /sortOptions/);
});

test("development create and edit module fields remain text inputs with a shared datalist", async () => {
  const source = await readFile(new URL("../src/components/DevelopmentPanel.jsx", import.meta.url), "utf8");
  assert.equal((source.match(/allowCustomInput/g) || []).length, 0);
  assert.equal((source.match(/list=\"dev-module-datalist\"/g) || []).length, 2);
  assert.match(source, /<datalist id=\"dev-module-datalist\">/);
});

test("development module filter keeps the all-modules option separate from inputs", async () => {
  const source = await readFile(new URL("../src/components/DevelopmentPanel.jsx", import.meta.url), "utf8");
  assert.match(source, /const moduleOptions =/);
  assert.match(source, /options=\{moduleOptions\}/);
  assert.doesNotMatch(source, /options=\{moduleOptionsFor\(/);
});

test("filterSearchableOptions prioritizes prefixes and searches code plus label", () => {
  const options = [
    { value: "sap", label: "SAP" },
    { value: "isap", label: "Internal SAP" },
    { value: "ru", label: "Русский" },
  ];
  assert.deepEqual(filterSearchableOptions(options, "sap").map((item) => item.value), ["sap", "isap"]);
  assert.deepEqual(filterSearchableOptions(options, "рус").map((item) => item.value), ["ru"]);
});

test("filterSearchableOptions limits the result count", () => {
  const many = Array.from({ length: 4 }, (_, index) => ({ value: `x${index}`, label: `X${index}` }));
  assert.equal(filterSearchableOptions(many, "", 2).length, 2);
});
