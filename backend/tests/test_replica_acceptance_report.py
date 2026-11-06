"""Qualification belongs to exact inputs and complete evidence, never a green subset."""
import copy
import importlib
import json
from pathlib import Path

import pytest


def module():
    name = 'test_scripts.replica_acceptance_report'
    assert importlib.util.find_spec(name) is not None, 'Profile-bound certification validator is missing'
    return importlib.import_module(name)


def binding():
    root = Path(__file__).resolve().parents[2]
    matrix = json.loads((root / 'deploy/kubernetes/replicas/support-matrix.json').read_bytes())
    return copy.deepcopy(next(p['binding'] for p in matrix['profiles'] if p['id'] == 'distributed-managed-s3'))


def evidence(value):
    tool = module()
    fingerprint = tool.binding_digest(value)
    return [{'name': name, 'status': 'passed', 'kind': kind, 'binding_sha256': fingerprint,
        'artifact_sha256': 'a' * 64, **({'restore_verified': True} if name == 'operations.independent_restore' else {})}
        for name, kind in tool.required_checks(value).items()]


def test_complete_exact_profile_can_be_certified_without_echoing_inputs():
    tool, value = module(), binding()
    report = tool.build_certification_report(value, evidence(value))
    assert report['status'] == 'basic-certified'
    assert report['qualification_complete'] and report['certification']['binding_sha256'] == tool.binding_digest(value)
    assert tool.certificate_applies(report['certification'], value)
    assert not report['missing_required'] and not report['failed_required']
    assert 'components' not in report and 'runtime' not in report


@pytest.mark.parametrize('status', ['skipped', 'pending', 'failed', 'missing'])
def test_required_check_cannot_be_skipped(status):
    tool, value = module(), binding()
    checks = evidence(value)
    item = next(c for c in checks if c['name'] == 'storage.publication_full')
    if status == 'missing':
        checks.remove(item)
    else:
        item['status'] = status
    report = tool.build_certification_report(value, checks)
    assert not report['qualification_complete'] and report['certification'] is None
    assert report['status'] == 'tested'


@pytest.mark.parametrize('status,verified', [('failed', True), ('passed', False), ('passed', None)])
def test_failed_restore_blocks_certification(status, verified):
    tool, value = module(), binding()
    checks = evidence(value)
    restore = next(c for c in checks if c['name'] == 'operations.independent_restore')
    restore.update(status=status, restore_verified=verified)
    assert tool.build_certification_report(value, checks)['certification'] is None


@pytest.mark.parametrize('change', ['image', 'version', 'provider', 'topology', 'identity', 'model'])
def test_profile_change_invalidates_report(change):
    tool, original = module(), binding()
    certificate = tool.build_certification_report(original, evidence(original))['certification']
    changed = copy.deepcopy(original)
    if change == 'image': changed['components']['postgres']['image'] = 'postgres@sha256:' + 'b'*64
    elif change == 'version': changed['components']['qdrant']['version'] = '1.19.1'
    elif change == 'provider': changed['components']['postgres']['provider'] = 'external'
    elif change == 'topology': changed['topology']['schedulable_nodes'] = 2
    elif change == 'identity': changed['runtime']['identity_fingerprint'] = 'b'*64
    else: changed['runtime']['model_provider'] = 'external-model'
    assert tool.binding_digest(changed) != tool.binding_digest(original)
    assert not tool.certificate_applies(certificate, changed)
    assert tool.build_certification_report(changed, evidence(original))['certification'] is None


@pytest.mark.parametrize('kind', ['unit', 'integration', 'ci', 'review'])
def test_non_live_evidence_cannot_qualify_real_restore(kind):
    tool, value = module(), binding()
    checks = evidence(value)
    next(c for c in checks if c['name'] == 'operations.independent_restore')['kind'] = kind
    assert tool.build_certification_report(value, checks)['certification'] is None


