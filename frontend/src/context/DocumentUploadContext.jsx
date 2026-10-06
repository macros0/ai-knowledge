"use client";
import {createContext,useContext} from "react";
import Link from "next/link";
import {useAuth} from "./AuthContext";
import useDocumentUpload from "@/hooks/useDocumentUpload";
import UploadReviewDialog from "@/components/UploadReviewDialog";
import {useI18n} from "@/i18n/LocaleContext";
const Context=createContext(null);
export function DocumentUploadProvider({children}) {
  const {user,mode}=useAuth();const owner=`${mode}:${user?.user_id ?? user?.username ?? "anonymous"}`;
  return <UploadState key={owner}>{children}</UploadState>;
}
function UploadState({children}) {
  const upload=useDocumentUpload();const {t}=useI18n();
  return <Context.Provider value={upload}>{children}<UploadReviewDialog review={upload.review} onDecision={upload.resolveReview}/>
    {upload.busy && <div className="upload-global-progress" role="status"><Link href="/?view=upload">{t("upload.batchProgress",{current:upload.progress.completed,total:upload.progress.total})}</Link><span>{t("ux.uploadKeepTab")}</span></div>}
  </Context.Provider>;
}
export function useDocumentUploadContext() {
  const value=useContext(Context);if(!value)throw new Error("DocumentUploadProvider is required");return value;
}
