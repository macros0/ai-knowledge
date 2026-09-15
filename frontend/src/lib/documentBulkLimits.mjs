export const BULK_LIMITS = Object.freeze({ export: 1000, tags: 50, delete: 50, regenerate: 20 });

export const selectionLimit = ({ isAdmin, exportEnabled, exportMaxDocs = BULK_LIMITS.export }) =>
  isAdmin && exportEnabled ? exportMaxDocs : BULK_LIMITS.tags;

export function bulkCapabilities(count, { isAdmin, exportEnabled, exportMaxDocs = BULK_LIMITS.export }) {
  return {
    export: isAdmin && exportEnabled && count > 0 && count <= exportMaxDocs,
    tags: count > 0 && count <= BULK_LIMITS.tags,
    delete: isAdmin && count > 0 && count <= BULK_LIMITS.delete,
    regenerate: isAdmin && count > 0 && count <= BULK_LIMITS.regenerate,
  };
}
