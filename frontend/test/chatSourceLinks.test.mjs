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

test('retrieved passage links preserve canonical coordinates rather than choosing a concept quote', () => {
  const location_ref = {version: 1, basis: 'match', source_id: 'attachment', chunk_index: 53,
    chunk_hash: 'a'.repeat(64), spans: [{start: 1240, end: 1308}]};
  const url = new URL(sourceHref({doc_id: 'doc-1', source_slug: 'notes', location_ref}), 'http://localhost');
  assert.equal(url.pathname, '/documents/doc-1/fulltext');
  assert.deepEqual(JSON.parse(url.searchParams.get('location')), location_ref);
});

test('old history supplies a bounded recovery hint within its own source', () => {
  const url = new URL(sourceHref({doc_id: 'doc-1', point_type: 'concept', source_slug: 'notes',
    source_id: 'attachment', chunk_index: 53, snippet: '521396 IT0004(Challenge) and relevance to Sec80-U'}), 'http://localhost');
  assert.equal(url.pathname, '/documents/doc-1/fulltext');
  const ref = JSON.parse(url.searchParams.get('location'));
  assert.equal(ref.basis, 'legacy');
  assert.equal(ref.source_id, 'attachment');
  assert.equal(ref.source_slug, 'notes');
});
