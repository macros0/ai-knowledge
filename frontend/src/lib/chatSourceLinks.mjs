export function documentHref(source) {
  return source?.doc_id ? `/documents/${encodeURIComponent(source.doc_id)}/okf` : null;
}

export function sourceHref(source) {
  const documentUrl = documentHref(source);
  if (!documentUrl) return null;
  if (source.point_type === "chunk" && source.chunk_index != null) {
    return `/documents/${encodeURIComponent(source.doc_id)}/chunks/${source.chunk_index}`;
  }
  const filename = source.filepath?.split(/[\\/]/).at(-1) || "";
  const legacySlug = /\.md$/i.test(filename) ? filename.replace(/\.md$/i, "") : "";
  const slug = source.source_slug || legacySlug;
  return slug ? `${documentUrl}/${encodeURIComponent(slug)}.md` : documentUrl;
}
