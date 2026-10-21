import test from "node:test";
import assert from "node:assert/strict";
import {componentHarness} from "./support/componentHarness.mjs";
import * as api from "../src/lib/api.js";
import ru from "../src/i18n/locales/ru.js";
import en from "../src/i18n/locales/en.js";

const flush = () => new Promise(setImmediate);
const config = mode => ({effective_mode: mode, db_enabled: mode !== "external",
  stdout_mode: mode === "db" ? "disabled" : "durable", stdout_format: "native"});
const saved = {id: 7, created_at: "2026-10-20T10:00:00Z", username: "saved-user", user_id: 1,
  action_type: "auth_login_success", target_type: null, target_id: null, ip_address: "127.0.0.1"};

function fixture({locale = "en", getConfig = async () => config("db"), entries = [saved]} = {}) {
  const dictionary = locale === "ru" ? ru : en;
  const t = key => dictionary[key] ?? key;
  const showToast = () => {};
  const ui = componentHarness(new URL("../src/components/SecurityPanel.jsx", import.meta.url), {
    "@/lib/api": {getAuditConfig: getConfig, listAudit: async () => entries,
      listAuditActionTypes: async () => [], listAuditUsers: async () => [], listBlocks: async () => [],
      blockUser: async () => {}, unblockUser: async () => {}, friendlyApiError: () => "Error"},
    "@/lib/apiToast.mjs": {apiToast: () => ({})},
    "./Toast": {useToast: () => ({showToast})},
    "@/i18n/LocaleContext": {useI18n: () => ({t, fmtDateTime: value => value})},
  });
  const hasText = text => Boolean(ui.find(node => node.props?.children === text));
  return {...ui, hasText, t};
}

test("audit config uses an uncached protected request and leaves the list DTO unchanged", async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({url, init});
    return Response.json(url === "/api/audit/config" ? config("external") : {entries: [saved]});
  };
  try {
    assert.equal(typeof api.getAuditConfig, "function");
    assert.deepEqual(await api.getAuditConfig(), config("external"));
    assert.equal(calls[0].url, "/api/audit/config");
    assert.equal(calls[0].init.cache, "no-store");
    assert.deepEqual(await api.listAudit({limit: 200}), [saved]);
  } finally {globalThis.fetch = original;}
});

for (const locale of ["ru", "en"]) {
  for (const mode of ["db", "external", "both"]) {
    test(`${locale}: loaded ${mode} mode keeps saved history and block controls`, async () => {
      const ui = fixture({locale, getConfig: async () => config(mode)});
      try {
        ui.render(); await flush(); ui.render();
        assert.ok(ui.hasText(ui.t(`security.auditMode.${mode}`)));
        assert.ok(ui.hasText("saved-user"));
        assert.ok(ui.hasText(ui.t("security.blocks")));
        assert.ok(ui.hasText(ui.t("security.exportCsv")));
        assert.match(ui.t("security.exportCsv"), locale === "ru" ? /локальн/i : /local/i);
        assert.match(ui.t("security.anomalies"), locale === "ru" ? /локальн/i : /local/i);
        assert.equal(ui.hasText(ui.t("security.auditExternalHistory")), mode === "external");
        assert.equal(ui.hasText(ui.t("security.auditStdoutBoundary")), mode !== "db");
      } finally {ui.dispose();}
    });
  }
}

test("loading config never announces DB mode before the response", async () => {
  let resolve;
  const ui = fixture({getConfig: () => new Promise(done => {resolve = done;})});
  try {
    ui.render(); await flush(); ui.render();
    assert.ok(ui.hasText(ui.t("security.auditMode.loading")));
    assert.equal(ui.hasText(ui.t("security.auditMode.db")), false);
    resolve(config("both")); await flush(); ui.render();
    assert.ok(ui.hasText(ui.t("security.auditMode.both")));
  } finally {ui.dispose();}
});

for (const [state, getConfig] of [
  ["request failure", async () => {throw new Error("unavailable");}],
  ["empty DTO", async () => ({})],
  ["future mode", async () => ({...config("db"), effective_mode: "future"})],
  ["inconsistent sinks", async () => ({...config("external"), db_enabled: true})],
  ["inherited mode name", async () => ({effective_mode: "__proto__", stdout_format: "native"})],
  ["null DTO", async () => null],
]) {
  test(`${state}: config is unknown and local history remains available`, async () => {
    const ui = fixture({getConfig});
    try {
      ui.render(); await flush(); ui.render();
      assert.ok(ui.hasText(ui.t("security.auditMode.unknown")));
      assert.ok(ui.hasText("saved-user"));
      assert.ok(ui.hasText(ui.t("security.blocks")));
      assert.equal(ui.hasText(ui.t("security.auditMode.db")), false);
    } finally {ui.dispose();}
  });
}

test("external-only empty history keeps its explanation and working block form", async () => {
  const ui = fixture({entries: [], getConfig: async () => config("external")});
  try {
    ui.render(); await flush(); ui.render();
    assert.ok(ui.hasText(ui.t("security.noRecords")));
    assert.ok(ui.hasText(ui.t("security.auditExternalHistory")));
    assert.ok(ui.find(node => node.type === "form" && node.props.className === "block-form"));
    assert.equal(ui.find(node => node.type === "button" && node.props.children === ui.t("security.exportCsv")).props.disabled, true);
  } finally {ui.dispose();}
});
