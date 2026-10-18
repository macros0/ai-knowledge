import test from 'node:test';
import assert from 'node:assert/strict';
import {runDocumentUploadBatch} from '../src/lib/documentUploadBatch.mjs';
import {setImmediate} from 'node:timers/promises';
import {componentHarness} from './support/componentHarness.mjs';
import * as scope from '../src/lib/processingScope.mjs';
import * as sourceTree from '../src/lib/sourceTree.mjs';
import * as lease from '../src/lib/editorLeaseSession.mjs';
import * as adapters from '../src/lib/editorLeaseAdapters.mjs';

const t=key=>key;
function scopeDialog(api,canEdit=true,globals={},props={}, {enterEditor=true,translate=t}={}){
  const completedLease=async(action,...args)=>{
    const value=await action(...args);
    if(value?.state!=='editing')return value;
    const now=(globals.Date||Date).now();
    return {server_now:new Date(now).toISOString(),expires_at:new Date(now+Math.min(180000,(value.idle_remaining_seconds??600)*1000)).toISOString(),
      idle_expires_at:new Date(now+(value.idle_remaining_seconds??600)*1000).toISOString(),...value};
  };
  const leaseApi={...api};
  for(const name of ['acquireProcessingScopeEditLock','renewProcessingScopeEditLock'])
    if(api[name])leaseApi[name]=(...args)=>completedLease(api[name],...args);
  const mounted=componentHarness(new URL('../src/components/DocumentProcessingScopeDialog.jsx',import.meta.url),{
    '@/i18n/LocaleContext':{useI18n:()=>({t:translate})},
    '@/context/AuthContext':{useAuth:()=>({mode:'keycloak_oidc',hasRole:()=>typeof canEdit==='function'?canEdit():canEdit})},
    '@/lib/api':{friendlyApiError:err=>err.message,getProcessingScopeLabels:async()=>({labels:{}}),...leaseApi},
    '@/lib/processingScope.mjs':scope,'@/lib/sourceTree.mjs':sourceTree,
    '@/lib/editorLeaseSession.mjs':lease,'@/lib/editorLeaseAdapters.mjs':adapters,
    './EditorLeaseNotice':()=>null,
    './Modal':()=>null,'./AttachmentPreviewDialog':()=>null,
  },{docId:'doc',open:true,onClose:()=>{},...props},globals);
  // Most regressions exercise editing callbacks. Perform the real entry click;
  // view-first tests explicitly opt out rather than passing test-only UI props.
  const render=mounted.render;
  let entered=false;
  mounted.render=(...args)=>{
    let tree=render(...args);
    if(enterEditor&&!entered){
      const edit=mounted.find(n=>n.type==='button'&&n.props.children==='scope.edit');
      if(edit&&!edit.props.disabled){entered=true;edit.props.onClick();tree=render();}
    }
    return tree;
  };
  return mounted;
}
const scopeState=()=>({active:null,pending:{id:'review',revision:1,state:'draft',inventory:{nodes:[
  {source_key:'excel',parent_key:'root',display_name:'table.xlsx',supported:true,assessment:'heavy',kind:'xlsx',saved_path:'x.xlsx'},
  {source_key:'unknown',parent_key:'root',display_name:'nested.zip',supported:true,assessment:'unknown',children_complete:false},
]},decisions:[{source_key:'excel',effective_included:false},{source_key:'unknown',effective_included:false}]}});

test('hard depth limit is explicit and never offers repeated expansion or inclusion',async()=>{
  const server=scopeState();
  Object.assign(server.pending.inventory.nodes[1],{status:'skipped_depth',assessment:'unknown',children_complete:false});
  const mounted=scopeDialog({getProcessingScope:async()=>server});
  mounted.render();await setImmediate();mounted.render();
  assert(mounted.find(n=>n.type==='span'&&n.props.children==='scope.depthLimit'));
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.expand'));
  assert.equal(mounted.find(n=>n.props?.label==='nested.zip').props.disabled,true);
  mounted.dispose();
});

test('depth-cutoff container stays visible with its captured global limit',async()=>{
  const server=scopeState();
  server.pending.inventory.traversal_limits={max_depth:0};
  Object.assign(server.pending.inventory.nodes[1],{status:'skipped_depth',assessment:'unknown',children_complete:false});
  const translate=(key,params)=>key==='scope.depthLimit'?`${key}:${params.depth}`:key;
  const mounted=scopeDialog({getProcessingScope:async()=>server},true,{}, {},{translate});
  mounted.render();await setImmediate();mounted.render();
  assert(mounted.find(n=>n.type==='strong'&&n.props.children==='nested.zip'));
  assert(mounted.find(n=>n.type==='span'&&n.props.children==='scope.depthLimit:0'));
  assert.equal(mounted.find(n=>n.props?.label==='nested.zip').props.disabled,true);
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.expand'));
  assert.equal(mounted.find(n=>n.type==='button'&&n.props.children==='scope.selectAll').props.disabled,true);
  mounted.dispose();
});

