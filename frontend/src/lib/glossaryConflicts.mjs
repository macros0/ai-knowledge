export function normalizeGlossaryIdentity(value) {
  return String(value || "").normalize("NFC").trim().replace(/\s+/g, " ").toLowerCase().replace(/\u0307/g, "");
}

export function conflictSummary(error) {
  const conflicts = error?.conflicts || error?.detail?.conflicts || [];
  return conflicts.map((item) => ({
    key: item.key_value || "",
    termId: item.term_id || item.left_term_id || item.right_term_id || null,
    field: item.field_name || item.conflicting_field || "",
  }));
}

export function shouldOfferMerge(error) {
  return error?.code === "glossary_identity_conflict" && conflictSummary(error).length > 0;
}

export function resolveRejectedAlias({ isNew, savedAlias = "", draftAlias = "" }) {
  return isNew ? "" : savedAlias || draftAlias;
}

export function glossaryDraftPayload(term, aliases = term.aliases || []) {
  return {
    kind: term.kind,
    original_name: term.original_name.trim(),
    original_description: term.original_description || null,
    canonical_locale: term.canonical_locale || "und",
    infotype_number: term.kind === "sap_infotype" ? term.infotype_number : null,
    ...(typeof term.enabled === "boolean" ? {enabled: term.enabled} : {}),
    aliases: aliases.map(({alias, locale, auto_expand, search_enabled}) => ({
      alias: alias.trim(), locale: locale === "und" ? null : locale || null,
      auto_expand: Boolean(auto_expand), search_enabled: Boolean(search_enabled),
    })),
  };
}

export function defaultGlossaryMergeChoice(left, right) {
  const empty = (value) => value === null || value === undefined || value === "";
  return left === right || empty(left) ? "target" : empty(right) ? "source" : "";
}

export function rejectConflictingCreateAlias(alias, conflicts) {
  const key = normalizeGlossaryIdentity(alias);
  return key && conflicts.some((item) => normalizeGlossaryIdentity(item.key) === key) ? "" : alias;
}

const equalDraftValue = (left, right) => (left ?? "") === (right ?? "");

export function reconcileGlossaryDraft(draft, previous, updated) {
  return Object.fromEntries(Object.entries(draft).map(([key, value]) =>
    [key, equalDraftValue(value, previous[key]) ? updated[key] ?? "" : value]));
}

export function reconcileGlossaryAliasDrafts(drafts, previous = [], updated = []) {
  return Object.fromEntries(updated.map((alias) => {
    const before = previous.find((item) => item.id === alias.id);
    return [alias.id, before && drafts[alias.id] ? reconcileGlossaryDraft(drafts[alias.id], before, alias) : alias];
  }));
}

export function hasPendingGlossaryEdits(term, source, aliases, newAlias) {
  return Boolean(newAlias?.alias?.trim()) ||
    Object.entries(source).some(([key, value]) => !equalDraftValue(value, term[key])) ||
    (term.aliases || []).some((alias) => ["alias", "locale", "auto_expand", "search_enabled"].some((key) =>
      !equalDraftValue((aliases[alias.id] || alias)[key], alias[key])));
}

export function createGlossaryTargetSearch(list) {
  let sequence = 0;
  const search = async (query) => {
    const current = ++sequence;
    try {
      const result = await list({q: query.trim(), limit: 50, offset: 0});
      return current === sequence ? result : null;
    } catch (error) {
      if (current === sequence) throw error;
      return null;
    }
  };
  search.invalidate = () => { ++sequence; };
  return search;
}

export function buildGlossaryMergeRequest({ source, target, draft, sourceEdit, requestId, selections, proposal }) {
  return {
    request_id: requestId,
    target_term_id: target.id,
    target_version: proposal?.target?.version ?? target.version,
    ...(draft ? {draft} : {
      source_term_id: source.id,
      source_version: proposal?.source?.version ?? source.version,
      ...(sourceEdit ? {source_edit: sourceEdit} : {}),
    }),
    selections,
    ...(proposal ? {preview_digest: proposal.digest, expected_revision: proposal.glossary_revision} : {}),
  };
}
