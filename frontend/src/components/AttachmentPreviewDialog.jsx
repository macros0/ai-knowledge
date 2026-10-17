"use client";

import {useEffect,useId,useState} from 'react';
import {useI18n} from '@/i18n/LocaleContext';
import {getAttachmentPreview,friendlyApiError} from '@/lib/api';
import Modal from './Modal';

// Keep only in-flight work, never preview content after completion. A closed
// dialog can rejoin the same request while its server worker is finishing.
const previewLanes=new Map();
function requestPreview(docId,reviewId,node,sheet) {
  let lane=previewLanes.get(docId);
  if(!lane){lane={active:null,pending:null};previewLanes.set(docId,lane);}
  const key=JSON.stringify([reviewId,node.source_key,node.content_fingerprint,sheet]);
  let request=[lane.active,lane.pending].find(item=>item?.key===key);
  const cancel=request=>request.reject(Object.assign(new Error('Preview superseded'),{name:'AbortError'}));
  const start=request=>{
    lane.active=request;
    Promise.resolve().then(request.load)
      .then(request.resolve,request.reject).finally(()=>{
        lane.active=null;
        const next=lane.pending;lane.pending=null;
        if(next?.users)start(next);
        else previewLanes.delete(docId);
      });
  };
  if(!request){
    request={key,sheet,users:0,load:()=>getAttachmentPreview(docId,node.source_key,reviewId,sheet)};
    request.promise=new Promise((resolve,reject)=>{request.resolve=resolve;request.reject=reject;});
    if(lane.active){if(lane.pending)cancel(lane.pending);lane.pending=request;}
    else start(request);
  }
  request.users++;
  return {promise:request.promise,release(){
    request.users--;
    if(!request.users&&lane.pending===request){lane.pending=null;cancel(request);}
  }};
}

export function AttachmentPreviewTable({data,t}) {
  return <>
    {data.limited && <p className="source-location-note" role="status">{t('scope.previewLimited')}</p>}
    {!data.rows.length && <p>{t('scope.previewEmpty')}</p>}
    {!!data.rows.length && <div className="attachment-preview-scroll" role="region" aria-label={t('scope.previewTable')} tabIndex={0}>
      <table className="attachment-preview-table">
        <thead><tr><th scope="col">№</th>{data.columns.map(col=><th scope="col" key={col}>{col}</th>)}</tr></thead>
        <tbody>{data.rows.map((row,index)=><tr key={`${row.number}-${index}`}>
          <th scope="row">{row.number}</th>
          {row.cells.map((cell,column)=><td key={column}>{cell}</td>)}
        </tr>)}</tbody>
      </table>
    </div>}
  </>;
}

export default function AttachmentPreviewDialog({docId,reviewId,node,included,onToggle,onClose,onReviewChanged,sessionNotice=null}) {
  const {t}=useI18n();
  const selectId=useId();
  const [sheet,setSheet]=useState(0),[data,setData]=useState(null),[error,setError]=useState('');
  const [loading,setLoading]=useState(true),[retry,setRetry]=useState(0);
  useEffect(()=>{
    let current=true;
    const request=requestPreview(docId,reviewId,node,sheet);
    request.promise.then(next=>{
      if(current)setData(next);
    }).catch(err=>{if(current&&err.name!=='AbortError')setError({text:friendlyApiError(err,t),conflict:err.status===409});})
      .finally(()=>{if(current)setLoading(false);});
    return ()=>{current=false;request.release();};
  },[docId,node,reviewId,sheet,retry,t]);
  return <div className="attachment-preview"><Modal title={t('scope.previewTitle')} onClose={onClose} footer={<>
    <button type="button" className="btn ghost" onClick={onClose}>{t('scope.previewBack')}</button>
    {onToggle && <button type="button" className="btn" onClick={()=>{onToggle();onClose();}}>
      {t(included?'scope.previewExclude':'scope.previewInclude')}
    </button>}
  </>}>
    {sessionNotice}
    <p className="attachment-preview-name">{node.display_name}</p>
    <p className="source-location-note">{t('scope.previewHint')}</p>
    <p className="source-location-note">{t('scope.previewRawValues')}</p>
    {data && <label className="attachment-preview-sheet" htmlFor={selectId}>{t('scope.previewSheet')}
      <select id={selectId} value={sheet} onChange={event=>{setLoading(true);setError('');setSheet(Number(event.target.value));}}>
        {data.sheets.map(item=><option key={item.index} value={item.index}>{item.name}</option>)}
      </select>
    </label>}
    <div className="attachment-preview-content" aria-busy={loading}>
      {loading && <p role="status">{t('scope.previewLoading')}</p>}
      {error && <div role="alert"><p className="error">{error.text}</p>
        {error.conflict?<button type="button" className="btn ghost" onClick={onReviewChanged||onClose}>{t('scope.previewRefresh')}</button>:
          <button type="button" className="btn ghost" onClick={()=>{setLoading(true);setError('');setRetry(value=>value+1);}}>{t('scope.previewRetry')}</button>}</div>}
      {!loading && !error && data && <AttachmentPreviewTable data={data} t={t}/>}
    </div>
  </Modal></div>;
}
