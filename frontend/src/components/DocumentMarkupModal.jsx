"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  friendlyApiError, getDocument, listActiveLocales, listDevelopments,
  setDocumentDevelopment, setDocumentSourceLocale, updateDocumentTags,
} from "@/lib/api";
import { markupDraft, reconcileMarkupRecovery, sameMarkupTags, saveDocumentMarkup } from "@/lib/documentMarkup.mjs";
import { buildLocaleOptions } from "@/lib/sourceLocales.mjs";
import { bumpTagVersion } from "@/lib/tagDictionary";
import { useI18n } from "@/i18n/LocaleContext";
import Modal from "./Modal";
import TagPicker from "./TagPicker";
import DevelopmentPicker from "./DevelopmentPicker";
import SearchableSelect from "./SearchableSelect";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";
import { useToast } from "./Toast";

const writeApi = { updateDocumentTags, setDocumentDevelopment, setDocumentSourceLocale };

export default function DocumentMarkupModal({ docId, onClose }) {
  const { t, locale } = useI18n();
  const { showToast } = useToast();
  const [baseline, setBaseline] = useState(null);
  const [draft, setDraft] = useState(null);
  const [developments, setDevelopments] = useState([]);
  const [activeLocales, setActiveLocales] = useState([]);
  const [loadErrors, setLoadErrors] = useState({});
  const [loadVersion, setLoadVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [developmentTouched, setDevelopmentTouched] = useState(false);
  const [error, setError] = useState(null);
  const [applied, setApplied] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [recoveryNeeded, setRecoveryNeeded] = useState(false);
  const [portalContainer, setPortalContainer] = useState(null);
  const busyRef = useRef(false);
  const aliveRef = useRef(true);
  const recoveryRef = useRef(null);

  const acceptReadback = useCallback((document) => {
    setBaseline(document);
    setRecoveryNeeded(false);
    if (!recoveryRef.current) return;
    const recovered = reconcileMarkupRecovery({ document, ...recoveryRef.current });
    setDraft(recovered.draft);
    setDevelopmentTouched(recovered.developmentTouched);
    setApplied((previous) => [...new Set([...previous, ...recovered.applied])]);
    if (recovered.applied.length) setError(null);
    if (recovered.applied.includes("tags")) bumpTagVersion();
    recoveryRef.current = null;
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getDocument(docId), listDevelopments(), listActiveLocales()]).then(([doc, devs, languages]) => {
      if (cancelled) return;
      const errors = {};
      if (doc.status === "fulfilled") {
        acceptReadback(doc.value);
        setDraft((previous) => previous || markupDraft(doc.value, locale));
      } else errors.document = doc.reason;
      if (devs.status === "fulfilled") setDevelopments(devs.value);
      else errors.development = devs.reason;
      if (languages.status === "fulfilled") setActiveLocales(languages.value);
      else errors.source_locale = languages.reason;
      setLoadErrors(errors);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [docId, loadVersion, locale, acceptReadback]);

  const close = () => { if (!busyRef.current) onClose(); };
  const retryLoad = () => { setLoading(true); setLoadVersion((version) => version + 1); };
  const change = (field, value) => setDraft((previous) => ({ ...previous, [field]: value }));
  const changed = baseline && draft && (!sameMarkupTags(baseline.tags, draft.tags) ||
    developmentTouched || (baseline.source_locale ?? null) !== draft.sourceLocale);

  const save = async () => {
    if (busyRef.current || loading || recoveryNeeded || loadErrors.document || !changed) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    const result = await saveDocumentMarkup({
      docId, baseline, draft, developmentTouched, api: writeApi,
      onApplied: ({ field, document }) => {
        if (!aliveRef.current) return;
        setBaseline(document);
        setApplied((previous) => [...new Set([...previous, field])]);
        if (field === "development") setDevelopmentTouched(false);
        if (field === "tags") bumpTagVersion();
        setDraft((previous) => ({
          ...previous,
          ...(field === "tags" ? { tags: document.tags || [] } : {}),
          ...(!developmentTouched || field === "development" ? { developmentId: document.development_id ?? null } : {}),
          ...(field === "source_locale" ? { sourceLocale: document.source_locale ?? null } : {}),
        }));
      },
    });
    if (!aliveRef.current) return;
    setWarnings((previous) => [...new Set([...previous, ...result.warnings])]);
    if (result.error) {
      setError(result.error);
      // A failed response can still follow a committed write. Read the document
      // before retrying and keep the remaining user draft intact.
      setRecoveryNeeded(true);
      recoveryRef.current = {
        draft: { ...draft, ...(result.applied.includes("tags") ? { tags: result.document.tags || [] } : {}) },
        failedField: result.failedField,
        developmentTouched: developmentTouched && !result.applied.includes("development"),
      };
      try {
        const current = await getDocument(docId);
        if (!aliveRef.current) return;
        acceptReadback(current);
      } catch {
        // The retry button loads a trustworthy baseline; writes stay disabled.
      }
    } else {
      showToast(t(warnings.length || result.warnings.length ? "documentMarkup.syncPending" : "documentMarkup.saved"),
        { type: warnings.length || result.warnings.length ? "warning" : "success" });
      onClose();
    }
    busyRef.current = false;
    setBusy(false);
  };

  return (
    <Modal title={t("documentMarkup.title")} onClose={close} footer={
      <>
        <button type="button" className="btn" onClick={close} disabled={busy}>
          {t(applied.length || error ? "common.close" : "common.cancel")}
        </button>
        <button type="button" className="btn primary" onClick={save}
          disabled={busy || loading || recoveryNeeded || !!loadErrors.document || !changed}>
          {t(busy ? "documentMarkup.saving" : "documentMarkup.save")}
        </button>
      </>
    }>
      <div className="document-markup" ref={setPortalContainer}>
        {baseline && <p className="document-markup-filename">{baseline.filename}</p>}
        {loading && <p role="status">{t("common.loading")}</p>}
        {Object.keys(loadErrors).length > 0 && <div role="alert" className="document-markup-notice">
          {t("documentMarkup.loadError", { message: friendlyApiError(Object.values(loadErrors)[0], t) })}
          <button type="button" className="btn" onClick={retryLoad} disabled={busy || loading}>{t("common.retry")}</button>
        </div>}
        {draft && portalContainer && <div className="document-markup-fields">
          <fieldset disabled={busy || loading || !!loadErrors.document || recoveryNeeded}>
            <legend>{t("documentMarkup.tags")}</legend>
            <TagPicker selected={draft.tags} onChange={(tags) => change("tags", tags)}
              placeholder={t("docs.addTagPlaceholder")} portalContainer={portalContainer}
              disabled={busy || loading || !!loadErrors.document || recoveryNeeded} />
            <ReferenceLocaleSelect value={draft.canonicalLocale} onChange={(value) => change("canonicalLocale", value)}
              activeLocales={activeLocales} portalContainer={portalContainer}
              disabled={busy || loading || !!loadErrors.document || !!loadErrors.source_locale || recoveryNeeded}
              labelKey="documentMarkup.tagLanguage" />
          </fieldset>
          <fieldset disabled={busy || loading || !!loadErrors.document || !!loadErrors.development || recoveryNeeded}>
            <legend>{t("documentMarkup.development")}</legend>
            <DevelopmentPicker developments={developments} value={draft.developmentId}
              currentDocument={baseline} suggestion={baseline?.development_suggestion}
              portalContainer={portalContainer}
              disabled={busy || loading || !!loadErrors.document || !!loadErrors.development || recoveryNeeded}
              onChange={(value) => { change("developmentId", value); setDevelopmentTouched(true); }} />
          </fieldset>
          <fieldset disabled={busy || loading || !!loadErrors.document || !!loadErrors.source_locale || recoveryNeeded}>
            <legend>{t("documentMarkup.sourceLanguage")}</legend>
            <SearchableSelect options={[
              { value: "", label: t("docs.localeNone") },
              ...buildLocaleOptions(draft.sourceLocale, activeLocales, locale).map((item) => ({ value: item.code, label: item.label })),
            ]} value={draft.sourceLocale || ""} onChange={(value) => change("sourceLocale", value || null)}
              searchPlaceholder={t("docs.localeSearchPlaceholder")} emptyLabel={t("docs.empty")}
              ariaLabel={t("docs.localeEditAria")} portalContainer={portalContainer}
              disabled={busy || loading || !!loadErrors.document || !!loadErrors.source_locale || recoveryNeeded} />
          </fieldset>
        </div>}
        {error && <div role="alert" className="document-markup-notice">
          <p>{t("documentMarkup.saveError", { message: friendlyApiError(error, t) })}</p>
        </div>}
        {applied.length > 0 && <p role="status">{t("documentMarkup.partial", {
          fields: applied.map((field) => t(`documentMarkup.field.${field}`)).join(", "),
        })}</p>}
        {recoveryNeeded && !busy && <div role="alert" className="document-markup-notice">
          <p>{t("documentMarkup.recoveryNeeded")}</p>
          <button type="button" className="btn" onClick={retryLoad} disabled={loading}>{t("common.retry")}</button>
        </div>}
        {warnings.length > 0 && <p role="status">{t("documentMarkup.syncPending")}</p>}
      </div>
    </Modal>
  );
}
