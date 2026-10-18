import test from "node:test";
import assert from "node:assert/strict";
import {componentHarness} from "./support/componentHarness.mjs";

test("module conflict refreshes the catalog but retains input for an explicit retry", async () => {
  const writes = []; let revision = 5;
  const t = key => key, noop = () => null;
  const ui = componentHarness(new URL("../src/components/DevelopmentPanel.jsx", import.meta.url), {
    "@/lib/api": {listDevelopmentsPage: async () => ({developments: [], total: 0}),
      getAttributeCatalog: async () => ({values: [{value: "PY"}], reference_revision: revision}),
      listAttributeValues: async () => [{value: "PY"}],
      addAttributeValue: async (key, value, options) => {writes.push({key, value, ...options});
        if (writes.length === 1) {revision = 6; throw Object.assign(new Error("stale"), {code: "version_conflict"});}
        return {value, reference_revision: 7};}, friendlyApiError: () => "stale"},
    "@/lib/apiToast.mjs": {apiToast: () => ({})}, "next/link": noop,
    "@/context/AuthContext": {useAuth: () => ({mode: "disabled", hasRole: () => true})},
    "@/lib/constants": {TYPED_CONFIRM_THRESHOLD: 50}, "./Toast": {useToast: () => ({showToast() {}})},
    "@/i18n/LocaleContext": {useI18n: () => ({t, locale: "ru"})},
    "./ConfirmModal": noop, "./ReferenceLocaleSelect": noop, "./SearchableSelect": noop,
    "./ReferenceConflictDialog": noop, "./ReferenceEditNotice": noop,
    "@/lib/referenceEditing.mjs": {}, "@/lib/editorLeaseSession.mjs": {}, "@/lib/editorLeaseAdapters.mjs": {},
    "@/hooks/useReferenceEditSession": {useReferenceEditSession: () => ({state: "viewing"})},
  }, {}, {window: {confirm: () => true}, setTimeout: () => 1, clearTimeout() {}});
  try {
    ui.render(); await new Promise(setImmediate); ui.render();
    ui.find(node => node.type === "button" && node.props.className === "dev-spoiler-toggle").props.onClick(); ui.render();
    ui.find(node => node.type === "input" && node.props.placeholder === "devpanel.addModulePlaceholder").props.onChange({target: {value: "ZZ"}}); ui.render();
    const submit = () => ui.find(node => node.type === "form" && node.props.className === "dev-module-add").props.onSubmit({preventDefault() {}});
    await submit(); ui.render();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].expected_reference_revision, 5);
    assert.equal(ui.find(node => node.type === "input" && node.props.placeholder === "devpanel.addModulePlaceholder").props.value, "ZZ");
    await submit(); ui.render();
    assert.equal(writes[1].expected_reference_revision, 6);
    assert.equal(ui.find(node => node.type === "input" && node.props.placeholder === "devpanel.addModulePlaceholder").props.value, "");
  } finally {ui.dispose();}
});
