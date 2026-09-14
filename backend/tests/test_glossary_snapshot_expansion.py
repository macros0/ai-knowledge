from types import SimpleNamespace

import pytest

from app.services.glossary.expansion import prepare_query
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.rule_registry import GlossaryRuleRegistry
from app.services.glossary.snapshot import load_glossary_snapshot
from app.services.glossary.snapshot import GlossaryMigrationRequiredError
from app.db.models import GlossaryState
from app.db.models import DomainTerm
from app.db.session import session_scope


def test_configured_rule_changes_next_query_without_reindex():
    GlossaryRegistry().create("IT0003", "sap_infotype", "Пособие", infotype_number="0003")
    rules = GlossaryRuleRegistry()
    rules.create(name="PA", number_from=0, number_to=999, prefixes=("IT",), actor_id="admin")
    settings = SimpleNamespace(glossary_max_added_aliases_per_term=50, glossary_max_added_tokens=512, glossary_max_added_chars=8192, glossary_max_terms_per_query=5, glossary_query_text_max_chars=8192)
    before = prepare_query("ИТ 0003", ui_locale="ru", enabled=True, settings=settings)
    assert before.status == "no_match"
    rule = rules.list()[0]
    rules.update(rule["id"], rule["version"], prefixes=("IT", "ИТ "), actor_id="admin")
    after = prepare_query("ИТ 0003", ui_locale="ru", enabled=True, settings=settings)
    assert after.status == "applied"
    assert after.glossary_revision > before.glossary_revision
    assert after.added_sparse_texts
    assert load_glossary_snapshot().rules[0].version == 2


def test_configured_rule_never_accepts_similar_identifier():
    GlossaryRegistry().create("IT0003", "sap_infotype", "Пособие")
    GlossaryRuleRegistry().create(name="PA", number_from=0, number_to=999, prefixes=("IT",), actor_id="admin")
    for query in ("инфотип 3", "IT00037", "XIT0003Y"):
        assert prepare_query(query, ui_locale="ru", enabled=True).status == "no_match"


def test_unready_glossary_raises_explicit_migration_error():
    with session_scope() as session:
        session.get(GlossaryState, 1).identities_ready = False
    with pytest.raises(GlossaryMigrationRequiredError):
        prepare_query("IT0003", ui_locale="ru", enabled=True)


def test_query_snapshot_does_not_reuse_registry_terms_after_external_revision_change():
    registry = GlossaryRegistry()
    created = registry.create(None, "business_term", "Старое имя")
    first = prepare_query("Старое имя", ui_locale="ru", enabled=True)
    assert first.status == "applied"

    # Simulate another worker's committed write: the process-local registry
    # cache is intentionally not invalidated in this process.
    with session_scope() as session:
        term = session.get(DomainTerm, created["id"])
        term.original_name = "Новое имя"
        term.version += 1
        state = session.get(GlossaryState, 1)
        state.revision += 1

    second = prepare_query("Новое имя", ui_locale="ru", enabled=True)
    assert second.status == "applied"
    assert second.applied_terms[0].canonical == created["canonical"]
