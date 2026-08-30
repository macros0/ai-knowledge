// Чистая логика фильтра списка документов (без React-зависимостей).
// Вынесена из DocumentList.jsx, чтобы её можно было юнит-тестировать и
// бенчмаркать в Node (frontend/test/docFilter.bench.mjs), а не только в браузере.

export function globToRegExp(glob) {
  const pattern = glob
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");
  return new RegExp(`^${pattern}$`, "i");
}

export function filterDocuments(
  docs,
  { mask = "", uploaderFilter = "", scope = "all" } = {}
) {
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
    if (scope === "all" && uploaderFilter && d.uploaded_by !== uploaderFilter) {
      return false;
    }
    return true;
  });
}
