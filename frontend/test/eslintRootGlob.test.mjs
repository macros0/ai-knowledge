import test from "node:test";
import { spawnSync } from "node:child_process";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, parse } from "node:path";
import { ESLint } from "eslint";

const require = createRequire(import.meta.url);
const pluginRequire = createRequire(require.resolve("@next/eslint-plugin-next"));
const { getRootDirs } = pluginRequire("./utils/get-root-dirs.js");

test("Next rootDir uses the scoped tinyglobby adapter", () => {
  assert.equal(pluginRequire("fast-glob/package.json").name, "@okf/eslint-root-glob");
  assert.equal(pluginRequire("fast-glob/package.json").dependencies.tinyglobby, "0.2.17");
});

const cases = [
  ["default cwd", undefined, ["."]],
  ["relative path", "packages/one/", ["packages/one"]],
  ["directory glob", "packages/*/", ["packages/one", "packages/two"]],
  ["mixed array", ["packages/one/", "other/*", 123], ["packages/one", "other/three"]],
  ["Windows separators", "packages\\*\\", ["packages/one", "packages/two"]],
  ["brace alternatives", "packages/{one,two}", ["packages/one", "packages/two"]],
  ["recursive glob excludes its base", "packages/**", ["packages/one", "packages/one/nested", "packages/two"]],
  ["parent after glob stays literal", "packages/*/../one", []],
  ["static parent directory", "packages/../packages/one", ["packages/one"]],
  ["numeric range", "app{1..10}", ["app1", "app2", "app3", "app5", "app10"]],
  ["stepped numeric range", "app{1..5..2}", ["app1", "app3", "app5"]],
  ["padded numeric range", "padded{01..10}", ["padded01", "padded02", "padded10"]],
  ["duplicate separators", "packages//one/*", ["packages/one/nested"]],
  ["static parent prefix before glob", "./packages/../packages/*", ["packages/one", "packages/two"]],
  ["no matching directory", "missing/*", []],
  ["files excluded", "packages/*", ["packages/one", "packages/two"]],
  ["hidden directories excluded", "*/", ["packages", "other"]],
  ["explicit hidden directory", ".hidden/four", [".hidden/four"]],
  ["array entries resolved independently", ["packages/*", "!packages/two"], ["packages/one", "packages/two"]],
  ["negative string", "!packages/two", []],
  ["absolute path", "absolute", ["packages/one"]],
  ["absolute glob", "absolute-glob", ["packages/one", "packages/two"]],
  ["recursive trailing slash", "packages/**/", ["packages/one", "packages/one/nested", "packages/two"]],
];
for (const [label, pattern, expected] of cases) {
  test(`Next rootDir: ${label}`, () => {
    const fixture = mkdtempSync(join(tmpdir(), "okf-root-glob-"));
    const previous = process.cwd();
    try {
      for (const dir of ["packages/one", "packages/two", "other/three", ".hidden/four", "packages/one/nested"]) {
        mkdirSync(join(fixture, dir), { recursive: true });
      }
      if (label.includes("range")) {
        for (const dir of ["app1", "app2", "app3", "app5", "app10", "padded01", "padded02", "padded10"]) mkdirSync(join(fixture, dir));
      }
      writeFileSync(join(fixture, "packages/file.js"), "");
      process.chdir(fixture);
      const rootDir = pattern === "absolute" ? join(fixture, "packages/one") : pattern === "absolute-glob" ? join(fixture, "packages/*") : pattern;
      const result = getRootDirs({ cwd: fixture, settings: { next: { rootDir } } });
      assert.deepEqual(result.map((dir) => resolve(dir)).sort(), expected.map((dir) => resolve(dir)).sort());
    } finally {
      process.chdir(previous);
      rmSync(fixture, { recursive: true, force: true });
    }
  });
}

test("Next lint still reports internal links with a glob rootDir", async () => {
  const fixture = mkdtempSync(join(tmpdir(), "okf-next-lint-"));
  const previous = process.cwd();
  try {
    mkdirSync(join(fixture, "packages/site/pages"), { recursive: true });
    writeFileSync(join(fixture, "packages/site/pages/product.js"), "export default function Page() { return null; }");
    process.chdir(fixture);
    const eslint = new ESLint({ cwd: fixture, overrideConfigFile: true, overrideConfig: {
      files: ["**/*.jsx"],
      languageOptions: { parserOptions: { ecmaFeatures: { jsx: true } } },
      plugins: { "@next/next": pluginRequire("@next/eslint-plugin-next") },
      settings: { next: { rootDir: "packages/*/" } },
      rules: { "@next/next/no-html-link-for-pages": "error" },
    } });
    const [result] = await eslint.lintText('const link = <a href="/product/">Product</a>;', { filePath: "example.jsx" });
    assert.deepEqual(result.messages.map((message) => message.ruleId), ["@next/next/no-html-link-for-pages"]);
  } finally {
    process.chdir(previous);
    rmSync(fixture, { recursive: true, force: true });
  }
});

