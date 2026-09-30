import assert from 'node:assert/strict';
import test from 'node:test';

test('selection is available in every response mode only for saved fragments', async () => {
  const { selectableSources } = await import('../src/lib/chatSourceSelection.mjs');
  const sources = [
    { source_index: 1, selectable: true, in_model_context: true },
    { source_index: 2, selectable: true, in_model_context: false },
    { source_index: 3, selectable: false },
    { source_index: 4 },
  ];
  for (const responseMode of ['documents', 'fast', 'full']) {
    assert.deepEqual(selectableSources({ attemptId: 'saved', responseMode, sources }), sources.slice(0, 2));
  }
  assert.deepEqual(selectableSources({ responseMode: 'full', sources }), []);
  assert.deepEqual(selectableSources({ attemptId: 'legacy', responseMode: 'full', sources: [{ source_index: 1 }] }), []);
  assert.deepEqual(selectableSources({ attemptId: 'legacy', sources }), []);
});

test('document and fragment selection share indices and keep result order', async () => {
  const { updateSelection, selectionState } = await import('../src/lib/chatSourceSelection.mjs');
  const sources = [{ doc_id: 'a', source_index: 1 }, { doc_id: 'b', source_index: 2 }, { doc_id: 'a', source_index: 3 }];
  let selected = updateSelection([], [1, 3], true, sources);
  assert.deepEqual(selected, [1, 3]);
  assert.equal(selectionState(selected, [1, 3]), 'all');
  selected = updateSelection(selected, [3], false, sources);
  assert.equal(selectionState(selected, [1, 3]), 'some');
  assert.deepEqual(updateSelection(selected, [2, 99, 2], true, sources), [1, 2]);
  assert.equal(selectionState([], [1, 3]), 'none');
});
