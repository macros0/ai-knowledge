"use client";
import {useEffect,useState} from "react";
import Link from "next/link";
import {getDocument,friendlyApiError,ApiError} from "@/lib/api";
import {useI18n} from "@/i18n/LocaleContext";
import ErrorReference from "./ErrorReference";
const ACTIVE=["uploaded","queued","splitting","processing","indexing"];
export default function UploadBatchResults({upload}) {
  const {t}=useI18n();const [statuses,setStatuses]=useState({}); const [statusRevision,setStatusRevision]=useState(0);
  const idsKey=JSON.stringify(upload.items.filter(x=>x.docId).map(x=>x.docId));
  useEffect(()=>{
    const ids=JSON.parse(idsKey);if(!ids.length)return;
    let cancelled=false,timer;
    const refresh=async()=>{
      const results=await Promise.allSettled(ids.map(id=>getDocument(id)));
      if(cancelled)return;
      const next={};let active=false;
      results.forEach((result,index)=>{if(result.status === "fulfilled"){next[ids[index]]=result.value;active ||= ACTIVE.includes(result.value.status);}else next[ids[index]]={unknown:true};});
      setStatuses(next);if(active)timer=setTimeout(refresh,1500);
    };
    refresh();return()=>{cancelled=true;clearTimeout(timer);};
  },[idsKey,statusRevision]);
  if(!upload.items.length)return null;
  const retryCount=upload.items.filter(x=>["failed","uncertain","not-sent"].includes(x.state)).length;
  return <div className="upload-batch-results">
    <h3>{t("ux.uploadResults")}</h3>
    <ul>{upload.items.map(item=>{const doc=statuses[item.docId];return <li key={item.id}>
      <strong>{item.filename}</strong><span>{t(`ux.uploadState.${item.state}`)}</span>
      {item.docId && <><Link href={`/documents/${encodeURIComponent(item.docId)}/okf`}>{t("ux.openDocument")}</Link><span className="meta">{t("ux.processingStatus")}: {doc && !doc.unknown ? t(`status.${doc.status}`) : t("ux.statusUnknown")}</span></>}
      {doc?.problem && <span className="doc-problem-badge">{doc.problem_message || t("ux.documentWarning")}</span>}
      {item.duplicateInTrash && <span className="meta">{t("upload.trashTwin",{file:item.filename,name:item.duplicateInTrash.filename})}</span>}
      {item.errorCode && item.errorCode !== "unsupported_format" && <span className="meta">{friendlyApiError(new ApiError("",{code:item.errorCode,status:item.errorStatus}),t)}</span>}
      {item.state === "uncertain" && <span className="meta">{t("ux.uploadUncertainHint")}</span>}
      <ErrorReference requestId={item.requestId} localReportId={item.localReportId}/>
    </li>;})}</ul>
    <div className="upload-results-actions">
      {Object.values(statuses).some(doc=>doc.unknown) && <button type="button" className="btn ghost" onClick={()=>setStatusRevision(value=>value+1)}>{t("ux.refreshStatus")}</button>}
      {retryCount>0 && <button type="button" className="btn" disabled={upload.busy} onClick={upload.retryFailed}>{t("ux.retryUpload",{count:retryCount})}</button>}
      <button type="button" className="btn ghost" disabled={upload.busy} onClick={upload.clearCompleted}>{t("ux.clearUploadResults")}</button>
    </div>
  </div>;
}
