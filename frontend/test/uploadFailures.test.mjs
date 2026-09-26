import test from "node:test";
import assert from "node:assert/strict";
import { ApiError } from "../src/lib/api.js";
import { uploadFailureMessage } from "../src/lib/uploadFailures.mjs";

test("disabled mail import keeps the backend reason in the upload toast", () => {
  const t = (key, params = {}) => key === "apiError.mail_import_disabled"
    ? "Mail import is disabled"
    : key === "upload.failedReason"
      ? `Upload failed: ${params.file}: ${params.reason}`
      : key;
  assert.equal(
    uploadFailureMessage("letter.eml", new ApiError("disabled", { code: "mail_import_disabled" }), t),
    "Upload failed: letter.eml: Mail import is disabled"
  );
});
