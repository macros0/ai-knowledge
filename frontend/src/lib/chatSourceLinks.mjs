export function documentHref(source) {
  return source?.doc_id ? `/documents/${encodeURIComponent(source.doc_id)}/okf` : null;
}

export function sourceHref(source) {
  const documentUrl = documentHref(source);
  if (!documentUrl) return null;
  const table = source.table_refs?.[0];
  if (table?.table_id && table?.generation_id && Number.isInteger(table.row_number)) {
    const referenced = table.text_start != null ? table.columns : [...new Set(source.table_refs.filter(ref => ref.table_id === table.table_id &&
      ref.generation_id === table.generation_id && ref.row_number === table.row_number)
      .flatMap(ref => ref.columns))].sort((a, b) => a - b);
    const columns = referenced.length <= 12 ? referenced : [...referenced.slice(0, 6), ...referenced.slice(-6)];
    const query = new URLSearchParams({generation_id: table.generation_id, row: String(table.row_number),
                                      columns: columns.join(","), source_id: table.source_id});
    if (table.text_start != null) {query.set("text_start", String(table.text_start));query.set("text_end", String(table.text_end));}
    return `/documents/${encodeURIComponent(source.doc_id)}/tables/${encodeURIComponent(table.table_id)}?${query}`;
  }
  if (source.point_type === "chunk" && source.chunk_index != null) {
    return `/documents/${encodeURIComponent(source.doc_id)}/chunks/${source.chunk_index}`;
  }
  const filename = source.filepath?.split(/[\\/]/).at(-1) || "";
  const legacySlug = /\.md$/i.test(filename) ? filename.replace(/\.md$/i, "") : "";
  const slug = source.source_slug || legacySlug;
  return slug ? `${documentUrl}/${encodeURIComponent(slug)}.md` : documentUrl;
}
