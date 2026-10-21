"""Domain history and migration replay remain independent of audit retention/sinks."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth.models import User
from app.db.models import AuditLog, DomainTerm
from app.db.session import session_scope
from app.services import locale_service as service
from app.services.glossary.registry import GlossaryRegistry
from scripts.migrate_glossary_identities import _set_ready, apply_identity_migration


ACTOR = User(user_id="admin", username="Admin", roles=["admin"])


def _import(words):
    return service.import_stopwords("zz", words, "bm25", "replace", confirm=True, user=ACTOR)


def _prepared_merge():
    registry = GlossaryRegistry()
    source = registry.create(None, "business_term", "Source")
    target = registry.create(None, "business_term", "Target")
    state = _set_ready(False)
    return registry, source, target, {"expected_revision": state["glossary_revision"], "merges": [{
        "request_id": str(uuid4()), "source_term_id": source["id"], "target_term_id": target["id"],
        "source_version": source["version"], "target_version": target["version"],
        "selections": {"original_name": "target"},
    }]}


def test_stopword_import_history_and_rollback_survive_audit_removal():
    service.create_locale("zz", "Test")
    _import(["one"])
    _import(["two"])
    previous = service.stopwords_history("zz")
    entry = next(item for item in previous if item["words"] == ["two"])
    with session_scope() as session:
        session.query(AuditLog).delete()
    assert service.stopwords_history("zz") == previous
    assert set(entry) == {"id", "created_at", "username", "kind", "meta", "words"}
    assert entry["username"] == "Admin"
    assert entry["meta"] == {"mode": "replace", "added": 1, "removed": 1, "unchanged": 0}
    preview = service.rollback_stopwords("zz", entry["id"], user=ACTOR, confirm=False)
    assert preview["applied"] is False
    result = service.rollback_stopwords("zz", entry["id"], user=ACTOR, preview_digest=preview["preview_digest"])
    assert result["applied"] is True
    assert [row["word"] for row in service.list_stopwords("zz", "bm25")] == ["one"]


def test_glossary_migration_replay_survives_audit_removal():
    registry, source, target, plan = _prepared_merge()
    first = apply_identity_migration(plan, execute=True)
    with session_scope() as session:
        session.query(AuditLog).delete()
    second = apply_identity_migration(plan, execute=True)
    assert second["status"] == "unchanged"
    assert second["result_checksum"] == first["result_checksum"]
    assert registry.get(source["id"]) is None
    assert registry.get(target["id"])["version"] == target["version"] + 1
    with session_scope() as session:
        assert list(session.scalars(select(AuditLog))) == []


def test_glossary_receipt_still_detects_post_migration_changes_without_audit():
    _registry, _source, target, plan = _prepared_merge()
    apply_identity_migration(plan, execute=True)
    with session_scope() as session:
        session.query(AuditLog).delete()
        session.get(DomainTerm, target["id"]).original_description = "Changed externally"
    with pytest.raises(ValueError, match="итоговые метаданные изменены"):
        apply_identity_migration(plan, execute=True)

@pytest.mark.parametrize("db_enabled,stdout_mode", [(True, "disabled"), (False, "durable"), (True, "durable")])
def test_stopword_history_rollback_uses_selected_sinks(monkeypatch, db_enabled, stdout_mode):
    from app.config import get_settings
    from app.db.models import AuditOutbox, StopwordImportSnapshot
    from app.services import audit
    monkeypatch.setenv("AUDIT_DB_ENABLED", str(db_enabled).lower())
    monkeypatch.setenv("AUDIT_STDOUT_MODE", stdout_mode)
    get_settings.cache_clear()
    service.create_locale("zz", "Test")
    _import(["one"])
    _import(["two"])
    entry = next(item for item in service.stopwords_history("zz") if item["words"] == ["two"])
    service.rollback_stopwords("zz", entry["id"], user=ACTOR)
    assert [item["word"] for item in service.list_stopwords("zz", "bm25")] == ["one"]
    with session_scope() as session:
        assert len(list(session.scalars(select(StopwordImportSnapshot)))) == 2
        logs = list(session.scalars(select(AuditLog)))
        assert len(logs) == (3 if db_enabled else 0)
        if db_enabled:
            assert [log.action_type for log in logs] == [audit.STOPWORDS_IMPORT, audit.STOPWORDS_IMPORT, audit.STOPWORDS_ROLLBACK]
        assert len(list(session.scalars(select(AuditOutbox)))) == (3 if stdout_mode == "durable" else 0)


@pytest.mark.parametrize("db_enabled,stdout_mode", [(True, "disabled"), (False, "durable"), (True, "durable")])
def test_glossary_migration_repeat_uses_receipt_in_selected_sinks(monkeypatch, db_enabled, stdout_mode):
    from app.config import get_settings
    from app.db.models import AuditOutbox, GlossaryMigrationReceipt
    from scripts.migrate_glossary_identities import plan_checksum
    monkeypatch.setenv("AUDIT_DB_ENABLED", str(db_enabled).lower())
    monkeypatch.setenv("AUDIT_STDOUT_MODE", stdout_mode)
    get_settings.cache_clear()
    registry, source, target, plan = _prepared_merge()
    first = apply_identity_migration(plan, execute=True)
    with session_scope() as session:
        log_count = len(list(session.scalars(select(AuditLog))))
        queue_count = len(list(session.scalars(select(AuditOutbox))))
        receipt = session.get(GlossaryMigrationReceipt, plan_checksum(plan))
        assert receipt.result_checksum == first["result_checksum"]
        if not db_enabled:
            assert log_count == 0
    second = apply_identity_migration(plan, execute=True)
    assert second["status"] == "unchanged"
    assert second["result_checksum"] == first["result_checksum"]
    assert registry.get(source["id"]) is None
    assert registry.get(target["id"])["version"] == target["version"] + 1
    with session_scope() as session:
        assert len(list(session.scalars(select(AuditLog)))) == log_count
        assert len(list(session.scalars(select(AuditOutbox)))) == queue_count
        assert len(list(session.scalars(select(GlossaryMigrationReceipt)))) == 1


def test_imported_historical_id_targets_snapshot_despite_audit_id_collision():
    from app.db.models import StopwordImportSnapshot
    service.create_locale("zz", "Test")
    service.add_stopword("zz", "current", "bm25", user=ACTOR)
    with session_scope() as session:
        session.add(StopwordImportSnapshot(id=540, locale="zz", kind="bm25", username="Original actor",
            old_words=["original"], new_words=["new"], import_meta={"mode": "replace"}))
        session.add(AuditLog(id=540, action_type="stopwords_import", target_id="zz",
            old_value={"kind": "bm25", "words": ["incorrect"]}, new_value={"kind": "bm25", "words": ["audit"]}))
    entry = service.stopwords_history("zz")[0]
    assert entry["id"] == 540
    assert entry["username"] == "Original actor"
    assert entry["words"] == ["new"]
    service.rollback_stopwords("zz", 540, user=ACTOR)
    assert [row["word"] for row in service.list_stopwords("zz", "bm25")] == ["original"]
    _import(["later"])
    assert service.stopwords_history("zz")[0]["id"] > 540


def test_rollback_snapshot_preserves_locale_kind_empty_and_preview_guards():
    from app.db.models import StopwordImportSnapshot
    from app.services.reference_concurrency import ReferenceError
    service.create_locale("zz", "Test")
    _import(["one"])
    empty_before = service.stopwords_history("zz")[0]
    with pytest.raises(service.LocaleError, match="пуст"):
        service.rollback_stopwords("zz", empty_before["id"], user=ACTOR)
    _import(["two"])
    entry = service.stopwords_history("zz")[0]
    with pytest.raises(service.LocaleNotFoundError):
        service.rollback_stopwords("en", entry["id"], user=ACTOR)
    with pytest.raises(ReferenceError) as wrong_kind:
        service.rollback_stopwords("zz", entry["id"], user=ACTOR, expected_kind="marker")
    assert wrong_kind.value.code == "reference_preview_stale"
    with pytest.raises(ReferenceError) as stale:
        service.rollback_stopwords("zz", entry["id"], user=ACTOR, preview_digest="stale")
    assert stale.value.code == "reference_preview_stale"
    with session_scope() as session:
        session.add(StopwordImportSnapshot(locale="zz", kind="bogus", old_words=["one"], new_words=["two"]))
        session.flush()
        invalid_id = session.scalar(select(StopwordImportSnapshot.id).where(StopwordImportSnapshot.kind == "bogus"))
    with pytest.raises(service.LocaleError, match="некорректен"):
        service.rollback_stopwords("zz", invalid_id, user=ACTOR)
    assert [row["word"] for row in service.list_stopwords("zz", "bm25")] == ["two"]


@pytest.mark.parametrize("failed_write", ["snapshot", "audit"])
def test_stopword_snapshot_and_audit_failures_rollback_word_change(failed_write):
    from sqlalchemy import event
    from app.db.models import StopwordImportSnapshot
    service.create_locale("zz", "Test")
    service.add_stopword("zz", "one", "bm25", user=ACTOR)
    model = StopwordImportSnapshot if failed_write == "snapshot" else AuditLog
    def reject(_mapper, _connection, _target):
        raise RuntimeError("forced insert failure")
    event.listen(model, "before_insert", reject)
    try:
        with pytest.raises(RuntimeError, match="forced insert failure"):
            _import(["two"])
    finally:
        event.remove(model, "before_insert", reject)
    assert [row["word"] for row in service.list_stopwords("zz", "bm25")] == ["one"]
    assert service.stopwords_history("zz") == []
    with session_scope() as session:
        assert not list(session.scalars(select(AuditLog).where(AuditLog.action_type == "stopwords_import")))


@pytest.mark.parametrize("failed_write", ["receipt", "audit"])
def test_glossary_receipt_and_audit_failures_rollback_merge(failed_write):
    from sqlalchemy import event
    from app.db.models import GlossaryMigrationReceipt, GlossaryState
    registry, source, target, plan = _prepared_merge()
    model = GlossaryMigrationReceipt if failed_write == "receipt" else AuditLog
    def reject(_mapper, _connection, row):
        if failed_write == "receipt" or row.action_type == "glossary_identity_migration":
            raise RuntimeError("forced insert failure")
    event.listen(model, "before_insert", reject)
    try:
        with pytest.raises(RuntimeError, match="forced insert failure"):
            apply_identity_migration(plan, execute=True)
    finally:
        event.remove(model, "before_insert", reject)
    assert registry.get(source["id"]) is not None
    assert registry.get(target["id"])["version"] == target["version"]
    with session_scope() as session:
        state = session.get(GlossaryState, 1)
        assert state.revision == plan["expected_revision"]
        assert state.identities_ready is False
        assert list(session.scalars(select(GlossaryMigrationReceipt))) == []
        assert not list(session.scalars(select(AuditLog).where(AuditLog.action_type.in_(["glossary_identity_migration", "glossary_merge"]))))

@pytest.mark.parametrize("operation", ["stopwords", "glossary"])
def test_external_audit_failure_rolls_back_domain_result(monkeypatch, operation):
    import json
    from sqlalchemy import event
    from app.config import get_settings
    from app.db.models import AuditOutbox, GlossaryMigrationReceipt
    monkeypatch.setenv("AUDIT_DB_ENABLED", "false")
    monkeypatch.setenv("AUDIT_STDOUT_MODE", "durable")
    get_settings.cache_clear()
    if operation == "stopwords":
        service.create_locale("zz", "Test")
        service.add_stopword("zz", "one", "bm25", user=ACTOR)
        action = "stopwords_import"
    else:
        registry, source, target, plan = _prepared_merge()
        action = "glossary_identity_migration"
    with session_scope() as session:
        pending_before = len(list(session.scalars(select(AuditOutbox))))
    def reject(_mapper, _connection, row):
        if json.loads(row.payload)["action"] == action:
            raise RuntimeError("forced external insert failure")
    event.listen(AuditOutbox, "before_insert", reject)
    try:
        with pytest.raises(RuntimeError, match="forced external insert failure"):
            if operation == "stopwords":
                _import(["two"])
            else:
                apply_identity_migration(plan, execute=True)
    finally:
        event.remove(AuditOutbox, "before_insert", reject)
    if operation == "stopwords":
        assert service.stopwords_history("zz") == []
        assert [row["word"] for row in service.list_stopwords("zz", "bm25")] == ["one"]
    else:
        assert registry.get(source["id"]) is not None
        assert registry.get(target["id"])["version"] == target["version"]
    with session_scope() as session:
        assert list(session.scalars(select(AuditLog))) == []
        assert list(session.scalars(select(GlossaryMigrationReceipt))) == []
        assert len(list(session.scalars(select(AuditOutbox)))) == pending_before


def test_v1_historical_import_id_still_rolls_back_after_upgrade_and_external_switch(monkeypatch):
    from datetime import datetime, timezone
    from app.config import get_settings
    from app.db.models import AuditOutbox, GlossaryMigrationReceipt, StopwordImportSnapshot
    from app.db.session import get_engine
    from app.services.audit_schema import upgrade_audit_schema
    from app.services.audit_schema_v1_models import AuditOutbox as V1Outbox
    service.create_locale("zz", "Test")
    service.add_stopword("zz", "two", "bm25", user=ACTOR)
    with get_engine().begin() as connection:
        AuditOutbox.__table__.drop(connection)
        StopwordImportSnapshot.__table__.drop(connection)
        GlossaryMigrationReceipt.__table__.drop(connection)
        V1Outbox.__table__.create(connection)
        connection.execute(AuditLog.__table__.insert().values(id=540,
            created_at=datetime(2026, 10, 20, tzinfo=timezone.utc), username="Historic actor",
            action_type="stopwords_import", target_type="locale", target_id="zz",
            old_value={"kind": "bm25", "words": ["one"]}, new_value={"kind": "bm25", "words": ["two"]},
            meta={"mode": "replace", "added": 1, "removed": 1, "unchanged": 0}))
        upgrade_audit_schema(connection)
    monkeypatch.setenv("AUDIT_DB_ENABLED", "false")
    monkeypatch.setenv("AUDIT_STDOUT_MODE", "durable")
    get_settings.cache_clear()
    entry = service.stopwords_history("zz")[0]
    assert entry["id"] == 540
    assert entry["username"] == "Historic actor"
    assert entry["words"] == ["two"]
    preview = service.rollback_stopwords("zz", 540, user=ACTOR, confirm=False, expected_kind="bm25")
    result = service.rollback_stopwords("zz", 540, user=ACTOR, preview_digest=preview["preview_digest"], expected_kind="bm25")
    assert result["applied"] is True
    assert [row["word"] for row in service.list_stopwords("zz", "bm25")] == ["one"]
    _import(["later"])
    assert service.stopwords_history("zz")[0]["id"] > 540
    with session_scope() as session:
        assert len(list(session.scalars(select(AuditLog)))) == 2
        assert session.get(AuditLog, 540).new_value["words"] == ["two"]
        assert len(list(session.scalars(select(AuditOutbox)))) == 2
