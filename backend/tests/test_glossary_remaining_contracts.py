"""Independent acceptance probes; isolated SQLite, no application changes."""
import pytest

from app.db.session import configure_for_tests, init_db
from app.services.glossary.identity import collect_identity_conflicts
from app.services.glossary.registry import GlossaryRegistry, GlossaryVersionConflictError
from app.services.glossary.rule_registry import GlossaryRuleRegistry
from app.services.glossary.types import InfotypeRuleSnapshot
from app.services.glossary.expansion import prepare_query
from app.services.glossary.matching import exact_excerpt


@pytest.fixture(autouse=True)
def isolated(tmp_path):
    configure_for_tests(f"sqlite:///{(tmp_path / 'latest-review.db').as_posix()}")
    init_db()


def test_stale_noop_source_update_must_reject_version():
    registry = GlossaryRegistry()
    term = registry.create(None, 'business_term', 'Source name')
    current = registry.update(term['id'], term['version'], original_name='Changed name')
    with pytest.raises(GlossaryVersionConflictError):
        registry.update(term['id'], term['version'], original_name=current['original_name'])


def test_number_only_edit_must_not_make_translations_stale():
    registry = GlossaryRegistry()
    term = registry.create(None, 'sap_infotype', 'Personnel infotype', infotype_number='0003')
    current = registry.update(term['id'], term['version'], infotype_number='0004')
    assert current['source_revision'] == term['source_revision']


def test_rule_generated_alias_must_not_be_saved_as_explicit_duplicate():
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    registry = GlossaryRegistry()
    term = registry.create(None, 'sap_infotype', 'Personnel infotype', infotype_number='0003')
    with pytest.raises(ValueError):
        registry.add_alias(term['id'], term['version'], alias='IT0003', auto_expand=True, search_enabled=True)


def test_offline_audit_detects_rule_form_owned_by_another_term():
    rule = InfotypeRuleSnapshot(rule_id=1, name='PA', number_from=0, number_to=999,
                                prefixes=('IT',), enabled=True, version=1)
    terms = (
        dict(id=1, kind='sap_infotype', original_name='Personnel infotype', infotype_number='0003'),
        dict(id=2, kind='sap_transaction', original_name='IT0003'),
    )
    assert collect_identity_conflicts(terms, (rule,))


def test_offline_audit_reserves_number_outside_rules():
    rule = InfotypeRuleSnapshot(rule_id=1, name='OM', number_from=1000, number_to=1999,
                                prefixes=('IT',), enabled=True, version=1)
    terms = (
        dict(id=1, kind='sap_infotype', original_name='First infotype', infotype_number='0003'),
        dict(id=2, kind='sap_infotype', original_name='Second infotype', infotype_number='0003'),
    )
    assert collect_identity_conflicts(terms, (rule,))


def test_excerpt_must_not_truncate_longer_code_into_false_exact_match():
    GlossaryRegistry().create(None, 'sap_transaction', 'PA30')
    groups = prepare_query('PA30', ui_locale='en', enabled=True).strict_groups
    text = 'x ' * 148 + 'PA3000 wrong. ' + 'y ' * 200 + 'PA30 exact.'
    excerpt = exact_excerpt(text, 300, groups)
    assert 'PA30 exact.' in excerpt, repr(excerpt)


def test_infotype_does_not_accept_another_number_as_explicit_alias():
    registry = GlossaryRegistry()
    term = registry.create(None, 'sap_infotype', 'Personnel infotype', infotype_number='0003')
    with pytest.raises(ValueError):
        registry.add_alias(term['id'], term['version'], alias='0004', search_enabled=True)


def test_alias_noop_must_still_check_saved_version():
    registry = GlossaryRegistry()
    term = registry.create(None, 'business_term', 'Source')
    term = registry.add_alias(term['id'], term['version'], alias='Existing alias')
    registry.update(term['id'], term['version'], enabled=False)
    with pytest.raises(GlossaryVersionConflictError):
        registry.update_alias(term['id'], term['version'], term['aliases'][0]['id'], alias='Existing alias')
