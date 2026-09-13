"use client";

import { useEffect, useState } from "react";
import { checkGlossaryAliases } from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";

export function GlossaryConflicts({ conflicts = [], onOpen, draft = false }) {
  const { t } = useI18n();
  if (!conflicts.length) return null;
  return <div className="glossary-conflicts" role="status">
    <p>{t(draft ? "admin.glossary.duplicateDraft" : "admin.glossary.duplicateHint")}</p>
    <ul>{conflicts.map((item) => <li key={`${item.alias_id}:${item.term_id}`}>
      <span>«{item.alias}» — </span>
      {onOpen ? <button type="button" className="btn ghost" onClick={() => onOpen(item.term_id)}>
        {item.name} ({item.locale})
      </button> : <span>{item.name} ({item.locale})</span>}
    </li>)}</ul>
  </div>;
}

export default function GlossaryAliasCheck({ value, termId, onOpen }) {
  const { t } = useI18n();
  const [result, setResult] = useState(null);
  useEffect(() => {
    if (!value.trim() || value.length > 256) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      checkGlossaryAliases([value], termId)
        .then((data) => { if (!cancelled) setResult({ value, termId, conflicts: data.conflicts || [] }); })
        .catch(() => { if (!cancelled) setResult({ value, termId, error: true }); });
    }, 350);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [value, termId]);
  if (!result || result.value !== value || result.termId !== termId) return null;
  if (result.error) return <p className="glossary-conflicts" role="status">{t("admin.glossary.duplicateCheckFailed")}</p>;
  return <GlossaryConflicts conflicts={result.conflicts} onOpen={onOpen} draft />;
}