def test_duplicate_evidence_is_ambiguous_and_cannot_hide_a_failed_restore():
    tool, value = module(), binding()
    checks = evidence(value)
    checks.append({**next(c for c in checks if c['name'] == 'operations.independent_restore'), 'status': 'failed'})
    with pytest.raises(ValueError):
        tool.build_certification_report(value, checks)


@pytest.mark.parametrize('field', ['artifact_sha256', 'binding_sha256'])
def test_passed_evidence_requires_bounded_exact_hashes(field):
    tool, value = module(), binding()
    checks = evidence(value)
    checks[0][field] = 'not-a-hash-private-canary'
    with pytest.raises(ValueError):
        tool.build_certification_report(value, checks)


def test_untrusted_extra_input_fields_are_refused_and_not_echoed():
    tool, value = module(), binding()
    value['database_url'] = 'postgresql://user:private-canary@db/prod'
    with pytest.raises(ValueError) as caught:
        tool.build_certification_report(value, [])
    assert 'private-canary' not in str(caught.value)


def test_no_checks_cannot_qualify_a_profile():
    report = module().build_certification_report(binding(), [])
    assert report['status'] == 'planned' and report['certification'] is None
    assert report['missing_required']


def test_single_physical_domain_cannot_be_resilient_even_with_all_passed_evidence():
    tool, value = module(), binding()
    value['profile'] = 'resilient'
    report = tool.build_certification_report(value, evidence(value))
    assert report['certification'] is None and not report['qualification_complete']
    assert 'resilience.independent_domains' in report['failed_required']


def test_resilient_needs_its_own_live_node_storage_and_domain_restore_evidence():
    tool, value = module(), binding()
    value['profile'] = 'resilient'
    value['topology'].update(orchestrator='kubernetes', schedulable_nodes=3, physical_failure_domains=3)
    required = tool.required_checks(value)
    assert all(required[name] == 'live' for name in (
        'resilience.node_loss', 'resilience.storage_loss', 'resilience.independent_domain_restore'))
    assert tool.build_certification_report(value, evidence(value))['status'] == 'resilient-certified'


def test_kind_is_not_a_physical_resilience_certificate():
    tool, value = module(), binding()
    value['profile'] = 'resilient'
    value['topology'].update(schedulable_nodes=3, physical_failure_domains=3)
    assert tool.build_certification_report(value, evidence(value))['certification'] is None


@pytest.mark.parametrize('fault', ['class', 'policy', 'binding', 'evidence', 'extra'])
def test_a_changed_certificate_is_not_accepted(fault):
    tool, value = module(), binding()
    certificate = tool.build_certification_report(value, evidence(value))['certification']
    if fault == 'class': certificate['class'] = 'resilient-certified'
    elif fault == 'policy': certificate['policy_sha256'] = '0'*64
    elif fault == 'binding': certificate['binding_sha256'] = '0'*64
    elif fault == 'evidence': certificate['evidence_sha256'] = 'private-canary'
    else: certificate['secret'] = 'private-canary'
    assert not tool.certificate_applies(certificate, value)


def test_external_ownership_requires_each_external_component_live_check():
    tool, value = module(), binding()
    value['components']['postgres']['provider'] = 'external'
    value['components']['artifacts']['provider'] = 'external'
    checks = tool.required_checks(value)
    assert checks['storage.postgres.external_ownership'] == checks['storage.artifacts.external_ownership'] == 'live'
    assert 'storage.qdrant.external_ownership' not in checks


def test_inventory_binding_digest_agrees_and_does_not_issue_an_actual_certificate():
    tool = module()
    root = Path(__file__).resolve().parents[2]
    matrix = json.loads((root / 'deploy/kubernetes/replicas/support-matrix.json').read_bytes())
    selected = next(p for p in matrix['profiles'] if p['id'] == 'distributed-managed-s3')
    assert tool.binding_digest(selected['binding']) == selected['binding_sha256']
    assert matrix['release_status'] == 'incomplete' and all(p['certification'] is None for p in matrix['profiles'])
