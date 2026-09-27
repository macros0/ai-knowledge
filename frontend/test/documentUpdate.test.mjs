import assert from "node:assert/strict";
import test from "node:test";
import { documentUpdateView, shouldPollOkfDocument } from "../src/lib/documentUpdate.mjs";

test("a first upload never offers keeping a previous version", () => {
  assert.equal(documentUpdateView({ status: "processing" }).noticeKey, null);
  assert.equal(documentUpdateView({ status: "failed" }).actionKey, null);
});

test("a running update explains which version remains available", () => {
  const view = documentUpdateView({
    status: "indexing", has_published_version: true, can_cancel_update: true,
  });
  assert.equal(view.statusKey, "docs.updateRunning");
  assert.equal(view.noticeKey, "docs.previousVersionAvailable");
  assert.equal(view.actionKey, "docs.cancelUpdate");
  assert.equal(view.disabled, false);
});

test("a failed update offers keeping its published base", () => {
  const view = documentUpdateView({
    status: "failed", has_published_version: true, can_cancel_update: true,
  });
  assert.equal(view.noticeKey, "docs.updateFailedPreviousSaved");
  assert.equal(view.actionKey, "docs.keepPreviousVersion");
});

test("durable cancellation stays visible and disables repeated actions after reload", () => {
  const view = documentUpdateView({
    status: "paused", has_published_version: true, can_cancel_update: true, update_cancelling: true,
  });
  assert.equal(view.statusKey, "docs.updateCancelling");
  assert.equal(view.disabled, true);
});

test("published results have no cancellation action", () => {
  assert.equal(documentUpdateView({ status: "done", has_published_version: true }).actionKey, null);
});

test("detail view keeps polling cancellation after an intermediate failure", () => {
  assert.equal(shouldPollOkfDocument({ status: "failed", update_cancelling: true }), true);
  assert.equal(shouldPollOkfDocument({ status: "done", update_cancelling: false }), false);
});
