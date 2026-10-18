"use client";

import { apiToast } from "@/lib/apiToast.mjs";

import { useEffect, useMemo, useState } from "react";
import { getGlossaryTerm, glossaryTranslation, listActiveLocales, reviewGlossaryTranslation, friendlyApiError } from "@/lib/api";
import { translationState } from "@/lib/glossaryUi.mjs";
import { SUPPORTED_LOCALES } from "@/i18n/core";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";
import SearchableSelect from "./SearchableSelect";
import ReferenceConflictDialog from "./ReferenceConflictDialog";
import { languageLabel } from "@/lib/sourceLocales.mjs";

export default function GlossaryTranslations({ term, onSaved, beforeSave, session, onDirtyChange }) {
  const { t, locale: uiLocale } = useI18n();
  const { showToast } = useToast();
  const [active, setActive] = useState([]);
  const [locale, setLocale] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [base, setBase] = useState(term);
  const [conflict, setConflict] = useState(null);
  const writable = session?.state === "editing" && !busy && !conflict;

  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  useEffect(() => {
    let cancelled = false;
    listActiveLocales().then((items) => { if (!cancelled) setActive(items || []); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const targets = useMemo(() => {
    const values = new Set([...(active || []).map((item) => item.code), ...SUPPORTED_LOCALES]);
    values.delete(term.canonical_locale);
    if (dirty && locale) values.add(locale);
    return [...values].filter(Boolean).sort();
  }, [active, term.canonical_locale, dirty, locale]);

  const targetOptions = useMemo(() => targets.map((code) => ({
    value: code,
    label: languageLabel(code, uiLocale) || code,
    searchText: `${code} ${languageLabel(code, uiLocale) || ""}`,
  })), [targets, uiLocale]);

  useEffect(() => {
    if (dirty) return;
    const next = locale && targets.includes(locale) ? locale : targets[0] || "";
    // Keep the selected target valid when the active locale list arrives.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (next !== locale) setLocale(next);
    if (!next) return;
    setBase(term);
    const current = (term.translations || []).find((item) => item.locale === next);
    setName(current?.display_name || "");
    setDescription(current?.description || "");
  }, [locale, targets, term, dirty]);

  const current = (base.translations || []).find((item) => item.locale === locale);
  const state = translationState(base, locale);
  const mark = (setter) => (event) => { setDirty(true); setter(event.target.value); };

  const save = async () => {
    if (!writable || !locale || !name.trim()) return;
    if (beforeSave && !beforeSave()) return;
    setBusy(true);
    try {
      const updated = await glossaryTranslation(term.id, locale, {
        translation_version: current?.version || 0,
        source_revision: base.source_revision,
        expected_reference_revision: base.reference_revision,
        display_name: name.trim(),
        description: description || null,
      }, {headers: session.getMutationHeaders()});
      setBase(updated); setDirty(false);
      onSaved(updated);
      showToast(t("admin.glossary.translationSaved"), { type: "success" });
    } catch (err) {
      session?.handleMutationError?.(err);
      if ((err.code || err.data?.code) === "version_conflict") {
        try { setConflict({remote: await getGlossaryTerm(term.id), local: {display_name: name, description}}); }
        catch (loadError) { showToast(apiToast(loadError, t, {type: "error"})); }
      } else showToast(apiToast(err, t, { type: "error" }));
    } finally { setBusy(false); }
  };

  const review = async () => {
    if (!writable || !current) return;
    if (beforeSave && !beforeSave()) return;
    setBusy(true);
    try { onSaved(await reviewGlossaryTranslation(term.id, locale, { translation_version: current.version, source_revision: base.source_revision,
      expected_reference_revision: base.reference_revision }, {headers: session.getMutationHeaders()})); }
    catch (err) { session?.handleMutationError?.(err); showToast(apiToast(err, t, { type: "error" })); }
    finally { setBusy(false); }
  };

  if (!targets.length) return <p className="muted">{t("admin.glossary.noTranslationTargets")}</p>;
  return (
    <section className="glossary-section">
      <h3>{t("admin.glossary.translations")}</h3>
      <p className="muted glossary-save-hint">{t("admin.glossary.translationSaveHint")}</p>
      <label className="glossary-field">{t("admin.glossary.targetLocale")}
        <SearchableSelect
          options={targetOptions}
          value={locale}
          disabled={busy || Boolean(conflict)}
          onChange={(next) => { if (busy || conflict) return; if (!dirty || window.confirm(t("admin.glossary.unsavedChangesConfirm"))) {setDirty(false); setLocale(next);} }}
          searchPlaceholder={t("docs.localeSearchPlaceholder")}
          emptyLabel={t("docs.empty")}
          ariaLabel={t("admin.glossary.targetLocale")}
        />
      </label>
      <div className="glossary-source-preview"><b>{term.original_name}</b><small>{term.canonical_locale}</small></div>
      <span className={`glossary-translation-state ${state.kind}`}>{t(`admin.glossary.state.${state.kind}`)}</span>
      <input value={name} onChange={mark(setName)} readOnly={!writable} placeholder={t("admin.glossary.translationName")} />
      <textarea value={description} onChange={mark(setDescription)} readOnly={!writable} placeholder={t("admin.glossary.translationDescription")} rows={3} />
      <div className="glossary-actions">
        {!writable && <button type="button" className="modal-btn" onClick={() => navigator.clipboard.writeText(`${locale}\n${name}\n${description}`).catch(() => {})}>{t("reference.copyDraft")}</button>}
        <button className="modal-btn" disabled={!writable || !name.trim()} onClick={save}>{t("admin.glossary.save")}</button>
        {current?.is_machine_translated && !current.reviewed_by && <button className="modal-btn" disabled={!writable} onClick={review}>{t("admin.glossary.review")}</button>}
      </div>
      {conflict && <ReferenceConflictDialog base={(base.translations || []).find(item => item.locale === locale) || {}}
        local={conflict.local} remote={(conflict.remote.translations || []).find(item => item.locale === locale) || {}}
        fields={["display_name", "description"]} labels={{display_name: t("admin.glossary.translationName"), description: t("admin.glossary.translationDescription")}}
        onClose={() => setConflict(null)} onConfirm={value => {setBase(conflict.remote); setName(value.display_name || ""); setDescription(value.description || ""); setDirty(true); setConflict(null);}} />}
    </section>
  );
}
