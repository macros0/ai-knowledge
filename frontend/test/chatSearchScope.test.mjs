import assert from 'node:assert/strict';
import test from 'node:test';

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
