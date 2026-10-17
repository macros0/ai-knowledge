export function tableStatusKey(quality) {
  if (quality?.cause_code === 'content_omitted') return 'tables.contentOmitted';
  if (quality?.complete !== true) return 'tables.coverageUnknown';
  if (quality.header_status === 'ambiguous') return 'tables.headerAmbiguous';
  if (quality.header_status !== 'verified') return 'tables.headerUnknown';
  return quality.classifier_cause ? 'tables.fallbackVerified' : 'tables.coverageVerified';
}

export function mergeTablePages(previous, next) {
  const continuations = new Map((previous.continuation || []).map(item => [`${item.row_number}:${item.column}`, item]));
  for (const item of next.continuation || []) continuations.set(`${item.row_number}:${item.column}`, item);
  return {...next, rows:[...previous.rows, ...next.rows], continuation:[...continuations.values()]};
}
