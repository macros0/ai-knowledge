import test from 'node:test';
import assert from 'node:assert/strict';
import {guardSourceLocation} from '../src/lib/sourceLocationGuard.mjs';

const location = {status:'exact', basis:'match', chunk_index:53, source_id:'attachment-a',
  generation_id:'old', chunk_hash:'old-hash', spans:[{start:2,end:7}]};
const target = {chunk_index:53, source_id:'attachment-a', generation_id:'old', chunk_hash:'old-hash'};

test('a different displayed generation cannot inherit exact offsets even when text is identical', () => {
  const safe = guardSourceLocation(location, [{...target, generation_id:'new'}]);
  assert.equal(safe.status, 'chunk');
  assert.equal(safe.reason, 'changed');
  assert.deepEqual(safe.spans, []);
});

test('a reused chunk number in another attachment is unavailable', () => {
  const safe = guardSourceLocation(location, [{...target, source_id:'attachment-b'}]);
  assert.equal(safe.status, 'unavailable');
  assert.equal(safe.chunk_index, null);
});

test('only the validated canonical hash receives the exact spans', () => {
  assert.equal(guardSourceLocation(location, [target]), location);
  assert.deepEqual(guardSourceLocation(location, [{...target, chunk_hash:'new-hash'}]).spans, []);
});
