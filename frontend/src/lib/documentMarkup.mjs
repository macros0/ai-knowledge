// Keep the editable draft separate from server acknowledgements: writing tags
// can also change the development association on the server.
export function markupDraft(document, canonicalLocale) {
  return {
    tags: [...(document.tags || [])],
    canonicalLocale,
    developmentId: document.development_id ?? null,
    sourceLocale: document.source_locale ?? null,
  };
}

export function sameMarkupTags(left = [], right = []) {
  const tags = new Set(left);
  const other = new Set(right);
  return tags.size === other.size && [...tags].every((tag) => other.has(tag));
}

// Used for both immediate readback and the user's Retry after a read failure.
// A matching development alone is insufficient: it may be an unconfirmed
// association made by the preceding tag write.
export function reconcileMarkupRecovery({ document, draft, failedField, developmentTouched = false }) {
  const confirmed = failedField === "tags" ? sameMarkupTags(document.tags, draft.tags)
    : failedField === "development" ? (document.development_id ?? null) === draft.developmentId &&
      (draft.developmentId === null || !!document.development_confirmed_by)
      : failedField === "source_locale" && (document.source_locale ?? null) === draft.sourceLocale &&
        (draft.sourceLocale === null || document.source_locale_source === "manual");
  return {
    applied: confirmed ? [failedField] : [],
    developmentTouched: developmentTouched && !(confirmed && failedField === "development"),
    draft: {
      ...draft,
      ...(confirmed && failedField === "tags" ? { tags: [...(document.tags || [])] } : {}),
      ...(!developmentTouched ? { developmentId: document.development_id ?? null } : {}),
    },
  };
}

export async function saveDocumentMarkup({ docId, baseline, draft, developmentTouched = false, api, onApplied }) {
  const { tags, canonicalLocale, developmentId, sourceLocale } = { ...draft, tags: [...draft.tags] };
  const operations = [];
  if (!sameMarkupTags(baseline.tags, tags)) {
    operations.push(["tags", () => api.updateDocumentTags(docId, tags, canonicalLocale)]);
  }
  // An explicit choice wins over tag-driven reconciliation, including a choice
  // of the previous development or an explicit disassociation.
  if (developmentTouched) {
    operations.push(["development", () => api.setDocumentDevelopment(docId, developmentId, developmentId !== null)]);
  }
  if ((baseline.source_locale ?? null) !== sourceLocale) {
    operations.push(["source_locale", () => api.setDocumentSourceLocale(docId, sourceLocale)]);
  }

  let document = baseline;
  const applied = [];
  const warnings = new Set();
  for (const [field, write] of operations) {
    try {
      document = await write();
    } catch (error) {
      return { document, applied, warnings: [...warnings], failedField: field, error };
    }
    applied.push(field);
    for (const flag of ["dev_tags_sync_pending", "source_locale_sync_pending"]) {
      if (document[flag]) warnings.add(flag);
    }
    onApplied?.({ field, document });
  }
  return { document, applied, warnings: [...warnings], failedField: null, error: null };
}
