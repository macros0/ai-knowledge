"""Exercise additive Kubernetes policy semantics on the actual rendered split chart."""
import pytest

from tests.test_kubernetes_chart import render


def selected(selector, labels):
    if any(labels.get(k) != v for k, v in selector.get('matchLabels', {}).items()):
        return False
    for expression in selector.get('matchExpressions', []):
        key, operator = expression['key'], expression['operator']
        values = expression.get('values', [])
        if operator == 'In' and labels.get(key) not in values:
            return False
        if operator == 'NotIn' and labels.get(key) in values:
            return False
    return True


def allows(docs, source, target, port, namespace='test', protocol='TCP'):
    source = {'app.kubernetes.io/instance': 'test', 'okf-role': source}
    target = {'app.kubernetes.io/instance': 'test', 'okf-role': target}
    policies = [d['spec'] for d in docs if d['kind'] == 'NetworkPolicy'
                and 'Egress' in d['spec']['policyTypes'] and selected(d['spec']['podSelector'], source)]
    if not policies:
        return True
    for policy in policies:
        for rule in policy.get('egress', []):
            if not any(p['port'] == port and p.get('protocol', 'TCP') == protocol
                       for p in rule.get('ports', [{'port': port, 'protocol': protocol}])):
                continue
            for peer in rule.get('to', [{}]):
                # These tests use Pod peers, not ipBlock destinations.
                if 'ipBlock' in peer:
                    continue
                ns = peer.get('namespaceSelector')
                if ns is None and namespace != 'test':
                    continue
                if ns is not None and not selected(ns, {'kubernetes.io/metadata.name': namespace}):
                    continue
                if selected(peer.get('podSelector', {}), target):
                    return True
    return False


@pytest.mark.parametrize('role,port', [('postgres', 5432), ('qdrant', 6333), ('qdrant', 6334),
                                       ('model-stub', 8000), ('unrelated', 8000), ('application', 5432)])
def test_split_frontend_cannot_reach_storage_models_or_unrelated_ports(role, port):
    assert not allows(render('deployment.mode=frontend-split'), 'frontend', role, port)


def test_split_frontend_only_backend_and_dns_positive_controls():
    docs = render('deployment.mode=frontend-split')
    assert allows(docs, 'frontend', 'application', 8000)
    for protocol in ['TCP', 'UDP']:
        assert allows(docs, 'frontend', 'coredns', 53, 'kube-system', protocol)
    assert not allows(docs, 'frontend', 'application', 8000, 'foreign-namespace')
    assert not allows(docs, 'frontend', 'coredns', 8000, 'kube-system')


def test_split_external_egress_is_backend_only():
    docs = render('deployment.mode=frontend-split',
                  'networkPolicy.externalEgress[0].to[0].namespaceSelector.matchLabels.owner=provider',
                  'networkPolicy.externalEgress[0].ports[0].port=9443')
    policies = {d['metadata']['name']: d['spec'] for d in docs if d['kind'] == 'NetworkPolicy'}
    assert 'application-boundaries' not in policies
    assert set(policies) >= {'frontend-boundaries', 'combined-boundaries', 'frontend-split-default-deny'}
    extra = next(r for r in policies['combined-boundaries']['egress']
                 if r.get('ports') == [{'port': 9443}])
    assert extra not in policies['frontend-boundaries']['egress']


@pytest.mark.parametrize('role,port', [('postgres', 5432), ('qdrant', 6333), ('qdrant', 6334), ('model-stub', 8000)])
def test_split_combined_backend_retains_required_services(role, port):
    assert allows(render('deployment.mode=frontend-split'), 'application', role, port)


def test_compact_keeps_its_existing_combined_pod_contract():
    docs = render()
    assert {d['metadata']['name'] for d in docs if d['kind'] == 'NetworkPolicy'} == {
        'application-boundaries', 'stub-no-egress'}
    assert allows(docs, 'application', 'postgres', 5432)
    assert not allows(docs, 'model-stub', 'application', 8000)
