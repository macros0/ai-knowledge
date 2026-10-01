export const SEARCH_SCOPE_MAX_DOCUMENTS = 500;

export function addScopeDocuments(documents, sources, selectedIndexes) {
  const selected = new Set(selectedIndexes);
  const next = new Map(documents.map((document) => [document.doc_id, document]));
  for (const source of sources) {
    if (!source.doc_id || !selected.has(source.source_index) || next.has(source.doc_id)) continue;
    next.set(source.doc_id, { doc_id: source.doc_id, filename: source.filename || source.doc_id });
  }
  if (next.size > SEARCH_SCOPE_MAX_DOCUMENTS) throw new RangeError('Search scope document limit exceeded');
  return [...next.values()];
}

export function scopeRequestIds(documents, enabled) {
  return enabled ? documents.map((document) => document.doc_id) : null;
}
