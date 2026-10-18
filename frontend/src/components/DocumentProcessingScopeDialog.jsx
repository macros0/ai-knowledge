"use client";
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {useI18n} from '@/i18n/LocaleContext';
import {useAuth} from '@/context/AuthContext';
import {getProcessingScope,getProcessingScopeLabels,createProcessingScopeReview,saveProcessingScopeReview,confirmProcessingScope,cancelProcessingScopeReview,friendlyApiError,previewProcessingScopeChange,applyProcessingScopeChange} from '@/lib/api';
import {effectiveSelection,toggleSourceSelection,scopeTreeSources,scopeChangeAction,attachmentPreviewFormat,savedScopeChanges} from '@/lib/processingScope.mjs';
import {sourceAncestors} from '@/lib/sourceTree.mjs';
import {acquireProcessingScopeEditLock,renewProcessingScopeEditLock,releaseProcessingScopeEditLock} from '@/lib/api';
import {createEditorLeaseSession} from '@/lib/editorLeaseSession.mjs';
import {createScopeLeaseAdapter} from '@/lib/editorLeaseAdapters.mjs';
import EditorLeaseNotice from './EditorLeaseNotice';
import Modal from './Modal';
import AttachmentPreviewDialog from './AttachmentPreviewDialog';

function ScopeCheckbox({checked,mixed,disabled,onChange,label}) {
  const input=useRef(null);
  useEffect(()=>{if(input.current)input.current.indeterminate=mixed;},[mixed]);
  return <input ref={input} type="checkbox" checked={checked} disabled={disabled}
    aria-label={label} aria-checked={mixed?'mixed':checked} onChange={onChange}/>;
}

function selectionDiffers(nodes,current,decisions) {
  // Legacy publications without an inventory still require the server's check.
  if(!Array.isArray(decisions))return true;
  const previous=effectiveSelection(nodes,new Set(decisions.filter(d=>d.effective_included).map(d=>d.source_key)));
  return current.size!==previous.size||[...current].some(key=>!previous.has(key));
}

function displayedScope(data,editing) {
  return editing?(data?.pending||data?.active):(data?.active||data?.pending);
}

