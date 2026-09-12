import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../src/components/DevelopmentPanel.jsx", import.meta.url),
  "utf8",
);

test("development edit preserves the optimistic-lock version", () => {
  assert.match(source, /version:\s*dev\.version/);
  assert.match(
    source,
    /updateDevelopment\(devId,\s*\{\s*number,\s*name,\s*module:\s*moduleName,\s*version:\s*editForm\.version,\s*\}\)/s,
  );
});
