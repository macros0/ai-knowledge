import { execFileSync, spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function git(root, ...args) {
  return execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
}

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const error = new Error(`Check failed (exit ${result.status ?? result.signal}).`);
    error.status = result.status;
    throw error;
  }
}

function npmCli() {
  const nodeDirectory = dirname(process.execPath);
  const candidates = [process.env.NPM_CLI_PATH, process.env.npm_execpath,
    join(nodeDirectory, 'node_modules/npm/bin/npm-cli.js'),
    resolve(nodeDirectory, '../lib/node_modules/npm/bin/npm-cli.js'),
    '/usr/share/nodejs/npm/bin/npm-cli.js'];
  const cli = candidates.find((path) => path && existsSync(path));
  if (!cli) throw new Error('npm CLI not found. Install Node.js with npm or set NPM_CLI_PATH to npm-cli.js.');
  return cli;
}

function audit(directory) {
  run(process.execPath, [npmCli(), 'audit', '--audit-level=high',
    '--include=prod', '--include=dev', '--include=optional', '--include=peer'], directory);
}

export function outgoingRefs(input) {
  const refs = new Set();
  for (const line of input.trim().split('\n').filter(Boolean)) {
    const fields = line.trim().split(/\s+/);
    if (fields.length !== 4 || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i.test(fields[1])) {
      throw new Error('Invalid push reference input.');
    }
    if (!/^0+$/.test(fields[1])) refs.add(fields[1]);
  }
  return [...refs];
}

export function auditSnapshot(root, ref, check = audit) {
  if (!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i.test(ref)) throw new Error('Expected a commit SHA.');
  const temporaryRoot = resolve(tmpdir());
  const snapshot = mkdtempSync(join(temporaryRoot, 'pre-push-audit-'));
  try {
    for (const name of ['package.json', 'package-lock.json']) {
      writeFileSync(join(snapshot, name), git(root, 'show', `${ref}:frontend/${name}`));
    }
    const config = spawnSync('git', ['show', `${ref}:frontend/.npmrc`], { cwd: root, encoding: 'utf8' });
    if (config.status === 0) writeFileSync(join(snapshot, '.npmrc'), config.stdout);
    check(snapshot);
  } finally {
    if (dirname(snapshot) !== temporaryRoot) throw new Error('Unsafe temporary directory.');
    rmSync(snapshot, { recursive: true, force: true });
  }
}

export function installHooks(root) {
  const existing = spawnSync('git', ['config', '--get', 'core.hooksPath'], { cwd: root, encoding: 'utf8' });
  if (existing.status !== 0 && existing.status !== 1) throw new Error('Cannot read Git hook configuration.');
  const path = existing.stdout?.trim();
  if (path && path !== '.githooks') throw new Error(`Existing hooks (${path}) preserved; integrate pre-push manually.`);
  if (!path) {
    const defaultDirectory = resolve(root, git(root, 'rev-parse', '--git-path', 'hooks'));
    if (existsSync(defaultDirectory) && readdirSync(defaultDirectory).some((name) => !name.endsWith('.sample'))) {
      throw new Error('Existing default hooks preserved; integrate pre-push manually.');
    }
  }
  const hook = join(root, '.githooks/pre-push');
  if (existsSync(hook)) chmodSync(hook, 0o755);
  git(root, 'config', '--local', 'core.hooksPath', '.githooks');
}

function checkProject(root) {
  const frontend = join(root, 'frontend');
  console.log('[check] npm audit: package-lock.json');
  audit(frontend);
  console.log('[check] frontend: ESLint');
  run(process.execPath, ['node_modules/eslint/bin/eslint.js', '.'], frontend);
  console.log('[check] frontend: tests and locale manifest consistency');
  run(process.execPath, ['--test'], frontend);
  console.log('[check] backend: Ruff');
  const python = join(root, 'backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  run(existsSync(python) ? python : (process.env.PYTHON ?? (process.platform === 'win32' ? 'python' : 'python3')),
    ['-m', 'ruff', 'check', '--no-cache', '.'], join(root, 'backend'));
  console.log('[check] Passed. Full backend tests and build remain separate CI gates.');
}

function main(args) {
  if (args.length === 1 && args[0] === '--install-hooks') {
    installHooks(projectRoot);
    console.log('[check] Local pre-push hook enabled.');
  } else if (args.length === 1 && args[0] === '--pre-push') {
    for (const ref of outgoingRefs(readFileSync(0, 'utf8'))) {
      console.log(`[pre-push] Audit sent commit ${ref.slice(0, 12)}`);
      auditSnapshot(projectRoot, ref);
    }
  } else if (args.length === 2 && args[0] === '--audit-ref') {
    auditSnapshot(projectRoot, args[1]);
  } else if (args.length === 0) {
    checkProject(projectRoot);
  } else {
    throw new Error('Usage: node scripts/check-project.mjs [--install-hooks | --pre-push | --audit-ref SHA]');
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(process.argv.slice(2)); }
  catch (error) {
    console.error(`[check] ${error.message}`);
    process.exitCode = error.status > 0 ? error.status : 1;
  }
}