test('first screen shows saved exclusions and a blocked check without presenting them as published',async()=>{
  const server=scopeState();
  server.active={inventory:null,decisions:[],summary:{mode:'legacy_all',generation_id:'base'}};
  server.pending.last_check={mode:'blocked',reasons:['incomplete_provenance']};
  const mounted=scopeDialog({getProcessingScope:async()=>server},true,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  assert(mounted.find(n=>n.type==='p'&&n.props.children==='scope.savedNotApplied'));
  assert(mounted.find(n=>n.type==='li'&&n.props.children==='table.xlsx'));
  assert(mounted.find(n=>n.type==='p'&&n.props.children==='scope.savedCheckBlocked'));
  assert(mounted.find(n=>n.type==='p'&&n.props.children==='scope.changeReason.incomplete_provenance'));
  assert(!mounted.find(n=>n.props?.label==='table.xlsx'),'Draft must not replace the published tree');
  mounted.dispose();
});

test('an unchanged saved selection does not appear as an unapplied exclusion',async()=>{
  const server=scopeState();server.active={...structuredClone(server.pending),summary:{generation_id:'base'}};
  const mounted=scopeDialog({getProcessingScope:async()=>server},true,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  assert(!mounted.find(n=>n.type==='p'&&n.props.children==='scope.savedNotApplied'));
  mounted.dispose();
});

test('first screen loads original icon labels without opening preview or changing selection',async()=>{
  const server=scopeState();server.pending.inventory.nodes[0].content_fingerprint='fp';
  const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>server,getProcessingScopeLabels:async(...args)=>{
    calls.push(args.slice(0,3));return {scope_type:'pending',scope_id:'review',labels:{excel:{display_label:'Original source.pdf',content_fingerprint:'fp'}}};
  }},false,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  assert(mounted.find(n=>n.type==='strong'&&n.props.children==='Original source.pdf'));
  assert.deepEqual(calls,[['doc','pending','review']]);
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,false);
  assert(!mounted.find(n=>n.props?.reviewId==='review'));
  mounted.dispose();
});

test('refreshing an unchanged scope does not restart an in-flight label read',async()=>{
  const server=scopeState();let calls=0,finish;
  const mounted=scopeDialog({getProcessingScope:async()=>structuredClone(server),getProcessingScopeLabels:()=>{
    calls++;return new Promise(resolve=>{finish=resolve;});
  }},false,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  mounted.find(n=>n.type==='button'&&n.props.children==='scope.preview').props.onClick();mounted.render();
  await mounted.find(n=>n.props?.reviewId==='review').props.onReviewChanged();mounted.render();
  assert.equal(calls,1,'The same scope must keep its original label request');
  finish({labels:{}});await setImmediate();mounted.dispose();
});

test('stored icon caption takes priority over a workbook title on the first screen',async()=>{
  const server=scopeState();Object.assign(server.pending.inventory.nodes[0],{display_label:'Original.xlsx',title:'Internal title',title_source:'properties'});
  const mounted=scopeDialog({getProcessingScope:async()=>server},false,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  assert(mounted.find(n=>n.type==='strong'&&n.props.children==='Original.xlsx'));
  mounted.dispose();
});

test('a label response for a different fingerprint cannot rename an attachment',async()=>{
  const server=scopeState();server.pending.inventory.nodes[0].content_fingerprint='current';
  const mounted=scopeDialog({getProcessingScope:async()=>server,getProcessingScopeLabels:async()=>({scope_type:'pending',scope_id:'review',
    labels:{excel:{display_label:'Wrong.pdf',content_fingerprint:'old'}}})},false,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  assert(!mounted.find(n=>n.type==='strong'&&n.props.children==='Wrong.pdf'));
  assert(mounted.find(n=>n.type==='strong'&&n.props.children==='table.xlsx'));
  mounted.dispose();
});

test('late labels from the published view cannot rename draft attachments',async()=>{
  const server=scopeState();server.pending.inventory.nodes[0].content_fingerprint='same';
  server.active={...structuredClone(server.pending),summary:{generation_id:'published'}};
  let finish;
  const mounted=scopeDialog({getProcessingScope:async()=>server,getProcessingScopeLabels:async(_doc,type)=>{
    if(type==='active')return new Promise(resolve=>{finish=resolve;});
    return {scope_type:'pending',scope_id:'review',labels:{}};
  }},true,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  await mounted.find(n=>n.type==='button'&&n.props.children==='scope.edit').props.onClick();mounted.render();
  finish({scope_type:'active',scope_id:'published',labels:{excel:{display_label:'Late.pdf',content_fingerprint:'same'}}});
  await setImmediate();mounted.render();
  assert(!mounted.find(n=>n.type==='strong'&&n.props.children==='Late.pdf'));
  mounted.dispose();
});

for(const [kind,extension] of [['docx','docx'],['pdf','pdf'],['mail','eml'],['mail','msg'],['archive','zip'],['archive','7z'],['archive','rar']])test(`supported ${kind}/${extension} attachment offers preview and shows its real format`,async()=>{
  const server=scopeState();Object.assign(server.pending.inventory.nodes[0],{kind,saved_path:'payload.'+extension,display_name:'oleObject1.bin'});
  const mounted=scopeDialog({getProcessingScope:async()=>server});
  mounted.render();await setImmediate();mounted.render();
  assert(mounted.find(n=>n.type==='button'&&n.props.children==='scope.preview'));
  assert(mounted.find(n=>n.type==='span'&&n.props.children===extension.toUpperCase()));
  mounted.dispose();
});

for(const change of ['fingerprint','review'])test(`a discovered title stays local and cannot leak across a changed ${change}`,async()=>{
  let server=scopeState();server.pending.inventory.nodes[0].content_fingerprint='first';
  const mounted=scopeDialog({getProcessingScope:async()=>server});
  mounted.render();await setImmediate();mounted.render();
  mounted.find(n=>n.type==='button'&&n.props.children==='scope.preview').props.onClick();mounted.render();
  mounted.find(n=>n.props?.reviewId==='review').props.onTitle('Actual workbook title','properties');mounted.render();
  assert(mounted.find(n=>n.type==='strong'&&n.props.children==='Actual workbook title'));
  server=structuredClone(server);
  if(change==='fingerprint')server.pending.inventory.nodes[0].content_fingerprint='second';else server.pending.id='new-review';
  await mounted.find(n=>n.props?.reviewId==='review').props.onReviewChanged();mounted.render();
  assert(!mounted.find(n=>n.type==='strong'&&n.props.children==='Actual workbook title'));
  mounted.dispose();
});

test('first open shows the published scope; explicit edit resumes the saved draft',async()=>{
  const draft=scopeState();draft.pending.decisions[0].effective_included=true;
  const active=structuredClone(draft.pending);active.decisions[0].effective_included=false;
  const server={...draft,active:{...active,summary:{generation_id:'base'}},capabilities:{cached_restore:true}};
  const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    createProcessingScopeReview:async()=>calls.push('create'),
    saveProcessingScopeReview:async()=>calls.push('save'),
  },true,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  let checkbox=mounted.find(n=>n.props?.label==='table.xlsx');
  assert.equal(checkbox.props.checked,false,'Initial view is the actual published selection');
  assert.equal(checkbox.props.disabled,true);
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.save'));
  await mounted.find(n=>n.type==='button'&&n.props.children==='scope.edit').props.onClick();mounted.render();
  checkbox=mounted.find(n=>n.props?.label==='table.xlsx');
  assert.equal(checkbox.props.checked,true,'Resume the existing saved draft after entering editing');
  assert.equal(checkbox.props.disabled,false);
  assert.deepEqual(calls,[],'Viewing and resuming must not replace or save the draft');
  mounted.dispose();
});

test('first open without a draft creates it only after the edit click and remains editable',async()=>{
  const draft=scopeState();const active={...structuredClone(draft.pending),summary:{generation_id:'base'}};
  const published={...draft,active,pending:null,capabilities:{fast_exclusion:true}};
  const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>published,
    createProcessingScopeReview:async(_id,body)=>{calls.push(body);return {...published,pending:draft.pending};},
  },true,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  assert.deepEqual(calls,[]);
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,true);
  await mounted.find(n=>n.type==='button'&&n.props.children==='scope.edit').props.onClick();mounted.render();
  assert.equal(calls.length,1);assert.equal(calls[0].expected_active_generation_id,'base');
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,false);
  mounted.dispose();
});

