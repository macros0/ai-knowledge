"use client";

import { useRef, useState } from "react";
import Modal from "./Modal";
import TagCombobox from "./TagCombobox";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import { bulkUpdateTags, friendlyApiError } from "@/lib/api";
import { buildBulkTagChange } from "@/lib/bulkTags.mjs";
import { bumpTagVersion } from "@/lib/tagDictionary";
import { useI18n } from "@/i18n/LocaleContext";

export default function BulkTagsModal({ docIds, onClose, onDone }) {
  const { t, locale } = useI18n();
  const [ids] = useState(() => [...docIds]);
  const [addTag, setAddTag] = useState("");
  const [removeTag, setRemoveTag] = useState("");
  const [tagLocale, setTagLocale] = useState(locale);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [error, setError] = useState(null);
  const [portalContainer, setPortalContainer] = useState(null);
  const payload = buildBulkTagChange({ addTag, removeTag, canonicalLocale: tagLocale });
  const conflict = addTag.trim() && addTag.trim() === removeTag.trim();
  const close = () => { if (!submitting.current) onClose(); };
  const apply = async () => {
    if (!payload || !ids.length || submitting.current) return;
    submitting.current = true;
    setBusy(true); setError(null);
    try {
      const result = await bulkUpdateTags(ids, payload);
      bumpTagVersion();
      onDone(result);
    } catch (err) {
      setError(friendlyApiError(err, t));
      submitting.current = false; setBusy(false);
    }
  };
  return <Modal title={t("selection.editTags")} onClose={close} footer={<>
    <button type="button" className="modal-btn" onClick={close} disabled={busy}>{t("common.cancel")}</button>
    <button type="button" className="modal-btn primary" onClick={apply} disabled={busy || !payload}>{t("selection.apply")}</button>
  </>}>
    <div ref={setPortalContainer} className="bulk-tags-form">
      <p>{t("selection.tagsTarget", { count: ids.length })}</p>
      <label>{t("selection.addPlaceholder")}<TagCombobox value={addTag} onChange={setAddTag} disabled={busy}
        ariaLabel={t("selection.addAria")} className="bulk-tag-input" portalContainer={portalContainer} /></label>
      <ReferenceLocaleSelect value={tagLocale} onChange={setTagLocale} disabled={busy} portalContainer={portalContainer}
        labelKey="selection.tagLanguage" />
      <label>{t("selection.removePlaceholder")}<TagCombobox value={removeTag} onChange={setRemoveTag} disabled={busy}
        allowNew={false} ariaLabel={t("selection.removeAria")} className="bulk-tag-input" portalContainer={portalContainer} /></label>
      {conflict && <p role="alert">{t("selection.tagConflict")}</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  </Modal>;
}
