export function buildBulkTagChange({ addTag = "", removeTag = "", canonicalLocale } = {}) {
  const add = addTag.trim(), remove = removeTag.trim();
  if ((!add && !remove) || (add && add === remove)) return null;
  return { add: add ? [add] : [], remove: remove ? [remove] : [], canonicalLocale };
}
