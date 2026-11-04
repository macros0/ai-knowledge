"""A lost frontend side effect is observed, never submitted a second time."""
import copy
import importlib
import json
from pathlib import Path

import pytest


STAGES = ('preflight', 'session', 'upload', 'publish', 'export', 'export-observe',
          'stream', 'loss', 'loss-observe', 'replacement', 'logout', 'final')


def module():
    path = Path(__file__).resolve().parents[1] / 'test_scripts/replica_frontend_run.py'
    assert path.is_file(), 'Durable frontend run is missing'
    return importlib.import_module('test_scripts.replica_frontend_run')


class Adapter:
    def __init__(self, private):
        self.private = private
        self.binding = {'namespace_uid': 'owned', 'configuration_sha256': 'b' * 64}
        self.calls = []
        self.fail = False
        self.receipt = None
        self.pending = False

    def bind(self):
        return self.binding

    def before(self, stage, state):
        assert state['binding_sha256'] == module().digest(self.binding)

    def execute(self, stage, state):
        # Verify intent is on disk BEFORE a login, upload or DELETE can happen.
        assert json.loads((self.private / 'run.json').read_bytes())['active'] == state['active']
        self.calls.append(stage)
        self.receipt = {k: state[k] for k in ('execution_id', 'code_sha256', 'binding_sha256')}
        self.receipt.update(version=1, stage=stage, intent_id=state['active']['intent_id'],
                            status='pending' if self.pending else 'passed', evidence={'observed': True})
        if self.fail:
            raise TimeoutError('PRIVATE_COOKIE_CANARY')
        return copy.deepcopy(self.receipt)

    def observe(self, stage, state):
        return copy.deepcopy(self.receipt)


def run(tmp_path):
    tool = module()
    adapter = Adapter(tmp_path)
    current = tool.FrontendRun(tmp_path / 'run.json', adapter, code_sha256='a' * 64)
    return tool, adapter, current


def test_complete_run_has_all_stages_and_no_production_qualification(tmp_path):
    tool, adapter, current = run(tmp_path)
    assert tuple(tool.STAGES) == STAGES
    for stage in STAGES:
        result = current.execute(stage, apply=True)
        assert result['selected_case_passed'] is (stage == 'final')
        assert result['certification'] is None
    assert adapter.calls == list(STAGES)
    assert len(result['checks']) == len(STAGES)
    assert result['failed'] == result['skipped_required'] == 0
    assert result['production_schedule'] == 'disabled'


def test_transport_failure_is_adopted_from_same_native_receipt_without_replay(tmp_path):
    tool, adapter, current = run(tmp_path)
    current.execute('preflight')
    adapter.fail = True
    with pytest.raises(ValueError, match='uncertain'):
        current.execute('session', apply=True)
    intent = current.state['active']['intent_id']
    resumed = tool.FrontendRun(tmp_path / 'run.json', adapter, code_sha256='a' * 64)
    result = resumed.execute('session', observe=True)
    assert resumed.state['completed']['session']['intent_id'] == intent
    assert adapter.calls == ['preflight', 'session']
    assert result['failed'] == 0
    assert 'PRIVATE_COOKIE_CANARY' not in json.dumps(resumed.state) + json.dumps(result)


def test_unknown_mutation_cannot_be_replayed_even_with_apply(tmp_path):
    tool, adapter, current = run(tmp_path)
    current.execute('preflight')
    adapter.fail = True
    with pytest.raises(ValueError):
        current.execute('session', apply=True)
    adapter.receipt = None
    for _ in range(2):
        resumed = tool.FrontendRun(tmp_path / 'run.json', adapter, code_sha256='a' * 64)
        result = resumed.execute('session', apply=True)
        assert result['failed'] == 1 and not result['selected_case_passed']
    assert adapter.calls == ['preflight', 'session']


@pytest.mark.parametrize('field', ['execution_id', 'code_sha256', 'binding_sha256', 'intent_id', 'stage', 'version', 'status'])
def test_foreign_or_failed_native_receipt_never_advances(field, tmp_path):
    _, adapter, current = run(tmp_path)
    adapter.fail = True
    with pytest.raises(ValueError): current.execute('preflight')
    adapter.receipt[field] = 'foreign'
    with pytest.raises(ValueError): current.execute('preflight', observe=True)
    assert not current.state['completed'] and current.state['active'] is not None
    assert adapter.calls == ['preflight']


