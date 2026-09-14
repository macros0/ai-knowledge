"""Opt-in PostgreSQL lock/rollback checks for the glossary writer protocol."""

from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
from urllib.parse import urlparse

import pytest


@pytest.fixture
def postgres_glossary_db():
    url = os.getenv("GLOSSARY_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("GLOSSARY_TEST_POSTGRES_URL is not configured")
    database = urlparse(url).path.rsplit("/", 1)[-1]
    if not database.startswith("test_glossary_"):
        pytest.fail("GLOSSARY_TEST_POSTGRES_URL must point to a test_glossary_* database")

    from app.db.base import Base
    from app.db.session import configure_for_tests, get_engine, init_db

    configure_for_tests(url)
    Base.metadata.drop_all(get_engine())
    init_db()
    yield
    Base.metadata.drop_all(get_engine())


def test_postgres_writers_serialize_on_state_row(postgres_glossary_db):
    from app.services.glossary.registry import GlossaryIdentityConflictError, GlossaryRegistry

    def create_one(_index):
        try:
            GlossaryRegistry().create(None, "business_term", "Общее имя")
            return "created"
        except GlossaryIdentityConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = sorted(pool.map(create_one, range(2)))
    assert result == ["conflict", "created"]


def test_postgres_identity_conflict_rolls_back_every_table(postgres_glossary_db):
    from app.services.glossary.registry import GlossaryIdentityConflictError, GlossaryRegistry
    from app.services.glossary.types import GlossaryAliasInput

    registry = GlossaryRegistry()
    registry.create(None, "business_term", "Первый", aliases=[GlossaryAliasInput("уникальный", auto_expand=True)])
    with pytest.raises(GlossaryIdentityConflictError):
        registry.create(None, "business_term", "Второй", aliases=[GlossaryAliasInput("УНИКАЛЬНЫЙ", auto_expand=True)])
    assert [term["original_name"] for term in registry.list()] == ["Первый"]


def test_postgres_name_and_alias_race_has_one_identity_owner(postgres_glossary_db):
    from app.services.glossary.registry import GlossaryIdentityConflictError, GlossaryRegistry
    registry = GlossaryRegistry()
    existing = registry.create(None, 'business_term', 'Existing owner')

    def mutate(which):
        try:
            if which == 'name':
                registry.create(None, 'business_term', 'Shared identity')
            else:
                registry.add_alias(existing['id'], existing['version'], 'Shared identity', auto_expand=True)
            return 'created'
        except GlossaryIdentityConflictError:
            return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(mutate, ['name', 'alias'])) == ['conflict', 'created']
    assert len(registry.check_aliases(['Shared identity'])) == 1


def test_postgres_rule_and_literal_race_reserves_the_same_namespace(postgres_glossary_db):
    from app.services.glossary.registry import GlossaryIdentityConflictError, GlossaryRegistry
    from app.services.glossary.rule_registry import GlossaryRuleConflictError, GlossaryRuleRegistry

    def mutate(which):
        try:
            if which == 'literal':
                GlossaryRegistry().create(None, 'sap_transaction', 'IT0003')
            else:
                GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
            return 'created'
        except (GlossaryIdentityConflictError, GlossaryRuleConflictError):
            return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(mutate, ['literal', 'rule'])) == ['conflict', 'created']


def test_postgres_snapshot_observes_a_real_other_process_mutation(postgres_glossary_db):
    from app.services.glossary.registry import GlossaryRegistry
    from app.services.glossary.expansion import prepare_query
    term = GlossaryRegistry().create(None, 'sap_transaction', 'PA30')
    assert not prepare_query('Personnel editor', ui_locale='en', enabled=True).strict_groups
    script = (
        "import os,sys; from app.db.session import configure_for_tests; "
        "configure_for_tests(os.environ['GLOSSARY_TEST_POSTGRES_URL']); "
        "from app.services.glossary.registry import GlossaryRegistry; "
        "GlossaryRegistry().add_alias(int(sys.argv[1]), int(sys.argv[2]), "
        "'Personnel editor', auto_expand=True, search_enabled=True)"
    )
    result = subprocess.run([sys.executable, '-c', script, str(term['id']), str(term['version'])],
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert prepare_query('Personnel editor', ui_locale='en', enabled=True).strict_groups
