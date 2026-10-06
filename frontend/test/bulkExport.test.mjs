import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {runDocumentUploadBatch} from "../src/lib/documentUploadBatch.mjs";
import {friendlyApiError,ApiError} from "../src/lib/api.js";
import {createTranslator} from "../src/i18n/core.js";
import ru from "../src/i18n/locales/ru.js";
import { bulkCapabilities, selectionLimit } from "../src/lib/documentBulkLimits.mjs";

test("admin can select 1000 for export without enabling 50-doc mutations", () => {
  const capabilities = bulkCapabilities(1000, { isAdmin: true, exportEnabled: true, exportMaxDocs: 1000 });
  assert.equal(capabilities.export, true);
  assert.equal(capabilities.tags, false);
  assert.equal(capabilities.delete, false);
  assert.equal(capabilities.regenerate, false);
});

test("editor selection remains capped at 50 and has no export", () => {
  assert.equal(selectionLimit({ isAdmin: false, exportEnabled: true, exportMaxDocs: 1000 }), 50);
  assert.equal(bulkCapabilities(20, { isAdmin: false, exportEnabled: true, exportMaxDocs: 1000 }).export, false);
});

test("disabled export does not raise the admin selection cap", () => {
  assert.equal(selectionLimit({ isAdmin: true, exportEnabled: false, exportMaxDocs: 1000 }), 50);
});

test("admin panel keeps export downloads as direct links and excludes generic controls", async () => {
  const source = await readFile(new URL("../src/components/AdminPanel.jsx", import.meta.url), "utf8");
  assert.match(source, /href=\{`\/api\/jobs\/\$\{job\.id\}\/export\/\$\{part\.number\}`\}/);
  assert.match(source, /job\.job_type !== "bulk_export"/);
  assert.doesNotMatch(source, /blob\(\)|URL\.createObjectURL/);
});

test("security panel registers each export audit action", async () => {
  const source = await readFile(new URL("../src/components/SecurityPanel.jsx", import.meta.url), "utf8");
  for (const action of [
    "document_bulk_export_requested", "document_bulk_export_completed", "document_bulk_export_failed",
    "document_bulk_export_download_started", "document_bulk_export_expired", "document_bulk_export_deleted",
  ]) assert.match(source, new RegExp(action));
});

test("document and export errors use the stable storage-full code for localization", async () => {
  const [documents, admin, ru, en] = await Promise.all([
    readFile(new URL("../src/components/DocumentList.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/AdminPanel.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/i18n/locales/ru.js", import.meta.url), "utf8"),
    readFile(new URL("../src/i18n/locales/en.js", import.meta.url), "utf8"),
  ]);
  assert.match(documents, /doc\.error_code/);
  assert.match(admin, /job\.result\?\.error_code/);
  assert.match(ru, /"apiError\.storage_full"/);
  assert.match(en, /"apiError\.storage_full"/);
});

test("upload results stop at storage full and retain a localized reason", async () => {
 const states=[],sent=[];
 await runDocumentUploadBatch([{name:"a.pdf"},{name:"b.pdf"}],{
  uploadOne:async file=>{sent.push(file.name);throw Object.assign(new Error(),{code:"storage_full",status:507});},
  onItemState:(file,state,details)=>states.push({name:file.name,state,...details}),
 });
 assert.deepEqual(sent,["a.pdf"]);
 assert.equal(states.find(x=>x.name === "a.pdf" && x.state === "failed").errorCode,"storage_full");
 const last=states.at(-1); assert.equal(last.state,"not-sent");
 const {t}=createTranslator("ru",ru);
 assert.equal(friendlyApiError(new ApiError("",{code:last.errorCode}),t),ru["apiError.storage_full"]);
});
