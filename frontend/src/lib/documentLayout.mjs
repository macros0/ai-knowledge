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

export function documentQueueView(status, t) {
  const fields = ["processing", "processing_limit", "queued", "queue_limit", "available"];
  if (!status || fields.some((field) => !Number.isInteger(status[field]) || status[field] < 0)) return null;
  return {
    summary: t("docs.queue.summary", {
      processing: status.processing,
      processingLimit: status.processing_limit,
      queued: status.queued,
      queueLimit: status.queue_limit,
    }),
    full: status.available === 0,
  };
}

export function resetDocumentFilters() {
  return {
    searchInput: "",
    search: "",
    chosenUploader: "",
    problemOnly: false,
    moduleFilter: "",
    devFilter: null,
    tagFilter: "",
    statusFilter: "",
    dateFrom: "",
    dateTo: "",
    localeFilter: "",
    page: 0,
  };
}

// Явные фильтры ссылки заменяют сохранённый вид целиком. Параметры
// одноразового префилла загрузки не должны мешать восстановлению списка.
export function resolveDocumentFilterParams(searchParams, savedQuery = "", savedUrlQuery = null) {
  const keys = ["q", "uploader", "module", "tag", "dev", "problem", "status", "from", "to", "locale", "sort", "page", "group"];
  // При возврате из корзины URL списка может отставать от последнего ввода
  // из-за debounce. Только отличающаяся ссылка задаёт новый явный выбор.
  const previousUrl = new URLSearchParams(savedUrlQuery ?? "");
  const isNewLink = savedUrlQuery === null || keys.some((key) => searchParams.get(key) !== previousUrl.get(key));
  const source = keys.some((key) => searchParams.has(key)) && isNewLink
    ? searchParams
    : new URLSearchParams(savedQuery);
  const params = new URLSearchParams();
  for (const key of keys) {
    if (source.has(key)) params.set(key, source.get(key));
  }
  return params;
}
