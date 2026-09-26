export function sourceDepth(sourceId) {
  if (!sourceId || sourceId === "root") return 0;
  return sourceId.split("/").length - 1;
}

export function sourceDownloadUrl(docId, sourceId) {
  return `/api/documents/${encodeURIComponent(docId)}/sources/download?source_id=${encodeURIComponent(sourceId)}`;
}

export function canDownloadSource(source) {
  return source?.artifact_kind === "original" || source?.artifact_kind === "container_only";
}

export function sourceStatusKey(status) {
  return status ? `sources.status.${status}` : null;
}

export function sourceLocationKey(source) {
  const location = source?.metadata?.document_location;
  return ["body", "table", "header", "footer", "textbox"].includes(location)
    ? `sources.location.${location}` : null;
}

const warningLabels = {
  mail_decode_recovered: "decode",
  mail_alternative_mismatch: "alternative",
  unsupported_rtf_body: "rtf",
  external_attachment: "external",
  attachment_unavailable: "unavailable",
  encrypted_mail: "protected",
  protected_mail: "protected",
  unsupported_mail_class: "unsupported",
  unsupported_attachment_method: "unsupported",
  unsupported_attachment_format: "format",
  cid_image_unavailable: "cid",
  mail_parse_failed: "parseFailed",
  attachment_parse_failed: "parseFailed",
  mime_part_parse_failed: "parseFailed",
  attachment_count_exceeded: "count",
  mime_count_exceeded: "count",
  attachment_depth_exceeded: "depth",
  mime_depth_exceeded: "depth",
  attachment_size_exceeded: "size",
  text_limit_exceeded: "textLimit",
  attachment_storage_blocked: "storage",
  mail_import_disabled: "disabled",
};

export function sourceWarningKeys(source) {
  if (!Array.isArray(source?.warnings)) return [];
  return [...new Set(source.warnings.filter((warning) => typeof warning?.code === "string").map(({ code }) =>
    `sources.warning.${Object.hasOwn(warningLabels, code) ? warningLabels[code] : "unknown"}`
  ))];
}

export function sourceDateText(source, format, t) {
  if (source?.kind !== "mail" && source?.metadata?.mail !== true) return null;
  const date = source.metadata?.sent_at;
  return typeof date === "string" && Number.isFinite(Date.parse(date))
    ? format(date) : t("sources.dateUnknown");
}
