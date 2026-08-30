// Чистая логика фильтра и сортировки списка документов (без React-зависимостей).
// Вынесена из DocumentList.jsx, чтобы её можно было юнит-тестировать и
// бенчмаркать в Node (frontend/test/docFilter.bench.mjs), а не только в браузере.
//
// Фильтр по загрузчику вынесен на бэкенд (?uploader=) — здесь осталась только
// маска по имени файла и сортировка по полям документа.

// Сортировка: ключ дропдауна → { field, direction }. Ссылки на объекты стабильны,
// поэтому маппинг можно читать в useMemo без расширения массива зависимостей.
export const SORT_OPTIONS = {
  date_desc: { field: "created_at", direction: "desc" },
  date_asc: { field: "created_at", direction: "asc" },
  name_asc: { field: "filename", direction: "asc" },
  name_desc: { field: "filename", direction: "desc" },
  uploader_asc: { field: "uploaded_by", direction: "asc" },
  uploader_desc: { field: "uploaded_by", direction: "desc" },
};

export function globToRegExp(glob) {
  const pattern = glob
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");
  return new RegExp(`^${pattern}$`, "i");
}

export function filterDocuments(docs, { mask = "" } = {}) {
  const trimmed = (mask ?? "").trim();
  let re = null;
  if (trimmed) {
    try {
      re = globToRegExp(trimmed);
    } catch {
      re = null;
    }
  }
  return docs.filter((d) => {
    if (re && !re.test(d.filename || "")) return false;
    return true;
  });
}

function compareDates(a, b) {
  const ta = Date.parse(a ?? "");
  const tb = Date.parse(b ?? "");
  const na = Number.isNaN(ta) ? 0 : ta;
  const nb = Number.isNaN(tb) ? 0 : tb;
  return na - nb;
}

export function sortDocuments(docs, { field, direction } = {}) {
  const dir = direction === "asc" ? 1 : -1;
  const sorted = [...docs];
  sorted.sort((a, b) => {
    if (field === "created_at") {
      return dir * compareDates(a.created_at, b.created_at);
    }
    const av = a[field];
    const bv = b[field];
    // null/undefined — всегда в конец, независимо от направления.
    if (av == null || bv == null) {
      if (av == null && bv == null) return 0;
      return av == null ? 1 : -1;
    }
    const cmp = av.localeCompare(bv, undefined, { sensitivity: "base" });
    if (cmp !== 0) return dir * cmp;
    // Tie-breaker по дате (новые сначала). Компенсирует непредсказуемый исходный
    // порядок docs (БД без явного ORDER BY может менять порядок между запросами),
    // а не «нестабильность» Array.prototype.sort — с ES2019 sort и так стабилен.
    // Без этого равные по полю сортировки элементы «прыгали» бы между рендерами.
    return -1 * compareDates(a.created_at, b.created_at);
  });
  return sorted;
}
