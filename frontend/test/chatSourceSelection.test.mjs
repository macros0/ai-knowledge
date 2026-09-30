import assert from 'node:assert/strict';
import test from 'node:test';

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