test('legacy published view distinguishes an unknown inventory from an empty attachment list',async()=>{
  const draft=scopeState();draft.pending.decisions[0].effective_included=true;
  const server={...draft,active:{summary:{mode:'legacy_all',generation_id:'base'},inventory:null,decisions:[]},capabilities:{cached_restore:true}};
  const mounted=scopeDialog({getProcessingScope:async()=>server},true,{}, {},{enterEditor:false});
  const message=key=>mounted.find(n=>n.type==='p'&&n.props.children===key);
  mounted.render();await setImmediate();mounted.render();
  assert(message('scope.viewLegacy'),'Explain that the published inventory was never saved');
  for(const key of ['scope.viewPublished','scope.selectionStats','scope.selectionPartial','scope.noAttachments','scope.oldActive'])assert(!message(key),key);
  assert(!mounted.find(n=>n.props?.label==='table.xlsx'),'Do not show draft attachments as published');
  await mounted.find(n=>n.type==='button'&&n.props.children==='scope.edit').props.onClick();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,true);
  assert(message('scope.selectionPartial'),'The actual draft inventory has a meaningful count');
  assert(!message('scope.oldActive'),'A draft is not an admitted processing operation');
  mounted.dispose();
});

test('a known empty published inventory can still show zero attachments',async()=>{
  const active={summary:{generation_id:'base'},inventory:{nodes:[],complete:true},decisions:[]};
  const mounted=scopeDialog({getProcessingScope:async()=>({active,pending:null})},true,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  assert(mounted.find(n=>n.type==='p'&&n.props.children==='scope.viewPublished'));
  assert(mounted.find(n=>n.type==='p'&&n.props.children==='scope.noAttachments'));
  assert(mounted.find(n=>n.type==='p'&&n.props.children==='scope.selectionStats'));
  assert(!mounted.find(n=>n.type==='p'&&n.props.children==='scope.viewLegacy'));
  mounted.dispose();
});

test('selection statistics exclude unsupported objects while keeping them visible',async()=>{
  const server=scopeState();
  server.pending.inventory.nodes.push({source_key:'image',parent_key:'root',display_name:'image.wmf',kind:'unsupported',supported:false});
  const translate=(key,params)=>key==='scope.selectionPartial'?`${key}:${params.total}`:key;
  const mounted=scopeDialog({getProcessingScope:async()=>server},true,{}, {},{translate});
  mounted.render();await setImmediate();mounted.render();
  assert(mounted.find(n=>n.type==='strong'&&n.props.children==='image.wmf'));
  assert(mounted.find(n=>n.type==='p'&&n.props.children==='scope.selectionPartial:2'));
  mounted.dispose();
});

for(const state of ['draft','awaiting_similar','validating','submitted'])test(`published-version processing notice follows actual review state ${state}`,async()=>{
  const server=scopeState();server.pending.state=state;
  server.active={...structuredClone(server.pending),summary:{generation_id:'base'}};
  const mounted=scopeDialog({getProcessingScope:async()=>server},true,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  const notice=mounted.find(n=>n.type==='p'&&n.props.children==='scope.oldActive');
  assert.equal(!!notice,['validating','submitted'].includes(state));
  mounted.dispose();
});

test('failed entry creation stays in view mode with an explicit retry, without a request loop',async()=>{
  const draft=scopeState();
  const published={...draft,active:{...draft.pending,summary:{generation_id:'base'}},pending:null};
  let attempts=0;
  const mounted=scopeDialog({getProcessingScope:async()=>published,
    createProcessingScopeReview:async()=>{attempts++;throw new Error('creation failed');},
  },true,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  await mounted.find(n=>n.type==='button'&&n.props.children==='scope.edit').props.onClick();mounted.render();
  assert.equal(attempts,1);
  assert(mounted.find(n=>n.type==='button'&&n.props.children==='scope.edit'));
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,true);
  mounted.dispose();
});

test('non-timeout save failure retains local choices without a reload action',async()=>{
  const server=scopeState();
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    saveProcessingScopeReview:async()=>{throw new Error('save failed');},
  });
  const button=key=>mounted.find(n=>n.type==='button'&&n.props.children===key);
  mounted.render();await setImmediate();mounted.render();
  assert(!button('scope.refresh'),'Do not offer manual reloading in normal editing');
  mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:true}});mounted.render();
  await button('scope.save').props.onClick();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,true);
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,false);
  assert(!button('scope.refresh'),'No redundant reload control after errors');
  mounted.dispose();
});