def test_pending_observation_keeps_same_intent_until_native_completion(tmp_path):
    _, adapter, current = run(tmp_path)
    adapter.pending = True
    result = current.execute('preflight')
    assert result['failed'] == 0 and result['skipped_required'] == len(STAGES)
    intent = current.state['active']['intent_id']
    current.execute('preflight', observe=True)
    adapter.receipt['status'] = 'passed'
    current.execute('preflight', observe=True)
    assert adapter.calls == ['preflight']
    assert current.state['completed']['preflight']['intent_id'] == intent


@pytest.mark.parametrize('stage', ['session', 'upload', 'loss', 'final', '../loss'])
def test_out_of_order_stage_has_no_external_action(stage, tmp_path):
    _, adapter, current = run(tmp_path)
    with pytest.raises(ValueError): current.execute(stage, apply=True)
    assert adapter.calls == [] and not (tmp_path / 'run.json').exists()


def test_apply_required_only_for_new_side_effect_and_observe_never_creates_one(tmp_path):
    _, adapter, current = run(tmp_path)
    with pytest.raises(ValueError): current.execute('preflight', observe=True)
    current.execute('preflight')
    with pytest.raises(ValueError): current.execute('session')
    with pytest.raises(ValueError): current.execute('session', observe=True, apply=True)
    assert adapter.calls == ['preflight']


@pytest.mark.parametrize('change', ['code', 'binding', 'missing_stage', 'false_receipt', 'foreign_active', 'write_marker', 'hardlink'])
def test_changed_or_aliased_journal_refused_before_external_action(change, tmp_path):
    tool, adapter, current = run(tmp_path)
    current.execute('preflight')
    path = tmp_path / 'run.json'
    state = json.loads(path.read_bytes())
    code = 'a' * 64
    if change == 'code': code = 'c' * 64
    elif change == 'binding': adapter.binding['namespace_uid'] = 'foreign'
    elif change == 'missing_stage': state['completed']['final'] = state['completed'].pop('preflight')
    elif change == 'false_receipt': state['completed']['preflight']['receipt_sha256'] = 'invalid'
    elif change == 'foreign_active': state['active'] = {'stage': 'loss', 'intent_id': 'f' * 32}
    elif change == 'write_marker': path.with_name(path.name + '.writing').touch()
    elif change == 'hardlink': (tmp_path / 'alias').hardlink_to(path)
    if change in {'missing_stage', 'false_receipt', 'foreign_active'}:
        path.write_text(json.dumps(state))
    with pytest.raises((ValueError, OSError)):
        tool.FrontendRun(path, adapter, code_sha256=code)
    assert adapter.calls == ['preflight']


def test_precheck_failure_does_not_claim_unknown_side_effect(tmp_path):
    _, adapter, current = run(tmp_path)
    def refused(*args): raise ValueError('PRIVATE_DSN_CANARY')
    adapter.before = refused
    with pytest.raises(ValueError) as failure: current.execute('preflight')
    assert 'PRIVATE_DSN_CANARY' not in str(failure.value)
    assert current.state['active'] is None and adapter.calls == []


def test_receipt_body_is_not_copied_into_parent_journal_or_public_report(tmp_path):
    _, adapter, current = run(tmp_path)
    original = adapter.execute
    def execute(*args):
        receipt = original(*args)
        receipt['evidence'] = {'cookie': 'PRIVATE_COOKIE_CANARY'}
        return receipt
    adapter.execute = execute
    result = current.execute('preflight')
    assert 'PRIVATE_COOKIE_CANARY' not in (tmp_path / 'run.json').read_text() + json.dumps(result)
    assert len(current.state['completed']['preflight']['receipt_sha256']) == 64


def test_stale_parent_instance_cannot_overwrite_newer_journal(tmp_path):
    tool, adapter, first = run(tmp_path)
    first.execute('preflight')
    second = tool.FrontendRun(tmp_path / 'run.json', adapter, code_sha256='a' * 64)
    first.execute('session', apply=True)
    with pytest.raises(ValueError): second.execute('session', apply=True)
    assert adapter.calls == ['preflight', 'session']


def test_duplicate_intent_across_stages_refused(tmp_path):
    tool, adapter, current = run(tmp_path)
    current.execute('preflight')
    current.execute('session', apply=True)
    state = json.loads((tmp_path / 'run.json').read_bytes())
    state['completed']['session']['intent_id'] = state['completed']['preflight']['intent_id']
    (tmp_path / 'run.json').write_text(json.dumps(state))
    with pytest.raises(ValueError): tool.FrontendRun(tmp_path / 'run.json', adapter, code_sha256='a' * 64)
    assert adapter.calls == ['preflight', 'session']
