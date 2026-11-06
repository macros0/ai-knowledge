"use client";
import {useEffect,useRef,useState} from "react";
import {useI18n} from "@/i18n/LocaleContext";
import {listAnswerScenarios,getAnswerScenario,friendlyApiError} from "@/lib/api";
import Modal from "./Modal";
import AnswerScenarioEditor from "./AnswerScenarioEditor";
export default function AnswerScenarioManager({search,onSelect,onClose,onChanged}) {
  const {t}=useI18n();const [scope,setScope]=useState("all"),[query,setQuery]=useState(""),[offset,setOffset]=useState(0);
  const [page,setPage]=useState({items:[],total:0}),[loading,setLoading]=useState(false),[error,setError]=useState(null);
  const editGeneration=useRef(0);
  useEffect(()=>()=>{editGeneration.current++;},[]);
  const openEditor=value=>{editGeneration.current++;setEditing(value);};
  const [editing,setEditing]=useState(undefined),[revision,setRevision]=useState(0);
  useEffect(()=>{
    let active=true;
    const timer=setTimeout(()=>{
      setLoading(true);setError(null);
      listAnswerScenarios({scope,q:query,limit:50,offset}).then(result=>{if(active)setPage(result);}).catch(err=>{if(active)setError(err);}).finally(()=>{if(active)setLoading(false);});
    },150);
    return ()=>{active=false;clearTimeout(timer);};
  },[scope,query,offset,revision]);
  const edit=async item=>{const token=++editGeneration.current;setError(null);try{const result=await getAnswerScenario(item.id);if(token===editGeneration.current)setEditing(result);}catch(err){if(token===editGeneration.current)setError(err);}};
  const changed=item=>{onChanged(item);openEditor(undefined);setRevision(value=>value+1);};
  if(editing!==undefined)return <AnswerScenarioEditor key={editing?.id ?? "new"} initial={editing} search={search}
    onClose={()=>openEditor(undefined)} onSaved={changed} onCopied={openEditor}
    onDeleted={id=>{onChanged(null,id);openEditor(undefined);setRevision(value=>value+1);}}/>;
  return <Modal title={t("scenario.catalog")} onClose={onClose} footer={<button className="modal-btn" type="button" onClick={onClose}>{t("scenario.close")}</button>}>
    <div className="scenario-editor-actions"><button className="btn" type="button" onClick={()=>openEditor(null)}>{t("scenario.create")}</button>
      <label>{t("scenario.scope")}<select value={scope} onChange={e=>{setOffset(0);setScope(e.target.value);}}><option value="all">{t("scenario.all")}</option><option value="mine">{t("scenario.mine")}</option><option value="public">{t("scenario.public")}</option></select></label>
    </div>
    <label>{t("scenario.search")}<input className="scenario-search" maxLength={200} value={query} onChange={e=>{setOffset(0);setQuery(e.target.value);}} /></label>
    {loading && <p role="status">{t("scenario.loading")}</p>}{error && <p role="alert">{friendlyApiError(error,t)}</p>}
    <div className="scenario-catalog">{page.items.map(item=><article key={item.id} className="scenario-card"><strong>{item.name}</strong>
      <div className="meta">{item.owner_username ?? item.owner_id} · v{item.version} · {t(item.visibility==="public" ? "scenario.public" : "scenario.private")}</div>
      <p>{item.description}</p><div className="scenario-editor-actions"><button type="button" className="btn" onClick={()=>onSelect(item)}>{t("scenario.use")}</button>
      <button type="button" className="btn ghost" onClick={()=>edit(item)}>{t(item.can_edit ? "scenario.edit" : "scenario.view")}</button></div></article>)}</div>
    {!loading && !page.items.length && <p>{t("scenario.empty")}</p>}
    <div className="scenario-editor-actions"><button type="button" className="btn ghost" disabled={!offset || loading} onClick={()=>setOffset(value=>Math.max(0,value-50))}>{t("scenario.previous")}</button>
      <span>{Math.min(offset+page.items.length,page.total)} / {page.total}</span><button type="button" className="btn ghost" disabled={offset+page.items.length>=page.total || loading} onClick={()=>setOffset(value=>value+50)}>{t("scenario.next")}</button></div>
  </Modal>;
}
