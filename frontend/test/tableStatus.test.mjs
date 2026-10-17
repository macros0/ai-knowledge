import test from 'node:test';
import assert from 'node:assert/strict';
import {tableStatusKey} from '../src/lib/tableSourceStatus.mjs';
test('coverage, header correctness and fallback remain separate', () => {
 assert.equal(tableStatusKey(null), 'tables.coverageUnknown');
 assert.equal(tableStatusKey({complete:true,header_status:'ambiguous'}),'tables.headerAmbiguous');
 assert.equal(tableStatusKey({complete:true,header_status:'verified',classifier_cause:'classifier_timeout'}),'tables.fallbackVerified');
 assert.equal(tableStatusKey({complete:false,cause_code:'content_omitted'}),'tables.contentOmitted');
});

test('row pagination keeps continuations for previously loaded cells', async () => {
 const {mergeTablePages}=await import('../src/lib/tableSourceStatus.mjs');
 const old={rows:[{row_number:1}],columns:[{index:1}],continuation:[{row_number:1,column:1,next_text_start:4000}],next_row:11};
 const next={rows:[{row_number:11}],columns:[{index:1}],continuation:[],next_row:21};
 assert.deepEqual(mergeTablePages(old,next).continuation,old.continuation);
 assert.equal(mergeTablePages(old,next).rows.length,2);
 assert.equal(mergeTablePages(old,next).next_row,21);
});