test('initial load failure leaves close available without pretending a scope was loaded',async()=>{
  const mounted=scopeDialog({getProcessingScope:async()=>{throw new Error('load failed');}},true,{}, {},{enterEditor:false});
  mounted.render();await setImmediate();mounted.render();
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.refresh'));
  assert(mounted.find(n=>n.type==='button'&&n.props.children==='scope.later'));
  assert(!mounted.find(n=>n.props?.label==='table.xlsx'));
  mounted.dispose();
});

for(const failure of [{code:'timeout',status:0},{status:408},{status:504}])test(`save timeout ${failure.code||failure.status} returns to view; edit reconciles the saved server revision`,async()=>{
  let server=scopeState();
  server.active={...structuredClone(server.pending),summary:{generation_id:'base'}};
  const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>{calls.push('get');return server;},
    saveProcessingScopeReview:async()=>{
      calls.push('save');server=structuredClone(server);
      server.pending.revision=2;server.pending.decisions[0].effective_included=true;
      throw Object.assign(new Error('timeout'),failure);
    },
  });
  const button=key=>mounted.find(n=>n.type==='button'&&n.props.children===key);
  mounted.render();await setImmediate();mounted.render();
  mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:true}});mounted.render();
  await button('scope.save').props.onClick();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,true);
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,false,'Return to published choices');
  assert(!button('scope.save'));assert(!button('scope.refresh'));
  assert.deepEqual(calls,['get','save'],'Never repeat an uncertain save automatically');
  await button('scope.edit').props.onClick();mounted.render();
  assert.deepEqual(calls,['get','save','get']);
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,true,'Resume what the server actually saved');
  assert.equal(button('scope.save').props.disabled,true,'The reconciled server draft is already saved');
  mounted.dispose();
});

test('uncertain apply returns to view and reconciles a submitted review without resending the operation',async()=>{
  let server=scopeState();server.pending.decisions[0].effective_included=true;
  const active=structuredClone(server.pending);active.decisions[0].effective_included=false;
  server={...server,active:{...active,summary:{generation_id:'base'}},capabilities:{cached_restore:true}};
  const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>{calls.push('get');return server;},
    previewProcessingScopeChange:async()=>({mode:'reuse_only',plan_id:'plan'}),
    applyProcessingScopeChange:async()=>{
      calls.push('apply');server={...server,pending:{...server.pending,state:'submitted'}};
      throw Object.assign(new Error('timeout'),{code:'timeout'});
    },
  });
  const button=key=>mounted.find(n=>n.type==='button'&&n.props.children===key);
  mounted.render();await setImmediate();mounted.render();
  await button('scope.checkChanges').props.onClick();mounted.render();
  await button('scope.restoreCached').props.onClick();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,true);
  assert(!button('scope.restoreCached'));assert(!button('scope.refresh'));
  await button('scope.edit').props.onClick();mounted.render();
  assert.deepEqual(calls,['get','apply','get']);
  assert(!button('scope.edit'),'A submitted operation cannot be opened as a draft');
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,false);
  mounted.dispose();
});

test('unchanged published scope disables save/check, including toggle back to the original choice',async()=>{
  const draft=scopeState();
  const server={...draft,active:{...structuredClone(draft.pending),summary:{generation_id:'base'}},
    capabilities:{fast_exclusion:true,cached_restore:true}};
  const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    saveProcessingScopeReview:async()=>{calls.push('save');return server;},
    previewProcessingScopeChange:async()=>{calls.push('preview');},
  });
  const button=key=>mounted.find(n=>n.type==='button'&&n.props.children===key);
  const check=()=>button('scope.noChanges')||button('scope.checkChanges');
  mounted.render();await setImmediate();mounted.render();
  assert.equal(button('scope.save').props.disabled,true);
  assert.equal(check().props.disabled,true);
  assert(!button('scope.discard'),'Unchanged choices must not offer a second reset action');
  assert(!mounted.find(n=>n.type==='p'&&n.props.children==='scope.pendingHint'));
  assert(!mounted.find(n=>n.type==='p'&&n.props.children==='scope.oldActive'));
  await button('scope.save').props.onClick();await check().props.onClick();
  assert.deepEqual(calls,[],'Unchanged choices must not create a save or preview request');
  mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:true}});mounted.render();
  assert.equal(button('scope.save').props.disabled,false);
  assert.equal(check().props.disabled,false);
  assert(button('scope.discard'));
  mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:false}});mounted.render();
  assert.equal(button('scope.save').props.disabled,true);
  assert.equal(check().props.disabled,true);
  assert(!button('scope.discard'));
  assert.equal(button('scope.clear').props.disabled,true);
  assert.equal(button('scope.selectAll').props.disabled,false);
  assert.equal(button('scope.regenerate').props.disabled,false,'Explicit full regeneration is independent');
  mounted.dispose();
});

