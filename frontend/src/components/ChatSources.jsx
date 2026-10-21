"use client";

import { memo, useMemo, useState } from "react";
import Link from "next/link";
import { documentHref, sourceHref } from "@/lib/chatSources";
import { inModelContext } from "@/lib/chatSourceContext.mjs";
import { groupSourcesByDocument, sourcesForView } from "@/lib/chatAnswerState.mjs";
import { selectionState } from "@/lib/chatSourceSelection.mjs";
import { useI18n } from "@/i18n/LocaleContext";

function SelectionCheckbox({ state, label, onChange }) {
  return <input type="checkbox" className="source-select" aria-label={label}
    aria-checked={state === "some" ? "mixed" : state === "all"}
    checked={state === "all"} ref={(element) => { if (element) element.indeterminate = state === "some"; }}
    onChange={(event) => onChange(event.target.checked)} />;
}


function DocumentChoice({source, selection}) {
  const {t} = useI18n();
  const document = selection.documents.get(source.doc_id);
  if (document?.available === false) return <span className="meta">{t("chat.documentUnavailable")}</span>;
  if (document?.available !== true) return null;
  return <SelectionCheckbox state={selection.selectedIds.has(source.doc_id) ? "all" : "none"}
    label={t("chat.selectDocument", {name: document.filename || source.filename || source.doc_id})}
    onChange={checked => selection.onSelect([source.doc_id], checked)} />;
}

const ChatSourceRow = memo(function ChatSourceRow({ source: s, selected, selectable, responseMode, onSelect, showDocument = false, documentSelection }) {
  const { t } = useI18n();
  const href = sourceHref(s);
  const badge = s.point_type === "chunk" ? "\u{1F4E6}" : "\u{1F4C4}";
  return (
                      <li value={s.display_index ?? s.source_index}>
                        {documentSelection && <DocumentChoice source={s} selection={documentSelection} />}
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
                        {Number.isFinite(s.score) && <span className="source-rating meta" title={t("chat.sourceRatingHint")}>{t("chat.sourceRating", { score: s.score.toFixed(4) })}</span>}
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
                        {showDocument && <div className="source-document-name meta">
                          {documentHref(s) ? <Link href={documentHref(s)}>{s.filename || s.title}</Link> : s.filename || s.title}
                        </div>}
                        {s.snippet && <div className="source-snippet">{s.snippet}</div>}
                      </li>
  );
});

