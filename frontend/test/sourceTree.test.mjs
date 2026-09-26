import test from "node:test";
import assert from "node:assert/strict";
import { canDownloadSource, sourceDepth, sourceDownloadUrl, sourceStatusKey } from "../src/lib/sourceTree.mjs";
import * as sourceTree from "../src/lib/sourceTree.mjs";

test("source tree keeps the root and nested deterministic paths distinct", () => {
  assert.equal(sourceDepth("root"), 0);
  assert.equal(sourceDepth("root/0"), 1);
  assert.equal(sourceDepth("root/0/12"), 2);
});

test("source location labels preserve known Word positions without inventing legacy metadata", () => {
  assert.equal(typeof sourceTree.sourceLocationKey, "function");
  const { sourceLocationKey } = sourceTree;
  for (const location of ["body", "table", "header", "footer", "textbox"]) {
    assert.equal(sourceLocationKey({ metadata: { document_location: location } }), `sources.location.${location}`);
  }
  assert.equal(sourceLocationKey({}), null);
  assert.equal(sourceLocationKey({ metadata: { document_location: "unknown" } }), null);
});

test("source download URL preserves nested source id as a query value", () => {
  assert.equal(
    sourceDownloadUrl("doc/id", "root/0/1"),
    "/api/documents/doc%2Fid/sources/download?source_id=root%2F0%2F1"
  );
});

test("only persisted sources and their containers get a download action", () => {
  assert.equal(canDownloadSource({ artifact_kind: "original" }), true);
  assert.equal(canDownloadSource({ artifact_kind: "container_only" }), true);
  assert.equal(canDownloadSource({ artifact_kind: "derived" }), false);
  assert.equal(sourceStatusKey("parsed"), "sources.status.parsed");
  assert.equal(sourceStatusKey(null), null);
});

test("source warning labels explain known reasons without exposing arbitrary diagnostic text", () => {
  assert.equal(typeof sourceTree.sourceWarningKeys, "function");
  assert.deepEqual(sourceTree.sourceWarningKeys({ warnings: [
    { code: "unsupported_rtf_body", detail: "PRIVATE DEBUG DATA" },
    { code: "unsupported_attachment_format", detail: "PRIVATE FILE CONTENTS" },
    { code: "cid_image_unavailable" },
    { code: "external_attachment" }, { code: "external_attachment" },
    { code: "unexpected-private-code" }, null,
  ] }), ["sources.warning.rtf", "sources.warning.format", "sources.warning.cid", "sources.warning.external", "sources.warning.unknown"]);
  assert.deepEqual(sourceTree.sourceWarningKeys({}), []);
});

test("mail date is explicitly unknown when missing or invalid; document date is not invented", () => {
  assert.equal(typeof sourceTree.sourceDateText, "function");
  const t = (key) => key;
  const format = (value) => `formatted:${value}`;
  assert.equal(sourceTree.sourceDateText({ kind: "mail" }, format, t), "sources.dateUnknown");
  assert.equal(sourceTree.sourceDateText({ kind: "mail", metadata: { sent_at: "invalid" } }, format, t), "sources.dateUnknown");
  assert.equal(sourceTree.sourceDateText({ kind: "mail", metadata: { sent_at: "2026-09-25T07:30:00Z" } }, format, t), "formatted:2026-09-25T07:30:00Z");
  assert.equal(sourceTree.sourceDateText({ kind: "document" }, format, t), null);
});

test("decoding and MIME representation warnings have actionable labels", () => {
  assert.deepEqual(sourceTree.sourceWarningKeys({ warnings: [
    { code: "mail_decode_recovered" }, { code: "mail_alternative_mismatch" },
  ] }), ["sources.warning.decode", "sources.warning.alternative"]);
});