test('saved draft differences remain applicable while save and redundant bulk selection stay disabled',async()=>{
  const draft=scopeState();
  draft.pending.decisions[0].effective_included=true;
  const active=structuredClone(draft.pending);active.decisions[0].effective_included=false;
  const server={...draft,active:{...active,summary:{generation_id:'base'}},capabilities:{cached_restore:true}};
  const mounted=scopeDialog({getProcessingScope:async()=>server});
  const button=key=>mounted.find(n=>n.type==='button'&&n.props.children===key);
  mounted.render();await setImmediate();mounted.render();
  assert.equal(button('scope.save').props.disabled,true);
  assert.equal(button('scope.checkChanges').props.disabled,false,'A previously saved change is still pending');
  assert(button('scope.discard'),'A saved changed draft can be explicitly reset');
  assert.equal(button('scope.selectAll').props.disabled,true,'Unknown branch cannot count as selectable');
  assert.equal(button('scope.clear').props.disabled,false);
  button('scope.clear').props.onClick();mounted.render();
  assert.equal(button('scope.clear').props.disabled,true);
  assert.equal(button('scope.selectAll').props.disabled,false);
  assert.equal(button('scope.save').props.disabled,false);
  assert.equal(button('scope.noChanges').props.disabled,true);
  mounted.dispose();
});

test('bulk return to published choices clears the obsolete apply preview',async()=>{
  const draft=scopeState();const active=structuredClone(draft.pending);active.decisions[0].effective_included=true;
  const server={...draft,active:{...active,summary:{generation_id:'base'}},capabilities:{fast_exclusion:true}};
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    previewProcessingScopeChange:async()=>({plan_id:'plan',mode:'fast_exclude',counts:{removed:{sources:1}}}),
  });
  const button=key=>mounted.find(n=>n.type==='button'&&n.props.children===key);
  mounted.render();await setImmediate();mounted.render();
  await button('scope.checkChanges').props.onClick();mounted.render();
  assert(button('scope.applyExclusions'));
  assert(mounted.find(n=>n.type==='p'&&n.props.children==='scope.exclusionPreview'));
  button('scope.selectAll').props.onClick();mounted.render();
  assert.equal(button('scope.noChanges').props.disabled,true);
  assert(!mounted.find(n=>n.type==='p'&&n.props.children==='scope.exclusionPreview'));
  mounted.dispose();
});

test('a newly unavailable attachment is still a difference from the published included source',async()=>{
  const draft=scopeState();const active=structuredClone(draft.pending);active.decisions[0].effective_included=true;
  draft.pending.inventory.nodes[0].assessment='unknown';
  draft.pending.inventory.nodes[0].children_complete=false;
  const server={...draft,active:{...active,summary:{generation_id:'base'}},capabilities:{fast_exclusion:true}};
  const mounted=scopeDialog({getProcessingScope:async()=>server});
  mounted.render();await setImmediate();mounted.render();
  const check=mounted.find(n=>n.type==='button'&&n.props.children==='scope.checkChanges');
  assert(check,'Pending eligibility must not erase the included published baseline');
  assert.equal(check.props.disabled,false);
  mounted.dispose();
});

test('legacy server noop preview cannot be applied; first document still offers initial processing',async()=>{
  const draft=scopeState();const server={...draft,active:{summary:{generation_id:'base'}},capabilities:{fast_exclusion:true}};
  const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    previewProcessingScopeChange:async()=>({plan_id:'noop',mode:'noop'}),
    applyProcessingScopeChange:async()=>calls.push('apply'),
  });
  const button=key=>mounted.find(n=>n.type==='button'&&n.props.children===key);
  mounted.render();await setImmediate();mounted.render();
  await button('scope.checkChanges').props.onClick();mounted.render();
  assert.equal(button('scope.noChanges').props.disabled,true);
  await button('scope.noChanges').props.onClick();assert.deepEqual(calls,[]);
  mounted.dispose();
  const first=scopeDialog({getProcessingScope:async()=>draft});
  first.render();await setImmediate();first.render();
  assert.equal(first.find(n=>n.type==='button'&&n.props.children==='scope.confirm').props.disabled,false);
  first.dispose();
});

test('close keeps the saved draft while explicit reset removes it without applying a publication',async()=>{
  const draft=scopeState();draft.pending.decisions[0].effective_included=true;
  const active=structuredClone(draft.pending);active.decisions[0].effective_included=false;
  let server={...draft,active:{...active,summary:{generation_id:'base'}},capabilities:{cached_restore:true}};
  let closes=0;const calls=[];
  const api={getProcessingScope:async()=>server,
    cancelProcessingScopeReview:async(_id,body)=>{calls.push(['reset',body]);server={...server,pending:null};return server;},
    confirmProcessingScope:async()=>calls.push(['confirm']),applyProcessingScopeChange:async()=>calls.push(['apply']),
  };
  const first=scopeDialog(api,true,{}, {onClose:()=>closes++});
  first.render();await setImmediate();first.render();
  first.find(n=>n.type==='button'&&n.props.children==='scope.later').props.onClick();
  assert.equal(closes,1);assert.deepEqual(calls,[]);assert(server.pending);
  first.dispose();
  const second=scopeDialog(api);
  second.render();await setImmediate();second.render();
  await second.find(n=>n.type==='button'&&n.props.children==='scope.discard').props.onClick();second.render();
  assert.deepEqual(calls.map(call=>call[0]),['reset']);
  assert.equal(calls[0][1].review_id,'review');
  assert.equal(server.pending,null);assert.equal(server.active.summary.generation_id,'base');
  assert(second.find(n=>n.type==='button'&&n.props.children==='scope.edit'));
  second.dispose();
});

