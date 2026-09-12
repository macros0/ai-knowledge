import pytest

from app.db.models import DomainTermTranslation
from app.db.session import session_scope
from app.services import audit
from app.services.audit import AuditService
from app.services.glossary.registry import (
    GlossaryAliasConflictError,
    GlossaryCanonicalConflictError,
    GlossaryVersionConflictError,
    GlossaryRegistry,
)
from app.services.glossary.expansion import load_glossary_snapshot
from app.services.glossary.types import GlossaryAliasInput


def test_create_term_atomically_adds_reserved_canonical_alias_and_audit():
    registry = GlossaryRegistry()

    term = registry.create(
        canonical="it0003",
        kind="sap_infotype",
        original_name="Пособие",
        original_description="Описание",
        canonical_locale="de",
        created_by="u1",
    )

    assert term["canonical"] == "IT0003"
    assert term["canonical_locale"] == "de"
    assert term["version"] == 1
    assert len(term["aliases"]) == 1
    assert term["aliases"][0]["alias"] == "IT0003"
    assert term["aliases"][0]["normalized_alias"] == "it0003"
    assert AuditService().query(action_type=audit.GLOSSARY_TERM_CREATE)[0]["target_id"] == str(term["id"])


def test_duplicate_normalized_alias_is_rejected_without_partial_term():
    registry = GlossaryRegistry()
    first = registry.create("IT0003", "sap_infotype", "First")

    with pytest.raises(GlossaryAliasConflictError):
        registry.add_alias(
            first["id"],
            first["version"],
            " it0003 ",
            auto_expand=True,
            search_enabled=True,
        )

    assert len(registry.get(first["id"])["aliases"]) == 1


def test_canonical_is_global_and_cannot_conflict_across_kinds():
    registry = GlossaryRegistry()
    registry.create("IT0003", "sap_infotype", "First")

    with pytest.raises(GlossaryCanonicalConflictError):
        registry.create("it0003", "business_term", "Second")


def test_noop_does_not_increment_version_or_write_audit():
    registry = GlossaryRegistry()
    term = registry.create("IT0003", "sap_infotype", "First")
    before = len(AuditService().query(action_type=audit.GLOSSARY_TERM_UPDATE))

    updated = registry.update(term["id"], term["version"], enabled=True, updated_by="u1")

    assert updated["version"] == term["version"]
    assert len(AuditService().query(action_type=audit.GLOSSARY_TERM_UPDATE)) == before


def test_disable_preserves_aliases_and_increments_version():
    registry = GlossaryRegistry()
    term = registry.create("IT0003", "sap_infotype", "First")
    disabled = registry.update(term["id"], term["version"], enabled=False, updated_by="u1")

    assert disabled["enabled"] is False
    assert disabled["version"] == term["version"] + 1
    assert registry.get(term["id"])["aliases"][0]["alias"] == "IT0003"


def test_stale_cas_does_not_write_audit():
    registry = GlossaryRegistry()
    term = registry.create("IT0003", "sap_infotype", "First")
    before = len(AuditService().query(action_type=audit.GLOSSARY_TERM_UPDATE))

    with pytest.raises(GlossaryVersionConflictError):
        registry.update(term["id"], term["version"] - 1, enabled=False, updated_by="u1")

    assert len(AuditService().query(action_type=audit.GLOSSARY_TERM_UPDATE)) == before


def test_audit_failure_rolls_back_term_and_alias(monkeypatch):
    registry = GlossaryRegistry()

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit, "record_in_session", fail)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        registry.create("IT0003", "sap_infotype", "First")

    assert registry.list() == []


def test_initial_aliases_are_validated_and_created_atomically():
    registry = GlossaryRegistry()

    term = registry.create(
        "PA20",
        "sap_transaction",
        "Изменение данных",
        aliases=[GlossaryAliasInput("/nPA20", auto_expand=True, search_enabled=True)],
    )

    assert [item["normalized_alias"] for item in term["aliases"]] == ["pa20", "/npa20"]


def test_snapshot_is_reused_until_a_registry_mutation_invalidates_it():
    registry = GlossaryRegistry()
    first_term = registry.create("PA20", "sap_transaction", "Изменение данных")

    first = load_glossary_snapshot()
    assert load_glossary_snapshot() is first

    registry.create("PA30", "sap_transaction", "Создание данных")
    second = load_glossary_snapshot()

    assert second is not first
    assert {item.canonical for item in second} == {"PA20", "PA30"}
    assert first_term["canonical"] in {item.canonical for item in second}


def test_alias_update_uses_the_term_cas_and_audit():
    registry = GlossaryRegistry()
    term = registry.create(
        "PA20",
        "sap_transaction",
        "Изменение данных",
        aliases=[GlossaryAliasInput("old-pa20", auto_expand=True, search_enabled=True)],
    )
    alias_id = term["aliases"][1]["id"]

    updated = registry.update_alias(
        term["id"],
        term["version"],
        alias_id,
        alias="new-pa20",
        updated_by="u1",
    )

    assert updated["version"] == term["version"] + 1
    assert updated["aliases"][1]["normalized_alias"] == "new-pa20"
    with pytest.raises(GlossaryVersionConflictError):
        registry.update_alias(term["id"], term["version"], alias_id, alias="other-pa20")


def test_term_update_with_translation_serializes_audit_timestamps():
    registry = GlossaryRegistry()
    term = registry.create("IT0003", "sap_infotype", "First", created_by="user-id")
    with session_scope() as session:
        session.add(
            DomainTermTranslation(
                term_id=term["id"],
                locale="ru",
                display_name="Первый",
                source_revision=term["source_revision"],
                is_machine_translated=False,
            )
        )

    updated = registry.update(
        term["id"],
        term["version"],
        enabled=False,
        updated_by="user-id",
        audit_username="user.name",
    )
    assert updated["enabled"] is False
    entry = AuditService().query(action_type=audit.GLOSSARY_TERM_UPDATE)[0]
    assert entry["username"] == "user.name"
    assert entry["old_value"]["enabled"] is True
