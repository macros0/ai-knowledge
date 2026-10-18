/** Three-way comparison of explicitly editable scalar fields. Never changes revisions. */
export function reconcileReferenceDraft({ base, local, remote, fields }) {
  const merged = {}, conflicts = [];
  const valueOf = (record, field) => record[field] ?? "";
  for (const field of fields) {
    const original = valueOf(base, field), mine = valueOf(local, field), current = valueOf(remote, field);
    merged[field] = Object.is(mine, original) ? current : mine;
    if (!Object.is(mine, original) && !Object.is(current, original) && !Object.is(mine, current)) {
      conflicts.push({ field, base: original, local: mine, remote: current });
    }
  }
  return { merged, conflicts };
}

export function chunkReferenceCards(items, limit = 100) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new RangeError("Invalid card batch limit");
  const groups = new Map();
  for (const item of items) {
    if (!groups.has(item.tag_id)) groups.set(item.tag_id, []);
    groups.get(item.tag_id).push(item);
  }
  const batches = []; let batch = [], cards = 0;
  for (const group of groups.values()) {
    if (cards === limit) { batches.push(batch); batch = []; cards = 0; }
    batch.push(...group); cards++;
  }
  if (batch.length) batches.push(batch);
  return batches;
}
