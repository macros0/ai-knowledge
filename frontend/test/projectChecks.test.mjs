import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { auditSnapshot, installHooks, outgoingRefs } from '../../scripts/check-project.mjs';

test('push checks each sent commit once and skips branch deletion', () => {
  const sha = 'a'.repeat(40);
  const zero = '0'.repeat(40);
  assert.deepEqual(outgoingRefs(`refs/heads/main ${sha} refs/heads/main ${zero}\nrefs/heads/other ${sha} refs/heads/other ${zero}\n(delete) ${zero} refs/heads/old ${sha}`), [sha]);
  assert.deepEqual(outgoingRefs(''), []);
  assert.throws(() => outgoingRefs('refs/heads/main --help refs/heads/main invalid'), /Invalid push/);
});

function repository() {
  const root = mkdtempSync(join(tmpdir(), 'project-checks-test-'));
  const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim();
  git('init', '--quiet');
  git('config', 'user.name', 'Checks test');
  git('config', 'user.email', 'checks@example.test');
  mkdirSync(join(root, 'frontend'));
  writeFileSync(join(root, 'frontend/package.json'), '{"name":"test","private":true}');
  writeFileSync(join(root, 'frontend/package-lock.json'), '{"marker":"committed"}');
  git('add', '--', 'frontend');
  git('commit', '--quiet', '-m', 'fixture');
  return { root, git };
}

test('audit uses sent commit even when working lock file has different contents', () => {
  const { root, git } = repository();
  let snapshot;
  try {
    writeFileSync(join(root, 'frontend/package-lock.json'), '{"marker":"dirty"}');
    auditSnapshot(root, git('rev-parse', 'HEAD'), (directory) => {
      snapshot = directory;
      assert.equal(JSON.parse(readFileSync(join(directory, 'package-lock.json'))).marker, 'committed');
      assert.equal(JSON.parse(readFileSync(join(directory, 'package.json'))).name, 'test');
    });
    assert.throws(() => readFileSync(join(snapshot, 'package-lock.json')), { code: 'ENOENT' });
    assert.equal(JSON.parse(readFileSync(join(root, 'frontend/package-lock.json'))).marker, 'dirty');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('failed audit blocks push and removes only its temporary snapshot', () => {
  const { root, git } = repository();
  let snapshot;
  try {
    assert.throws(() => auditSnapshot(root, git('rev-parse', 'HEAD'), (directory) => {
      snapshot = directory;
      throw new Error('audit rejected dependency');
    }), /audit rejected dependency/);
    assert.throws(() => readFileSync(join(snapshot, 'package-lock.json')), { code: 'ENOENT' });
    assert.equal(JSON.parse(readFileSync(join(root, 'frontend/package-lock.json'))).marker, 'committed');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('hook setup is local and preserves an existing hook configuration', () => {
  const { root, git } = repository();
  try {
    git('config', '--local', 'core.hooksPath', '.custom-hooks');
    assert.throws(() => installHooks(root), /Existing hooks/);
    assert.equal(git('config', '--local', '--get', 'core.hooksPath'), '.custom-hooks');
    git('config', '--local', '--unset', 'core.hooksPath');
    installHooks(root);
    installHooks(root);
    assert.equal(git('config', '--local', '--get', 'core.hooksPath'), '.githooks');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('hook setup preserves active hooks in the default Git hooks directory', () => {
  const { root, git } = repository();
  try {
    const hook = join(root, '.git/hooks/pre-commit');
    writeFileSync(hook, '#!/bin/sh\nexit 1\n');
    assert.throws(() => installHooks(root), /Existing default hooks/);
    assert.throws(() => git('config', '--local', '--get', 'core.hooksPath'));
    assert.equal(readFileSync(hook, 'utf8'), '#!/bin/sh\nexit 1\n');
  } finally { rmSync(root, { recursive: true, force: true }); }
});
