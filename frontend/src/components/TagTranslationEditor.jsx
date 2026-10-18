"use client";

import { useEffect, useState } from "react";
import { listTags, updateTagTranslation } from "@/lib/api";
import { apiToast } from "@/lib/apiToast.mjs";
import { useReferenceEditSession } from "@/hooks/useReferenceEditSession";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";
import ReferenceConflictDialog from "./ReferenceConflictDialog";
import ReferenceEditNotice from "./ReferenceEditNotice";

const translationText = (tag, locale) => tag.translations.find(item => item.locale === locale)?.text || "";

export default function TagTranslationEditor({ tag, locale, onSaved, onDirtyChange }) {
  const { t } = useI18n();
  const { showToast } = useToast();
  const [editing, setEditing] = useState(false), [busy, setBusy] = useState(false);
  const [base, setBase] = useState(tag), [text, setText] = useState(() => translationText(tag, locale));
  const [editorLocale, setEditorLocale] = useState(locale);
  const [conflict, setConflict] = useState(null);
  const session = useReferenceEditSession({ resource: { resource_type: "tag", resource_id: String(tag.id) }, enabled: editing,
    onAcquired: async (_status, isCurrent) => {
      const fresh = (await listTags()).find(item => item.id === tag.id);
      if (!fresh) throw Object.assign(new Error("Tag removed"), {code: "not_found"});
      if (isCurrent() && fresh.reference_revision !== base.reference_revision) {
        setConflict({snapshot: fresh, base: {text: translationText(base, editorLocale)},
          local: {text}, remote: {text: translationText(fresh, editorLocale)}});
      }
    }});
  const current = tag.reference_revision >= base.reference_revision ? tag : base;
  const targetLocale = editing ? editorLocale : locale;
  const dirty = editing && text !== translationText(base, editorLocale);
  useEffect(() => { onDirtyChange?.(tag.id, dirty); }, [tag.id, dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(tag.id, false), [tag.id, onDirtyChange]);
  const save = async () => {
    if (busy || conflict || !editing || session.state !== "editing" || !text.trim()) return;
    setBusy(true);
    const submitted = text.trim();
    try {
      const saved = await updateTagTranslation(tag.id, editorLocale, submitted, {
        expected_reference_revision: base.reference_revision, headers: session.getMutationHeaders() });
      setBase({ ...base, reference_revision: saved.reference_revision,
        translations: [...base.translations.filter(item => item.locale !== editorLocale), { locale: editorLocale, text: submitted }] });
      setText(submitted); setEditing(false); onDirtyChange?.(tag.id, false);
      showToast(t("tags.manager.translationSaved"), { type: "success" });
      await onSaved?.();
    } catch (error) {
      session.handleMutationError?.(error);
      if (error.code === "version_conflict") {
        try {
          const remote = (await listTags()).find(item => item.id === tag.id);
          if (remote) setConflict({ snapshot: remote, base: { text: translationText(base, editorLocale) },
            local: { text: submitted }, remote: { text: translationText(remote, editorLocale) } });
        } catch { /* Keep the draft and its old revision; retry cannot overwrite new data. */ }
      }
      showToast(apiToast(error, t, { type: "error" }));
    } finally { setBusy(false); }
  };
  const cancel = () => {
    if (busy || (dirty && !window.confirm(t("admin.glossary.unsavedChangesConfirm")))) return;
    setText(translationText(base, editorLocale)); setEditing(false); setConflict(null);
  };
  if (!editing && locale === tag.canonical_locale) return null;
  return <div onPointerDownCapture={session.markActivity} onKeyDownCapture={session.markActivity} onScrollCapture={session.markActivity}>
    {editing && <ReferenceEditNotice session={session} onCopy={() => navigator.clipboard?.writeText(text).catch(() => {})} />}
    <div className="tag-manager-edit">
      <input id={`tag-tr-${tag.id}`} value={editing ? text : translationText(current, locale)} readOnly={!editing || busy || Boolean(conflict) || session.state !== "editing"}
        onChange={event => setText(event.target.value)} aria-label={t("tags.manager.editPlaceholder", { locale: targetLocale })}
        placeholder={t("tags.manager.editPlaceholder", { locale: targetLocale })} />
      {!editing ? <button type="button" className="bulk-tag-btn" disabled={busy} onClick={() => {
        setBase(current); setEditorLocale(locale); setText(translationText(current, locale)); setEditing(true);
      }}>{t("reference.startEditing")}</button> : <>
        <button type="button" className="bulk-tag-btn" onClick={save}
          disabled={busy || Boolean(conflict) || session.state !== "editing" || !text.trim()}>{t("tags.manager.save")}</button>
        <button type="button" className="bulk-tag-btn" disabled={busy} onClick={cancel}>{t("common.cancel")}</button>
      </>}
    </div>
    {conflict && <ReferenceConflictDialog key={conflict.snapshot.reference_revision} {...conflict}
      fields={["text"]} labels={{ text: t("reference.local") }} onClose={() => setConflict(null)}
      onConfirm={merged => { setBase(conflict.snapshot); setText(merged.text); setConflict(null); }} />}
  </div>;
}
