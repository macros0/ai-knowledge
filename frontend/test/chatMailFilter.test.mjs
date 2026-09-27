import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chat } from '../src/lib/api.js';
import { retryMailMode } from '../src/lib/chatMailFilter.mjs';

for (const mode of ['all', 'exclude', 'only']) {
  test(`chat always serializes ${mode}`, async () => {
    const original = globalThis.fetch;
    let request;
    globalThis.fetch = async (_url, init) => {
      request = JSON.parse(init.body);
      return { ok: true, json: async () => ({}) };
    };
    try {
      await chat('topic', [], 5, 'hybrid', null, '', true, mode);
      assert.equal(request.mail_mode, mode);
    } finally { globalThis.fetch = original; }
  });
}

test('old chat callers and retry messages default all', async () => {
  const original = globalThis.fetch;
  let request;
  globalThis.fetch = async (_url, init) => {
    request = JSON.parse(init.body);
    return { ok: true, json: async () => ({}) };
  };
  try {
    await chat('topic');
    assert.equal(request.mail_mode, 'all');
    assert.equal(retryMailMode({}), 'all');
    assert.equal(retryMailMode({requestMailMode: 'only'}), 'only');
  } finally { globalThis.fetch = original; }
});

test('pending request and retry retain snapshots while subsequent requests use new mode', async () => {
  const original = globalThis.fetch;
  const requests = [];
  let finish;
  globalThis.fetch = async (_url, init) => {
    requests.push(JSON.parse(init.body));
    if (requests.length === 1) await new Promise(resolve => { finish = resolve; });
    return { ok: true, json: async () => ({}) };
  };
  try {
    let selectedMode = 'exclude';
    const message = {requestMailMode: selectedMode};
    const pending = chat('topic', [], 5, 'hybrid', null, '', true, selectedMode);
    selectedMode = 'only';
    assert.equal(requests[0].mail_mode, 'exclude');
    finish();
    await pending;
    await chat('topic', [], 5, 'hybrid', null, '', false, retryMailMode(message));
    await chat('next', [], 5, 'hybrid', null, '', true, selectedMode);
    assert.deepEqual(requests.map(r => r.mail_mode), ['exclude', 'exclude', 'only']);
    assert.equal(requests[1].use_glossary, false);
  } finally { globalThis.fetch = original; }
});
