"""Search acceptance uses the same saved forms as glossary recognition."""
from types import SimpleNamespace

import pytest

from app.services.glossary.expansion import prepare_query
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.rule_registry import GlossaryRuleRegistry
from app.services.glossary.matching import group_form_matches
from app.services.glossary.types import GlossaryAliasInput
from app.services.context_builder import merge_and_format
from app.services.fusion import Hit


def query_plan(query, **overrides):
    values = dict(glossary_max_added_aliases_per_term=50, glossary_max_added_tokens=512,
                  glossary_max_added_chars=8192, glossary_max_terms_per_query=5,
                  glossary_query_text_max_chars=8192)
    values.update(overrides)
    return prepare_query(query, ui_locale='ru', enabled=True, settings=SimpleNamespace(**values))


@pytest.mark.parametrize('query', ['PA30', 'HR editor'])
@pytest.mark.parametrize('budget', [0, 50])
def test_strict_filter_keeps_other_searchable_alias_and_name(query, budget):
    GlossaryRegistry().create(None, 'sap_transaction', 'PA30', aliases=[
        GlossaryAliasInput('HR editor', auto_expand=True, search_enabled=False),
        GlossaryAliasInput('Maintain HR master data', auto_expand=False, search_enabled=True),
        GlossaryAliasInput('Unapproved phrase', auto_expand=False, search_enabled=False)])
    plan = query_plan(query, glossary_max_added_aliases_per_term=budget)
    group = plan.strict_groups[0]
    assert group_form_matches('Maintain HR master data', group)
    assert group_form_matches('PA30', group)
    assert group_form_matches(query, group)
    assert not group_form_matches('Unapproved phrase', group)
    hits = [Hit('exact', 1.0, {'doc_id': 'doc', 'point_type': 'concept',
        'title': 'Maintain HR master data', 'content': 'Saved explicit alias.',
        'slug': 'allowed', 'tags': []})]
    assert merge_and_format(hits, exact_groups=plan.strict_groups)


def test_rule_and_explicit_aliases_share_all_search_forms():
    GlossaryRegistry().create(None, 'sap_infotype', 'Payroll status', infotype_number='0003', aliases=[
        GlossaryAliasInput('Personnel status record', auto_expand=True, search_enabled=True)])
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    plan = query_plan('IT0003')
    assert 'Personnel status record' in plan.added_sparse_texts
    assert group_form_matches('Personnel status record', plan.strict_groups[0])


def test_disabled_number_has_no_virtual_substitute():
    registry = GlossaryRegistry()
    term = registry.create(None, 'sap_infotype', 'Payroll status', infotype_number='0003')
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    registry.update(term['id'], term['version'], enabled=False)
    assert query_plan('IT0003').status == 'no_match'
    assert query_plan('IT0004').status == 'applied'


def test_virtual_number_has_one_group_with_all_spans():
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT', 'ИТ', 'ИТ '])
    plan = query_plan('IT0003 ИТ 0003')
    assert len(plan.applied_terms) == 1
    assert len(plan.strict_groups[0].spans) == 2


@pytest.mark.parametrize('text', ['/ABC/Z_REPORT', 'Z_REPORT_OLD', 'Z_REPORT-OLD', 'Z_REPORT.OLD'])
def test_sap_technical_boundary_rejects_embedded_object(text):
    GlossaryRegistry().create(None, 'sap_program', 'Z_REPORT')
    assert query_plan(text).status == 'no_match'
    group = query_plan('Z_REPORT').strict_groups[0]
    assert not group_form_matches(text, group)
    assert group_form_matches('(Z_REPORT)', group)


def test_short_source_name_is_not_an_automatic_trigger():
    GlossaryRegistry().create(None, 'business_term', 'PA', aliases=[
        GlossaryAliasInput('Personnel administration', auto_expand=True, search_enabled=True)])
    assert query_plan('PA').status == 'no_match'
    assert query_plan('Personnel administration').status == 'applied'


@pytest.mark.parametrize('text', ['IT0003-OLD', '/ABC/IT0003', 'IT0003.OLD'])
def test_virtual_rule_uses_same_technical_boundaries(text):
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    assert query_plan(text).status == 'no_match'


@pytest.mark.parametrize('code', ['PA30', 'IT0003'])
@pytest.mark.parametrize('template', ['{}.', '{}:', '({}).', '{}. Next sentence', 'Code: {}', '— {} —'])
def test_sentence_punctuation_keeps_exact_sap_matches(code, template):
    if code == 'IT0003':
        GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    else:
        GlossaryRegistry().create(None, 'sap_transaction', code)
    plan = query_plan(template.format(code))
    assert len(plan.strict_groups) == 1
    assert group_form_matches(template.format(code), plan.strict_groups[0])
    assert not group_form_matches(f'{code}.OLD', plan.strict_groups[0])
    assert not group_form_matches(f'OLD:{code}', plan.strict_groups[0])


def test_rule_budget_visits_explicit_prefixes_and_never_repeats_query():
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT', 'ИТ', 'Infotyp', 'IT '])
    plan = query_plan('IT0003', glossary_max_added_aliases_per_term=3)
    assert plan.added_sparse_texts == ('ит0003', 'infotyp0003', 'it 0003')
    assert plan.dense_query.lower().count('it0003') == 1
    assert plan.status == 'applied'


def test_explicit_query_form_does_not_consume_added_form_budget():
    GlossaryRegistry().create(None, 'sap_transaction', 'PA30', aliases=[
        GlossaryAliasInput('Personnel editor', auto_expand=True, search_enabled=True)])
    plan = query_plan('PA30', glossary_max_added_aliases_per_term=1)
    assert plan.added_sparse_texts == ('Personnel editor',)
    assert plan.dense_query.count('PA30') == 1
    assert plan.status == 'applied'


def test_context_excerpt_keeps_exact_form_found_after_the_content_limit():
    from app.config import Settings
    GlossaryRegistry().create(None, 'sap_transaction', 'PA30')
    plan = query_plan('PA30')
    content = 'Unrelated introduction. ' * 100 + 'PA30 is the relevant transaction.'
    hits = [Hit('late-match', 1.0, dict(point_type='concept', doc_id='doc', slug='late',
                                      title='Personnel guide', content=content, tags=[]))]
    blocks = merge_and_format(hits, Settings(_env_file=None, chat_concept_max_chars=300), exact_groups=plan.strict_groups)
    assert len(blocks) == 1
    assert 'PA30' in blocks[0]['content']
    assert len(blocks[0]['content']) <= 300
