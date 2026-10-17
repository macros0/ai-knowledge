import { sourceAncestors } from './sourceTree.mjs';

export function scopeTreeSources(nodes = []) {
  return nodes.map(node => ({...node, source_id: node.source_key, parent_source_id: node.parent_key}));
}

export function effectiveSelection(nodes, selected) {
  const byKey = new Map(nodes.map(node => [node.source_key, node]));
  const chosen = selected instanceof Set ? selected : new Set(selected);
  return new Set(nodes.filter(node => {
    let current = node;
    const seen = new Set();
    while (current) {
      if (!chosen.has(current.source_key) || current.supported === false || current.children_complete === false || current.assessment === 'unknown' || seen.has(current.source_key)) return false;
      seen.add(current.source_key);
      if (current.parent_key === 'root') return true;
      current = byKey.get(current.parent_key);
    }
    return false;
  }).map(node => node.source_key));
}

export function toggleSourceSelection(nodes, selected, key, checked) {
  const next = new Set(selected);
  const sources = scopeTreeSources(nodes);
  if (checked) {
    next.add(key);
    for (const ancestor of sourceAncestors(sources, key)) next.add(ancestor);
  } else {
    next.delete(key);
    for (const node of nodes) if (sourceAncestors(sources, node.source_key).includes(key)) next.delete(node.source_key);
  }
  return effectiveSelection(nodes, next);
}

export function scopeBadgeModel(active, pending) {
  return {showExcluded: Number(active?.excluded) > 0, excluded: active?.excluded || 0,
    total: active?.total, countComplete: active?.count_complete === true,
    pendingAction: pending?.requires_action === true,
    updating: !!active && ['submitted', 'validating'].includes(pending?.state)};
}

export function scopeChangeAction(preview, capabilities = {}) {
  const mode=preview?.mode;
  if(mode==='noop')return {action:'noop',endpoint:'apply-exclusions',disabled:false,reason:null};
  const capability={fast_exclude:'fast_exclusion',reuse_only:'cached_restore',incremental_generate:'incremental_generation'}[mode];
  if(!capability || capabilities[capability]!==true)return {
    action:mode||'analyze',endpoint:null,disabled:true,reason:mode==='blocked'?'blocked':'unavailable'};
  return {action:mode,endpoint:mode==='fast_exclude'?'apply-exclusions':'apply-changes',disabled:false,reason:null};
}

export function scopeChoiceAvailable(availability, summary, pending) {
  if(Number(summary?.excluded)>0 || pending?.requires_action)return true;
  if(availability)return availability==='available';
  // Older running servers omit availability. Cancellation removes pending,
  // but must not strand an uninspected legacy publication without an entry.
  if(summary?.mode==='legacy_all'&&summary.count_complete!==true)return true;
  return Number(summary?.total)>Number(summary?.unsupported||0);
}
