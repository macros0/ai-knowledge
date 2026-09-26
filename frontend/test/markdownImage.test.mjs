import test from "node:test";
import assert from "node:assert/strict";
import { resolveDocumentImage } from "../src/lib/markdownImage.mjs";

test("document images resolve only to attachments of the current document", () => {
  assert.equal(resolveDocumentImage("attachments/picture.png", "abc"), "/api/documents/abc/okf/attachments/picture.png");
  assert.equal(resolveDocumentImage("./attachments/%D1%82%D0%B5%D1%81%D1%82.png", "abc"), "/api/documents/abc/okf/attachments/%D1%82%D0%B5%D1%81%D1%82.png");
  for (const src of ["https://example.invalid/pixel.png", "//example.invalid/pixel.png", "data:image/png;base64,abc", "/api/admin", "attachments/../secret", "attachments/%2e%2e", "attachments/%2Fsecret", "attachments/a%5Cb.png", "attachments/a?x=1", "attachments/a#x", "attachments/%00.png", "attachments/%ZZ"]) {
    assert.equal(resolveDocumentImage(src, "abc"), null, src);
  }
});
