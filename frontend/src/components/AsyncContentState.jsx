"use client";
import ErrorReference from "./ErrorReference";
import {useI18n} from "@/i18n/LocaleContext";
export default function AsyncContentState({state,message,error,emptyMessage,onRetry,onReset,onUpload,emptyActionLabel}) {
  const {t}=useI18n();
  if(state === "ready") return null;
  const failed=["error","stale-error"].includes(state);
  const text=failed ? (message || t("ux.loadFailed")) : state === "loading" ? t("ux.loading") : state === "refreshing" ? t("ux.refreshing") : state === "filtered-empty" ? t("ux.filteredEmpty") : emptyMessage || t("ux.documentsEmpty");
  return <div className={`async-content-state ${state}`} role={failed ? "alert" : "status"}>
    <p>{text}</p>
    {failed && <ErrorReference requestId={error?.requestId} localReportId={error?.localReportId}/>}
    {failed && onRetry && <button type="button" className="btn ghost" onClick={onRetry}>{t("common.retry")}</button>}
    {state === "filtered-empty" && onReset && <button type="button" className="btn ghost" onClick={onReset}>{t("docs.resetFilters")}</button>}
    {state === "empty" && onUpload && <button type="button" className="btn" onClick={onUpload}>{emptyActionLabel || t("docs.uploadAction")}</button>}
  </div>;
}
