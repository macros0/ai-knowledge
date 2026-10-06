"use client";

import { memo, useMemo, useState } from "react";
import Link from "next/link";
import { documentHref, sourceHref } from "@/lib/chatSources";
import { inModelContext } from "@/lib/chatSourceContext.mjs";
import { groupSourcesByDocument } from "@/lib/chatAnswerState.mjs";
import { selectionState } from "@/lib/chatSourceSelection.mjs";
import { useI18n } from "@/i18n/LocaleContext";

function SelectionCheckbox({ state, label, onChange }) {
  return <input type="checkbox" className="source-select" aria-label={label}
    aria-checked={state === "some" ? "mixed" : state === "all"}
    checked={state === "all"} ref={(element) => { if (element) element.indeterminate = state === "some"; }}
    onChange={(event) => onChange(event.target.checked)} />;
}


const ChatSourceRow = memo(function ChatSourceRow({ source: s, selected, selectable, responseMode, onSelect }) {
  const { t } = useI18n();
  const href = sourceHref(s);
  const badge = s.point_type === "chunk" ? "\u{1F4E6}" : "\u{1F4C4}";
  return (
                      <li value={s.display_index ?? s.source_index}>
                        {selectable && (
                          <SelectionCheckbox state={selected ? "all" : "none"}
                            label={t("chat.selectFragment", { index: s.source_index, name: s.title })}
                            onChange={(checked) => onSelect([s.source_index], checked)} />
                        )}
                        <span className="source-badge">{badge}</span>{" "}
                        {href ? (
                          <>
                            <Link className="source-link" href={href}>
                              {s.title}
                            </Link>{" "}
                          </>
                        ) : (
                          s.title
                        )}
                        {" "}<span className="meta">{t(responseMode === "documents" ? "chat.sourceFound" : inModelContext(s) ? "chat.sourceInContext" : "chat.sourceSearchOnly")}</span>
                        {s.development_number && (
                          <span
                            className="source-dev-badge"
                            title={s.development_name || t("chat.developmentTitle")}
                          >
                            {s.development_number}
                          </span>
                        )}
                        {s.development_module && (
                          <span className="source-dev-badge source-module-badge">
                            {s.development_module}
                          </span>
                        )}
                        {s.snippet && <div className="source-snippet">{s.snippet}</div>}
                      </li>
  );
});

const SourceGroup=memo(function SourceGroup({group,selectedSources,availableIndexes,responseMode,onSelect,expanded,onToggle}) {
  const {t}=useI18n();
  const indexes=group.sources.filter(s=>availableIndexes.has(s.source_index)).map(s=>s.source_index);
  return <div className="source-document-group">
    <div className="source-group-header">
      {indexes.length>0 && <SelectionCheckbox state={selectionState(selectedSources,indexes)} label={t("chat.selectDocument",{name:group.filename})} onChange={checked=>onSelect(indexes,checked)}/>}
      {documentHref(group.source) ? <Link href={documentHref(group.source)}>{group.filename}</Link> : <span>{group.filename}</span>}
    </div>
    <details open={expanded} onToggle={e=>{if(e.currentTarget.open !== expanded)onToggle(e.currentTarget.open);}}>
      <summary>{group.sources.length} {t("chat.fragments")}</summary>
      {expanded && <ol style={{"--source-number-digits":String(Math.max(...group.sources.map(s=>s.display_index || s.source_index || 1))).length}}>{group.sources.map((source,index)=><ChatSourceRow
        key={source.source_index ?? index} source={source} selected={selectedSources.has(source.source_index)} selectable={availableIndexes.has(source.source_index)} responseMode={responseMode} onSelect={onSelect}/>)}</ol>}
    </details>
  </div>;
});
export default memo(function ChatSources({sources=[],selectedIndexes=[],selectionEnabled=false,responseMode,open,onSelect=()=>{},onToggle,expandedGroups,onToggleGroup}) {
  const {t}=useI18n();
  const [localGroups,setLocalGroups]=useState({});
  const groupStates=expandedGroups || localGroups;
  const toggleGroup=onToggleGroup || ((id,expanded)=>setLocalGroups(previous=>({...previous,[id]:expanded})));
  const selectedSources=useMemo(()=>new Set(selectedIndexes),[selectedIndexes]);
  const availableIndexes=useMemo(()=>new Set(selectionEnabled ? sources.filter(s=>s.selectable === true && Number.isInteger(s.source_index)).map(s=>s.source_index) : []),[sources,selectionEnabled]);
  const groups=useMemo(()=>groupSourcesByDocument(sources.map((s,index)=>({...s,display_index:s.source_index ?? index+1}))),[sources]);
  if(!sources.length)return null;
  return <details className="sources" open={Boolean(open)} onToggle={e=>{if(e.currentTarget.open !== Boolean(open))onToggle(e.currentTarget.open);}}>
    <summary>{t("ux.sourcesSummary",{documents:groups.length,fragments:sources.length})}</summary>
    {open && <div className="source-groups">
      {availableIndexes.size>0 && <button type="button" className="btn ghost" onClick={()=>onSelect([...availableIndexes],true)}>{t("chat.selectAllSources")}</button>}
      {groups.map(group=><SourceGroup key={group.doc_id} group={group} selectedSources={selectedSources} availableIndexes={availableIndexes} responseMode={responseMode} onSelect={onSelect} expanded={groupStates[group.doc_id] ?? (group.sources.length <= 10 || group.sources.some(s=>selectedSources.has(s.source_index)))} onToggle={expanded=>toggleGroup(group.doc_id,expanded)}/>)}
    </div>}
  </details>;
});
