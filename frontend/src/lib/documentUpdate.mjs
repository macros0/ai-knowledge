const RUNNING = new Set(["queued", "processing", "splitting", "indexing"]);
const STOPPED = new Set(["paused", "failed", "error"]);

export function shouldPollOkfDocument(doc) {
  return RUNNING.has(doc.status) || doc.status === "uploaded" || doc.status === "paused" || Boolean(doc.update_cancelling);
}

export function documentUpdateView(doc) {
  const published = doc.has_published_version;
  const cancelling = Boolean(doc.update_cancelling);
  const running = RUNNING.has(doc.status);
  const failed = STOPPED.has(doc.status);
  return {
    statusKey: cancelling ? "docs.updateCancelling" : published && running ? "docs.updateRunning" : null,
    noticeKey: published && (running || cancelling) ? "docs.previousVersionAvailable"
      : published && failed ? "docs.updateFailedPreviousSaved" : null,
    actionKey: published && doc.can_cancel_update && (running || failed)
      ? running ? "docs.cancelUpdate" : "docs.keepPreviousVersion" : null,
    disabled: cancelling,
  };
}
