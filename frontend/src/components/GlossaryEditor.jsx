"use client";

import { useEffect, useRef, useState } from "react";
import { addGlossaryAlias, deleteGlossaryAlias, friendlyApiError, getGlossaryTerm, updateGlossaryAlias, updateGlossarySource } from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import GlossaryTranslations from "./GlossaryTranslations";
import GlossaryPreview from "./GlossaryPreview";
import GlossaryAliasCheck, { GlossaryConflicts, GlossaryCheckResult, useGlossaryAliasCheck } from "./GlossaryAliasCheck";
import { conflictSummary, glossaryDraftPayload, shouldOfferMerge, hasPendingGlossaryEdits, reconcileGlossaryDraft, reconcileGlossaryAliasDrafts } from "@/lib/glossaryConflicts.mjs";
import GlossaryMergeDialog from "./GlossaryMergeDialog";

const emptyAlias = { alias: "", locale: null, auto_expand: false, search_enabled: false };
const KINDS = ["sap_infotype", "sap_transaction", "sap_table", "sap_program", "sap_object", "abbreviation", "business_term"];

const aliasDraftsFor = (aliases) => Object.fromEntries(
  (aliases || []).map((alias) => [alias.id, {
    alias: alias.alias,
    locale: alias.locale,
    auto_expand: alias.auto_expand,
    search_enabled: alias.search_enabled,
  }]),
);

