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

export function hasPendingGlossaryAlias(draft) {
  return Boolean(draft?.alias?.trim());
}

export function glossaryTermDisplay(term) {
  return {
    name: term?.original_name || "",
    locale: term?.canonical_locale || "und",
  };
}

export function glossarySystemRuleForKind(kind) {
  // Structural forms are returned by the server's user-owned rules.  The
  // client must never carry a second hardcoded SAP vocabulary.
  return null;
}

export function appliedTermsSummary(appliedTerms = []) {
  return appliedTerms.map((term) => ({
    canonical: term.canonical,
    matched: term.matched_texts || [],
    added: term.system_rule ? (term.saved_alias_forms || []) : (term.added_forms || []),
    displayName: term.display_name || (term.matched_texts || []).join(", "),
    systemRule: term.system_rule || null,
  }));
}

export function glossaryCheckKey({value, termId, kind, infotypeNumber, revision}) {
  return JSON.stringify([value, termId ?? null, kind ?? null, infotypeNumber ?? null, revision ?? null]);
}

export function currentGlossaryCheck(result, request) {
  return result?.key === glossaryCheckKey(request) ? result : null;
}

export function scheduleGlossaryCheck(check, request, onResult) {
  const {value, termId, kind, infotypeNumber} = request;
  let cancelled = false;
  if (!value.trim() || value.length > 256) return () => {};
  const key = glossaryCheckKey(request);
  const timer = setTimeout(async () => {
    try {
      const context = kind ? {kind, infotype_number: /^[0-9]{4}$/.test(infotypeNumber || "") ? infotypeNumber : null} : undefined;
      const data = await check([value], termId, context);
      if (!cancelled) onResult({key, conflicts: data.conflicts || []});
    } catch {
      if (!cancelled) onResult({key, error: true});
    }
  }, 350);
  return () => { cancelled = true; clearTimeout(timer); };
}
