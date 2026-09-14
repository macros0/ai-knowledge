"""Regression coverage for independent final glossary review findings."""
from uuid import uuid4

import pytest

from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.rule_registry import GlossaryRuleRegistry
from app.services.glossary.normalization import GlossaryValidationError
from app.services.glossary.seed import seed_glossary


@pytest.mark.parametrize('alias', ['IT0003', '0004'])
@pytest.mark.parametrize('saved_source', [False, True])
def test_merge_rejects_new_rule_duplicate_or_other_number(alias, saved_source):
    registry = GlossaryRegistry()
    GlossaryRuleRegistry().create(name='IT', number_from=0, number_to=999, prefixes=['IT'])
    target = registry.create(None, 'sap_infotype', 'Payroll status', infotype_number='0003')
    draft = dict(kind='sap_infotype', infotype_number='0003', original_name='Payroll status',
                 aliases=[dict(alias=alias, auto_expand=False, search_enabled=True)])
    request = dict(request_id=str(uuid4()), target_term_id=target['id'])
    if saved_source:
        source = registry.create(None, 'business_term', 'Source')
        request.update(source_term_id=source['id'], source_edit=draft)
    else:
        request['draft'] = draft
    with pytest.raises(GlossaryValidationError):
        preview = registry.merge_preview(request)
        registry.merge(request, preview['digest'], actor_id='review')
    assert registry.get(target['id'])['aliases'] == []


def test_seed_dry_run_reports_alias_conflict_with_existing_name():
    registry = GlossaryRegistry()
    registry.create('TERM_A', 'business_term', 'Existing name', canonical_locale='en')
    entries = [dict(canonical='TERM_B', kind='business_term', original_name='Another name',
                    canonical_locale='en', aliases=[dict(alias='Existing name')])]
    report = seed_glossary(registry, entries, apply=False)
    assert report['conflict'] == 1, report
    assert len(registry.list()) == 1


def test_merge_cannot_reclassify_existing_alias_to_another_infotype_number():
    from app.services.glossary.types import GlossaryAliasInput
    registry = GlossaryRegistry()
    source = registry.create(None, 'business_term', 'Legacy number',
        aliases=[GlossaryAliasInput('0004', auto_expand=False, search_enabled=False)])
    target = registry.create(None, 'sap_infotype', 'Payroll', infotype_number='0003')
    request = dict(source_term_id=source['id'], target_term_id=target['id'],
        selections=dict(kind='target', original_name='target'))
    with pytest.raises(GlossaryValidationError):
        registry.merge_preview(request)


def test_seed_dry_run_checks_disabled_rules_and_existing_alias_changes():
    registry = GlossaryRegistry()
    rules = GlossaryRuleRegistry()
    rules.create(name='IT', number_from=0, number_to=999, prefixes=['IT'], enabled=False)
    entry = dict(canonical='FIRST', kind='business_term', original_name='IT0003',
                 canonical_locale='en', aliases=[])
    assert seed_glossary(registry, [entry])['conflict'] == 1
    registry.create('SECOND', 'business_term', 'Payroll', canonical_locale='en')
    entry.update(canonical='SECOND', original_name='Payroll', aliases=[dict(alias='New alias')])
    assert seed_glossary(registry, [entry])['conflict'] == 1


def test_unsafe_rule_merge_explains_preservation_restriction():
    from app.services.glossary.rule_registry import GlossaryRuleConflictError
    registry = GlossaryRuleRegistry()
    target = registry.create(name='Main', number_from=0, number_to=999, prefixes=['IT'])
    with pytest.raises(GlossaryRuleConflictError) as error:
        registry.merge_preview(dict(target_rule_id=target['id'], draft=dict(name='Draft',
            number_from=900, number_to=1100, prefixes=['IT', 'NEW'])))
    assert error.value.code == 'glossary_rule_merge_unsafe'


