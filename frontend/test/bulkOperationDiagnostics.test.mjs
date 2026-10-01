import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { transformSync } from "next/dist/build/swc/index.js";
import * as api from "../src/lib/api.js";
import * as identifiers from "../src/lib/diagnosticIdentifiers.mjs";
import * as bulkTags from "../src/lib/bulkTags.mjs";
import * as generationPreview from "../src/lib/generationPreview.mjs";
import { createTranslator } from "../src/i18n/core.js";
import ru from "../src/i18n/locales/ru.js";

const require = createRequire(import.meta.url);
const translate = createTranslator("ru", ru).t;

function renderFailure(component, error) {
  let stateIndex = 0;
  const modules = new Map();
  function load(name) {
    if (modules.has(name)) return modules.get(name);
    const source = readFileSync(new URL(`../src/components/${name}.jsx`, import.meta.url), "utf8");
    const compiled = transformSync(source, { filename: `${name}.jsx`,
      jsc: { parser: { syntax: "ecmascript", jsx: true }, target: "es2020",
        transform: { react: { runtime: "automatic" } } }, module: { type: "commonjs" } }).code;
    const compiledModule = { exports: {} };
    const resolve = (id) => {
      if (id === "react" && name === component) return { ...React,
        useState(initial) {
          const index = stateIndex++;
          return [index === (component === "BulkTagsModal" ? 5 : 1) ? error
            : typeof initial === "function" ? initial() : initial, () => {}];
        } };
      if (id === "@/i18n/LocaleContext") return { useI18n: () => ({ t: translate, locale: "ru" }) };
      if (id === "@/lib/api") return api;
      if (id === "@/lib/diagnosticIdentifiers.mjs") return identifiers;
      if (id === "@/lib/bulkTags.mjs") return bulkTags;
      if (id === "@/lib/generationPreview.mjs") return generationPreview;
      if (id === "@/lib/tagDictionary") return { bumpTagVersion: () => {} };
      if (id === "./Toast") return { useToast: () => ({ showToast: () => {} }) };
      if (id === "./Modal" || id === "./ErrorReference") return load(id.slice(2));
      if (id.startsWith("./")) return () => null;
      return require(id);
    };
    runInNewContext(compiled, { module: compiledModule, exports: compiledModule.exports, require: resolve });
    modules.set(name, compiledModule.exports);
    return compiledModule.exports;
  }
  return renderToStaticMarkup(React.createElement(load(component).default,
    { operation: "regenerate", docIds: ["synthetic"], onClose: () => {}, onDone: () => {} }));
}

for (const component of ["BulkTagsModal", "BulkGenerationModal"]) {
  for (const reference of ["requestId", "localReportId"]) {
    test(`${component} retains a copyable ${reference} after document actions move`, () => {
      const id = "12345678-1234-1234-1234-123456789abc";
      const error = new api.ApiError("PRIVATE provider diagnostic", {
        status: reference === "requestId" ? 500 : 0, [reference]: id });
      const html = renderFailure(component, error);
      assert.ok(html.includes(`<code>${id}</code>`), html);
      assert.ok(html.includes(translate("diagnostics.copy")), html);
      assert.ok(!html.includes("PRIVATE provider diagnostic"), html);
    });
  }
}
