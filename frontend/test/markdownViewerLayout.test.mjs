import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("wide markdown tables get an isolated horizontal scroll container", async () => {
  const source = await readFile(
    new URL("../src/components/MarkdownViewer.jsx", import.meta.url),
    "utf8",
  );
  const styles = await readFile(
    new URL("../src/app/globals.css", import.meta.url),
    "utf8",
  );

  assert.match(source, /table\(\{[\s\S]*?okf-table-scroll/);
  assert.match(styles, /\.okf-table-scroll\s*\{[\s\S]*?overflow-x:\s*auto\s*;/);
  assert.match(styles, /\.okf-table-scroll\s*\{[\s\S]*?max-width:\s*100%\s*;/);
});
