import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("navigation does not clip the expanded overflow menu", async () => {
  const css = await readFile(new URL("../src/app/globals.css", import.meta.url), "utf8");
  const navRule = css.match(/\bnav\s*\{([\s\S]*?)\}/)?.[1] ?? "";

  assert.match(navRule, /overflow\s*:\s*visible\s*;/);
});
