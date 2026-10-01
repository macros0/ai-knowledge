import { uploadWithReview } from "./uploadReview.mjs";

export const SUPPORTED_DOCUMENT_EXTENSIONS = [".docx", ".xlsx", ".pdf", ".eml", ".msg"];

export async function runDocumentUploadBatch(files, {
  uploadOne, reviewUpload = async () => false, onUploaded = () => {},
  onProgress = () => {}, isActive = () => true,
}) {
  const supported = [], skipped = [];
  for (const file of Array.from(files)) {
    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    (SUPPORTED_DOCUMENT_EXTENSIONS.includes(extension) ? supported : skipped).push(file);
  }
  const summary = { uploadedCount: 0, supportedCount: supported.length, skipped: skipped.map((file) => file.name), failed: [], storageFull: false, trashTwins: [] };
  onProgress({ completed: 0, total: supported.length });
  let completed = 0;
  for (const file of supported) {
    if (!isActive()) break;
    try {
      const doc = await uploadWithReview(file, (allow) => uploadOne(file, allow), reviewUpload);
      if (doc) {
        summary.uploadedCount += 1;
        if (doc.duplicate_in_trash) summary.trashTwins.push({ file: file.name, twin: doc.duplicate_in_trash });
        if (isActive()) onUploaded(doc);
      }
    } catch (error) {
      summary.failed.push({ file: file.name, error });
      if (error.code === "storage_full") summary.storageFull = true;
    }
    completed += 1;
    if (isActive()) onProgress({ completed, total: supported.length });
    if (summary.storageFull) break;
  }
  return summary;
}

export function createDocumentUploadRunner({ uploadDocument, onBusy = () => {}, ...callbacks }) {
  let busy = false;
  return {
    async start(files, parameters = {}) {
      if (busy || !files?.length) return null;
      const { tags = [], developmentId = null, canonicalLocale } = parameters;
      const snapshotTags = [...tags];
      busy = true;
      onBusy(true);
      try {
        return await runDocumentUploadBatch(files, {
          ...callbacks,
          uploadOne: (file, allowSimilar) => uploadDocument(file, snapshotTags, { developmentId, canonicalLocale, allowSimilar }),
        });
      } finally {
        busy = false;
        onBusy(false);
      }
    },
  };
}
