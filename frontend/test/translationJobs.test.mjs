import test from "node:test";
import assert from "node:assert/strict";
import {backfillTranslations, backfillGlossaryTranslations, ApiError} from "../src/lib/api.js";
import {componentHarness} from "./support/componentHarness.mjs";

test("translation API waits for the accepted durable job and returns its counters", async () => {
  const original = globalThis.fetch, calls = [];
  const result = {locale: "en", result: {tags: {created: 2, failed: 0}}};
  globalThis.fetch = async (url) => {
    calls.push(url);
    return Response.json(url.includes("/jobs/")
      ? {id: 42, job_type: "reference_translation", status: "completed", result}
      : {job_id: 42, status: "queued", locale: "en"});
  };
  try {
    assert.deepEqual(await backfillTranslations("en", ["tags"]), result);
    assert.deepEqual(calls, ["/api/tags/translations/backfill", "/api/jobs/42"]);
  } finally {globalThis.fetch = original;}
});

test("failed translation jobs return a stable error without raw provider text", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url) => Response.json(url.includes("/jobs/")
    ? {id: 42, job_type: "reference_translation", status: "failed", error: "PRIVATE_PROVIDER_TEXT", result: {error_code: "dependency_unavailable"}}
    : {job_id: 42, status: "queued"});
  try {
    await assert.rejects(backfillTranslations("en", ["tags"]), error =>
      error instanceof ApiError && error.code === "dependency_unavailable" && !error.message.includes("PRIVATE"));
  } finally {globalThis.fetch = original;}
});

test("glossary translation returns the completed job's original counter shape", async () => {
  const original = globalThis.fetch;
  const result = {locale: "en", requested: 1, created: 1, failed: 0, status: "completed"};
  globalThis.fetch = async (url) => Response.json(url.includes("/jobs/")
    ? {id: 43, job_type: "glossary_translation", status: "completed", result}
    : {job_id: 43, status: "queued"});
  try {assert.deepEqual(await backfillGlossaryTranslations("en", [1]), result);}
  finally {globalThis.fetch = original;}
});

for (const jobType of ["reference_translation", "glossary_translation", "bulk_export", "bulk_resume"]) {
test(`system operations names ${jobType} and exposes only supported actions`, async () => {
  const ui = componentHarness(new URL("../src/components/AdminPanel.jsx", import.meta.url), {
    "@/lib/api": {listJobs: async () => [{id: 42, job_type: jobType, status: "queued", params: {locale: "en"}}]},
    "@/lib/apiToast.mjs": {apiToast: () => ({})},
    "@/i18n/LocaleContext": {useI18n: () => ({t: key => key})},
    "@/context/ChatContext": {useChat: () => ({settings: {}})},
    "./Toast": {useToast: () => ({showToast: () => {}})},
    "next/link": () => null,
  });
  try {
    ui.render();
    await new Promise(setImmediate);
    ui.render();
    assert.ok(ui.find(node => node.type === "strong" && node.props.children === `admin.jobType.${jobType}`));
    assert.equal(Boolean(ui.find(node => node.type === "button" && node.props.children === "admin.cancel")), jobType === "bulk_resume");
  } finally {ui.dispose();}
});
}
