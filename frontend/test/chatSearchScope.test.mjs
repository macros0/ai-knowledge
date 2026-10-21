import assert from 'node:assert/strict';
import test from 'node:test';

test('removing the last document or clearing the scope switches new searches to the whole database', async () => {
  const { reduceSearchScope, scopeRequestIds } = await import('../src/lib/chatSearchScope.mjs');
  assert.equal(typeof reduceSearchScope, 'function');
  const original = { documents: [{doc_id:'a'}, {doc_id:'b'}], enabled:true };
  const partial = reduceSearchScope(original, {type:'documents', value:items => items.filter(item => item.doc_id !== 'a')});
  assert.deepEqual(partial, {documents:[{doc_id:'b'}], enabled:true});
  const empty = reduceSearchScope(partial, {type:'documents', value:items => items.filter(item => item.doc_id !== 'b')});
  assert.deepEqual(empty, {documents:[], enabled:false});
  assert.equal(scopeRequestIds(empty.documents, empty.enabled), null);
  assert.deepEqual(reduceSearchScope(original, {type:'documents', value:[]}), {documents:[], enabled:false});
  assert.equal(original.documents.length, 2);
});

test('scope mode changes preserve documents and cannot enable an empty selection', async () => {
  const { reduceSearchScope } = await import('../src/lib/chatSearchScope.mjs');
  assert.equal(typeof reduceSearchScope, 'function');
  const empty = {documents:[], enabled:false};
  assert.deepEqual(reduceSearchScope(empty, {type:'enabled', value:true}), empty);
  const populated = reduceSearchScope(empty, {type:'documents', value:[{doc_id:'a'}]});
  assert.equal(populated.enabled, false);
  const active = reduceSearchScope(populated, {type:'enabled', value:true});
  assert.equal(active.enabled, true);
  assert.deepEqual(reduceSearchScope(active, {type:'enabled', value:false}), populated);
});

test('scope accumulates whole documents from selections across answers without duplicates', async () => {
  const { addScopeDocuments, scopeRequestIds } = await import('../src/lib/chatSearchScope.mjs');
  const first = addScopeDocuments([], [
    { doc_id: 'a', filename: 'A.docx', source_index: 1 },
    { doc_id: 'b', filename: 'B.docx', source_index: 2 },
    { doc_id: 'a', filename: 'A.docx', source_index: 3 },
  ], [1, 3]);
  assert.deepEqual(first, [{ doc_id: 'a', filename: 'A.docx' }]);
  const next = addScopeDocuments(first, [
    { doc_id: 'a', filename: 'A.docx', source_index: 1 },
    { doc_id: 'c', filename: 'C.pdf', source_index: 2 },
  ], [1, 2]);
  assert.deepEqual(next, [{ doc_id: 'a', filename: 'A.docx' }, { doc_id: 'c', filename: 'C.pdf' }]);
  assert.deepEqual(first, [{ doc_id: 'a', filename: 'A.docx' }]);
  assert.deepEqual(scopeRequestIds(next, true), ['a', 'c']);
  assert.equal(scopeRequestIds(next, false), null);
  assert.deepEqual(scopeRequestIds([], true), []);
});

test('scope capacity rejects additions without truncating the existing or new documents', async () => {
  const { addScopeDocuments, SEARCH_SCOPE_MAX_DOCUMENTS } = await import('../src/lib/chatSearchScope.mjs');
  const existing = Array.from({ length: SEARCH_SCOPE_MAX_DOCUMENTS }, (_, i) => ({ doc_id: String(i), filename: 'doc' }));
  assert.throws(() => addScopeDocuments(existing, [{ doc_id: 'new', filename: 'new', source_index: 1 }], [1]), RangeError);
  assert.equal(existing.length, SEARCH_SCOPE_MAX_DOCUMENTS);
});

test('history adds available current documents independently of saved fragment selectability', async () => {
  const { addAvailableScopeDocuments } = await import('../src/lib/chatSearchScope.mjs');
  assert.equal(typeof addAvailableScopeDocuments, 'function');
  const existing = [{ doc_id: 'existing', filename: 'Existing.docx' }];
  const next = addAvailableScopeDocuments(existing, [
    { doc_id: 'updated', filename: 'Current.docx', available: true },
    { doc_id: 'existing', filename: 'Existing.docx', available: true },
    { doc_id: 'deleted', filename: null, available: false },
  ], ['updated', 'existing', 'deleted', 'unknown']);
  assert.deepEqual(next, [...existing, { doc_id: 'updated', filename: 'Current.docx' }]);
  assert.deepEqual(existing, [{ doc_id: 'existing', filename: 'Existing.docx' }]);
});

test('history scope additions enforce capacity atomically', async () => {
  const { addAvailableScopeDocuments, SEARCH_SCOPE_MAX_DOCUMENTS } = await import('../src/lib/chatSearchScope.mjs');
  assert.equal(typeof addAvailableScopeDocuments, 'function');
  const existing = Array.from({ length: SEARCH_SCOPE_MAX_DOCUMENTS }, (_, i) => ({ doc_id: String(i), filename: 'doc' }));
  assert.throws(() => addAvailableScopeDocuments(existing,
    [{ doc_id: 'new', filename: 'Current.docx', available: true }], ['new']), RangeError);
  assert.equal(existing.length, SEARCH_SCOPE_MAX_DOCUMENTS);
});

test('chat API preserves an enabled empty scope and sends document ids independently of fragments', async () => {
  const { chat } = await import('../src/lib/api.js');
  const original = globalThis.fetch;
  const bodies = [];
  globalThis.fetch = async (_url, options) => {
    bodies.push(JSON.parse(options.body));
    return new Response(JSON.stringify({ answer: 'ready', sources: [] }), { headers: { 'content-type': 'application/json' } });
  };
  try {
    await chat('question', [], 5, 'hybrid', null, '', true, 'all', null, null, { searchDocIds: [] });
    await chat('question', [], 5, 'hybrid', null, '', true, 'all', null, null, { searchDocIds: ['a'] });
    await chat('question');
    assert.deepEqual(bodies[0].search_doc_ids, []);
    assert.deepEqual(bodies[1].search_doc_ids, ['a']);
    assert.equal('search_doc_ids' in bodies[2], false);
    assert.equal('source_selection' in bodies[1], false);
  } finally { globalThis.fetch = original; }
});
