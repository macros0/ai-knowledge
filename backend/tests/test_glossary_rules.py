from app.services.glossary.forms import forms_for_number
from app.services.glossary.rules import match_infotypes, validate_rule
from app.services.glossary.types import InfotypeRuleSnapshot
from app.services.glossary.rule_registry import GlossaryRuleRegistry
import pytest


RULE = InfotypeRuleSnapshot(
    1,
    "Основные данные",
    0,
    999,
    ("IT", "IT ", "ИТ", "ИТ ", "Infotyp", "Infotyp ", "инфо-типа", "инфо-типа "),
)


def test_rule_matches_only_configured_prefixes_and_four_digits():
    cases = {
        "IT0003": "0003",
        "ИТ 0003": "0003",
        "Infotyp 0003": "0003",
        "инфо-типа 0003": "0003",
        "инфотип 0003": None,
        "инфотип 3": None,
        "IT00037": None,
        "XIT0003Y": None,
        "IT1000": None,
        "Infotype 0003": None,
    }
    for text, expected in cases.items():
        matches = match_infotypes(text, (RULE,))
        assert tuple(item.number for item in matches) == (() if expected is None else (expected,))


def test_rule_forms_are_query_only_and_belong_to_one_number():
    forms = forms_for_number("0003", (RULE,))
    assert {item.normalized for item in forms} == {
        "it0003", "it 0003", "ит0003", "ит 0003",
        "infotyp0003", "infotyp 0003", "инфо-типа0003", "инфо-типа 0003",
    }
    assert all(item.identity_key == "infotype:0003" for item in forms)


def test_rule_respects_boundaries_and_range_edges():
    rule = InfotypeRuleSnapshot(2, "Time", 2000, 2999, ("IT",))
    assert match_infotypes("IT2000 IT2999 IT1999 IT3000", (rule,))[0].number == "2000"
    assert tuple(item.number for item in match_infotypes("IT2000 IT2999 IT1999 IT3000", (rule,))) == (
        "2000", "2999"
    )


def test_rule_prefixes_are_literal_and_normalized_once():
    assert validate_rule(0, 9999, ("Infotyp", "инфо-типа ")) == ("infotyp", "инфо-типа ")


def test_rule_prefix_spacing_is_explicit():
    tight = InfotypeRuleSnapshot(3, "Tight", 0, 9999, ("ИТ",))
    spaced = InfotypeRuleSnapshot(4, "Spaced", 0, 9999, ("ИТ ",))

    assert tuple(item.number for item in match_infotypes("ИТ0000", (tight,))) == ("0000",)
    assert match_infotypes("ИТ 0000", (tight,)) == ()
    assert tuple(item.number for item in match_infotypes("ИТ 0000", (spaced,))) == ("0000",)
    assert match_infotypes("ИТ0000", (spaced,)) == ()


def test_rule_conflict_checks_keep_prefix_spacing_exact():
    from app.services.glossary.rule_registry import _matches_rule_form

    assert _matches_rule_form("ИТ0000", 0, 9999, ("ИТ",)) == "0000"
    assert _matches_rule_form("ИТ 0000", 0, 9999, ("ИТ",)) is None
    assert _matches_rule_form("ИТ 0000", 0, 9999, ("ИТ ",)) == "0000"
    assert _matches_rule_form("ИТ0000", 0, 9999, ("ИТ ",)) is None


def test_disabled_rule_reserves_coverage_for_all_writers():
    registry = GlossaryRuleRegistry()
    disabled = registry.create(name='Disabled', number_from=0, number_to=999, prefixes=['IT'], enabled=False)
    assert registry.check(number_from=0, number_to=999, prefixes=['IT'])[0]['id'] == disabled['id']
    with pytest.raises(ValueError):
        registry.create(name='Duplicate', number_from=0, number_to=999, prefixes=['IT'])


def test_same_range_merges_distinct_prefixes_without_new_combinations():
    registry = GlossaryRuleRegistry()
    source = registry.create(name='Latin', number_from=0, number_to=999, prefixes=['IT'])
    target = registry.create(name='Russian', number_from=0, number_to=999, prefixes=['ИТ'])
    request = dict(source_rule_id=source['id'], target_rule_id=target['id'])
    preview = registry.merge_preview(request)
    result = registry.merge(request, preview['digest'], actor_id='test')
    assert set(result['prefixes']) == {'it', 'ит'}
    assert (result['number_from'], result['number_to']) == (0, 999)


def test_rule_merge_rechecks_digest_inside_write_transaction(monkeypatch):
    registry = GlossaryRuleRegistry()
    source = registry.create(name='First', number_from=0, number_to=999, prefixes=['IT'])
    target = registry.create(name='Second', number_from=1000, number_to=1999, prefixes=['IT'])
    request = dict(source_rule_id=source['id'], target_rule_id=target['id'])
    preview = registry.merge_preview(request)
    original = registry.merge_preview
    def update_after_preview(request):
        proposal = original(request)
        registry.update(target['id'], target['version'], name='Changed by another writer')
        return proposal
    monkeypatch.setattr(registry, 'merge_preview', update_after_preview)
    try:
        result = registry.merge(request, preview['digest'], actor_id='test')
    except ValueError:
        return
    # A correct implementation never invokes the public preview during commit;
    # otherwise it must reject the mutation made after that preview.
    assert result['name'] == 'Second'


def test_rule_check_includes_conflicting_term_and_disabled_rules_reserve_literals():
    from app.services.glossary.registry import GlossaryRegistry
    terms = GlossaryRegistry()
    rules = GlossaryRuleRegistry()
    owner = terms.create(None, 'sap_transaction', 'IT0003')
    assert any(item.get('term_id') == owner['id'] for item in rules.check(number_from=0, number_to=999, prefixes=['IT']))
    with pytest.raises(ValueError):
        rules.create(name='Disabled', number_from=0, number_to=999, prefixes=['IT'], enabled=False)
    rules.create(name='Other disabled', number_from=1000, number_to=1999, prefixes=['IT'], enabled=False)
    with pytest.raises(ValueError):
        terms.create(None, 'sap_transaction', 'IT1000')


def test_rule_count_limit_includes_disabled_rules():
    from app.db.models import GlossaryInfotypeRule
    from app.db.session import session_scope
    with session_scope() as session:
        session.add_all(GlossaryInfotypeRule(name=f'Rule {index}', number_from=0, number_to=0, enabled=False)
                        for index in range(200))
    with pytest.raises(ValueError):
        GlossaryRuleRegistry().create(name='Overflow', number_from=0, number_to=999, prefixes=['IT'])
