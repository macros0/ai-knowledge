"use client";
import {useI18n} from '@/i18n/LocaleContext';
import {scopeBadgeModel} from '@/lib/processingScope.mjs';

export default function ProcessingScopeBadge({summary,pending,onOpen}) {
  const {t}=useI18n();
  const view=scopeBadgeModel(summary,pending);
  if (!view.showExcluded && !view.pendingAction) return null;
  return <button type="button" className="processing-scope-badge" onClick={onOpen}
    title={t(view.pendingAction?'scope.choose':'scope.excludedHint')}>
    {view.showExcluded && t(view.countComplete?'scope.excludedBadge':'scope.excludedPartial',{count:view.excluded,total:view.total})}
    {view.showExcluded && view.pendingAction && ' · '}
    {view.pendingAction && t('scope.choose')}
  </button>;
}