const SourceGroup=memo(function SourceGroup({group,selectedSources,availableIndexes,responseMode,onSelect,expanded,onToggle,documentSelection}) {
  const {t,tc}=useI18n();
  const toggleLabel=tc(expanded ? "chat.collapseDocumentFragments" : "chat.showDocumentFragments", group.sources.length);
  const indexes=group.sources.filter(s=>availableIndexes.has(s.source_index)).map(s=>s.source_index);
  return <div className="source-document-group">
    <div className="source-group-header">
      {documentSelection && <DocumentChoice source={group.source} selection={documentSelection} />}
      {indexes.length>0 && <SelectionCheckbox state={selectionState(selectedSources,indexes)} label={t("chat.selectDocument",{name:group.filename})} onChange={checked=>onSelect(indexes,checked)}/>}
      {documentHref(group.source) ? <Link href={documentHref(group.source)}>{group.filename}</Link> : <span>{group.filename}</span>}
    </div>
    <details open={expanded} onToggle={e=>{if(e.currentTarget.open !== expanded)onToggle(e.currentTarget.open);}}>
      <summary aria-label={`${toggleLabel}: ${group.filename}`}><span className="source-toggle-action">{toggleLabel}</span></summary>
      {expanded && <ol style={{"--source-number-digits":String(Math.max(...group.sources.map(s=>s.display_index || s.source_index || 1))).length}}>{group.sources.map((source,index)=><ChatSourceRow
        key={source.source_index ?? index} source={source} selected={selectedSources.has(source.source_index)} selectable={availableIndexes.has(source.source_index)} responseMode={responseMode} onSelect={onSelect}/>)}</ol>}
    </details>
  </div>;
});
export default memo(function ChatSources({sources=[],selectedIndexes=[],selectionEnabled=false,responseMode,open,onSelect=()=>{},onToggle,expandedGroups,onToggleGroup,view,onViewChange,documentSelection,children}) {
  const {t}=useI18n();
  const [localGroups,setLocalGroups]=useState({});
  const [localView,setLocalView]=useState("documents");
  const sourceView=view ?? localView;
  const changeView=onViewChange || setLocalView;
  const groupStates=expandedGroups || localGroups;
  const toggleGroup=onToggleGroup || ((id,expanded)=>setLocalGroups(previous=>({...previous,[id]:expanded})));
  const selectedSources=useMemo(()=>new Set(selectedIndexes),[selectedIndexes]);
  const availableIndexes=useMemo(()=>new Set(selectionEnabled && !documentSelection ? sources.filter(s=>s.selectable === true && Number.isInteger(s.source_index)).map(s=>s.source_index) : []),[sources,selectionEnabled,documentSelection]);
  const documents=useMemo(()=>documentSelection ? {
    ...documentSelection,
    documents:new Map(documentSelection.documents.map(document=>[document.doc_id,document])),
    selectedIds:new Set(documentSelection.selectedIds),
  } : null,[documentSelection]);
  const availableDocIds=documents ? [...documents.documents.values()].filter(document=>document.available === true).map(document=>document.doc_id) : [];
  const allDocumentsSelected=availableDocIds.length>0 && selectionState(documents.selectedIds,availableDocIds) === "all";
  const allSelected=availableIndexes.size>0 && selectionState(selectedSources,[...availableIndexes]) === "all";
  const displaySources=useMemo(()=>sourcesForView(sources,sourceView),[sources,sourceView]);
  const groups=useMemo(()=>groupSourcesByDocument(displaySources),[displaySources]);
  if(!sources.length)return null;
  return <details className="sources" open={Boolean(open)} onToggle={e=>{if(e.currentTarget.open !== Boolean(open))onToggle(e.currentTarget.open);}}>
    <summary><span className="source-toggle-action">{t(open ? "chat.collapseAllSources" : "chat.showSources")}</span>{" "}<span className="source-toggle-counts">{t("chat.sourceCounts",{documents:groups.length,fragments:sources.length})}</span></summary>
    {open && <div className="source-groups">
      {children}
      <div className="source-view-toolbar">
        <div className="source-view-switch" role="group" aria-label={t("chat.sourceView")}>
          <button type="button" className="btn ghost" aria-pressed={sourceView === "documents"} onClick={()=>changeView("documents")}>{t("chat.sourceViewDocuments")}</button>
          <button type="button" className="btn ghost" aria-pressed={sourceView === "rating"} onClick={()=>changeView("rating")}>{t("chat.sourceViewRating")}</button>
        </div>
        {availableIndexes.size>0 && <button type="button" className="btn ghost source-select-all" aria-pressed={allSelected} title={t(allSelected ? "chat.clearSourceSelection" : "chat.selectAllSources")} onClick={()=>onSelect([...availableIndexes],!allSelected)}>{t("chat.toggleAllSources")}</button>}
        {availableDocIds.length>0 && <button type="button" className="btn ghost source-select-all" aria-pressed={allDocumentsSelected}
          title={t(allDocumentsSelected ? "chat.clearDocumentSelection" : "chat.selectAllDocuments")}
          onClick={()=>documents.onSelect(availableDocIds,!allDocumentsSelected)}>{t("chat.toggleAllDocuments")}</button>}
      </div>
      {sourceView === "rating" ? <ol className="source-rating-list" style={{"--source-number-digits":String(Math.max(...displaySources.map(s=>s.display_index))).length}}>
        {displaySources.map(source=><ChatSourceRow key={source.display_index} source={source} selected={selectedSources.has(source.source_index)} selectable={availableIndexes.has(source.source_index)} responseMode={responseMode} onSelect={onSelect} showDocument documentSelection={documents}/>)}
      </ol> : groups.map(group=><SourceGroup key={group.doc_id} group={group} selectedSources={selectedSources} availableIndexes={availableIndexes} responseMode={responseMode} onSelect={onSelect} expanded={groupStates[group.doc_id] ?? false} onToggle={expanded=>toggleGroup(group.doc_id,expanded)} documentSelection={documents}/>)}
    </div>}
  </details>;
});
