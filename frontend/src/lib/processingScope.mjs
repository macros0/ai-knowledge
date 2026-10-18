import { sourceAncestors } from './sourceTree.mjs';

export function attachmentPreviewFormat(node) {
  const groups={mail:['eml','msg'],archive:['zip','7z','rar']};
  let kind=node.format||node.kind;
  if(groups[kind]){
    const suffix=(node.saved_path||'').split('.').pop().toLowerCase();
    kind=groups[kind].includes(suffix)?suffix:null;
  }
  return node.supported!==false&&['docx','xlsx','pdf','eml','msg','zip','7z','rar'].includes(kind)?kind:null;
}

export function scopeTreeSources(nodes = []) {
  return nodes.map(node => ({...node, source_id: node.source_key, parent_source_id: node.parent_key}));
}

export function savedScopeChanges(active,pending) {
  if(!active||!pending||!['draft','awaiting_similar'].includes(pending.state))return null;
  const nodes=pending.inventory?.nodes||[];
  const previous=active.inventory?new Map((active.decisions||[]).map(d=>[d.source_key,d.effective_included])):null;
  const selected=new Map((pending.decisions||[]).map(d=>[d.source_key,d.effective_included]));
  const changed=nodes.filter(n=>n.supported!==false&&n.assessment!=='unknown'&&n.children_complete!==false&&
    (previous?selected.get(n.source_key)!==(previous.get(n.source_key)??false):selected.get(n.source_key)===false));
  const sources=scopeTreeSources(nodes);
  const topChanges=included=>{
    const candidates=changed.filter(n=>selected.get(n.source_key)===included);
    const keys=new Set(candidates.map(n=>n.source_key));
    return candidates.filter(n=>!sourceAncestors(sources,n.source_key).some(key=>keys.has(key)));
  };
  const excluded=topChanges(false),included=topChanges(true);
  return excluded.length||included.length?{excluded,included}:null;
}

export function effectiveSelection(nodes, selected, maximumDepth = Infinity) {
  const byKey = new Map(nodes.map(node => [node.source_key, node]));
  const chosen = selected instanceof Set ? selected : new Set(selected);
  return new Set(nodes.filter(node => {
    let current = node;
    const seen = new Set();
    while (current) {
      if (!chosen.has(current.source_key) || current.supported === false || current.status === 'skipped_depth' || current.children_complete === false || current.assessment === 'unknown' || seen.has(current.source_key)) return false;
      seen.add(current.source_key);
      if (seen.size > maximumDepth) return false;
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

function supportedTotal(summary) {
  if(summary?.total==null)return summary?.total;
  return summary.counts_version>=2?summary.total:Math.max(0,summary.total-(summary.unsupported||0));
}

export function scopeBadgeModel(active, pending) {
  return {showExcluded: Number(active?.excluded) > 0, excluded: active?.excluded || 0,
    total: supportedTotal(active), countComplete: active?.count_complete === true,
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
  return Number(supportedTotal(summary))>0;
}
