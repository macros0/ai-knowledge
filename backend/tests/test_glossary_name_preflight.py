import pytest
from app.api import glossary as api
from app.models import glossary as models

from app.api.glossary import check_glossary_aliases
from app.db.session import configure_for_tests, init_db
from app.models.glossary import GlossaryAliasCheck
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.rule_registry import GlossaryRuleRegistry


@pytest.fixture(autouse=True)
def isolated(tmp_path):
    configure_for_tests(f"sqlite:///{(tmp_path / 'name-preflight.db').as_posix()}")
    init_db()


def check(**payload):
    return check_glossary_aliases(GlossaryAliasCheck(**payload), user=None)['conflicts']


def test_new_infotype_name_can_use_its_proposed_number_without_owning_a_saved_card():
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    assert check(aliases=['IT0003'], kind='sap_infotype', infotype_number='0003') == []
    assert check(aliases=['IT0003'], kind='sap_infotype', infotype_number='0004')


def test_name_check_uses_draft_kind_instead_of_saved_infotype_kind():
    registry = GlossaryRegistry()
    owner = registry.create(None, 'sap_infotype', 'IT0003', infotype_number='0003')
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    assert check(aliases=['IT0003'], term_id=owner['id']) == []
    conflicts = check(aliases=['IT0003'], term_id=owner['id'], kind='sap_transaction')
    assert conflicts[0]['key_kind'] == 'infotype_rule'
    assert conflicts[0]['number'] == '0003'


def test_proposed_number_does_not_hide_foreign_term_ownership():
    registry = GlossaryRegistry()
    owner = registry.create(None, 'sap_infotype', 'Personnel', infotype_number='0003')
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    conflicts = check(aliases=['IT0003'], kind='sap_infotype', infotype_number='0003')
    assert conflicts[0]['term_id'] == owner['id']


def test_name_check_still_finds_aliases_and_excludes_current_owner():
    registry = GlossaryRegistry()
    owner = registry.create(None, 'sap_transaction', 'PA30')
    assert check(aliases=[' pa30 '], kind='business_term')[0]['term_id'] == owner['id']
    assert check(aliases=['PA30'], term_id=owner['id'], kind='sap_transaction') == []


def full_check(**payload):
    endpoint = getattr(api, 'check_glossary_conflicts', None)
    assert callable(endpoint), 'full-draft preflight route is missing'
    return endpoint(models.GlossaryConflictCheck(**payload), user=None)


def test_full_draft_checks_number_alias_and_local_redundancy_without_writing():
    registry = GlossaryRegistry()
    numbered = registry.create(None, 'sap_infotype', 'Existing personnel', infotype_number='0003')
    transaction = registry.create(None, 'sap_transaction', 'PA30')
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    before = registry.list()
    result = full_check(kind='sap_infotype', original_name='Draft personnel', infotype_number='0003',
                        aliases=[{'alias': 'PA30'}, {'alias': 'IT0003'}, {'alias': 'Draft personnel'}])
    assert {(item['key_kind'], item['term_id']) for item in result['conflicts']} >= {
        ('infotype', numbered['id']), ('literal', transaction['id'])}
    assert {item['alias'] for item in result['redundant_forms']} == {'IT0003', 'Draft personnel'}
    assert registry.list() == before


def test_full_draft_excludes_current_owner_but_uses_proposed_kind():
    registry = GlossaryRegistry()
    owner = registry.create(None, 'sap_infotype', 'IT0003', infotype_number='0003')
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    assert full_check(term_id=owner['id'], kind='sap_infotype', original_name='IT0003',
                      infotype_number='0003') == {'conflicts': [], 'redundant_forms': []}
    result = full_check(term_id=owner['id'], kind='sap_transaction', original_name='IT0003')
    assert result['conflicts'][0]['key_kind'] == 'infotype_rule'


def test_full_draft_reports_duplicate_alias_rows():
    result = full_check(kind='business_term', original_name='Draft', aliases=[{'alias': 'one'}, {'alias': ' ONE '}])
    assert result['conflicts'][0]['key_value'] == 'one'
    assert result['conflicts'][0]['conflicting_field'] == 'aliases[0]'
