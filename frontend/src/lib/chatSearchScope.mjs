export const SEARCH_SCOPE_MAX_DOCUMENTS = 500;

export function reduceSearchScope(state, action) {
  if (action.type === 'documents') {
    const documents = typeof action.value === 'function' ? action.value(state.documents) : action.value;
    return { documents, enabled: documents.length > 0 && state.enabled };
  }
  if (action.type === 'enabled') {
    const enabled = typeof action.value === 'function' ? action.value(state.enabled) : action.value;
    return { ...state, enabled: state.documents.length > 0 && enabled };
  }
  return state;
}

export function addScopeDocuments(documents, sources, selectedIndexes) {
  const selected = new Set(selectedIndexes);
  return mergeScopeDocuments(documents, sources.filter(source => selected.has(source.source_index)));
}

export function addAvailableScopeDocuments(documents, availableDocuments, selectedIds) {
  const selected = new Set(selectedIds);
  return mergeScopeDocuments(documents, availableDocuments.filter(document => document.available === true && selected.has(document.doc_id)));
}

function mergeScopeDocuments(documents, additions) {
  const next = new Map(documents.map((document) => [document.doc_id, document]));
  for (const source of additions) {
    if (!source.doc_id || next.has(source.doc_id)) continue;
    next.set(source.doc_id, { doc_id: source.doc_id, filename: source.filename || source.doc_id });
  }
  if (next.size > SEARCH_SCOPE_MAX_DOCUMENTS) throw new RangeError('Search scope document limit exceeded');
  return [...next.values()];
}

export function scopeRequestIds(documents, enabled) {
  return enabled ? documents.map((document) => document.doc_id) : null;
}
