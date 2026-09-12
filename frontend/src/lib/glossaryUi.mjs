export function buildGlossaryListParams({ query = "", kind = "", enabled = "", needsReview = false, page = 1, pageSize = 50 } = {}) {
  const params = {};
  if (query.trim()) params.q = query.trim();
  if (kind) params.kind = kind;
  if (enabled !== "") params.enabled = enabled;
  if (needsReview) params.needs_review = true;
  params.limit = pageSize;
  params.offset = Math.max(0, page - 1) * pageSize;
  return params;
}

export function chunkTermIds(ids, size = 10) {
  const result = [];
  for (let i = 0; i < ids.length; i += size) result.push(ids.slice(i, i + size));
  return result;
}

export function translationState(term, locale) {
  if (!term || term.canonical_locale === locale) return { kind: "source" };
  const translation = (term.translations || []).find((item) => item.locale === locale);
  if (!translation) return { kind: "missing" };
  if (translation.source_revision !== term.source_revision) {
    return { kind: translation.is_machine_translated ? "machine_stale" : "human_stale", translation };
  }
  if (translation.is_machine_translated && !translation.reviewed_by) {
    return { kind: "machine_unreviewed", translation };
  }
  return { kind: "reviewed", translation };
}

export function isCurrentGlossaryResponse(requestKey, selectedKey) {
  return requestKey === selectedKey;
}

export function keepGlossarySelection(current, updated) {
  return current && current.id !== updated?.id ? current : updated;
}

export function glossaryTermDisplay(term) {
  return {
    code: term?.canonical || "",
    name: term?.original_name || "",
    locale: term?.canonical_locale || "und",
  };
}

export function appliedTermsSummary(appliedTerms = []) {
  return appliedTerms.map((term) => ({
    canonical: term.canonical,
    matched: term.matched_texts || [],
    added: term.added_forms || [],
    displayName: term.display_name || term.canonical,
  }));
}
