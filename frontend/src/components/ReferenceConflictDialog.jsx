"use client";

import { useState } from "react";
import { reconcileReferenceDraft } from "@/lib/referenceEditing.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import Modal from "./Modal";

export default function ReferenceConflictDialog({ base, local, remote, fields, labels, onConfirm, onClose }) {
  const { t } = useI18n();
  const [choices, setChoices] = useState({});
  const { merged, conflicts } = reconcileReferenceDraft({ base, local, remote, fields });
  const conflictFields = new Set(conflicts.map(item => item.field));
  const unresolved = conflicts.some(item => !Object.hasOwn(choices, item.field));
  const accept = () => {
    if (unresolved) return;
    const result = { ...merged };
    for (const conflict of conflicts) result[conflict.field] = conflict[choices[conflict.field]];
    onConfirm(result);
  };
  const display = value => value == null || value === "" ? "—" : String(value);
  return <Modal title={t("reference.conflictTitle")} onClose={onClose} footer={<>
    <button type="button" className="modal-btn" onClick={onClose}>{t("common.cancel")}</button>
    <button type="button" className="modal-btn" disabled={unresolved} onClick={accept}>{t("reference.acceptComparison")}</button>
  </>}>
    <p>{t("reference.conflictDescription")}</p>
    <div className="reference-conflict-fields">
      {fields.map(field => <section key={field} className="reference-conflict-field">
        <h3>{labels[field]}</h3>
        <dl>
          <dt>{t("reference.base")}</dt><dd>{display(base[field])}</dd>
          <dt>{t("reference.local")}</dt><dd>{display(local[field])}</dd>
          <dt>{t("reference.remote")}</dt><dd>{display(remote[field])}</dd>
        </dl>
        {conflictFields.has(field) ? <label>{t("reference.chooseValue")}
          <select value={choices[field] || ""} onChange={event => setChoices(previous => ({ ...previous, [field]: event.target.value }))}>
            <option value="" disabled>{t("reference.chooseValue")}</option>
            <option value="local">{t("reference.keepLocal")}</option>
            <option value="remote">{t("reference.keepRemote")}</option>
          </select>
        </label> : <p>{t("reference.result")}: <strong>{display(merged[field])}</strong></p>}
      </section>)}
    </div>
  </Modal>;
}
