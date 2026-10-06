const SCOPE_FIELDS = ["searchInput", "uploader", "problem", "module", "development", "tag", "status", "from", "to", "locale"];

export function selectionScopeKey(filters) {
  return JSON.stringify(SCOPE_FIELDS.map((key) => filters[key] ?? ""));
}

export function shouldApplySelectionResult(requestScope, currentScope, requestVersion, currentVersion) {
  return requestScope === currentScope && requestVersion === currentVersion;
}

// Server data follows the debounced query; immediate input only invalidates selection.
export function documentRequestScopeKey(filters, search) {
  return selectionScopeKey({...filters, searchInput:search});
}
