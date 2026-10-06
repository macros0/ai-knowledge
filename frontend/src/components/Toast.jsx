"use client";
import {createContext,useCallback,useContext,useEffect,useRef,useState} from "react";
import {createPortal} from "react-dom";
import ErrorReference from "./ErrorReference";
import {useI18n} from "@/i18n/LocaleContext";
import {resolveToastDuration} from "@/lib/toastPolicy.mjs";
const ToastContext=createContext(null);
function ToastItem({toast,dismiss,container}) {
  const {t}=useI18n();
  const [hover,setHover]=useState(false); const [focused,setFocused]=useState(false);
  const remaining=useRef(toast.duration); const item=useRef(null);
  useEffect(()=>{
    if(!toast.duration || hover || focused) return;
    const start=Date.now();
    const timer=setTimeout(()=>dismiss(toast.id),remaining.current);
    return ()=>{clearTimeout(timer);remaining.current=Math.max(0,remaining.current-(Date.now()-start));};
  },[toast.id,toast.duration,hover,focused,dismiss]);
  const close=()=>{
    const restore=item.current?.contains(document.activeElement);
    dismiss(toast.id);
    if(restore) container.current?.focus();
  };
  return <div ref={item} className={`toast toast-${toast.type}`} onMouseEnter={()=>setHover(true)} onMouseLeave={()=>setHover(false)}
    onFocus={()=>setFocused(true)} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget)) setFocused(false);}}>
    <span className="toast-msg" role={toast.type === "error" ? "alert" : "status"}>{toast.message}</span>
    <ErrorReference requestId={toast.requestId} localReportId={toast.localReportId}/>
    {toast.action && <button type="button" className="toast-action" onClick={()=>{close();toast.action.onClick?.();}}>{toast.action.label}</button>}
    <button type="button" className="toast-close" aria-label={t("ux.closeNotification")} onClick={close}>×</button>
  </div>;
}
export function ToastProvider({children}) {
  const {t}=useI18n(); const [toasts,setToasts]=useState([]);const [mounted,setMounted]=useState(false);
  const container=useRef(null);
  useEffect(()=>setMounted(true),[]);
  const dismiss=useCallback(id=>setToasts(items=>items.filter(x=>x.id !== id)),[]);
  const showToast=useCallback((payload,options={})=>{
    const structured=payload && typeof payload === "object" && typeof payload.message === "string";
    const message=structured ? payload.message : payload;
    const {type="error",duration,action=null,requestId=null,localReportId=null}=structured ? payload.options || {} : options;
    const toast={id:Date.now()+Math.random(),message,type,duration:resolveToastDuration(type,duration),action,requestId,localReportId};
    setToasts(items=>items.some(x=>x.message === message && x.type === type && x.requestId === requestId && x.localReportId === localReportId) ? items : [...items,toast]);
  },[]);
  return <ToastContext.Provider value={{showToast}}>{children}{mounted && createPortal(
    <div ref={container} className="toast-container" tabIndex={-1} aria-label={t("ux.notifications")}>{toasts.map(toast=><ToastItem key={toast.id} toast={toast} dismiss={dismiss} container={container}/>)}</div>,document.body
  )}</ToastContext.Provider>;
}
export function useToast() {return useContext(ToastContext) || {showToast:()=>{}};}
