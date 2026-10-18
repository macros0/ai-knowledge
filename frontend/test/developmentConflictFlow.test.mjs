import test from "node:test";
import assert from "node:assert/strict";
import { componentHarness } from "./support/componentHarness.mjs";
import { reconcileReferenceDraft } from "../src/lib/referenceEditing.mjs";
import { createEditorToken } from "../src/lib/editorLeaseSession.mjs";
import { referenceMutationHeaders } from "../src/lib/editorLeaseAdapters.mjs";

const ConflictDialog = () => null;
const original = { id: 7, number: "100", name: "Original", module: "PY", version: 1, reference_revision: 1, documents_count: 0 };
const remote = { ...original, name: "Colleague", version: 2, reference_revision: 2 };
const flush = () => new Promise(setImmediate);

function fixture(editState = "editing") {
  const writes = [];
  const t = key => key;
  const api = {
    listDevelopmentsPage: async () => ({ developments: [original], total: 1 }),
    getAttributeCatalog: async () => ({values: [{value: "PY"}], reference_revision: 1}),
    updateDevelopment: async (id, body) => {
      writes.push({ id, ...body });
      if (body.version === 1) throw Object.assign(new Error("stale"), { code: "version_conflict", data: { current: remote } });
      return { ...remote, ...body, version: body.version + 1 };
    },
    getDevelopment: async () => remote,
  };
  const harness = componentHarness(new URL("../src/components/DevelopmentPanel.jsx", import.meta.url), {
    "@/lib/api": api, "@/lib/apiToast.mjs": { apiToast: () => ({}) },
    "next/link": () => null,
    "@/context/AuthContext": { useAuth: () => ({ mode: "disabled", hasRole: () => true }) },
    "@/lib/constants": { TYPED_CONFIRM_THRESHOLD: 50 },
    "./Toast": { useToast: () => ({ showToast() {} }) },
    "@/i18n/LocaleContext": { useI18n: () => ({ t, locale: "ru" }) },
    "./ConfirmModal": () => null,
    "./ReferenceLocaleSelect": () => null,
    "./SearchableSelect": () => null,
    "./ReferenceConflictDialog": ConflictDialog,
    "@/lib/referenceEditing.mjs": { reconcileReferenceDraft },
    "@/lib/editorLeaseSession.mjs": { createEditorToken },
    "@/lib/editorLeaseAdapters.mjs": { referenceMutationHeaders },
    "@/hooks/useReferenceEditSession": { useReferenceEditSession: () => ({ state: editState, status: null,
      getMutationHeaders: () => ({}), markActivity() {}, acquire() {} }) },
    "./ReferenceEditNotice": () => null,
  }, {}, { window: { confirm: () => true }, setTimeout: () => 1, clearTimeout() {} });
  const button = text => harness.find(node => node.type === "button" && node.props.children === text);
  return { ...harness, writes, button };
}

test("development conflict requires comparison and never retries a reversioned old form", async () => {
  const ui = fixture();
  try {
    ui.render(); await flush(); ui.render();
    ui.button("devpanel.editBtn").props.onClick(); ui.render();
    ui.find(node => node.type === "input" && node.props.value === "100").props.onChange({ target: { value: "101" } });
    ui.render();
    await ui.button("devpanel.saveBtn").props.onClick(); ui.render();
    assert.equal(ui.writes.length, 1);
    const dialog = ui.find(node => node.type === ConflictDialog);
    assert.ok(dialog, "must present an explicit comparison after a version conflict");
    assert.equal(dialog.props.base.name, "Original");
    assert.equal(dialog.props.local.number, "101");
    assert.equal(dialog.props.remote.name, "Colleague");
    assert.equal(ui.button("devpanel.saveBtn").props.disabled, true);
    await ui.button("devpanel.saveBtn").props.onClick();
    assert.equal(ui.writes.length, 1, "even a dispatched stale click must not submit while comparing");
    dialog.props.onConfirm({ number: "101", name: "Colleague", module: "PY" });
    ui.render();
    assert.equal(ui.writes.length, 1, "accepting comparison prepares a draft, not a server write");
    await ui.button("devpanel.saveBtn").props.onClick();
    assert.deepEqual(ui.writes[1], { id: 7, number: "101", name: "Colleague", module: "PY", version: 2, expected_reference_revision: 2 });
  } finally { ui.dispose(); }
});

test("an occupied development editor cannot submit even a dispatched click", async () => {
  const ui = fixture("blocked");
  try {
    ui.render(); await flush(); ui.render();
    ui.button("devpanel.editBtn").props.onClick(); ui.render();
    assert.equal(ui.button("devpanel.saveBtn").props.disabled, true);
    await ui.button("devpanel.saveBtn").props.onClick();
    assert.equal(ui.writes.length, 0);
  } finally { ui.dispose(); }
});