def test_merge_does_not_materialize_old_name_already_provided_by_active_rule():
    registry = GlossaryRegistry()
    source = registry.create(None, 'sap_infotype', 'IT0003', infotype_number='0003')
    target = registry.create(None, 'business_term', 'Payroll status')
    GlossaryRuleRegistry().create(name='IT', number_from=0, number_to=999, prefixes=['IT'])
    request = dict(request_id=str(uuid4()), source_term_id=source['id'], target_term_id=target['id'],
                   selections=dict(kind='source', original_name='target'))
    proposal = registry.merge_preview(request)
    assert 'IT0003' not in [item['alias'] for item in proposal['merged']['aliases']]
    result = registry.merge(request, proposal['digest'], actor_id='review')
    assert result['aliases'] == []
    from app.services.glossary.expansion import prepare_query
    assert prepare_query('IT0003', ui_locale='en', enabled=True).strict_groups


def test_seed_accepts_explicit_rules_atomically_and_is_repeatable():
    from app.services.glossary.types import InfotypeRuleSnapshot
    registry = GlossaryRegistry()
    rule = InfotypeRuleSnapshot(1, 'User rule', 0, 999, ('IT', 'ИТ'))
    entries = [dict(canonical='IT0003', kind='sap_infotype', original_name='Payroll status',
                    canonical_locale='en', aliases=[])]
    preview = seed_glossary(registry, entries, rules=(rule,))
    assert preview['rules_would_create'] == 1
    assert GlossaryRuleRegistry().list() == []
    first = seed_glossary(registry, entries, rules=(rule,), apply=True)
    assert first['rules_created'] == 1
    repeated = seed_glossary(registry, entries, rules=(rule,), apply=True)
    assert repeated['rules_created'] == 0
    assert len(GlossaryRuleRegistry().list()) == 1


def test_seed_preflight_checks_alias_against_rules_from_same_package():
    from app.services.glossary.types import InfotypeRuleSnapshot
    registry = GlossaryRegistry()
    rule = InfotypeRuleSnapshot(1, 'User rule', 0, 999, ('IT',))
    entries = [dict(canonical='IT0003', kind='sap_infotype', original_name='Payroll status',
                    canonical_locale='en', aliases=[dict(alias='IT0003')])]
    assert seed_glossary(registry, entries, rules=(rule,))['conflict'] == 1
    with pytest.raises(ValueError):
        seed_glossary(registry, entries, rules=(rule,), apply=True)
    assert registry.list() == []
    assert GlossaryRuleRegistry().list() == []


def test_unsafe_name_has_explicit_preview_reason_without_triggering():
    from app.services.glossary.expansion import prepare_query
    registry = GlossaryRegistry()
    term = registry.create(None, 'business_term', 'HR')
    plan = prepare_query('HR', ui_locale='en', enabled=True)
    assert plan.status == 'no_match'
    assert not plan.strict_groups
    assert any(item.reason == 'unsafe_name' and item.term_id == term['id'] for item in plan.skipped_reasons)


def test_context_budget_preserves_higher_ranked_other_group():
    from app.config import Settings
    from app.services.context_builder import merge_and_format
    from app.services.fusion import Hit
    from app.services.glossary.types import MatchGroup

    group = MatchGroup(term_id=1, canonical='PA30', kind='sap_transaction',
                       original_name='PA30', canonical_locale='und', term_version=1,
                       source_revision=1, matched_forms=('PA30',), spans=(), match_type='alias')

    def hit(doc, slug, score):
        return Hit(point_id=slug, score=score, payload=dict(doc_id=doc, slug=slug,
            chunk_index=0, point_type='concept', title='PA30', content='PA30 ' + 'x' * 95))

    hits = [hit('a', 'best', 1.0), hit('b', 'second', 0.9), hit('a', 'weak-sibling', 0.1)]
    settings = Settings(_env_file=None, chat_max_context_chars=200, chat_concept_max_chars=100)
    blocks = merge_and_format(hits, settings, exact_groups=(group,))
    assert [b['source_slug'] for b in blocks] == ['best', 'second']
