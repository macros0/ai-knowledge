"use client";

import { useState } from "react";
import { commitGlossaryMerge, friendlyApiError, previewGlossaryMerge } from "@/lib/api";
import { buildGlossaryMergeRequest, normalizeGlossaryIdentity, defaultGlossaryMergeChoice } from "@/lib/glossaryConflicts.mjs";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import { useGlossaryDialogFocus } from "@/lib/useGlossaryDialogFocus";

const FIELDS = ["original_name", "original_description", "canonical_locale", "kind", "infotype_number", "enabled"];

export default function GlossaryMergeDialog({ source: initialSource, target: initialTarget, draft, sourceEdit, targets = [], onTargetChange, onClose, onCompleted, returnFocusTo }) {
  const { t } = useI18n();
  const { showToast } = useToast();
  const [source, setSource] = useState(initialSource);
  const [target, setTarget] = useState(initialTarget);
  const [proposal, setProposal] = useState(null);
  const [selections, setSelections] = useState(() => Object.fromEntries(FIELDS.map((field) => {
    const left = source[field] ?? (field === "enabled" ? true : "");
    const right = target[field] ?? (field === "enabled" ? true : "");
    return [field, defaultGlossaryMergeChoice(left, right)];
  })));
  const [translationChoices, setTranslationChoices] = useState({});
  const [aliasChoices, setAliasChoices] = useState({});
  const [previewAliases, setPreviewAliases] = useState([]);
  const [busy, setBusy] = useState(false);
  const dialogRef = useGlossaryDialogFocus(true, onClose, busy, returnFocusTo);
  const [requestId] = useState(() => crypto.randomUUID());
  const sharedTranslations = (source.translations || []).filter((item) =>
    (target.translations || []).some((other) => other.locale === item.locale));
  const aliasMap = new Map();
  const conflictingAliases = new Map();
  for (const alias of [...(target.aliases || []), ...(source.aliases || [])]) {
    const key = normalizeGlossaryIdentity(alias.alias);
    const previous = aliasMap.get(key);
    if (previous && ["locale", "auto_expand", "search_enabled"].some((field) => previous[field] !== alias[field])) {
      conflictingAliases.set(key, {target: previous, source: alias});
    }
    if (!aliasMap.has(key)) aliasMap.set(key, alias);
  }
  for (const alias of previewAliases) {
    const key = normalizeGlossaryIdentity(alias.alias);
    if (!aliasMap.has(key)) aliasMap.set(key, alias);
  }
  const resultName = selections.original_name === "source" ? source.original_name : target.original_name;
  const resultNameKey = normalizeGlossaryIdentity(resultName);
  const fields = FIELDS.filter((field) => field !== "infotype_number" || source.kind === "sap_infotype" || target.kind === "sap_infotype");
  const needsChoice = fields.some((field) => !selections[field]) ||
    sharedTranslations.some((item) => !translationChoices[item.locale]) ||
    [...conflictingAliases.keys()].some((key) => key !== resultNameKey && !aliasChoices[key]);
  const selectedTranslations = Object.entries(translationChoices).map(([locale, side]) => {
    const owner = side === "source" ? source : target;
    const translated = owner.translations.find((item) => item.locale === locale);
    return {locale, from_term_id: owner.id, expected_version: translated.version};
  });
  const request = (commit = false) => buildGlossaryMergeRequest({
    source, target, draft, sourceEdit, requestId,
    selections: {...selections, translation_choices: selectedTranslations,
      alias_choices: Object.values(aliasChoices).filter((item) => item.normalized_alias !== resultNameKey)},
    proposal: commit ? proposal : null,
  });
  const preview = async () => {
    if (needsChoice) return;
    setBusy(true);
    try {
      const result = await previewGlossaryMerge(request());
      setProposal(result); setPreviewAliases(result.merged?.aliases || []);
    }
    catch (err) { showToast(friendlyApiError(err, t), { type: "error" }); }
    finally { setBusy(false); }
  };
  const commit = async () => {
    if (!proposal) return;
    setBusy(true);
    try { await commitGlossaryMerge(request(true)); onCompleted?.(); }
    catch (err) {
      const fresh = err.data?.preview;
      if (fresh) {
        setSource(fresh.source); setTarget(fresh.target); setProposal(null);
        setSelections(Object.fromEntries(FIELDS.map((field) => [field,
          defaultGlossaryMergeChoice(fresh.source[field] ?? (field === "enabled" ? true : ""),
            fresh.target[field] ?? (field === "enabled" ? true : ""))])));
        setAliasChoices({}); setTranslationChoices({}); setPreviewAliases(fresh.merged?.aliases || []);
      }
      showToast(friendlyApiError(err, t), { type: "error" });
    }
    finally { setBusy(false); }
  };
  const changeAlias = (key, alias, patch) => {
    setProposal(null);
    setAliasChoices((values) => ({...values, [key]: {
      normalized_alias: key, locale: alias.locale || null,
      auto_expand: Boolean(alias.auto_expand), search_enabled: Boolean(alias.search_enabled),
      ...values[key], ...patch,
    }}));
  };
  const fieldValue = (owner, field) => field === "enabled"
    ? t((owner.enabled ?? true) ? "admin.glossary.active" : "admin.glossary.disabled")
    : String(owner[field] ?? "");
  return <div ref={dialogRef} tabIndex={-1} className="modal-backdrop" role="dialog" aria-modal="true" aria-label={t("admin.glossary.mergeTitle")}>
    <div className="modal-card">
      <h3>{t("admin.glossary.mergeTitle")}</h3>
      <p className="muted">{t("admin.glossary.mergeHint")}</p>
      {targets.length > 1 && <label>{t("admin.glossary.mergeWith")}
        <select value={target.id} disabled={busy} onChange={(event) => onTargetChange?.(Number(event.target.value))}>
          {targets.map((item) => <option key={item.id} value={item.id}>{item.original_name}</option>)}
        </select>
      </label>}
      <div className="glossary-merge-fields">{fields.map((field) => <label key={field}>{t(`admin.glossary.mergeField.${field}`)}
        <select disabled={busy} value={selections[field] || ""} onChange={(event) => { setProposal(null); setSelections((value) => ({ ...value, [field]: event.target.value })); }}>
          <option value="">{t("admin.glossary.mergeChooseValue")}</option>
          <option value="target">{t("admin.glossary.mergeTarget")}: {fieldValue(target, field)}</option>
          <option value="source">{t("admin.glossary.mergeSource")}: {fieldValue(source, field)}</option>
        </select>
      </label>)}</div>
      {sharedTranslations.map((item) => <label className="glossary-field" key={item.locale}>
        {t("admin.glossary.mergeTranslation")} ({item.locale})
        <select disabled={busy} value={translationChoices[item.locale] || ""} onChange={(event) => {
          setProposal(null); setTranslationChoices((value) => ({...value, [item.locale]: event.target.value}));
        }}>
          <option value="">{t("admin.glossary.mergeChooseValue")}</option>
          <option value="target">{target.translations.find((other) => other.locale === item.locale).display_name}</option>
          <option value="source">{item.display_name}</option>
        </select>
      </label>)}
      {Array.from(aliasMap, ([key, alias]) => {
        const value = aliasChoices[key] || alias;
        if (key === resultNameKey) return null;
        return <div className="glossary-alias-row" key={key}>
          <span>{alias.alias}</span>
          {conflictingAliases.has(key) && !aliasChoices[key] && <select disabled={busy} value="" onChange={(event) => {
            const selected = conflictingAliases.get(key)[event.target.value];
            changeAlias(key, selected, {});
          }}>
            <option value="">{t("admin.glossary.mergeChooseValue")}</option>
            <option value="target">{t("admin.glossary.mergeTarget")}</option>
            <option value="source">{t("admin.glossary.mergeSource")}</option>
          </select>}
          <ReferenceLocaleSelect value={value.locale} disabled={busy} onChange={(locale) => changeAlias(key, alias, {locale})} />
          <label><input type="checkbox" disabled={busy} checked={Boolean(value.auto_expand)} onChange={(event) => changeAlias(key, alias, {auto_expand: event.target.checked})} />{t("admin.glossary.autoExpand")}</label>
          <label><input type="checkbox" disabled={busy} checked={Boolean(value.search_enabled)} onChange={(event) => changeAlias(key, alias, {search_enabled: event.target.checked})} />{t("admin.glossary.searchEnabled")}</label>
        </div>;
      })}
      <div className="modal-actions">
        <button type="button" className="modal-btn" disabled={busy} onClick={onClose}>{t("common.cancel")}</button>
        <button type="button" className="modal-btn" disabled={busy || needsChoice} onClick={preview}>{t("admin.glossary.mergePreview")}</button>
        {proposal && <button type="button" className="modal-btn" disabled={busy || proposal.unresolved_fields?.length > 0 || proposal.conflicts?.length > 0} onClick={commit}>{t("admin.glossary.mergeCommit")}</button>}
      </div>
      {proposal && <div className="glossary-merge-preview" role="status">
        <strong>{t("admin.glossary.mergeResult")}</strong>
        {proposal.conflicts?.length > 0 && <div role="alert">
          <p>{t("apiError.glossary_identity_conflict")}</p>
          <ul>{proposal.conflicts.map((item, index) => <li key={index}>{item.key_value} — {item.name || item.term_id || item.left_term_id}</li>)}</ul>
        </div>}
        <p>{proposal.merged?.original_name} ({proposal.merged?.canonical_locale})</p>
        <p>{t("admin.glossary.mergeField.enabled")}: {fieldValue(proposal.merged || {}, "enabled")}</p>
        {proposal.merged?.infotype_number && <p>{t("admin.glossary.mergeField.infotype_number")}: {proposal.merged.infotype_number}</p>}
        {proposal.merged?.original_description && <p>{proposal.merged.original_description}</p>}
        {proposal.merged?.aliases?.length > 0 && <p>{t("admin.glossary.mergeAliases")}: {proposal.merged.aliases.map((item) => item.alias).join(", ")}</p>}
        {(proposal.merged?.translations || []).map((item) => <p key={item.locale}>
          {item.locale}: {item.display_name} — {item.description}
          {item.source_revision !== proposal.merged.source_revision && <span> ({t("admin.glossary.needsReview")})</span>}
        </p>)}
      </div>}
    </div>
  </div>;
}
