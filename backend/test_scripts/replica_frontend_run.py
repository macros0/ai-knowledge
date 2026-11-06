"""Durable stages for an explicitly selected, owned frontend recipe.

This is an execution journal, not a signed qualification producer. Native
side effects have one intent each; an uncertain response permits observation
of that intent and never another submission of the same action.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/kubernetes'))
from replica_runtime_run import digest, read_json, write_json
from app.services.diagnostics.paths import assert_no_links

STAGES = ('preflight', 'session', 'upload', 'publish', 'export', 'export-observe',
          'stream', 'loss', 'loss-observe', 'replacement', 'logout', 'final')
READ_ONLY = frozenset({'preflight', 'publish', 'export-observe', 'loss-observe'})


def recipe_digest():
    files = [*Path(__file__).parent.glob('replica_frontend_*.py'),
             Path(__file__).parent / 'run_replica_acceptance.py',
             Path(__file__).parent / 'replica_runtime_acceptance.py',
             ROOT / 'scripts/kubernetes/replica_runtime_run.py',
             ROOT / 'backend/app/services/diagnostics/paths.py',
             ROOT / 'backend/app/deployment/replica_backup.py']
    return digest({p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in files})


def now():
    return datetime.now(timezone.utc).isoformat()


def hex_id(value, length):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def journal_guard(path):
    assert_no_links(path, path.parent)
    if path.exists():
        meta = path.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            raise ValueError('Frontend journal is aliased or not a regular file')


class FrontendRun:
    def __init__(self, path, adapter, *, code_sha256=None):
        self.path, self.adapter = Path(path).absolute(), adapter
        if self.path.parent != Path(adapter.private).resolve() or not self.path.parent.is_dir():
            raise ValueError('Frontend journal requires an existing protected directory')
        journal_guard(self.path)
        self.code = code_sha256 or recipe_digest()
        if not hex_id(self.code, 64): raise ValueError('Recipe digest required')
        self.state = read_json(self.path) if self.path.exists() else {
            'version': 1, 'execution_id': uuid4().hex, 'code_sha256': self.code,
            'binding_sha256': digest(adapter.bind()), 'created_at': now(), 'completed': {}, 'active': None}
        self.validate()
        journal_guard(self.path)
        if self.state['binding_sha256'] != digest(adapter.bind()):
            raise ValueError('Frontend input or installation binding changed')

    def validate(self):
        s = self.state
        if (not isinstance(s, dict) or type(s.get('version')) is not int or s['version'] != 1
                or s.get('code_sha256') != self.code or not hex_id(s.get('execution_id'), 32)
                or not hex_id(s.get('binding_sha256'), 64) or not isinstance(s.get('completed'), dict)):
            raise ValueError('Frontend journal version, code or identity differs')
        completed = s['completed']
        if len(completed) > len(STAGES) or set(completed) != set(STAGES[:len(completed)]):
            raise ValueError('Frontend history is incomplete or out of order')
        intents = []
        for row in completed.values():
            if (not isinstance(row, dict) or set(row) != {'intent_id', 'receipt_sha256', 'started_at', 'finished_at'}
                    or not hex_id(row.get('intent_id'), 32) or not hex_id(row.get('receipt_sha256'), 64)):
                raise ValueError('Frontend completion receipt differs')
            intents.append(row['intent_id'])
            for key in ('started_at', 'finished_at'): datetime.fromisoformat(row[key])
        active = s.get('active')
        if active is not None:
            if (not isinstance(active, dict) or len(completed) == len(STAGES)
                    or not {'stage', 'intent_id', 'started_at'} <= set(active)
                    or set(active) - {'stage', 'intent_id', 'started_at', 'interrupted'}
                    or active['stage'] != STAGES[len(completed)] or not hex_id(active['intent_id'], 32)
                    or ('interrupted' in active and type(active['interrupted']) is not bool)):
                raise ValueError('Foreign frontend intent')
            datetime.fromisoformat(active['started_at'])
            intents.append(active['intent_id'])
        if len(set(intents)) != len(intents): raise ValueError('Frontend intent was reused')

    def accept(self, stage, receipt):
        if receipt is None: return
        if (not isinstance(receipt, dict) or set(receipt) != {'version', 'execution_id', 'code_sha256',
                'binding_sha256', 'stage', 'intent_id', 'status', 'evidence'}
                or type(receipt['version']) is not int or receipt['version'] != 1
                or any(receipt[k] != self.state[k] for k in ('execution_id', 'code_sha256', 'binding_sha256'))
                or receipt['stage'] != stage or receipt['intent_id'] != self.state['active']['intent_id']
                or receipt['status'] not in {'passed', 'pending'} or not isinstance(receipt['evidence'], dict)):
            raise ValueError('Native frontend receipt does not match the current intent')
        if receipt['status'] == 'pending': return
        active = self.state['active']
        self.state['completed'][stage] = {'intent_id': active['intent_id'], 'receipt_sha256': digest(receipt),
            'started_at': active['started_at'], 'finished_at': now()}
        self.state['active'] = None

    def execute(self, stage, *, apply=False, observe=False):
        if stage not in STAGES: raise ValueError('Unknown frontend stage')
        lock = self.path.with_name(self.path.name + '.lock')
        assert_no_links(lock, self.path.parent)
        if lock.exists() and lock.stat().st_nlink != 1: raise ValueError('Aliased frontend lock')
        with FileLock(lock, timeout=5):
            journal_guard(self.path)
            if self.path.exists() and read_json(self.path) != self.state:
                raise ValueError('Frontend journal changed; reload the same run')
            self.validate()
            if stage in self.state['completed']: return self.report()
            if stage != STAGES[len(self.state['completed'])]:
                raise ValueError('Complete the preceding frontend stage first')
            active = self.state['active']
            if active is None:
                if observe: raise ValueError('No existing frontend intent to observe')
                if stage not in READ_ONLY and not apply: raise ValueError('Explicit frontend apply required')
            # Recheck input/transport before every dispatch, including observation.
            try:
                if digest(self.adapter.bind()) != self.state['binding_sha256']:
                    raise ValueError('Frontend binding changed')
                self.adapter.before(stage, self.state)
            except Exception:
                raise ValueError('Frontend precheck refused; no new intent was submitted') from None
            if active is None:
                self.state['active'] = {'stage': stage, 'intent_id': uuid4().hex, 'started_at': now()}
                write_json(self.path, self.state)
            try:
                receipt = (self.adapter.observe(stage, self.state) if active is not None
                           else self.adapter.execute(stage, self.state))
                self.accept(stage, receipt)
                write_json(self.path, self.state)
            except Exception:  # No private exception text or native receipt body in parent output.
                # A failed persistence after native completion must not be rewritten
                # as another intent: the .writing marker deliberately blocks it.
                if self.state['active'] is not None:
                    self.state['active']['interrupted'] = True
                    write_json(self.path, self.state)
                raise ValueError('Frontend outcome uncertain; observe the same intent') from None
        return self.report()

    def report(self):
        self.validate()
        completed, active = self.state['completed'], self.state['active']
        checks = [{'name': 'frontend.' + stage, 'status': 'passed' if stage in completed else 'pending',
                   **({'evidence_sha256': completed[stage]['receipt_sha256']} if stage in completed else {})}
                  for stage in STAGES]
        failed = int(active is not None and active.get('interrupted') is True)
        if failed: checks[len(completed)]['status'] = 'failed'
        return {'execution_id': self.state['execution_id'], 'runner_sha256': self.code,
            'binding_sha256': self.state['binding_sha256'], 'checks': checks,
            'selected_case_passed': len(completed) == len(STAGES) and active is None,
            'failed': failed, 'skipped_required': len(STAGES) - len(completed),
            'certification': None, 'production_schedule': 'disabled'}
