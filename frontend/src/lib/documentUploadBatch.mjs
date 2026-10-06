import { uploadWithReview } from "./uploadReview.mjs";

export const SUPPORTED_DOCUMENT_EXTENSIONS = [".docx", ".xlsx", ".pdf", ".eml", ".msg"];

export async function runDocumentUploadBatch(files, {
  uploadOne, reviewUpload = async () => false, onUploaded = () => {},
  onProgress = () => {}, onItemState = () => {}, isActive = () => true,
}) {
  const supported = [], skipped = [];
  for (const file of Array.from(files)) {
    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    (SUPPORTED_DOCUMENT_EXTENSIONS.includes(extension) ? supported : skipped).push(file);
  }
  const summary = { uploadedCount: 0, supportedCount: supported.length, skipped: skipped.map((file) => file.name), failed: [], storageFull: false, trashTwins: [] };
  const publish=(file,state,details={})=>{if(isActive()) onItemState(file,state,details);};
  for(const file of skipped) publish(file,"skipped",{errorCode:"unsupported_format"});
  if(isActive()) onProgress({ completed: 0, total: supported.length });
  let completed = 0;
  for (const file of supported) {
    if (!isActive()) break;
    publish(file,"uploading");
    try {
      const doc = await uploadWithReview(file, (allow) => isActive() ? uploadOne(file, allow) : Promise.resolve(null), async info=>{
        publish(file,"awaiting-review");
        const accepted=await reviewUpload(info);
        if(accepted && !info.exact) publish(file,"uploading");
        return accepted;
      });
      if (doc) {
        summary.uploadedCount += 1;
        if (doc.duplicate_in_trash) summary.trashTwins.push({ file: file.name, twin: doc.duplicate_in_trash });
        publish(file,"uploaded",{docId:doc.id,docStatus:doc.status,duplicateInTrash:doc.duplicate_in_trash});
        if (isActive()) onUploaded(doc);
      } else publish(file,"skipped");
    } catch (error) {
      summary.failed.push({ file: file.name, error });
      publish(file,error.status === 0 ? "uncertain" : "failed",{errorCode:error.code,requestId:error.requestId,localReportId:error.localReportId,errorStatus:error.status});
      if (error.code === "storage_full") summary.storageFull = true;
    }
    completed += 1;
    if (isActive()) onProgress({ completed, total: supported.length });
    if (summary.storageFull) {
      for(const remaining of supported.slice(completed)) publish(remaining,"not-sent",{errorCode:"storage_full"});
      break;
    }
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
