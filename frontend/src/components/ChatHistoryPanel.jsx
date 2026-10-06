"use client";
import {useCallback,useEffect,useRef,useState} from "react";
import {useRouter,useSearchParams} from "next/navigation";
import {deleteChatSession,friendlyApiError,getChatThread,listChatSessions} from "@/lib/api";
import {apiToast} from "@/lib/apiToast.mjs";
import {resolveAsyncContentState} from "@/lib/asyncContentState.mjs";
import {fmtDate,HistoryMessage} from "./ChatHistoryShared";
import {useToast} from "./Toast";
import {useI18n} from "@/i18n/LocaleContext";
import Modal from "./Modal";
import AsyncContentState from "./AsyncContentState";
export default function ChatHistoryPanel() {
  const {showToast}=useToast(); const {t,tc}=useI18n();
  const router=useRouter(); const params=useSearchParams();
  const session=params.get("session") || "";
  const [sessions,setSessions]=useState([]);
  const [listState,setListState]=useState({pending:true,hasLoaded:false,error:null});
  const [thread,setThread]=useState(null);
  const [pendingDelete,setPendingDelete]=useState(null);
  const [deleting,setDeleting]=useState(false);
  const [revision,setRevision]=useState(0);
  const sequence=useRef(0);
  const navigate=useCallback(id=>{
    const next=new URLSearchParams(params);
    if(id) next.set("session",id); else next.delete("session");
    router.push(next.size ? `/chat/history?${next}` : "/chat/history",{scroll:false});
  },[params,router]);
  const load=useCallback(async()=>{
    const seq=++sequence.current;
    setListState(prev=>({...prev,pending:true,error:null}));
    try { const result=await listChatSessions();
      if(seq !== sequence.current) return;
      setSessions(result.sessions);
      setListState({pending:false,hasLoaded:true,error:null});
    } catch(error) {if(seq === sequence.current) setListState(prev=>({...prev,pending:false,error}));}
  },[]);
  useEffect(()=>{load();return ()=>{sequence.current++;};},[load]);
  useEffect(()=>{
    if(!session) return;
    let cancelled=false;
    setThread({id:session,pending:true,data:null,error:null});
    getChatThread(session).then(data=>{if(!cancelled) setThread({id:session,pending:false,data,error:null});})
      .catch(error=>{if(!cancelled) setThread({id:session,pending:false,data:null,error});});
    return ()=>{cancelled=true;};
  },[session,revision]);
  const current=thread?.id === session ? thread : null;
  const confirmDelete=async()=>{
    if(deleting || !pendingDelete?.session_id) return;
    setDeleting(true);
    try { await deleteChatSession(pendingDelete.session_id);
      if(session === pendingDelete.session_id) navigate(null);
      setPendingDelete(null); load();
      showToast(t("chat.threadDeleted"),{type:"success"});
    } catch(error) {showToast({...apiToast(error,t,{type:"error"}),message:t("chat.deleteError",{message:friendlyApiError(error,t)})});}
    finally {setDeleting(false);}
  };
  return <section className="panel">
    <h2 className="panel-title">{t("chat.historyTitle")}</h2>
    {session ? <div className="history-thread">
      <div className="history-thread-head">
        <button type="button" className="btn ghost" onClick={()=>navigate(null)}>{t("chat.backToList")}</button>
        {current?.data && <div className="history-thread-title"><strong>{current.data.title || t("chat.untitled")}</strong><span className="meta">{fmtDate(current.data.created_at)}</span></div>}
      </div>
      <AsyncContentState state={current?.error ? "error" : !current || current.pending ? "loading" : "ready"}
        error={current?.error} message={current?.error ? t("chat.openThreadError",{message:friendlyApiError(current.error,t)}) : null} onRetry={()=>setRevision(n=>n+1)} />
      {current?.data && <div className="chat-log history-log">{current.data.messages.map((m,i)=><HistoryMessage key={i} m={m}/>)}</div>}
    </div> : <>
      <AsyncContentState state={resolveAsyncContentState({...listState,hasData:sessions.length>0})}
        error={listState.error} message={listState.error ? t("chat.historyLoadError",{message:friendlyApiError(listState.error,t)}) : null}
        emptyMessage={t("chat.historyEmpty")} emptyActionLabel={t("chat.askFirst")} onRetry={load} onUpload={()=>router.push("/chat")} />
      <ul className="history-list">{sessions.map(s=><li key={s.session_id} className="history-item">
        <button type="button" className="history-open" onClick={()=>navigate(s.session_id)}><span className="history-item-title">{s.title || t("chat.untitled")}</span><span className="meta">{tc("chat.messagesCount",s.message_count)} · {fmtDate(s.updated_at)}</span></button>
        <button type="button" className="btn ghost" onClick={()=>setPendingDelete(s)} title={t("chat.deleteThreadTitle")}>{t("chat.deleteThread")}</button>
      </li>)}</ul>
    </>}
    {pendingDelete && <Modal title={t("chat.deleteThreadConfirmTitle")} onClose={()=>{if(!deleting)setPendingDelete(null);}} footer={<>
      <button type="button" className="modal-btn" disabled={deleting} onClick={()=>setPendingDelete(null)}>{t("common.cancel")}</button>
      <button type="button" className="modal-btn danger" disabled={deleting} onClick={confirmDelete}>{t("chat.deleteThread")}</button>
    </>}><p>{t("chat.deleteThreadConfirmText",{title:pendingDelete.title || t("chat.untitled")})}</p></Modal>}
  </section>;
}
