import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("navigation does not clip the expanded overflow menu", async () => {
  const css = await readFile(new URL("../src/app/globals.css", import.meta.url), "utf8");
  const navRule = css.match(/\bnav\s*\{([\s\S]*?)\}/)?.[1] ?? "";

  assert.match(navRule, /overflow\s*:\s*visible\s*;/);
});

test("overflow menu closes after selecting a link or clicking outside", async () => {
  const source = await readFile(new URL("../src/components/Nav.jsx", import.meta.url), "utf8");

  assert.match(source, /onClick=\{\(\) => setMoreOpen\(false\)\}/);
  assert.match(source, /document\.addEventListener\("pointerdown"/);
  assert.match(source, /!moreRef\.current\?\.contains\(event\.target\)/);
});
