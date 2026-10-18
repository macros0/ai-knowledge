const stable = value => value && typeof value === "object" && !Array.isArray(value)
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])])) : value;
const encode = dictionary => Object.fromEntries(Object.entries(dictionary || {}).map(([key, value]) => [key, JSON.stringify(stable(value))]));

export function dictionaryComparison(base, local, remote) {
  const encoded = {base: encode(base), local: encode(local), remote: encode(remote)};
  const keys = new Set([...Object.keys(base || {}), ...Object.keys(local || {}), ...Object.keys(remote || {})]);
  return {...encoded, fields: [...keys].filter(key => encoded.base[key] !== encoded.local[key] || encoded.base[key] !== encoded.remote[key]).sort()};
}

export function applyDictionaryComparison(remote, selected) {
  const result = {...remote};
  for (const [key, value] of Object.entries(selected)) {
    if (!value) delete result[key];
    else Object.defineProperty(result, key, {value: JSON.parse(value), enumerable: true, configurable: true, writable: true});
  }
  return result;
}
