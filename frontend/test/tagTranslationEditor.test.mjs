import test from "node:test";
import assert from "node:assert/strict";
import { componentHarness } from "./support/componentHarness.mjs";

const Dialog = () => null;
const tag = { id: 1, reference_revision: 1, canonical_locale: "ru", translations: [{ locale: "en", text: "Original" }] };
const remote = { ...tag, reference_revision: 2, translations: [{ locale: "en", text: "Colleague" }] };
function fixture(state) {
  const writes = [], dirty = [];
  const t = key => key;
  const props = { tag, locale: "en", onSaved() {}, onDirtyChange: (id, value) => dirty.push([id, value]) };
  const ui = componentHarness(new URL("../src/components/TagTranslationEditor.jsx", import.meta.url), {
    "@/lib/api": { listTags: async () => [remote], updateTagTranslation: async (...args) => {
      writes.push(args); throw Object.assign(new Error("conflict"), { code: "version_conflict" }); } },
    "@/lib/apiToast.mjs": { apiToast: () => ({}) },
    "@/hooks/useReferenceEditSession": { useReferenceEditSession: () => ({ state, status: null,
      getMutationHeaders: () => ({}), markActivity() {}, acquire() {} }) },
    "@/i18n/LocaleContext": { useI18n: () => ({ t }) },
    "./Toast": { useToast: () => ({ showToast() {} }) },
    "./ReferenceConflictDialog": Dialog, "./ReferenceEditNotice": () => null,
  }, props);
  return { ...ui, props, writes, dirty, button: key => ui.find(n => n.type === "button" && n.props.children === key) };
}

test("tag translation retains draft and revision until explicit comparison acceptance", async () => {
  const ui = fixture("editing");
  try {
    ui.render(); ui.button("reference.startEditing").props.onClick(); ui.render();
    ui.find(n => n.type === "input").props.onChange({ target: { value: "My draft" } }); ui.render();
    await ui.button("tags.manager.save").props.onClick(); ui.render();
    assert.equal(ui.writes[0][3].expected_reference_revision, 1);
    const conflict = ui.find(n => n.type === Dialog);
    assert.equal(conflict.props.local.text, "My draft");
    assert.equal(conflict.props.remote.text, "Colleague");
    assert.equal(ui.find(n => n.type === "input").props.value, "My draft");
    conflict.props.onConfirm({ text: "My draft" }); ui.render();
    assert.equal(ui.writes.length, 1, "acceptance must not write the draft automatically");
  } finally { ui.dispose(); }
});

test("lost access preserves readable text and prevents a late save click", async () => {
  const ui = fixture("lost");
  try {
    ui.render(); ui.button("reference.startEditing").props.onClick(); ui.render();
    assert.equal(ui.find(n => n.type === "input").props.value, "Original");
    assert.equal(ui.find(n => n.type === "input").props.readOnly, true);
    assert.equal(ui.button("tags.manager.save").props.disabled, true);
    await ui.button("tags.manager.save").props.onClick();
    assert.equal(ui.writes.length, 0);
  } finally { ui.dispose(); }
});

test("changing the UI language does not redirect an open translation draft", async () => {
  const ui = fixture("editing");
  try {
    ui.render(); ui.button("reference.startEditing").props.onClick(); ui.render();
    ui.find(n => n.type === "input").props.onChange({ target: { value: "My English draft" } });
    ui.render({ ...ui.props, locale: "de" });
    await ui.button("tags.manager.save").props.onClick();
    assert.equal(ui.writes[0][1], "en");
    assert.equal(ui.writes[0][2], "My English draft");
  } finally { ui.dispose(); }
});
