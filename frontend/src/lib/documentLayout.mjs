/**
 * Build the optional values shown on the compact second line of a document row.
 * Keeping this rule pure makes the density contract easy to test independently
 * from React and the localized labels used by the caller.
 */
export function buildCompactDocumentMeta({
  progress = null,
  tags = [],
  development = null,
  locale = null,
  uploader = null,
  date = null,
} = {}) {
  const result = [];
  if (progress) result.push({ key: "progress", value: progress });
  if (Array.isArray(tags) && tags.length > 0) {
    result.push({ key: "tags", value: tags.join(", ") });
  }
  if (development) result.push({ key: "development", value: development });
  if (locale) result.push({ key: "locale", value: String(locale).toUpperCase() });
  if (uploader) result.push({ key: "uploader", value: uploader });
  if (date) result.push({ key: "date", value: date });
  return result;
}

export function countActiveDocumentFilters(filters = {}) {
  return Object.values(filters).filter((value) => value !== false && value !== null && value !== undefined && value !== "").length;
}