test('another editor keeps scope read-only while attachment preview remains available',async()=>{
  const server={...scopeState(),capabilities:{edit_lock:true},edit_lock:{state:'editing',owner:'Bob'}};
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    acquireProcessingScopeEditLock:async()=>{throw Object.assign(new Error('locked'),{code:'scope_edit_locked'});},
    releaseProcessingScopeEditLock:async()=>{},
  });
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,true);
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.confirm'));
  assert(mounted.find(n=>n.type==='button'&&n.props.children==='scope.preview'));
  mounted.dispose();
});

test('owning tab sends its opaque lease with saved choices and releases on close',async()=>{
  const server={...scopeState(),capabilities:{edit_lock:true},edit_lock:{state:'available'}};
  const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    acquireProcessingScopeEditLock:async(_id,body)=>{calls.push(['claim',body]);return {state:'editing',owner:'Alice'};},
    releaseProcessingScopeEditLock:async(_id,body)=>{calls.push(['release',body]);},
    saveProcessingScopeReview:async(_id,body)=>{calls.push(['save',body]);return server;},
  });
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,false);
  mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:true}});mounted.render();
  await mounted.find(n=>n.type==='button'&&n.props.children==='scope.save').props.onClick();
  mounted.dispose();await setImmediate();
  assert.match(calls[0][1].edit_token,/^[a-f0-9]{64}$/);
  assert.equal(calls.find(c=>c[0]==='save')[1].edit_token,calls[0][1].edit_token);
  assert.equal(calls.at(-1)[0],'release');
  assert.equal(calls.at(-1)[1].edit_token,calls[0][1].edit_token);
});

for(const failure of ['heartbeat','save'])test(`lost ${failure} lease immediately fences editing`,async context=>{
  const server={...scopeState(),capabilities:{edit_lock:true},edit_lock:{state:'available'}};
  server.active={...structuredClone(server.pending),summary:{generation_id:'base'}};
  server.pending.decisions[0].effective_included=true;
  let beat;
  const lost=async()=>{throw Object.assign(new Error('lost'),{code:'scope_edit_lock_lost'});};
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    acquireProcessingScopeEditLock:async()=>({state:'editing',owner:'Alice'}),
    releaseProcessingScopeEditLock:async()=>{},renewProcessingScopeEditLock:lost,saveProcessingScopeReview:lost,
  },true,{setTimeout:(fn,delay)=>{if(delay===60000)beat=fn;return 1;},clearTimeout:()=>{}});
  context.after(()=>mounted.dispose());
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,false);
  if(failure==='heartbeat'){beat();await setImmediate();}
  else {
    mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:false}});mounted.render();
    await mounted.find(n=>n.type==='button'&&n.props.children==='scope.save').props.onClick();
  }
  mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,true);
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,false,'An expired editor returns to the published view');
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.save'));
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.confirm'));
  assert.equal(mounted.find(n=>n.props?.expiryAction==='close').props.state,'lost');
  mounted.dispose();
});

test('reopening after an applying operation finishes acquires a fresh editor lease without a reload control',async()=>{
  let server={...scopeState(),capabilities:{edit_lock:true},edit_lock:{state:'applying'}};
  const api={getProcessingScope:async()=>server,
    acquireProcessingScopeEditLock:async()=>{
      if(server.edit_lock.state==='applying')throw Object.assign(new Error('busy'),{code:'scope_change_in_progress'});
      return {state:'editing',owner:'Alice'};
    },
    releaseProcessingScopeEditLock:async()=>{},
  };
  const mounted=scopeDialog(api);
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.acquireEditor'));
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.refresh'));
  mounted.dispose();
  server={...server,edit_lock:{state:'available'}};
  const reopened=scopeDialog(api,true,{}, {},{enterEditor:false});
  reopened.render();await setImmediate();reopened.render();await setImmediate();reopened.render();
  assert.equal(reopened.find(n=>n.type==='button'&&n.props.children==='scope.edit').props.disabled,false);
  assert.equal(reopened.find(n=>n.props?.label==='table.xlsx').props.disabled,true,'Reopening starts with viewing');
  reopened.dispose();
});

for(const action of ['confirm','preview','expand'])test(`lease loss during deferred save fences the following ${action} step`,async context=>{
  const server={...scopeState(),capabilities:{edit_lock:true,fast_exclusion:true},edit_lock:{state:'available'}};
  if(action==='preview')server.active={...structuredClone(server.pending),summary:{generation_id:'base'}};
  let beat,resolveSave;const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    acquireProcessingScopeEditLock:async()=>({state:'editing',owner:'Alice'}),
    releaseProcessingScopeEditLock:async()=>{},
    renewProcessingScopeEditLock:async()=>{throw new Error('connection lost');},
    saveProcessingScopeReview:async()=>{calls.push('save');return new Promise(resolve=>{resolveSave=resolve;});},
    confirmProcessingScope:async()=>calls.push('confirm'),
    previewProcessingScopeChange:async()=>calls.push('preview'),
    createProcessingScopeReview:async()=>{calls.push('expand');return server;},
  },true,{setTimeout:(fn,delay)=>{if(delay===60000)beat=fn;return 1;},clearTimeout:()=>{}});
  context.after(()=>mounted.dispose());
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:true}});mounted.render();
  const key=action==='expand'?'scope.expand':action==='preview'?'scope.checkChanges':'scope.confirm';
  const submitting=mounted.find(n=>n.type==='button'&&n.props.children===key).props.onClick();
  await beat();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,true);
  resolveSave({...server,pending:{...server.pending,revision:2,decisions:[{source_key:'excel',effective_included:true}]}});
  await submitting;mounted.render();
  assert.deepEqual(calls,['save'],'An old response cannot start another operation after editing was fenced');
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,false,'An old response cannot replace the view');
});

