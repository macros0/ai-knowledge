/** Images in document content may read only that document's attachment files. */
export function resolveDocumentImage(src, docId) {
  if (!docId) return null;
  const match = /^(?:\.\/)?attachments\/([^/?#\\]+)$/.exec(String(src || ""));
  if (!match) return null;
  let filename;
  try {
    filename = decodeURIComponent(match[1]);
  } catch {
    return null;
  }
  if (!filename || filename === "." || filename === ".." || /[/\\\x00-\x1f\x7f]/.test(filename)) return null;
  return `/api/documents/${encodeURIComponent(docId)}/okf/attachments/${encodeURIComponent(filename)}`;
}
