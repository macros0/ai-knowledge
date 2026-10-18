from datetime import timedelta

from app.db.session import session_scope
from app.services.reference_resources import ReferenceKey
from app.services.reference_concurrency import ReferenceError, lock_reference_resources


def test_bounded_counters_and_authoritative_active_counts():
    from app.services.diagnostics import reference_counters as counters
    before = counters.snapshot()["attempts"].get("version_conflict", 0)
    ReferenceError("version_conflict", secret="must-never-be-a-label")
    ReferenceError("arbitrary-secret-code")
    from app.services.reference_edit_lock import utc_now
    with session_scope() as session:
        row = lock_reference_resources(session, [ReferenceKey("tag", "1")])[ReferenceKey("tag", "1")]
        row.owner_id = "not-for-diagnostics"
        row.expires_at = row.idle_expires_at = utc_now() + timedelta(seconds=60)
    result = counters.snapshot()
    assert result["attempts"]["version_conflict"] == before + 1
    assert result["active_leases"]["tag"] == 1
    assert "secret" not in str(result)
    assert "not-for-diagnostics" not in str(result)
    assert result["attempts_scope"] == "backend_process_since_start"
    assert result["active_scope"] == "database_now"


def test_flag_transition_invalidates_generations_without_changing_content_revision():
    from app.services.reference_operations import invalidate_all_editor_generations
    key = ReferenceKey("tag", "8")
    with session_scope() as session:
        row = lock_reference_resources(session, [key])[key]
        row.lease_epoch = 9
        row.owner_id = "old-owner"
    with session_scope() as session:
        preview = invalidate_all_editor_generations(session, apply=False)
        assert preview["resources"] == 1
    with session_scope() as session:
        row = lock_reference_resources(session, [key])[key]
        assert row.lease_epoch == 9
        invalidate_all_editor_generations(session, apply=True)
    with session_scope() as session:
        row = lock_reference_resources(session, [key])[key]
        assert row.lease_epoch == 10
        assert row.revision == 1
        assert row.owner_id is None


def test_development_list_reads_content_and_revisions_in_one_query_without_n_plus_one():
    from sqlalchemy import event
    from app.db.session import get_engine
    from app.services.development_registry import DevelopmentRegistry
    registry = DevelopmentRegistry()
    for index in range(5):
        registry.create(str(index), f"Development {index}")
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        rows, total = registry.query()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(rows) == total == 5
    assert all(row["reference_revision"] == 1 for row in rows)
    assert len(statements) == 2, "one count plus one coherent content/revision snapshot"
    assert "reference_resource_states" in statements[-1]


def test_tag_snapshot_uses_one_statement_for_content_translations_counts_and_revision():
    from sqlalchemy import event
    from app.db.session import get_engine
    from app.services.tag_registry import TagRegistry
    registry = TagRegistry()
    registry.add(["one", "two"])
    identifier = registry.all()[0]["id"]
    registry.set_translation(identifier, "en", "Translated", expected_reference_revision=1)
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        rows = registry.all("en")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(rows) == 2
    assert rows[0]["display"] == "Translated"
    assert rows[0]["reference_revision"] == 2
    assert len(statements) == 1, "all versioned content must come from the same SQL snapshot"


def test_reference_locale_keys_preserve_existing_multiple_subtag_contract():
    from app.services.locale_service import create_locale, list_locales
    created = create_locale("zh-hans-cn", "Chinese Simplified")
    assert created["code"] == "zh-hans-cn"
    assert any(row["code"] == "zh-hans-cn" for row in list_locales())
    for kind in ("locale", "ui_dictionary", "stopword_set"):
        key = ReferenceKey(kind, "zh-hans-cn", kind="bm25" if kind == "stopword_set" else None)
        assert key.resource_id == "zh-hans-cn"


def test_union_catalog_revision_advances_for_scoped_values_and_translations():
    from app.services.attribute_registry import AttributeRegistry
    from app.services.reference_translation import capture_translation_candidate, commit_translation_candidate
    registry = AttributeRegistry()
    registry.add("module", "GLOBAL", label="Global")
    before = registry.snapshot("module")
    scoped = registry.add("module", "SCOPED", label="Scoped", org_id=1)
    after = registry.snapshot("module")
    assert after["reference_revision"] > before["reference_revision"]
    candidate, skipped = capture_translation_candidate("attributes", scoped["id"], "en")
    assert skipped is None
    with session_scope() as session:
        commit_translation_candidate(session, candidate, "Translated scoped")
    translated = registry.snapshot("module", locale="en")
    assert translated["reference_revision"] > after["reference_revision"]
    registry.remove("module", "SCOPED", org_id=1)
    assert registry.snapshot("module")["reference_revision"] > translated["reference_revision"]


def test_development_translation_is_part_of_the_versioned_read():
    from app.db.models import DevelopmentTranslation
    from app.services.development_registry import DevelopmentRegistry
    registry = DevelopmentRegistry()
    development = registry.create("TRANSLATED", "Original", canonical_locale="ru")
    with session_scope() as session:
        session.add(DevelopmentTranslation(development_id=development["id"], locale="en", name="English"))
    rows, total = registry.query(locale="en")
    assert total == 1
    assert rows[0]["display_name"] == "English"
    assert registry.get(development["id"], locale="en")["display_name"] == "English"


def test_development_create_rolls_back_content_and_revision_when_audit_fails(monkeypatch):
    import pytest
    from app.auth.models import User
    from app.services.development_registry import DevelopmentRegistry
    from app.services import audit
    registry = DevelopmentRegistry()
    def failed(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(audit, "record_in_session", failed)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        registry.create("ATOMIC-CREATE", "Must roll back", actor=User(user_id="admin", roles=["admin"]))
    assert registry.find_by_number("ATOMIC-CREATE") is None
