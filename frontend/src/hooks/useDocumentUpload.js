"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {uploadDocument} from "@/lib/api";
import {createDocumentUploadRunner} from "@/lib/documentUploadBatch.mjs";
import {retryableUploadItems,updateUploadItem} from "@/lib/documentUploadState.mjs";
import {useI18n} from "@/i18n/LocaleContext";
export default function useDocumentUpload() {
  const {t}=useI18n();
  const [busy,setBusy]=useState(false),[progress,setProgress]=useState({completed:0,total:0}),[review,setReview]=useState(null);
  const [items,setItems]=useState([]),[revision,setRevision]=useState(0);
  const mounted=useRef(true),decision=useRef(null),files=useRef(new Map()),ids=useRef(new Map()),parameters=useRef(null),busyRef=useRef(false);
  const runner=useRef(null);
  useEffect(()=>{const retainedFiles=files.current,retainedIds=ids.current;mounted.current=true;return()=>{mounted.current=false;decision.current?.(false);decision.current=null;retainedFiles.clear();retainedIds.clear();};},[]);
  useEffect(()=>{
    if(!busy) return;
    const warn=e=>{e.preventDefault();e.returnValue="";};
    window.addEventListener("beforeunload",warn);return()=>window.removeEventListener("beforeunload",warn);
  },[busy]);
  const resolveReview=useCallback(accepted=>{const resolve=decision.current;decision.current=null;setReview(null);resolve?.(accepted);},[]);
  const getRunner=()=>{
    if(!runner.current) runner.current=createDocumentUploadRunner({
      uploadDocument,isActive:()=>mounted.current,
      onBusy:value=>{busyRef.current=value;if(mounted.current)setBusy(value);},
      onProgress:value=>{if(mounted.current)setProgress(value);},
      onUploaded:()=>{if(mounted.current)setRevision(n=>n+1);},
      onItemState:(file,state,details)=>{
        if(!mounted.current) return;
        const id=ids.current.get(file);
        setItems(previous=>updateUploadItem(previous,id,state,details));
        if(["uploaded","skipped"].includes(state)){files.current.delete(id);ids.current.delete(file);}
      },
      reviewUpload:info=>!mounted.current ? Promise.resolve(false) : new Promise(resolve=>{decision.current=resolve;setReview(info);}),
    });
    return runner.current;
  };
  const start=async(selected,nextParameters={})=>{
    if(busyRef.current || !selected?.length) return;
    if(files.current.size && !window.confirm(t("ux.replaceUploadResults"))) return;
    const batch=Array.from(selected);
    files.current.clear();ids.current.clear();
    const rows=batch.map(file=>{const id=crypto.randomUUID();files.current.set(id,file);ids.current.set(file,id);return {id,filename:file.name,state:"pending"};});
    parameters.current={...nextParameters,tags:[...(nextParameters.tags || [])]};
    setItems(rows);
    return getRunner().start(batch,parameters.current);
  };
  const retryFailed=async()=>{
    if(busyRef.current) return;
    const retry=retryableUploadItems(items,files.current);
    if(!retry.length) return;
    const batch=retry.map(item=>files.current.get(item.id));
    setItems(previous=>previous.map(item=>retry.some(x=>x.id === item.id) ? {id:item.id,filename:item.filename,state:"pending"} : item));
    return getRunner().start(batch,parameters.current);
  };
  const clearCompleted=()=>{
    if(busyRef.current) return;
    files.current.clear();ids.current.clear();setItems([]);setProgress({completed:0,total:0});
  };
  return {busy,progress,items,revision,review,start,retryFailed,resolveReview,clearCompleted};
}
