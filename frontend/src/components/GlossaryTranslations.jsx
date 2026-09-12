"use client";

import { useEffect, useMemo, useState } from "react";
import { glossaryTranslation, listActiveLocales, reviewGlossaryTranslation, friendlyApiError } from "@/lib/api";
import { translationState } from "@/lib/glossaryUi.mjs";
import { SUPPORTED_LOCALES } from "@/i18n/core";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";

export default function GlossaryTranslations({ term, onSaved }) {
  const { t } = useI18n();
  const { showToast } = useToast();
  const [active, setActive] = useState([]);
  const [locale, setLocale] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listActiveLocales().then((items) => { if (!cancelled) setActive(items || []); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const targets = useMemo(() => {
    const values = new Set([...(active || []).map((item) => item.code), ...SUPPORTED_LOCALES]);
    values.delete(term.canonical_locale);
    return [...values].filter(Boolean).sort();
  }, [active, term.canonical_locale]);

  useEffect(() => {
    const next = locale && targets.includes(locale) ? locale : targets[0] || "";
    // Keep the selected target valid when the active locale list arrives.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (next !== locale) setLocale(next);
    if (!next || dirty) return;
    const current = (term.translations || []).find((item) => item.locale === next);
    setName(current?.display_name || "");
    setDescription(current?.description || "");
  }, [locale, targets, term, dirty]);

  const current = (term.translations || []).find((item) => item.locale === locale);
  const state = translationState(term, locale);
  const mark = (setter) => (event) => { setDirty(true); setter(event.target.value); };

  const save = async () => {
    if (!locale || !name.trim()) return;
    setBusy(true);
    try {
      const updated = await glossaryTranslation(term.id, locale, {
        translation_version: current?.version || 0,
        source_revision: term.source_revision,
        display_name: name.trim(),
        description: description || null,
      });
      setDirty(false);
      onSaved(updated);
      showToast(t("admin.glossary.translationSaved"), { type: "success" });
    } catch (err) {
      showToast(friendlyApiError(err, t), { type: "error" });
    } finally { setBusy(false); }
  };

  const review = async () => {
    if (!current) return;
    setBusy(true);
    try { onSaved(await reviewGlossaryTranslation(term.id, locale, { translation_version: current.version, source_revision: term.source_revision })); }
    catch (err) { showToast(friendlyApiError(err, t), { type: "error" }); }
    finally { setBusy(false); }
  };

  if (!targets.length) return <p className="muted">{t("admin.glossary.noTranslationTargets")}</p>;
  return (
    <section className="glossary-section">
      <h3>{t("admin.glossary.translations")}</h3>
      <p className="muted glossary-save-hint">{t("admin.glossary.translationSaveHint")}</p>
      <label className="glossary-field">{t("admin.glossary.targetLocale")}
        <select value={locale} onChange={(e) => { setDirty(false); setLocale(e.target.value); }}>
          {targets.map((code) => <option key={code} value={code}>{code}</option>)}
        </select>
      </label>
      <div className="glossary-source-preview"><b>{term.original_name}</b><small>{term.canonical_locale}</small></div>
      <span className={`glossary-translation-state ${state.kind}`}>{t(`admin.glossary.state.${state.kind}`)}</span>
      <input value={name} onChange={mark(setName)} placeholder={t("admin.glossary.translationName")} />
      <textarea value={description} onChange={mark(setDescription)} placeholder={t("admin.glossary.translationDescription")} rows={3} />
      <div className="glossary-actions">
        <button className="modal-btn" disabled={busy || !name.trim()} onClick={save}>{t("admin.glossary.save")}</button>
        {current?.is_machine_translated && !current.reviewed_by && <button className="modal-btn" disabled={busy} onClick={review}>{t("admin.glossary.review")}</button>}
      </div>
    </section>
  );
}
