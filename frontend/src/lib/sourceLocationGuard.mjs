// Location and text may arrive from separate requests during publication.
// Only apply coordinates to the exact canonical snapshot that was validated.
export function guardSourceLocation(location, chunks) {
  if (!location?.chunk_hash) return location;
  const target = chunks.find(chunk => chunk.chunk_index === location.chunk_index);
  if (!target) return {...location, status: 'unavailable', spans: [], reason: 'missing'};
  if (target.source_id !== location.source_id) {
    return {...location, status: 'unavailable', chunk_index: null, spans: [], reason: 'changed'};
  }
  if (target.chunk_hash !== location.chunk_hash || target.generation_id !== location.generation_id) {
    return {...location, status: 'chunk', basis: 'chunk', spans: [], reason: 'changed'};
  }
  return location;
}
