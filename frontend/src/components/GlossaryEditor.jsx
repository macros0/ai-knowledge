"use client";

import { useEffect, useState } from "react";
import { addGlossaryAlias, deleteGlossaryAlias, friendlyApiError, updateGlossaryAlias, updateGlossarySource } from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import GlossaryTranslations from "./GlossaryTranslations";
import GlossaryPreview from "./GlossaryPreview";
import GlossaryAliasCheck, { GlossaryConflicts } from "./GlossaryAliasCheck";
import { hasPendingGlossaryAlias } from "@/lib/glossaryUi.mjs";

const emptyAlias = { alias: "", locale: null, auto_expand: false, search_enabled: false };

const aliasDraftsFor = (aliases) => Object.fromEntries(
  (aliases || []).map((alias) => [alias.id, {
    alias: alias.alias,
    locale: alias.locale,
    auto_expand: alias.auto_expand,
    search_enabled: alias.search_enabled,
  }]),
);

export default function GlossaryEditor({ term, canManage = false, onSaved, onClose, onOpenTerm, onPendingAliasChange }) {
  const { t, locale } = useI18n();
  const { showToast } = useToast();
  const [name, setName] = useState(term.original_name);
  const [description, setDescription] = useState(term.original_description || "");
  const [canonicalLocale, setCanonicalLocale] = useState(term.canonical_locale);
  const [enabled, setEnabled] = useState(term.enabled);
  const [aliasDraft, setAliasDraft] = useState(emptyAlias);
  const [aliasDrafts, setAliasDrafts] = useState(() => aliasDraftsFor(term.aliases));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    onPendingAliasChange?.(hasPendingGlossaryAlias(aliasDraft));
  }, [aliasDraft, onPendingAliasChange]);

  useEffect(() => {
    // A newly selected card must replace the local draft.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setName(term.original_name); setDescription(term.original_description || "");
    setCanonicalLocale(term.canonical_locale); setEnabled(term.enabled); setAliasDraft(emptyAlias);
    setAliasDrafts(aliasDraftsFor(term.aliases));
  }, [term]);

  const apply = async (fn) => {
    setBusy(true);
    try {
      const updated = await fn();
      onSaved(updated);
      showToast(t(updated.has_duplicates ? "admin.glossary.savedWithDuplicates" : "admin.glossary.saved"), { type: updated.has_duplicates ? "warning" : "success" });
      return true;
    }
    catch (err) { showToast(friendlyApiError(err, t), { type: "error" }); return false; }
    finally { setBusy(false); }
  };
  const saveSource = () => apply(() => updateGlossarySource(term.id, { version: term.version, original_name: name, original_description: description || null, canonical_locale: canonicalLocale, enabled }));
  const updateAliasDraft = (aliasId, patch) => setAliasDrafts((current) => ({
    ...current,
    [aliasId]: { ...(current[aliasId] || emptyAlias), ...patch },
  }));
  const confirmDiscardNewAlias = () => {
    if (!hasPendingGlossaryAlias(aliasDraft)) return true;
    return window.confirm(t("admin.glossary.unsavedAliasConfirm"));
  };
  const saveAlias = (alias) => {
    if (!confirmDiscardNewAlias()) return;
    const draft = aliasDrafts[alias.id] || alias;
    if (!draft.alias.trim()) return;
    return apply(() => updateGlossaryAlias(term.id, alias.id, {
      version: term.version,
      alias: draft.alias.trim(),
      locale: draft.locale,
      auto_expand: draft.auto_expand,
      search_enabled: draft.search_enabled,
    }));
  };
  const addAlias = async () => {
    if (!aliasDraft.alias.trim()) return;
    const saved = await apply(() => addGlossaryAlias(term.id, { version: term.version, ...aliasDraft, locale: aliasDraft.locale || locale, alias: aliasDraft.alias.trim() }));
    if (saved) setAliasDraft(emptyAlias);
  };
  const removeAlias = (alias) => {
    if (!confirmDiscardNewAlias()) return;
    return apply(() => deleteGlossaryAlias(term.id, alias.id, term.version));
  };

  return (
    <div className="glossary-editor">
      <div className="glossary-editor-head"><div><strong>{term.original_name}</strong><span className="meta"> · {term.kind} · v{term.version}</span>{term.has_duplicates && <span className="glossary-duplicate-badge">{t("admin.glossary.duplicate")}</span>}</div><button className="btn ghost" onClick={onClose}>{t("common.cancel")}</button></div>
      <GlossaryConflicts conflicts={term.alias_conflicts} onOpen={onOpenTerm} />
      <section className="glossary-section">
        <h3>{t("admin.glossary.original")}</h3>
        <p className="muted glossary-save-hint">{t("admin.glossary.sourceSaveHint")}</p>
        <label className="glossary-field">{t("admin.glossary.name")}<input value={name} onChange={(e) => setName(e.target.value)} readOnly={!canManage} /></label>
        <label className="glossary-field">{t("admin.glossary.description")}<textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} readOnly={!canManage} /></label>
        <ReferenceLocaleSelect value={canonicalLocale} onChange={setCanonicalLocale} disabled={busy || !canManage} />
        <label className="glossary-check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} disabled={busy || !canManage} /> {t("admin.glossary.enabled")}</label>
        {canManage && <button className="modal-btn" onClick={() => confirmDiscardNewAlias() && saveSource()} disabled={busy || !name.trim()}>{t("admin.glossary.save")}</button>}
      </section>
      <section className="glossary-section">
        <h3>{t("admin.glossary.aliases")}</h3>
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
      <GlossaryTranslations key={`${term.id}:${term.canonical_locale}`} term={term} onSaved={onSaved} beforeSave={confirmDiscardNewAlias} />
      <GlossaryPreview locale={canonicalLocale} />
    </div>
  );
}
