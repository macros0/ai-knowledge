"use client";
import {useEffect,useMemo,useState} from "react";
import {useI18n} from "@/i18n/LocaleContext";
import {createAnswerScenario,updateAnswerScenario,deleteAnswerScenario,copyAnswerScenario,getAnswerScenario,previewAnswerScenario,friendlyApiError} from "@/lib/api";
import {scenarioDraft,validateScenarioDraft,createScenarioPreview} from "@/lib/chatScenarios.mjs";
import Modal from "./Modal";
import ChatAnswer from "./ChatAnswer";
export default function AnswerScenarioEditor({initial,search,onSaved,onClose,onCopied,onDeleted}) {
  const {t}=useI18n();
  const [base,setBase]=useState(initial),[draft,setDraft]=useState(()=>scenarioDraft(initial));
  const [busy,setBusy]=useState(false),[error,setError]=useState(null),[conflict,setConflict]=useState(null);
  const [question,setQuestion]=useState(""),[preview,setPreview]=useState(null);
  const readOnly=Boolean(base && !base.can_edit);
  const dirty=JSON.stringify(draft)!==JSON.stringify(scenarioDraft(base));
  const runner=useMemo(()=>createScenarioPreview({send:previewAnswerScenario,onChange:event=>{
    if(event.type==="start")setPreview({loading:true,text:""});
    else if(event.type==="delta")setPreview(current=>({...current,text:(current?.text ?? "")+event.text}));
    else if(event.type==="result")setPreview({loading:false,text:event.data.answer,result:event.data});
    else if(event.type==="error")setPreview({loading:false,text:"",error:event.error});
  }}),[]);
  useEffect(()=>()=>runner.cancel(),[runner]);
  const close=()=>{if(!busy && (!dirty || window.confirm(t("scenario.discard")))){runner.cancel();onClose();}};
  const change=(field,value)=>{runner.cancel();setPreview(null);setDraft(current=>({...current,[field]:value}));};
  const save=async()=>{
    if(readOnly || !validateScenarioDraft(draft))return;
    setBusy(true);setError(null);
    try {
      const item=base ? await updateAnswerScenario(base.id,{...draft,expected_version:base.version}) : await createAnswerScenario(draft);
      runner.cancel();onSaved(item);
    } catch(err) {
      setError(err);
      if(err.code==="scenario_version_conflict" && base){try{setConflict(await getAnswerScenario(base.id));}catch{/* Original draft remains available. */}}
    } finally {setBusy(false);}
  };
  const remove=async()=>{
    if(!window.confirm(t("scenario.deleteConfirm")))return;
    setBusy(true);setError(null);
    try{await deleteAnswerScenario(base.id,base.version);runner.cancel();onDeleted(base.id);}catch(err){setError(err);}finally{setBusy(false);}
  };
  const copy=async()=>{
    if(dirty && !window.confirm(t("scenario.discard")))return;
    runner.cancel();
    setBusy(true);setError(null);
    try{const item=await copyAnswerScenario(base.id,base.version);onCopied(item);}catch(err){setError(err);}finally{setBusy(false);}
  };
  return <Modal title={base?.name || t("scenario.create")} onClose={close} footer={<>
    {base && <button type="button" className="modal-btn" disabled={busy} onClick={copy}>{t("scenario.copy")}</button>}
    {base?.can_edit && <button type="button" className="modal-btn danger" disabled={busy} onClick={remove}>{t("scenario.delete")}</button>}
    <button type="button" className="modal-btn" disabled={busy} onClick={close}>{t("common.cancel")}</button>
    {!readOnly && <button type="button" className="modal-btn primary" disabled={busy || !validateScenarioDraft(draft)} onClick={save}>{t("scenario.save")}</button>}
  </>}>
    <div className="scenario-editor">
      <label>{t("scenario.name")}<input value={draft.name} maxLength={120} readOnly={readOnly} disabled={busy} onChange={event=>change("name",event.target.value)} /></label>
      <label>{t("scenario.description")}<textarea rows={2} value={draft.description} maxLength={1000} readOnly={readOnly} disabled={busy} onChange={event=>change("description",event.target.value)} /></label>
      <label>{t("scenario.instructions")}<textarea rows={10} value={draft.instructions} maxLength={12000} readOnly={readOnly} disabled={busy} onChange={event=>change("instructions",event.target.value)} /></label>
      <p className="meta">{t("scenario.grounding")}</p>
      <label>{t("scenario.visibility")}<select value={draft.visibility} disabled={readOnly || busy} onChange={event=>change("visibility",event.target.value)}>
        <option value="private">{t("scenario.private")}</option><option value="public">{t("scenario.public")}</option>
      </select></label>
      {draft.visibility==="public" && <p className="meta">{t("scenario.publishHint")}</p>}
      {error && <p role="alert">{friendlyApiError(error,t)}</p>}
      {conflict && <div role="status" className="scenario-conflict"><details><summary>{t("scenario.currentVersion",{version:conflict.version})}</summary>
        <strong>{conflict.name}</strong><p>{conflict.description}</p><pre className="scenario-instructions">{conflict.instructions}</pre>
      </details><button type="button" className="btn" onClick={()=>{setBase(conflict);setConflict(null);setError(null);}}>{t("scenario.reapply",{version:conflict.version})}</button></div>}
      <hr/><label>{t("scenario.testQuestion")}<textarea rows={2} value={question} disabled={busy} onChange={event=>{runner.cancel();setPreview(null);setQuestion(event.target.value);}} /></label>
      <p className="meta">{t("scenario.previewHint")}</p>
      <div className="scenario-editor-actions"><button className="btn" type="button" disabled={busy || !question.trim() || !validateScenarioDraft(draft)} onClick={()=>runner.run({draft,request:{...search,query:question.trim()}})}>{t("scenario.test")}</button>
        {preview?.loading && <button className="btn ghost" type="button" onClick={()=>{runner.cancel();setPreview(current=>({...current,loading:false}));}}>{t("scenario.stop")}</button>}</div>
      {preview && <div className="scenario-preview" aria-live="polite"><strong>{t("scenario.draft")}</strong>
        {preview.loading && <div role="status">{t("chat.thinking")}</div>}
        {preview.error && <p role="alert">{friendlyApiError(preview.error,t)}</p>}
        <ChatAnswer text={preview.text} sources={preview.result?.sources ?? []}/>
        {preview.result?.scenario_execution?.status==="protected_authorship" && <p className="meta">{t("scenario.protectedAuthorship")}</p>}
      </div>}
    </div>
  </Modal>;
}
