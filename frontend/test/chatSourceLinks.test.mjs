import assert from "node:assert/strict";
import test from "node:test";

import { documentHref, sourceHref } from "../src/lib/chatSourceLinks.mjs";

test("concept sources open the exact concept, independently of the document filename", () => {
  const source = {
    doc_id: "doc-1", point_type: "concept", source_slug: "sick-leave-reasons",
    filename: "Регламент.docx", filepath: "",
  };
  assert.equal(sourceHref(source), "/documents/doc-1/okf/sick-leave-reasons.md");
  assert.equal(documentHref(source), "/documents/doc-1/okf");
});

test("older concept sources use the markdown filename when available", () => {
  assert.equal(sourceHref({ doc_id: "doc-1", point_type: "concept", filepath: "bundle/legacy.md", filename: "Регламент.docx" }),
    "/documents/doc-1/okf/legacy.md");
});

test("chunk sources still open their chunk and unresolved concepts open the list", () => {
  assert.equal(sourceHref({ doc_id: "doc-1", point_type: "chunk", chunk_index: 3 }),
    "/documents/doc-1/chunks/3");
  assert.equal(sourceHref({ doc_id: "doc-1", point_type: "concept", filename: "Регламент.docx" }),
    "/documents/doc-1/okf");
});
