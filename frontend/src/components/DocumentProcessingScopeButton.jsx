"use client";
import {useState} from 'react';
import {useI18n} from '@/i18n/LocaleContext';
import ProcessingScopeBadge from './ProcessingScopeBadge';
import DocumentProcessingScopeDialog from './DocumentProcessingScopeDialog';
import {scopeBadgeModel,scopeChoiceAvailable} from '@/lib/processingScope.mjs';

export default function DocumentProcessingScopeButton({docId,summary,pending,availability,onUpdated,labelKey='scope.details',badge=false}) {
  const {t}=useI18n();
  const [open,setOpen]=useState(false);
  if(!scopeChoiceAvailable(availability,summary,pending))return null;
  const badgeView=scopeBadgeModel(summary,pending);
  const showBadge=badge&&(badgeView.showExcluded||badgeView.pendingAction);
  return <>
    {showBadge ? <ProcessingScopeBadge summary={summary} pending={pending} onOpen={()=>setOpen(true)}/>
      : <button type="button" className="bulk-tag-btn" onClick={()=>setOpen(true)}>{t(pending?.requires_action?'scope.choose':!availability&&summary?.mode==='legacy_all'?'scope.checkAttachments':labelKey)}</button>}
    {open && <DocumentProcessingScopeDialog docId={docId} open onClose={()=>setOpen(false)} onUpdated={onUpdated}/>}
  </>;
}