export default function GlossaryEditor({ term, canManage = false, onSaved, onClose, onOpenTerm, onPendingAliasChange, onDraftChange, onMergeCompleted }) {
  const { t, locale } = useI18n();
  const { showToast } = useToast();
  const [name, setName] = useState(term.original_name);
  const [description, setDescription] = useState(term.original_description || "");
  const [canonicalLocale, setCanonicalLocale] = useState(term.canonical_locale);
  const [enabled, setEnabled] = useState(term.enabled);
  const [kind, setKind] = useState(term.kind);
  const [infotypeNumber, setInfotypeNumber] = useState(term.infotype_number || "");
  const [aliasDraft, setAliasDraft] = useState(emptyAlias);
  const [aliasDrafts, setAliasDrafts] = useState(() => aliasDraftsFor(term.aliases));
  const [busy, setBusy] = useState(false);
  const [mergeTarget, setMergeTarget] = useState(null);
  const [mergeTargets, setMergeTargets] = useState([]);
  const [mergeDecline, setMergeDecline] = useState(null);
  const nameCheck = useGlossaryAliasCheck(canManage ? name : "", term.id, {kind, infotypeNumber, revision: term.version});
  const nameConflicted = Boolean(nameCheck?.conflicts?.length);
  const previousTerm = useRef(term);

  useEffect(() => {
    const source = {kind, original_name: name, original_description: description, canonical_locale: canonicalLocale, enabled,
      infotype_number: kind === "sap_infotype" ? infotypeNumber : null};
    const pending = hasPendingGlossaryEdits(term, source, aliasDrafts, aliasDraft);
    onPendingAliasChange?.(pending);
    const aliases = term.aliases.map((item) => aliasDrafts[item.id] || item);
    if (aliasDraft.alias.trim()) aliases.push({...aliasDraft, locale: aliasDraft.locale || locale});
    onDraftChange?.(pending ? glossaryDraftPayload(source, aliases) : null);
  }, [term, kind, name, description, canonicalLocale, enabled, infotypeNumber, aliasDrafts, aliasDraft, locale, onPendingAliasChange, onDraftChange]);

  useEffect(() => {
    // A save refreshes only unchanged fields; other sections retain their drafts.
    const before = previousTerm.current;
    const refresh = (key, setter) => setter((value) => reconcileGlossaryDraft({[key]: value}, before, term)[key]);
    refresh("original_name", setName); refresh("original_description", setDescription);
    refresh("canonical_locale", setCanonicalLocale); refresh("enabled", setEnabled);
    refresh("kind", setKind); refresh("infotype_number", setInfotypeNumber);
    setAliasDrafts((current) => reconcileGlossaryAliasDrafts(current, before.aliases, term.aliases));
    previousTerm.current = term;
  }, [term]);

  const offerConflictMerge = async (err, decline) => {
    if (!shouldOfferMerge(err.data || err)) return false;
    const ids = [...new Set(conflictSummary(err.data || err).map((item) => item.termId).filter((id) => id && id !== term.id))];
    if (!ids.length) return false;
    try {
      const targets = await Promise.all(ids.map(getGlossaryTerm));
      setMergeDecline(decline);
      setMergeTargets(targets);
      setMergeTarget(targets[0]);
      return true;
    } catch (loadError) {
      showToast(friendlyApiError(loadError, t), { type: "error" });
      return false;
    }
  };
  const apply = async (fn, decline, onSuccess) => {
    const returnFocusTo = document.activeElement;
    setBusy(true);
    try {
      const updated = await fn();
      onSuccess?.(updated);
      onSaved(updated);
      showToast(t(updated.has_duplicates ? "admin.glossary.savedWithDuplicates" : "admin.glossary.saved"), { type: updated.has_duplicates ? "warning" : "success" });
      return true;
    }
    catch (err) {
      if (!(await offerConflictMerge(err, {...decline, returnFocusTo}))) showToast(friendlyApiError(err, t), { type: "error" });
      return false;
    }
    finally { setBusy(false); }
  };
  const currentSourceEdit = () => glossaryDraftPayload({...term, kind, original_name: name, original_description: description || null,
    canonical_locale: canonicalLocale, enabled, infotype_number: infotypeNumber},
  [...term.aliases.map((item) => aliasDrafts[item.id] || item), ...(aliasDraft.alias.trim() ? [{...aliasDraft, locale: aliasDraft.locale || locale}] : [])]);
  const saveSource = () => {
    if (busy || nameConflicted) return;
    const submitted = {kind, original_name: name, original_description: description, canonical_locale: canonicalLocale, enabled, infotype_number: infotypeNumber};
    return apply(() => updateGlossarySource(term.id, { version: term.version, kind, original_name: name, original_description: description || null, canonical_locale: canonicalLocale, enabled, infotype_number: kind === "sap_infotype" ? infotypeNumber : null }),
      {sourceEdit: currentSourceEdit()}, (updated) => {
        for (const [key, setter] of [["kind", setKind], ["original_name", setName], ["original_description", setDescription],
          ["canonical_locale", setCanonicalLocale], ["enabled", setEnabled], ["infotype_number", setInfotypeNumber]]) {
          setter((value) => reconcileGlossaryDraft({[key]: value}, submitted, updated)[key]);
        }
      });
  };
  const mergeName = async (targetId, returnFocusTo) => {
    if (busy || !nameConflicted) return;
    setBusy(true);
    const conflicts = [...nameCheck.conflicts].sort((left, right) => Number(right.term_id === targetId) - Number(left.term_id === targetId));
    try { await offerConflictMerge({code: "glossary_identity_conflict", conflicts}, {sourceEdit: currentSourceEdit(), returnFocusTo}); }
    finally { setBusy(false); }
  };
  const updateAliasDraft = (aliasId, patch) => setAliasDrafts((current) => ({
    ...current,
    [aliasId]: { ...(current[aliasId] || emptyAlias), ...patch },
  }));
  const saveAlias = (alias) => {
    const draft = aliasDrafts[alias.id] || alias;
    if (!draft.alias.trim()) return;
    return apply(() => updateGlossaryAlias(term.id, alias.id, {
      version: term.version,
      alias: draft.alias.trim(),
      locale: draft.locale,
      auto_expand: draft.auto_expand,
      search_enabled: draft.search_enabled,
    }), { isNew: false, aliasId: alias.id, savedRow: aliasDraftsFor([alias])[alias.id],
      sourceEdit: currentSourceEdit() }, (updated) => {
      const row = updated.aliases.find((item) => item.id === alias.id);
      if (row) setAliasDrafts((current) => ({...current, [alias.id]: reconcileGlossaryDraft(current[alias.id] || draft, draft, row)}));
    });
  };
  const addAlias = async () => {
    if (!aliasDraft.alias.trim()) return;
    const saved = await apply(() => addGlossaryAlias(term.id, { version: term.version, ...aliasDraft, locale: aliasDraft.locale || locale, alias: aliasDraft.alias.trim() }),
      { isNew: true, sourceEdit: currentSourceEdit() });
    if (saved) setAliasDraft(emptyAlias);
  };
  const removeAlias = (alias) => {
    return apply(() => deleteGlossaryAlias(term.id, alias.id, term.version));
  };

  const closeMerge = () => {
    if (mergeDecline?.isNew) setAliasDraft((value) => ({...value, alias: ""}));
    else if (mergeDecline?.aliasId) setAliasDrafts((current) => ({ ...current, [mergeDecline.aliasId]: mergeDecline.savedRow }));
    setMergeTarget(null);
    setMergeDecline(null);
  };

  return (
    <div className="glossary-editor">
      <div className="glossary-editor-head"><div><strong>{term.original_name}</strong><span className="meta"> · {term.kind} · v{term.version}</span>{term.has_duplicates && <span className="glossary-duplicate-badge">{t("admin.glossary.duplicate")}</span>}</div><button className="btn ghost" onClick={onClose}>{t("common.cancel")}</button></div>
      <GlossaryConflicts conflicts={term.alias_conflicts} onOpen={onOpenTerm} />
      <section className="glossary-section">
        <h3>{t("admin.glossary.original")}</h3>
        <p className="muted glossary-save-hint">{t("admin.glossary.sourceSaveHint")}</p>
        <label className="glossary-field">{t("admin.glossary.mergeField.kind")}
          <select value={kind} disabled={busy || !canManage} onChange={(event) => setKind(event.target.value)}>
            {KINDS.map((value) => <option key={value} value={value}>{t(`admin.glossary.kind.${value}`)}</option>)}
          </select>
        </label>
        <label className="glossary-field">{t("admin.glossary.name")}<input value={name} onChange={(e) => setName(e.target.value)} readOnly={!canManage} /></label>
        <GlossaryCheckResult result={nameCheck} nameConflict onOpen={mergeName} excludeTermId={term.id}
          disabled={busy || (kind === "sap_infotype" && !/^[0-9]{4}$/.test(infotypeNumber))} />
        {kind === "sap_infotype" && <label className="glossary-field">{t("admin.glossary.mergeField.infotype_number")}
          <input value={infotypeNumber} onChange={(e) => setInfotypeNumber(e.target.value)} inputMode="numeric" pattern="[0-9]{4}" maxLength={4} readOnly={!canManage} />
        </label>}
        <label className="glossary-field">{t("admin.glossary.description")}<textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} readOnly={!canManage} /></label>
        <ReferenceLocaleSelect value={canonicalLocale} onChange={setCanonicalLocale} disabled={busy || !canManage} />
        <label className="glossary-check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} disabled={busy || !canManage} /> {t("admin.glossary.enabled")}</label>
        {canManage && <button className="modal-btn" onClick={saveSource} disabled={busy || nameConflicted || !name.trim() || (kind === "sap_infotype" && !/^[0-9]{4}$/.test(infotypeNumber))}>{t("admin.glossary.save")}</button>}
      </section>
      <section className="glossary-section">
        <h3>{t("admin.glossary.aliases")}</h3>
        {kind === "sap_infotype" && <p className="glossary-system-rule-inline">{t("admin.glossary.infotypeAliasHint")}</p>}
        <p className="muted glossary-save-hint">{t("admin.glossary.aliasSaveHint")}</p>
        {term.aliases.map((alias) => {
          const draft = aliasDrafts[alias.id] || alias;
          return <div className="glossary-alias-row" key={alias.id}>
          <input value={draft.alias} readOnly={!canManage} aria-label={t("admin.glossary.alias")} onChange={(e) => updateAliasDraft(alias.id, { alias: e.target.value })} />
          <ReferenceLocaleSelect value={draft.locale}
            onChange={(locale) => updateAliasDraft(alias.id, { locale })}
            labelKey="admin.glossary.aliasLocale"
            editLabelKey="admin.glossary.editAliasLocale"
            collapseLabelKey="admin.glossary.collapseAliasLocale"
            disabled={busy || !canManage} />
          <label><input type="checkbox" checked={draft.auto_expand} disabled={!canManage || busy} onChange={(e) => updateAliasDraft(alias.id, { auto_expand: e.target.checked })} /> {t("admin.glossary.autoExpand")}</label>
          <label><input type="checkbox" checked={draft.search_enabled} disabled={!canManage || busy} onChange={(e) => updateAliasDraft(alias.id, { search_enabled: e.target.checked })} /> {t("admin.glossary.searchEnabled")}</label>
          {canManage && <button className="modal-btn" onClick={() => saveAlias(alias)} disabled={busy || !draft.alias.trim()}>{t("admin.glossary.save")}</button>}
          {canManage && <button className="btn ghost" onClick={() => removeAlias(alias)} disabled={busy} aria-label={t("admin.glossary.deleteAlias")}>×</button>}
          {canManage && draft.alias !== alias.alias && <GlossaryAliasCheck value={draft.alias} termId={term.id} onOpen={onOpenTerm} />}
        </div>;
        })}
        {canManage && <div className="glossary-alias-row glossary-alias-new">
          <input value={aliasDraft.alias} onChange={(e) => setAliasDraft((v) => ({ ...v, alias: e.target.value }))} placeholder={t("admin.glossary.aliasPlaceholder")} />
          <ReferenceLocaleSelect value={aliasDraft.locale || locale}
            onChange={(locale) => setAliasDraft((v) => ({ ...v, locale }))}
            labelKey="admin.glossary.aliasLocale"
            editLabelKey="admin.glossary.editAliasLocale"
            collapseLabelKey="admin.glossary.collapseAliasLocale"
            disabled={busy} />
          <label><input type="checkbox" checked={aliasDraft.auto_expand} onChange={(e) => setAliasDraft((v) => ({ ...v, auto_expand: e.target.checked }))} /> {t("admin.glossary.autoExpand")}</label>
          <label><input type="checkbox" checked={aliasDraft.search_enabled} onChange={(e) => setAliasDraft((v) => ({ ...v, search_enabled: e.target.checked }))} /> {t("admin.glossary.searchEnabled")}</label>
          <button className="modal-btn" onClick={addAlias} disabled={busy || !aliasDraft.alias.trim()}>+</button>
          <GlossaryAliasCheck value={aliasDraft.alias} termId={term.id} onOpen={onOpenTerm} />
        </div>}
      </section>
      <GlossaryTranslations key={`${term.id}:${term.canonical_locale}`} term={term} onSaved={onSaved} />
      <GlossaryPreview locale={canonicalLocale} />
      {mergeTarget && <GlossaryMergeDialog
        key={mergeTarget.id}
        source={{...term, ...mergeDecline?.sourceEdit}}
        sourceEdit={mergeDecline?.sourceEdit}
        returnFocusTo={mergeDecline?.returnFocusTo}
        target={mergeTarget}
        targets={mergeTargets}
        onTargetChange={(id) => setMergeTarget(mergeTargets.find((item) => item.id === id))}
        onClose={closeMerge}
        onCompleted={() => { onPendingAliasChange?.(false); onMergeCompleted?.(); }}
      />}
    </div>
  );
}
