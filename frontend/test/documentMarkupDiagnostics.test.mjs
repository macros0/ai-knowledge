import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { transformSync } from "next/dist/build/swc/index.js";
import * as api from "../src/lib/api.js";
import * as markup from "../src/lib/documentMarkup.mjs";
import * as locales from "../src/lib/sourceLocales.mjs";
import * as identifiers from "../src/lib/diagnosticIdentifiers.mjs";
import { createTranslator } from "../src/i18n/core.js";
import ru from "../src/i18n/locales/ru.js";

const require = createRequire(import.meta.url);
const translate = createTranslator("ru", ru).t;
const requestId = "12345678-1234-1234-1234-123456789abc";
const localReportId = "abcdef12-1234-1234-1234-123456789abc";

// Render the real error branches and real ErrorReference/Modal. Seed only the
// owner's hook state; no browser/API calls are needed for this rendering check.
function renderFailure(kind, error) {
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
      if (id === "react" && name === "DocumentMarkupModal") return { ...React,
        useState(initial) {
          const index = stateIndex++;
          const value = index === 4 ? (kind === "load" ? { document: error } : {})
            : index === 6 ? false : index === 9 ? (kind === "save" ? error : null)
              : typeof initial === "function" ? initial() : initial;
          return [value, () => {}];
        } };
      if (id === "@/i18n/LocaleContext") return { useI18n: () => ({ t: translate, locale: "ru" }) };
      if (id === "@/lib/api") return api;
      if (id === "@/lib/documentMarkup.mjs") return markup;
      if (id === "@/lib/sourceLocales.mjs") return locales;
      if (id === "@/lib/diagnosticIdentifiers.mjs") return identifiers;
      if (id === "@/lib/tagDictionary") return { bumpTagVersion: () => {} };
      if (id === "./Toast") return { useToast: () => ({ showToast: () => {} }) };
      if (id === "./Modal" || id === "./ErrorReference") return load(id.slice(2));
      if (id.startsWith("./")) return () => null; // fields aren't in either error-only state
      return require(id);
    };
    runInNewContext(compiled, { module: compiledModule, exports: compiledModule.exports, require: resolve });
    modules.set(name, compiledModule.exports);
    return compiledModule.exports;
  }
  return renderToStaticMarkup(React.createElement(load("DocumentMarkupModal").default,
    { docId: "synthetic", onClose: () => {} }));
}

for (const kind of ["load", "save"]) {
  for (const reference of ["requestId", "localReportId"]) {
    test(`${kind} error renders copyable ${reference} and hides private details`, () => {
      const id = reference === "requestId" ? requestId : localReportId;
      const error = new api.ApiError("PRIVATE provider diagnostic", { status: reference === "localReportId" ? 0 : 500, [reference]: id });
      const html = renderFailure(kind, error);
      assert.ok(html.includes(`<code>${id}</code>`), html);
      assert.ok(html.includes(translate("diagnostics.copy")), html);
      assert.ok(!html.includes("PRIVATE provider diagnostic"), html);
    });
  }
}
