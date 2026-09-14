"use client";

import { useEffect, useState } from "react";
import { checkGlossaryAliases } from "@/lib/api";
import { useI18n } from "@/i18n/LocaleContext";
import { currentGlossaryCheck, scheduleGlossaryCheck } from "@/lib/glossaryUi.mjs";

export function GlossaryConflicts({ conflicts = [], onOpen, draft = false, nameConflict = false, disabled = false, excludeTermId }) {
  const { t } = useI18n();
  if (!conflicts.length) return null;
  return <div className="glossary-conflicts" role="status">
    <p>{t(nameConflict ? "admin.glossary.nameConflict" : draft ? "admin.glossary.duplicateDraft" : "admin.glossary.duplicateHint")}</p>
    <ul>{conflicts.map((item, index) => {
      const label = item.alias || item.key_value || item.rule_name || item.rule_id || "—";
      const owner = item.name || item.rule_name || item.rule_id || "—";
      const locale = item.locale ? ` (${item.locale})` : "";
      const canOpen = Boolean(onOpen && item.term_id && item.term_id !== excludeTermId);
      return <li key={`${item.alias_id || item.rule_id || label}:${item.term_id || index}`}>
        <span>«{label}» — </span>
        {canOpen ? <button type="button" className="btn ghost" disabled={disabled} onClick={(event) => onOpen(item.term_id, event.currentTarget)}>
          {nameConflict && `${t("admin.glossary.mergeWith")}: `}{owner}{locale}
        </button> : <span>{owner}{locale}</span>}
      </li>;
    })}</ul>
  </div>;
}

export function useGlossaryAliasCheck(value, termId, {kind, infotypeNumber, revision} = {}) {
  const [result, setResult] = useState(null);
  useEffect(() => scheduleGlossaryCheck(checkGlossaryAliases,
    {value, termId, kind, infotypeNumber, revision}, setResult), [value, termId, kind, infotypeNumber, revision]);
  return currentGlossaryCheck(result, {value, termId, kind, infotypeNumber, revision});
}

export function GlossaryCheckResult({ result, ...props }) {
  const { t } = useI18n();
  if (!result) return null;
  if (result.error) return <p className="glossary-conflicts" role="status">{t(props.nameConflict ? "admin.glossary.nameCheckFailed" : "admin.glossary.duplicateCheckFailed")}</p>;
  return <GlossaryConflicts conflicts={result.conflicts} {...props} draft />;
}

export default function GlossaryAliasCheck({ value, termId, onOpen }) {
  const result = useGlossaryAliasCheck(value, termId);
  return <GlossaryCheckResult result={result} onOpen={onOpen} />;
}
