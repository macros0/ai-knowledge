"use client";

import { useState } from "react";
import { previewGlossaryQuery, friendlyApiError } from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";
import { useToast } from "./Toast";

export default function GlossaryPreview({ locale }) {
  const { t } = useI18n();
  const { showToast } = useToast();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async (event) => {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    try { setResult(await previewGlossaryQuery(query.trim(), locale)); }
    catch (err) { showToast(friendlyApiError(err, t), { type: "error" }); }
    finally { setBusy(false); }
  };
  return (
    <section className="glossary-section glossary-preview">
      <h3>{t("admin.glossary.preview")}</h3>
      <p className="muted">{t("admin.glossary.previewSavedOnly")}</p>
      <form onSubmit={run} className="glossary-inline-form">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("admin.glossary.previewPlaceholder")} />
        <button className="modal-btn" disabled={busy || !query.trim()}>{busy ? "…" : t("admin.glossary.check")}</button>
      </form>
      {result && <div className="glossary-preview-result">
        <div><b>{t("admin.glossary.status")}</b>: {result.expansion_status}</div>
        <div><b>{t("admin.glossary.denseQuery")}</b>: {result.dense_query}</div>
        <div><b>{t("admin.glossary.addedForms")}</b>: {result.added_sparse_texts.join(", ") || "—"}</div>
        {result.applied_terms.length > 0 ? <ul>{result.applied_terms.map((item) => <li key={item.canonical}>{item.display_name || item.canonical}: {(item.matched_texts || []).join(", ")}</li>)}</ul> : <p className="muted">{t("admin.glossary.noMatches")}</p>}
      </div>}
    </section>
  );
}
