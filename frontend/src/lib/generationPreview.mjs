export function generationPreviewOverLimit(preview) {
  return !!preview && preview.eligible_doc_ids.length > preview.max_docs;
}