export default function DocumentProcessingScopeDialog({docId,open,onClose,onUpdated}) {
  const {t}=useI18n();
  const {mode,hasRole}=useAuth();
  const canEdit=mode==='disabled'||hasRole('editor','admin');
  const [data,setData]=useState(null),[selected,setSelected]=useState(new Set()),[error,setError]=useState('');
  const [editing,setEditing]=useState(false);
  const editingMode=useRef(false);
  const needsReconciliation=useRef(false);
  const latestScope=useRef(null);
  const editingSession=useRef(0);
  const [busy,setBusy]=useState(false),[similar,setSimilar]=useState(null),[expanded,setExpanded]=useState(new Set());
  const [preview,setPreview]=useState(null);
  const [sourceTitles,setSourceTitles]=useState({});
  const [sourceLabels,setSourceLabels]=useState({});
  const [changePreview,setChangePreview]=useState(null),[fullWarning,setFullWarning]=useState(false);
  const [ownsEditor,setOwnsEditor]=useState(false),[editorStatus,setEditorStatus]=useState(null),[editorRetry,setEditorRetry]=useState(0);
  const editToken=useRef(null);
  const idleSession=useRef(null),closeEditor=useRef(onClose);
  const [idleWarning,setIdleWarning]=useState(null);
  const [leaseState,setLeaseState]=useState('viewing');
  useEffect(()=>{closeEditor.current=onClose;},[onClose]);
  const sequence=useRef(0),mounted=useRef(true),requestId=useRef(null);
  useEffect(()=>()=>{mounted.current=false;sequence.current+=1;},[]);
  const apply=useCallback(next=>{
    latestScope.current=next;
    setChangePreview(null);setFullWarning(false);
    if(!next.pending){editingMode.current=false;setEditing(false);}
    setData(next);
    setEditorStatus(next.edit_lock||null);
    const view=displayedScope(next,editingMode.current);
    const nodes=view?.inventory?.nodes||[];
    setSelected(new Set((view?.decisions||[]).filter(d=>d.effective_included).map(d=>d.source_key)));
    setExpanded(new Set(nodes.filter(n=>nodes.some(child=>child.parent_key===n.source_key)).map(n=>n.source_key)));
  },[]);
  const returnToViewing=useCallback(()=>{
    editingSession.current+=1;
    needsReconciliation.current=true;
    editingMode.current=false;setEditing(false);
    if(latestScope.current)apply(latestScope.current);
    setPreview(null);setSimilar(null);setChangePreview(null);setFullWarning(false);requestId.current=null;
  },[apply]);
  const reload=useCallback(async()=>{
    const current=++sequence.current;
    const next=await getProcessingScope(docId);
    if(mounted.current && current===sequence.current)apply(next);
    return next;
  },[docId,apply]);
  useEffect(()=>{
    if(!open)return;
    mounted.current=true;
    reload().catch(err=>{if(mounted.current)setError(friendlyApiError(err,t));});
  },[open,reload,t]);
  const needsEditor=data?.capabilities?.edit_lock===true;
  useEffect(()=>{
    if(!open||!canEdit||!needsEditor)return;
    let stopped=false;
    const adapter=createScopeLeaseAdapter({getProcessingScope,acquireProcessingScopeEditLock,
      renewProcessingScopeEditLock,releaseProcessingScopeEditLock},docId);
    const session=createEditorLeaseSession({adapter,now:()=>Date.now(),setTimer:setTimeout,clearTimer:clearTimeout,
      onChange:next=>{
        if(stopped)return;
        setLeaseState(next.state);setEditorStatus(next.status);setIdleWarning(next.countdown);
        setOwnsEditor(next.state==='editing');
        editToken.current=next.state==='editing'?session.mutationContext()?.edit_token:null;
      },
      onAcquired:async(_status,isCurrent)=>{
        const next=await getProcessingScope(docId);
        if(!stopped&&isCurrent())apply(next);
      },
      onLost:error=>{
        if(stopped)return;
        if(error?.code==='scope_change_in_progress'){
          setLeaseState('applying');setEditorStatus({state:'applying'});
        }
        returnToViewing();setOwnsEditor(false);setPreview(null);setChangePreview(null);requestId.current=null;
      },
      onExpire:()=>{
        if(stopped)return;
        editingSession.current+=1;
        editToken.current=null;setOwnsEditor(false);setPreview(null);setChangePreview(null);
        requestId.current=null;closeEditor.current?.();
      }});
    idleSession.current=session;
    // A cleaned-up Strict Mode opening must not race its replacement's acquire.
    Promise.resolve().then(()=>session.start());
    return ()=>{
      editingSession.current+=1;
      stopped=true;session.dispose();
      if(idleSession.current===session)idleSession.current=null;
      editToken.current=null;setOwnsEditor(false);setIdleWarning(null);
    };
  },[open,canEdit,needsEditor,docId,editorRetry,apply,returnToViewing]);
  const withEditor=body=>{
    if(!needsEditor)return body;
    const context=idleSession.current?.mutationContext();
    if(!context)throw Object.assign(new Error('Editor access lost'),{code:'scope_edit_lock_lost'});
    return {...body,...context};
  };
  const sessionCurrent=session=>mounted.current&&session===editingSession.current;
  const recordFailure=err=>{
    if(!mounted.current)return;
    if(err.code==='timeout'||(!err.code&&[408,504].includes(err.status))){
      // A timed-out mutation may already have succeeded. Leave editing and
      // read the actual server state before accepting another edit action.
      returnToViewing();
    }
    if(['scope_edit_locked','scope_edit_lock_lost','scope_change_in_progress'].includes(err.code)){
      returnToViewing();
      idleSession.current?.dispose();idleSession.current=null;setIdleWarning(null);
      setOwnsEditor(false);setPreview(null);setChangePreview(null);requestId.current=null;
      setEditorStatus({state:err.code==='scope_change_in_progress'?'applying':'lost'});
      setLeaseState(err.code==='scope_change_in_progress'?'applying':'lost');
    }
    setError(friendlyApiError(err,t));
  };
  const view=displayedScope(data,editing);
  const savedChanges=useMemo(()=>savedScopeChanges(data?.active,data?.pending),[data]);
  const nodes=useMemo(()=>view?.inventory?.nodes||[],[view]);
  const labelScopeType=view===data?.active?'active':'pending';
  const labelScopeId=labelScopeType==='active'?view?.summary?.generation_id:view?.id;
  const labelScopeKey=`${docId}:${labelScopeType}:${labelScopeId}`;
  const labelTargets=useMemo(()=>JSON.stringify(nodes.map(node=>({source_key:node.source_key,
    content_fingerprint:node.content_fingerprint,display_label:node.display_label}))),[nodes]);
  useEffect(()=>{
    const targets=JSON.parse(labelTargets);
    if(!open||!labelScopeId||!targets.some(n=>!n.display_label))return;
    const controller=new AbortController();let stopped=false;
    getProcessingScopeLabels(docId,labelScopeType,labelScopeId,controller.signal).then(result=>{
      if(stopped||result.scope_type!==labelScopeType||result.scope_id!==labelScopeId)return;
      setSourceLabels(current=>{
        const next={...current};
        for(const node of targets){
          const label=result.labels?.[node.source_key];
          if(label?.display_label&&label.content_fingerprint===node.content_fingerprint){
            next[`${labelScopeKey}:${node.source_key}:${node.content_fingerprint}`]=label.display_label;
          }
        }
        return next;
      });
    }).catch(()=>{}); // Optional enrichment: preserve available names on failure.
    return ()=>{stopped=true;controller.abort();};
  },[open,docId,labelScopeType,labelScopeId,labelScopeKey,labelTargets]);
  const sources=useMemo(()=>scopeTreeSources(nodes),[nodes]);
  const maximumDepth=view?.inventory?.traversal_limits?.max_depth??6;
  const effective=useMemo(()=>effectiveSelection(nodes,selected,maximumDepth),[nodes,selected,maximumDepth]);
  const allAvailable=useMemo(()=>effectiveSelection(nodes,new Set(nodes.map(n=>n.source_key)),maximumDepth),[nodes,maximumDepth]);
  const editorAllowed=canEdit&&(!needsEditor||ownsEditor);
  const editable=editing && editorAllowed && ['draft','awaiting_similar'].includes(data?.pending?.state);
  const draftDirty=!!data?.pending&&selectionDiffers(nodes,effective,data.pending.decisions);
  const publishedDirty=!!data?.active&&selectionDiffers(data.active.inventory?.nodes||[],effective,data.active.inventory?data.active.decisions:null);
  const refreshPreservingSelection=async()=>{
    const session=editingSession.current;
    const serverSelection=new Set((view?.decisions||[]).filter(d=>d.effective_included).map(d=>d.source_key));
    const changes=nodes.filter(n=>effective.has(n.source_key)!==serverSelection.has(n.source_key))
      .map(n=>[n.source_key,effective.has(n.source_key)]);
    setBusy(true);setError('');setPreview(null);
    try{
      const next=await getProcessingScope(docId);
      if(!sessionCurrent(session))return;
      apply(next);
      const current=displayedScope(next,editingMode.current);
      const nextNodes=current?.inventory?.nodes||[];
      const keys=new Set(nextNodes.map(n=>n.source_key));
      const chosen=new Set((current?.decisions||[]).filter(d=>d.effective_included).map(d=>d.source_key));
      for(const [key,included] of changes)if(keys.has(key)){if(included)chosen.add(key);else chosen.delete(key);}
      setSelected(effectiveSelection(nextNodes,chosen));
      setSimilar(null);requestId.current=null;
    }catch(err){if(sessionCurrent(session))recordFailure(err);}
    finally{if(mounted.current)setBusy(false);}
  };
  const persistSelection=async(session)=>{
    let review=data.pending;
    const dirty=selectionDiffers(nodes,effective,review.decisions);
    if(dirty){
      const saved=await saveProcessingScopeReview(docId,withEditor({review_id:review.id,expected_revision:review.revision,
        selected_source_keys:[...effective]}));
      if(!sessionCurrent(session))return null;
      review=saved.pending;
      if(mounted.current)apply(saved);
      requestId.current=null;
      setSimilar(null);
    }
    return {review,dirty};
  };
  const newReview=async(expandKey=null,snapshot=data)=>{
    if(busy||!editorAllowed)return;
    const session=editingSession.current;
    setBusy(true);setError('');
    try{
      const persisted=expandKey?await persistSelection(session):null;
      if(!sessionCurrent(session)||(expandKey&&!persisted))return;
      const review=persisted?.review;
      const next=await createProcessingScopeReview(docId,withEditor({expected_active_generation_id:snapshot?.active?.summary?.generation_id||null,
        ...(expandKey?{review_id:review.id,expected_revision:review.revision,expand_source_keys:[expandKey]}:{})}));
      if(!sessionCurrent(session))return;
      if(mounted.current){
        if(!expandKey){editingMode.current=true;setEditing(true);}
        apply(next);setSimilar(null);requestId.current=null;
      }
      onUpdated?.();
    }catch(err){if(sessionCurrent(session))recordFailure(err);}
    finally{if(mounted.current)setBusy(false);}
  };
  const enterEditing=async()=>{
    if(busy||!editorAllowed)return;
    const session=editingSession.current;
    let current=data;
    if(needsReconciliation.current){
      setBusy(true);setError('');
      try{
        current=await getProcessingScope(docId);
        if(!sessionCurrent(session))return;
        apply(current);needsReconciliation.current=false;
      }catch(err){if(sessionCurrent(session))recordFailure(err);return;}
      finally{if(mounted.current)setBusy(false);}
    }
    if(current.pending){
      if(!['draft','awaiting_similar'].includes(current.pending.state))return;
      editingMode.current=true;setEditing(true);apply(current);setError('');
    }else await newReview(null,current);
  };
  const discardDraft=async()=>{
    if(busy||!editable||!data.active||(!draftDirty&&!publishedDirty))return;
    const session=editingSession.current;
    setBusy(true);setError('');
    try{
      const next=await cancelProcessingScopeReview(docId,withEditor({review_id:data.pending.id,
        expected_revision:data.pending.revision,selected_source_keys:[]}));
      if(!sessionCurrent(session))return;
      if(mounted.current){
        editingMode.current=false;setEditing(false);
        apply(next);requestId.current=null;setSimilar(null);setPreview(null);
      }
      onUpdated?.();
    }catch(err){if(sessionCurrent(session))recordFailure(err);}
    finally{if(mounted.current)setBusy(false);}
  };
  const saveDraft=async()=>{
    if(busy||!editable||!draftDirty)return;
    const session=editingSession.current;
    setBusy(true);setError('');
    try{
      const saved=await saveProcessingScopeReview(docId,withEditor({review_id:data.pending.id,
        expected_revision:data.pending.revision,selected_source_keys:[...effective]}));
      if(!sessionCurrent(session))return;
      if(mounted.current){apply(saved);requestId.current=null;setSimilar(null);}
      onUpdated?.();
    }catch(err){if(sessionCurrent(session))recordFailure(err);}
    finally{if(mounted.current)setBusy(false);}
  };
  const submit=async(useSimilar=false,full=false)=>{
    if(busy||!editable||(data.active&&!full&&(!publishedDirty||changePreview?.mode==='noop')))return;
    const session=editingSession.current;
    setBusy(true);setError('');
    try{
      const persisted=await persistSelection(session);
      if(!sessionCurrent(session)||!persisted)return;
      const {review,dirty}=persisted;
      if(data.active && !full){
        const generationId=data.active.summary.generation_id;
        if(!changePreview || dirty){
          const result=await previewProcessingScopeChange(docId,withEditor({review_id:review.id,revision:review.revision,
            expected_active_generation_id:generationId}));
          if(!sessionCurrent(session))return;
          if(mounted.current){setChangePreview(result);requestId.current=null;}
          return;
        }
        const action=scopeChangeAction(changePreview,data.capabilities);
        if(action.disabled)return;
        requestId.current ||= crypto.randomUUID().replaceAll('-','');
        await applyProcessingScopeChange(docId,withEditor({plan_id:changePreview.plan_id,request_id:requestId.current,
          expected_active_generation_id:generationId}),action.endpoint);
        if(!sessionCurrent(session))return;
        onUpdated?.();if(mounted.current)onClose();return;
      }
      requestId.current ||= crypto.randomUUID().replaceAll('-','');
      await confirmProcessingScope(docId,withEditor({review_id:review.id,revision:review.revision,request_id:requestId.current,
        ...(useSimilar && !dirty?{similarity_token:similar?.similarity_token}:{})}));
      if(!sessionCurrent(session))return;
      onUpdated?.();
      if(mounted.current)onClose();
    }catch(err){
      if(!sessionCurrent(session))return;
      if(err.code==='similar_document'){setSimilar(err.data);}
      else{
        setSimilar(null);recordFailure(err);
        // Non-timeout failures retain local choices. Timeouts return to viewing
        // and require a server read before another explicit edit action.
      }
    }finally{if(mounted.current)setBusy(false);}
  };
  if(!open)return null;
  const chooseSelection=next=>{setSelected(next);setSimilar(null);setChangePreview(null);setFullWarning(false);requestId.current=null;};
  const toggle=(key,checked)=>chooseSelection(current=>toggleSourceSelection(nodes,current,key,checked));
  const action=scopeChangeAction(changePreview,data?.capabilities);
  const supportsChanges=!!(data?.capabilities?.fast_exclusion||data?.capabilities?.cached_restore||data?.capabilities?.incremental_generation);
  const changeLabel=!publishedDirty?'scope.noChanges':({fast_exclude:'scope.applyExclusions',reuse_only:'scope.restoreCached',incremental_generate:'scope.processAdded',noop:'scope.noChanges',blocked:'scope.changeBlocked'}[changePreview?.mode]||'scope.checkChanges');
  const editorActivity=event=>{if(event?.isTrusted!==false)idleSession.current?.activity();};
  const idleNotice=needsEditor&&canEdit?<EditorLeaseNotice state={leaseState}
    ownerName={editorStatus?.owner||data?.edit_lock?.owner} remainingSeconds={idleWarning}
    expiryAction="close" onContinue={()=>idleSession.current?.activity()}
    onRetry={()=>{if(!busy)setEditorRetry(value=>value+1);}}/>:null;
  return <div data-scope-editor-session onPointerDownCapture={editorActivity} onKeyDownCapture={editorActivity} onScrollCapture={editorActivity}>
  <Modal title={t('scope.title')} onClose={onClose} footer={<>
    <button type="button" className="btn ghost" onClick={onClose}>{t('scope.later')}</button>
    {canEdit && data && !editing && (!data.pending||['draft','awaiting_similar'].includes(data.pending.state)) && <button type="button" className="btn" disabled={busy||!editorAllowed} onClick={enterEditing}>{t('scope.edit')}</button>}
    {editable && data.active && (draftDirty||publishedDirty) && <button type="button" className="btn ghost" disabled={busy} onClick={discardDraft}>{t('scope.discard')}</button>}
    {editable && <button type="button" className="btn ghost" disabled={busy||!draftDirty} onClick={saveDraft}>{t('scope.save')}</button>}
    {editable && data.active && <button type="button" className="btn ghost" disabled={busy} onClick={()=>{
      if(fullWarning){requestId.current=null;submit(false,true);}else setFullWarning(true);
    }}>{t(fullWarning?'scope.confirmFullRegeneration':'scope.regenerate')}</button>}
    {editable && <button type="button" className="btn" disabled={busy||(!!data.active&&(!publishedDirty||!supportsChanges||changePreview?.mode==='noop'||(!!changePreview&&action.disabled)))} onClick={()=>submit(!!similar)}>
      {t(busy?'scope.working':data.active?changeLabel:similar?'scope.confirmSimilar':'scope.confirm')}
    </button>}
  </>}>
    {!data && <p role="status">{t('scope.loading')}</p>}
    {data && <>
      <p>{t(editing?'scope.intro':data.active?(view?.inventory?'scope.viewPublished':'scope.viewLegacy'):view?'scope.viewPending':'scope.legacy')}</p>
      {!editing&&data.active&&['draft','awaiting_similar'].includes(data.pending?.state)&&<p className="source-location-note">{t('scope.savedSelectionHint')}</p>}
      {!editing&&savedChanges&&<div className="scope-saved-changes" role="status">
        <p>{t('scope.savedNotApplied')}</p>
        {['excluded','included'].map(kind=>!!savedChanges[kind].length&&<div key={kind}>
          <p>{t(kind==='excluded'?'scope.savedExcluded':'scope.savedIncluded')}</p>
          <ul>{savedChanges[kind].map(node=><li key={node.source_key}>{node.display_label||node.title||node.display_name}</li>)}</ul>
        </div>)}
        {data.pending?.last_check?.mode==='blocked'?<>
          <p>{t('scope.savedCheckBlocked')}</p>
          {data.pending.last_check.reasons?.map(reason=><p key={reason}>{t(`scope.changeReason.${reason}`)}</p>)}
        </>:<p>{t('scope.savedApplyHint')}</p>}
      </div>}
      {data.active?.summary?.chunk_counts && <p className="source-location-note">{t('scope.publishedChunkCounts',data.active.summary.chunk_counts)}</p>}
      {!preview&&idleNotice}
      {data.active && !supportsChanges && <p role="status">{t('scope.changesUnavailable')}</p>}
      {fullWarning && <p role="alert">{t('scope.fullRegenerationWarning')}</p>}
      {changePreview && <div role="status" aria-live="polite">
        <p>{t(changePreview.mode==='fast_exclude'?'scope.exclusionPreview':'scope.changePreview',{
          sources:changePreview.counts?.removed?.sources??'—',chunks:changePreview.counts?.removed?.chunks??'—',
          concepts:changePreview.counts?.removed?.concepts??'—'})}</p>
        {changePreview.reasons?.map(reason=><p key={reason}>{t(`scope.changeReason.${reason}`)}</p>)}
        {changePreview.workset?.sources?.map(work=><p key={work.source_key}>
          {nodes.find(n=>n.source_key===work.source_key)?.display_name}: {['parse','concepts','embeddings'].map(stage=>t(`scope.stage.${stage}.${work[stage]}`)).join('; ')}
        </p>)}
      </div>}
      {data.active && ['validating','submitted'].includes(data.pending?.state) && <p className="source-location-note">{t('scope.oldActive')}</p>}
      {data.active && editable && <p className="source-location-note">{t('scope.discardHint')}</p>}
      {!data.active && data.pending && <p>{t('scope.noActive')}</p>}
      {data.pending?.state==='validating' && <p role="status">{t('scope.validationPending')}</p>}
      {data.pending?.state==='submitted' && <p role="status">{t('scope.submitted')}</p>}
      <p className="scope-root"><input type="checkbox" checked disabled aria-label={t('scope.root')}/><strong>{t('scope.root')}</strong></p>
      {editable && <div className="scope-tools">
        <button type="button" className="btn ghost" disabled={busy||effective.size===allAvailable.size} onClick={()=>chooseSelection(new Set(allAvailable))}>{t('scope.selectAll')}</button>
        <button type="button" className="btn ghost" disabled={busy||effective.size===0} onClick={()=>chooseSelection(new Set())}>{t('scope.clear')}</button>
      </div>}
      {!nodes.length && view?.inventory && <p>{t('scope.noAttachments')}</p>}
      <ul className="scope-tree">{nodes.map(node=>{
        const parents=sourceAncestors(sources,node.source_key);
        if(parents.some(parent=>!expanded.has(parent)))return null;
        const children=nodes.filter(child=>sourceAncestors(sources,child.source_key).includes(node.source_key));
        const enabled=effective.has(node.source_key);
        const mixed=enabled && children.some(child=>!effective.has(child.source_key));
        const depthLimited=node.status==='skipped_depth'||(Number.isInteger(maximumDepth)&&parents.length+1>maximumDepth);
        const unknown=depthLimited||node.assessment==='unknown'||node.children_complete===false;
        const blockedAncestor=parents.some(key=>{const ancestor=nodes.find(n=>n.source_key===key);return !ancestor?.supported||ancestor.assessment==='unknown'||ancestor.children_complete===false;});
        const decision=(view.decisions||[]).find(d=>d.source_key===node.source_key);
        const recommendation=(view.inventory?.recommendations||[]).find(d=>d.source_key===node.source_key);
        const metrics=node.metrics||{};
        const previewFormat=attachmentPreviewFormat(node);
        const titleKey=`${data.pending?.id}:${node.source_key}:${node.content_fingerprint}`;
        const titleInfo=sourceTitles[titleKey]||node;
        const iconLabel=node.display_label||sourceLabels[`${labelScopeKey}:${node.source_key}:${node.content_fingerprint}`];
        const displayTitle=iconLabel||(titleInfo.title?(titleInfo.title_source==='sheet'?t('scope.previewSheetTitle',{name:titleInfo.title}):titleInfo.title):node.display_name);
        return <li key={node.source_key} className="scope-node" style={{'--scope-depth':parents.length}}>
          <div className="scope-node-heading">
            {!!children.length && <button type="button" className="scope-branch-toggle" aria-expanded={expanded.has(node.source_key)}
              aria-label={t(expanded.has(node.source_key)?'sources.collapse':'sources.expand',{name:node.display_name})}
              onClick={()=>setExpanded(current=>{const next=new Set(current);if(next.has(node.source_key))next.delete(node.source_key);else next.add(node.source_key);return next;})}>
              {expanded.has(node.source_key)?'▾':'▸'}</button>}
            <ScopeCheckbox checked={enabled} mixed={mixed} disabled={!editable||busy||!node.supported||unknown||blockedAncestor}
              label={node.display_name} onChange={event=>toggle(node.source_key,event.target.checked)}/>
            <strong>{displayTitle}</strong>
          </div>
          <div className="meta scope-node-meta">
            {previewFormat && <span>{previewFormat.toUpperCase()}</span>}
            {displayTitle!==node.display_name&&<span>{node.display_name}</span>}
            {blockedAncestor && <span>{t('scope.ancestorUnavailable')}</span>}
            {node.kind==='mail' && <span>{t('scope.mailBody')}</span>}
            {node.kind==='archive' && <span>{t('scope.container')}</span>}
            {metrics.rows!=null && <span>{t(node.precision==='lower_bound'?'scope.rowsLowerBound':'scope.rows',{count:metrics.rows})}</span>}
            {metrics.cells!=null && <span>{t('scope.cells',{count:metrics.cells})}</span>}
            {metrics.file_bytes>0 && <span>{t('scope.size',{size:(metrics.file_bytes/1024).toFixed(1)})}</span>}
            <span>{depthLimited?t('scope.depthLimit',{depth:maximumDepth??6}):t(!node.supported?'scope.unsupported':unknown?'scope.unknown':node.assessment==='heavy'?'scope.heavy':(recommendation||decision)?.reason==='aggregate_budget'?'scope.aggregateBudget':'scope.small')}</span>
            {decision?.reason==='user_excluded' && <span>{t('scope.userExcluded')}</span>}
            {view===data.active && <span>{t(enabled?'scope.includedLabel':'scope.excludedLabel')}</span>}
            {editable && unknown && !depthLimited && <button type="button" disabled={busy} className="btn ghost" onClick={()=>newReview(node.source_key)}>{t('scope.expand')}</button>}
            {data.pending && previewFormat && node.saved_path && <button type="button" className="btn ghost" disabled={busy}
              aria-label={t('scope.previewNamed',{name:node.display_name})}
              onClick={()=>setPreview({node,canToggle:editable&&node.supported&&!unknown&&!blockedAncestor})}>{t('scope.preview')}</button>}
            {data.pending && node.saved_path && <a href={`/api/documents/${encodeURIComponent(docId)}/processing-scope/attachments/${node.source_key}`} download>{t('scope.download')}</a>}
          </div>
        </li>;
      })}</ul>
      {view?.inventory && <p>{t(view.inventory.complete?'scope.selectionStats':'scope.selectionPartial',{count:effective.size,total:nodes.filter(node=>node.supported!==false).length})}</p>}
      {editing&&<p className="source-location-note">{t(data.active && data.pending?(publishedDirty?'scope.pendingHint':'scope.noChanges'):'scope.excludedHint')}</p>}
      {view?.confirmed_by && <p>{t('scope.confirmedBy',{name:view.confirmed_by})}</p>}
      {view?.confirmed_at && <p>{t('scope.confirmedAt',{date:new Date(view.confirmed_at).toLocaleString()})}</p>}
      {similar && <p role="alert">{t('scope.similar')}</p>}
    </>}
    {error && <p className="error" role="alert">{error}</p>}
  </Modal>
    {preview && data?.pending && <AttachmentPreviewDialog key={`${data.pending.id}-${preview.node.source_key}`}
      sessionNotice={idleNotice}
      docId={docId} reviewId={data.pending.id} node={preview.node} included={effective.has(preview.node.source_key)}
      onReviewChanged={refreshPreservingSelection}
      onTitle={(title,title_source)=>setSourceTitles(current=>({...current,[`${data.pending.id}:${preview.node.source_key}:${preview.node.content_fingerprint}`]:{title,title_source}}))}
      onClose={()=>setPreview(null)} onToggle={preview.canToggle&&editable?()=>toggle(preview.node.source_key,!effective.has(preview.node.source_key)):null}/>}
  </div>;
}
