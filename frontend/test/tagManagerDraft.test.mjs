import test from "node:test";
import assert from "node:assert/strict";
import { componentHarness } from "./support/componentHarness.mjs";
import { chunkReferenceCards } from "../src/lib/referenceEditing.mjs";

test("filtering, reload and attempted close retain an open dirty tag draft", async () => {
  const Child = () => null, t = key => key;
  const tag = { id: 1, name: "tag", display: "tag", canonical_locale: "ru", count: 0,
    reference_revision: 1, translations: [], needs_review: false };
  let records = [tag], closed = 0;
  const ui = componentHarness(new URL("../src/components/TagManagerModal.jsx", import.meta.url), {
    "@/lib/api": { listTags: async () => records }, "@/lib/apiToast.mjs": { apiToast: () => ({}) },
    "@/lib/tagDictionary": { bumpTagVersion() {} }, "./Toast": { useToast: () => ({ showToast() {} }) },
    "@/i18n/LocaleContext": { useI18n: () => ({ t, tc: t, locale: "en" }) },
    "./icons": { PencilIcon: () => null, TrashIcon: () => null },
    "./TagTranslationEditor": Child, "./Modal": () => null, "./ReferenceBatchResult": () => null,
    "@/lib/referenceEditing.mjs": { chunkReferenceCards },
  }, { onClose: () => closed++ }, { window: { confirm: () => false } });
  try {
    ui.render(); await new Promise(setImmediate); ui.render();
    ui.find(n => n.props?.className === "tag-manager-expand").props.onClick(); ui.render();
    ui.find(n => n.type === Child).props.onDirtyChange(1, true);
    ui.find(n => n.props?.className === "tag-manager-search").props.onChange({ target: { value: "no match" } }); ui.render();
    let editor = ui.find(n => n.type === Child);
    assert.ok(editor, "a dirty card must remain mounted when the filter no longer matches");
    records = []; await editor.props.onSaved(); ui.render();
    assert.ok(ui.find(n => n.type === Child), "a missing server record must not discard a local draft");
    ui.find(n => n.props?.className === "tag-manager-close").props.onClick();
    assert.equal(closed, 0);
  } finally { ui.dispose(); }
});
