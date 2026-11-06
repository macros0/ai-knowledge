import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { ESLint } from "eslint";
import { spawnSync } from "node:child_process";
const require = createRequire(import.meta.url);
const legacyRequire = createRequire(require.resolve("eslint-plugin-react"));
const legacy = legacyRequire("minimatch");

test("legacy ESLint callers use a patched implementation via the compatibility package", () => {
  assert.equal(legacyRequire("minimatch/package.json").name, "@okf/minimatch-legacy");
});

test("import rule retains rejection and legitimate matching through makeRe test predicates", async () => {
  const plugin = require("eslint-plugin-import");
  const eslint = new ESLint({ overrideConfigFile: true, overrideConfig: [{
    plugins: { import: plugin }, rules: { "import/no-internal-modules": ["error", { forbid: ["pkg/private/**"] }] },
  }] });
  const [bad] = await eslint.lintText('import value from "pkg/private/module";', { filePath: "probe.js" });
  assert.ok(bad.messages.some((message) => message.ruleId === "import/no-internal-modules"));
  const [good] = await eslint.lintText('import value from "pkg/public";', { filePath: "probe.js" });
  assert.equal(good.errorCount, 0);
});

test("callable, constructor, defaults and partial traversal remain compatible", () => {
  assert.equal(typeof legacy, "function");
  assert.equal(legacy("src/app.js", "**/*.js"), true);
  assert.equal(legacy("src/app.ts", "**/*.js"), false);
  assert.equal(legacy("src/app.js", "  **/*.js  "), true);
  assert.equal(legacy("a/c", "a/b/../c"), false);
  assert.equal(legacy(".hidden.js", "*.js"), false);
  assert.equal(legacy.defaults({ dot: true })(".hidden.js", "*.js"), true);
  assert.equal(new legacy.Minimatch("src/**/*.js").match("src", true), true);
  assert.equal(new (legacy.Minimatch.defaults({ dot: true }))("*.js").match(".hidden.js"), true);
  assert.deepEqual(legacy.match(["a.js", "b.ts"], "*.js"), ["a.js"]);
  assert.equal(legacy.makeRe("*.js").test("a.js"), true);
  assert.deepEqual(legacy.braceExpand("{a,b}.js"), ["a.js", "b.js"]);
  if (process.platform === "win32") {
    assert.equal(legacy("src/app.js", "src\\*.js"), true);
    assert.equal(legacy("C:/foo/bar", "c:/*/*"), false);
  }
});

test("patched glob preprocessing bounds amplification in a separate process", () => {
  const result = spawnSync(process.execPath, ["-e", `
    const {minimatch}=require('minimatch-modern');
    if (!minimatch('src/file.js','src/**/*.js')) process.exit(2);
    try { minimatch('file.js', '**/../'.repeat(40)+'*.js', {optimizationLevel:2}); }
    catch (error) { if (!(error instanceof RangeError)) throw error; }
  `], { cwd: new URL("..", import.meta.url), timeout: 2000, encoding: "utf8" });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr);
});

test("oversized constrained Zod array fails before accumulating per-item errors", () => {
  const { z } = require("zod");
  const result = z.array(z.number()).max(2).safeParse(Array(10000).fill("invalid"));
  assert.equal(result.success, false);
  assert.equal(result.error.issues.length, 1);
  assert.equal(result.error.issues[0].code, "too_big");
  assert.deepEqual(z.array(z.number()).max(2).parse([1, 2]), [1, 2]);
});
