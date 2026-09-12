export function filterSearchableOptions(options = [], query = "", limit = 20) {
  const items = Array.isArray(options) ? options : [];
  const cap = Number.isFinite(limit) && limit > 0 ? Math.floor(limit) : items.length;
  const q = String(query || "").trim().toLowerCase();
  if (!q) return items.slice(0, cap);

  const starts = [];
  const contains = [];
  for (const option of items) {
    const haystacks = [option.value, option.label, option.searchText]
      .filter((part) => part != null)
      .map((part) => String(part).toLowerCase());
    if (haystacks.some((part) => part.startsWith(q))) starts.push(option);
    else if (haystacks.some((part) => part.includes(q))) contains.push(option);
  }
  return [...starts, ...contains].slice(0, cap);
}