test("adapter rejects unsupported upstream options instead of silently changing lint", () => {
  const adapter = pluginRequire("fast-glob");
  assert.throws(() => adapter.globSync("*", {}), /contract changed/);
  assert.throws(() => adapter.globSync("*", { onlyDirectories: true, absolute: true }), /contract changed/);
});

test("Next rootDir follows directory symlinks (Windows junctions)", () => {
  const fixture = mkdtempSync(join(tmpdir(), "okf-root-symlink-"));
  const previous = process.cwd();
  try {
    mkdirSync(join(fixture, "target/nested"), { recursive: true });
    symlinkSync(join(fixture, "target"), join(fixture, "alias"), "junction");
    process.chdir(fixture);
    for (const [pattern, expected] of [["alias/", ["alias"]], ["*", ["alias", "target"]], ["*/nested", ["alias/nested", "target/nested"]]]) {
      assert.deepEqual(getRootDirs({ cwd: fixture, settings: { next: { rootDir: pattern } } }).map((dir) => resolve(dir)).sort(), expected.map((dir) => join(fixture, dir)).sort());
    }
  } finally {
    process.chdir(previous);
    rmSync(fixture, { recursive: true, force: true });
  }
});

test("Next rootDir preserves an absolute filesystem root", () => {
  const filesystemRoot = parse(process.cwd()).root;
  const result = getRootDirs({ cwd: process.cwd(), settings: { next: { rootDir: filesystemRoot } } });
  assert.equal(result.length, 1);
  assert.equal(resolve(result[0]), filesystemRoot);
});

test("negative rootDir never selects a literal exclamation directory", () => {
  const fixture = mkdtempSync(join(tmpdir(), "okf-negative-root-"));
  const previous = process.cwd();
  try {
    mkdirSync(join(fixture, "!blocked"));
    process.chdir(fixture);
    assert.deepEqual(getRootDirs({ cwd: fixture, settings: { next: { rootDir: "!blocked" } } }), []);
  } finally {
    process.chdir(previous);
    rmSync(fixture, { recursive: true, force: true });
  }
});

for (const code of ["EACCES", "EIO"]) {
  test(`Next rootDir propagates directory read ${code} instead of dropping lint coverage`, () => {
    const fixture = mkdtempSync(join(tmpdir(), "okf-glob-read-"));
    try {
      mkdirSync(join(fixture, "packages/one"), { recursive: true });
      const script = `
        const fs = require("node:fs");
        const { resolve } = require("node:path");
        const original = fs.readdirSync;
        fs.readdirSync = (dir, options) => {
          if (resolve(dir) === resolve(process.argv[2], "packages")) {
            throw Object.assign(new Error("controlled directory read failure"), { code: process.argv[3] });
          }
          return original(dir, options);
        };
        process.chdir(process.argv[2]);
        try { require(process.argv[1]).globSync("packages/*", { onlyDirectories: true }); console.log("no error"); }
        catch (error) { console.log(error.code); }
      `;
      const child = spawnSync(process.execPath, ["-e", script, pluginRequire.resolve("fast-glob"), fixture, code], { encoding: "utf8", timeout: 10000 });
      assert.equal(child.status, 0, child.stderr);
      assert.equal(child.stdout.trim(), code);
    } finally { rmSync(fixture, { recursive: true, force: true }); }
  });
}
test("deeply nested rootDir is rejected explicitly without exhausting the stack", () => {
  const fixture = mkdtempSync(join(tmpdir(), "okf-glob-nesting-"));
  const previous = process.cwd();
  try {
    process.chdir(fixture);
    const rootDir = "{".repeat(4000) + "a,b" + "}".repeat(4000);
    assert.throws(() => getRootDirs({ cwd: fixture, settings: { next: { rootDir } } }), /nesting exceeds/);
  } finally {
    process.chdir(previous);
    rmSync(fixture, { recursive: true, force: true });
  }
});
