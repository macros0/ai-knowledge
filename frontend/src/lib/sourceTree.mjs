export function sourceDepth(sourceId) {
  if (!sourceId || sourceId === "root") return 0;
  return sourceId.split("/").length - 1;
}

export function sourceAncestors(sources, sourceId) {
  const byId = new Map((sources || []).map((source) => [source.source_id, source]));
  const ancestors = [];
  const seen = new Set();
  let current = byId.get(sourceId);
  while (current?.parent_source_id && !seen.has(current.parent_source_id)) {
    const parentId = current.parent_source_id;
    const parent = byId.get(parentId);
    if (!parent) break;
    ancestors.unshift(parentId);
    seen.add(parentId);
    current = parent;
  }
  return ancestors;
}

export function sourceHasChildren(sources, sourceId) {
  return (sources || []).some((source) => source.parent_source_id === sourceId);
}

export function visibleSourceIds(sources, expandedSourceIds = []) {
  const expanded = expandedSourceIds instanceof Set ? expandedSourceIds : new Set(expandedSourceIds);
  return (sources || []).filter((source) => sourceAncestors(sources, source.source_id).every(
    (ancestorId) => ancestorId === "root" || expanded.has(ancestorId),
  )).map((source) => source.source_id);
}

export function visibleSourceContentIds(sources, expandedSourceIds = []) {
  const expanded = expandedSourceIds instanceof Set ? expandedSourceIds : new Set(expandedSourceIds);
  return (sources || []).filter((source) => source.source_id === "root" || (
    expanded.has(source.source_id) && sourceAncestors(sources, source.source_id).every(
      (ancestorId) => ancestorId === "root" || expanded.has(ancestorId),
    )
  )).map((source) => source.source_id);
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

export function sourceMailMeta(source, format, t) {
  const date = sourceDateText(source, format, t);
  if (!date) return null;
  const sender = source.metadata?.sender;
  return [
    sender ? t("sources.sender", { sender }) : null,
    t("sources.mailDate", { date }),
  ].filter(Boolean).join(" · ");
}
