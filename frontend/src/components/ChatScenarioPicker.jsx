"use client";
import {useEffect,useState} from "react";
import {useI18n} from "@/i18n/LocaleContext";
import {listAnswerScenarios,getAnswerScenario,friendlyApiError} from "@/lib/api";
import AnswerScenarioManager from "./AnswerScenarioManager";
export default function ChatScenarioPicker({ownerId,selectedId,selected,onIdChange,onSelectedChange,search}) {
  const {t}=useI18n();const [items,setItems]=useState([]),[manager,setManager]=useState(false),[revision,setRevision]=useState(0),[error,setError]=useState(null);
  useEffect(()=>{
    let active=true;
    listAnswerScenarios({limit:100}).then(page=>{if(active)setItems(page.items);}).catch(err=>{if(active)setError(err);});
    return ()=>{active=false;};
  },[revision]);
  useEffect(()=>{
    if(!selectedId)return;
    let active=true;
    getAnswerScenario(selectedId).then(item=>{if(active){onSelectedChange(item);setError(null);}}).catch(err=>{
      if(active){if(err.status===404)onSelectedChange({id:selectedId,unavailable:true});setError(err);}
    });
    return ()=>{active=false;};
  },[selectedId,revision,onSelectedChange]);
  const choose=item=>{onIdChange(item?.id ?? null);onSelectedChange(item);setManager(false);};
  const changed=(item,deletedId)=>{
    if(item?.id===selectedId)onSelectedChange(item);
    if(deletedId===selectedId)onSelectedChange({id:deletedId,unavailable:true});
    setRevision(value=>value+1);
  };
  const options=selectedId && selected && !items.some(item=>item.id===selectedId) ? [selected,...items] : items;
  return <div className="chat-scenario-picker">
    <label className="scenario-choice"><span className="sr-only">{t("scenario.choose")}</span><select value={selectedId ?? ""} onFocus={()=>setRevision(value=>value+1)} onChange={e=>choose(options.find(item=>item.id===e.target.value) ?? null)}>
      <option value="">{t("scenario.choose")}</option>
      <optgroup label={t("scenario.mine")}>{options.filter(item=>item.owner_id===ownerId).map(item=><option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</optgroup>
      <optgroup label={t("scenario.public")}>{options.filter(item=>item.owner_id!==ownerId).map(item=><option key={item.id} value={item.id}>{item.name ?? t("scenario.unavailable")} · {item.owner_username ?? ""} {item.version ? `· v${item.version}` : ""}</option>)}</optgroup>
    </select></label>
    <button type="button" className="btn ghost" onClick={()=>setManager(true)}>{t("scenario.catalog")}</button>
    {!selectedId && <span className="meta">{t("scenario.chooseHint")}</span>}
    {selected?.unavailable && <span role="alert" className="meta">{t("scenario.unavailable")}</span>}
    {error && <span role="alert" className="meta">{friendlyApiError(error,t)}</span>}
    {manager && <AnswerScenarioManager search={search} onSelect={choose} onChanged={changed} onClose={()=>setManager(false)}/>}
  </div>;
}
