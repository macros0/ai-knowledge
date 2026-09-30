export function updateSelection(selected, indexes, checked, sources) {
  const next = new Set(selected);
  for (const index of indexes) {
    if (checked) next.add(index);
    else next.delete(index);
  }
  return sources.map((source, index) => source.source_index ?? index + 1).filter((index) => next.has(index));
}

export function selectionState(selected, indexes) {
  const set = selected instanceof Set ? selected : new Set(selected);
  const count = indexes.filter((index) => set.has(index)).length;
  return count === 0 ? 'none' : count === indexes.length ? 'all' : 'some';
}