test('idle editor warns, continues on interaction, then closes both dialogs and releases without applying',async()=>{
  let time=0,nextId=0,closed=0,serverIdle=600000,lastActivity=0;const tasks=new Map(),renewals=[],released=[];
  const server={...scopeState(),capabilities:{edit_lock:true},edit_lock:{state:'available'}};
  const globals={Date:class extends Date {static now(){return time;}},
    setTimeout:(fn,delay)=>{tasks.set(++nextId,{fn,at:time+delay});return nextId;},clearTimeout:id=>tasks.delete(id),
    setInterval:()=>1,clearInterval:()=>{}};
  async function advance(ms){const target=time+ms;let job;
    while((job=[...tasks].filter(([,j])=>j.at<=target).sort((a,b)=>a[1].at-b[1].at)[0])){
      time=job[1].at;tasks.delete(job[0]);job[1].fn();await setImmediate();
    }time=target;}
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    acquireProcessingScopeEditLock:async()=>({state:'editing',owner:'Alice',idle_remaining_seconds:600}),
    renewProcessingScopeEditLock:async(_id,body)=>{renewals.push(body);
      if(body.activity_sequence>lastActivity){lastActivity=body.activity_sequence;serverIdle=time+600000;}
      return {state:'editing',idle_remaining_seconds:Math.max(0,(serverIdle-time)/1000)};},
    releaseProcessingScopeEditLock:async()=>{released.push(true);},
    confirmProcessingScope:async()=>{assert.fail('Idle expiry cannot apply choices');},
  },true,globals,{onClose:()=>{closed++;}});
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  mounted.find(n=>n.type==='button'&&n.props.children==='scope.preview').props.onClick();mounted.render();
  await advance(9*60000);mounted.render();
  assert(mounted.find(n=>n.props?.reviewId==='review').props.sessionNotice,'The topmost Excel preview must show the idle warning');
  const notice=mounted.find(n=>n.props?.expiryAction==='close');
  assert.equal(notice.props.remainingSeconds,60);
  await notice.props.onContinue();
  await setImmediate();mounted.render();
  assert.equal(mounted.find(n=>n.props?.expiryAction==='close').props.remainingSeconds,null);
  assert(renewals.at(-1).activity_sequence>0);
  await advance(10*60000);await setImmediate();mounted.render();
  assert.equal(closed,1);assert(released.length>0);
  assert(!mounted.find(n=>n.props?.reviewId==='review'));
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,true);
  mounted.dispose();
});

test('activity during an in-flight heartbeat is reported immediately after its response',async()=>{
  let time=0,beat,resolveFirst;const calls=[];
  const first=new Promise(resolve=>{resolveFirst=resolve;});
  const server={...scopeState(),capabilities:{edit_lock:true},edit_lock:{state:'available'}};
  const globals={Date:class extends Date {static now(){return time;}},
    setTimeout:(fn,delay)=>{if(delay===60000)beat=fn;return 1;},clearTimeout:()=>{}};
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    acquireProcessingScopeEditLock:async()=>({state:'editing',idle_remaining_seconds:600}),
    renewProcessingScopeEditLock:(_id,body)=>{calls.push(body);return calls.length===1?first:Promise.resolve({state:'editing',idle_remaining_seconds:600});},
    releaseProcessingScopeEditLock:async()=>{},
  },true,globals);
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  time=60000;beat();
  mounted.find(n=>n.props?.['data-scope-editor-session']!==undefined).props.onPointerDownCapture({isTrusted:true});
  resolveFirst({state:'editing',idle_remaining_seconds:60});await setImmediate();
  assert.equal(calls.length,2);assert.equal(calls[0].activity_sequence,0);assert.equal(calls[1].activity_sequence,1);
  mounted.dispose();
});

test('published changes are previewed and applied without full regeneration',async()=>{
  const server={...scopeState(),active:{summary:{generation_id:'base'}},capabilities:{fast_exclusion:true,cached_restore:true}};
  const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    previewProcessingScopeChange:async()=>{calls.push('preview');return {plan_id:'plan',mode:'fast_exclude',counts:{removed:{sources:1,chunks:2,concepts:3}}};},
    applyProcessingScopeChange:async()=>{calls.push('apply');return {state:'admitted'};},
    confirmProcessingScope:async()=>{throw new Error('Full regeneration is forbidden');},
  });
  mounted.render();await setImmediate();mounted.render();
  const analyze=mounted.find(n=>n.type==='button'&&n.props.children==='scope.checkChanges');
  assert(analyze,'Published scope must offer an analysis action');
  await analyze.props.onClick();mounted.render();
  await mounted.find(n=>n.type==='button'&&n.props.children==='scope.applyExclusions').props.onClick();
  assert.deepEqual(calls,['preview','apply']);
  mounted.dispose();
});

for(const conflict of [false,true])test(`expand preserves dirty preview choice; save conflict=${conflict}`,async()=>{
  let server=scopeState();const calls=[];
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    saveProcessingScopeReview:async(_id,body)=>{calls.push(['save',body]);
      if(conflict)throw new Error('conflict');
      server={...server,pending:{...server.pending,revision:2,decisions:body.selected_source_keys.map(source_key=>({source_key,effective_included:true}))}};
      return server;},
    createProcessingScopeReview:async(_id,body)=>{calls.push(['expand',body]);return server;},
  });
  mounted.render();await setImmediate();mounted.render();
  mounted.find(n=>n.type==='button'&&n.props.children==='scope.preview').props.onClick();mounted.render();
  mounted.find(n=>n.props?.reviewId==='review').props.onToggle();mounted.render();
  await mounted.find(n=>n.type==='button'&&n.props.children==='scope.expand').props.onClick();mounted.render();
  assert.equal(calls[0]?.[0],'save');
  assert.deepEqual([...calls[0][1].selected_source_keys],['excel']);
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,true);
  assert.equal(calls.length,conflict?1:2);
  if(!conflict)assert.equal(calls[1][1].expected_revision,2);
  mounted.dispose();
});

test('refresh from obsolete preview retains local changes for stable source keys',async()=>{
  let server=scopeState();const mounted=scopeDialog({getProcessingScope:async()=>server});
  mounted.render();await setImmediate();mounted.render();
  mounted.find(n=>n.type==='button'&&n.props.children==='scope.preview').props.onClick();mounted.render();
  mounted.find(n=>n.props?.reviewId==='review').props.onToggle();mounted.render();
  server={...server,pending:{...server.pending,id:'replacement',revision:2}};
  const preview=mounted.find(n=>n.props?.reviewId==='review');
  assert.equal(typeof preview.props.onReviewChanged,'function');
  await preview.props.onReviewChanged();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,true);
  mounted.dispose();
});

for(const status of [409,0])test(`confirm preserves dirty selection when saving fails (HTTP ${status})`,async()=>{
  let confirmations=0;
  const server=scopeState();const mounted=scopeDialog({getProcessingScope:async()=>server,
    saveProcessingScopeReview:async()=>{throw Object.assign(new Error('Save failed'),{status});},
    confirmProcessingScope:async()=>{confirmations++;},
  });
  mounted.render();await setImmediate();mounted.render();
  mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:true}});mounted.render();
  await mounted.find(n=>n.type==='button'&&n.props.children==='scope.confirm').props.onClick();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.checked,true);
  assert.equal(confirmations,0);
  mounted.dispose();
});

test('reader can preview excluded XLSX but cannot change or confirm its selection',async()=>{
  const server=scopeState();const mounted=scopeDialog({getProcessingScope:async()=>server},false);
  mounted.render();await setImmediate();mounted.render();
  assert.equal(mounted.find(n=>n.props?.label==='table.xlsx').props.disabled,true);
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.save'));
  assert(!mounted.find(n=>n.type==='button'&&n.props.children==='scope.confirm'));
  mounted.find(n=>n.type==='button'&&n.props.children==='scope.preview').props.onClick();mounted.render();
  assert.equal(mounted.find(n=>n.props?.reviewId==='review').props.onToggle,null);
  mounted.dispose();
});

test('waiting review is uploaded successfully and does not hold the next file',async()=>{
  const states=[];
  const result=await runDocumentUploadBatch([{name:'heavy.docx'},{name:'next.pdf'}],{
    uploadOne:async file=>({id:file.name,status:file.name==='heavy.docx'?'awaiting_attachments':'queued'}),
    onItemState:(file,state)=>states.push([file.name,state]),
  });
  assert.equal(result.uploadedCount,2);
  assert(states.some(([name,state])=>name==='heavy.docx' && state==='awaiting-attachments'));
  assert(states.some(([name,state])=>name==='next.pdf' && state==='uploaded'));
  assert.equal(result.failed.length,0);
});


for(const failure of ['heartbeat','save'])test(`accepted operation stays applying after ${failure} reports it`,async context=>{
  const server={...scopeState(),capabilities:{edit_lock:true},edit_lock:{state:'available'}};
  let beat,cancelled=0;
  const busy=async()=>{throw Object.assign(new Error('Applying'),{code:'scope_change_in_progress'});};
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    acquireProcessingScopeEditLock:async()=>({state:'editing',owner:'Alice'}),
    renewProcessingScopeEditLock:busy,releaseProcessingScopeEditLock:async()=>{},
    saveProcessingScopeReview:busy,cancelProcessingScopeReview:async()=>{cancelled++;},
  },true,{setTimeout:(fn,delay)=>{if(delay===60000)beat=fn;return 1;},clearTimeout:()=>{}});
  context.after(()=>mounted.dispose());
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  if(failure==='heartbeat'){await beat();await setImmediate();}
  else{
    mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:true}});mounted.render();
    await mounted.find(n=>n.type==='button'&&n.props.children==='scope.save').props.onClick();
  }
  mounted.render();
  assert.equal(mounted.find(n=>n.props?.expiryAction==='close').props.state,'applying');
  assert.equal(cancelled,0);
});

for(const action of ['confirm','preview','expand'])test(`replaced lease session fences deferred ${action} continuation`,async context=>{
  let allowed=true,resolveSave,acquired=0;const calls=[];
  const server={...scopeState(),capabilities:{edit_lock:true,fast_exclusion:true},edit_lock:{state:'available'}};
  if(action==='preview')server.active={...structuredClone(server.pending),summary:{generation_id:'base'}};
  const mounted=scopeDialog({getProcessingScope:async()=>server,
    acquireProcessingScopeEditLock:async()=>{acquired++;return {state:'editing',owner:'Alice'};},
    releaseProcessingScopeEditLock:async()=>{},
    saveProcessingScopeReview:async()=>{calls.push('save');return new Promise(resolve=>{resolveSave=resolve;});},
    confirmProcessingScope:async()=>calls.push('confirm'),previewProcessingScopeChange:async()=>calls.push('preview'),
    createProcessingScopeReview:async()=>{calls.push('expand');return server;},
  },()=>allowed);
  context.after(()=>mounted.dispose());
  mounted.render();await setImmediate();mounted.render();await setImmediate();mounted.render();
  mounted.find(n=>n.props?.label==='table.xlsx').props.onChange({target:{checked:true}});mounted.render();
  const key=action==='expand'?'scope.expand':action==='preview'?'scope.checkChanges':'scope.confirm';
  const pending=mounted.find(n=>n.type==='button'&&n.props.children===key).props.onClick();
  allowed=false;mounted.render();allowed=true;mounted.render();await setImmediate();mounted.render();
  assert.equal(acquired,2);
  resolveSave({...server,pending:{...server.pending,revision:2}});await pending;mounted.render();
  assert.deepEqual(calls,['save']);
});
