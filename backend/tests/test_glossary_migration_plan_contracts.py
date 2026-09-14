"""Offline plan simulation, complete backups and migration replay contracts."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.db.models import AuditLog, DomainTerm, DomainTermAlias, GlossaryState
from app.db.session import get_engine, session_scope
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.types import GlossaryAliasInput
from scripts.migrate_glossary_identities import _set_ready, apply_identity_migration, plan_checksum


def _prepared_pair():
    registry = GlossaryRegistry()
    source = registry.create(None, "business_term", "Source")
    target = registry.create(None, "business_term", "Target")
    state = _set_ready(False)
    plan = {"expected_revision": state["glossary_revision"], "merges": [{
        "request_id": str(uuid4()), "source_term_id": source["id"], "target_term_id": target["id"],
        "source_version": source["version"], "target_version": target["version"],
        "selections": {"original_name": "target"},
    }]}
    return registry, source, target, plan


def test_dry_run_simulates_merge_without_any_database_writes():
    registry, source, target, plan = _prepared_pair()
    statements = []

    def capture(_conn, _cursor, statement, _params, _ctx, _many):
        statements.append(statement.strip().split()[0].upper())

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = apply_identity_migration(plan, dry_run=True)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert result["status"] == "dry_run"
    assert {row["id"] for row in result["result"]["terms"]} == {target["id"]}
    assert result["result"]["state"]["identities_ready"] is True
    assert not ({"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"} & set(statements))
    assert registry.get(source["id"]) is not None
    with session_scope() as session:
        assert session.get(GlossaryState, 1).revision == plan["expected_revision"]
        assert not session.get(GlossaryState, 1).identities_ready


def test_dry_run_rejects_unresolved_final_namespace():
    term = GlossaryRegistry().create(None, "sap_infotype", "Personnel", infotype_number="0001")
    _set_ready(False)
    with pytest.raises(ValueError):
        apply_identity_migration({"term_updates": [{"term_id": term["id"], "infotype_number": None}]}, dry_run=True)


def test_plan_checks_initial_versions_for_all_metadata_actions():
    registry = GlossaryRegistry()
    term = registry.create(None, "sap_infotype", "Personnel", infotype_number="0001", aliases=[
        GlossaryAliasInput("legacy one"), GlossaryAliasInput("legacy two")])
    _set_ready(False)
    plan = {"term_updates": [{"term_id": term["id"], "version": term["version"], "infotype_number": "0002"}],
            "alias_deletions": [{"term_id": term["id"], "version": term["version"], "alias_id": alias["id"]}
                                for alias in term["aliases"]]}
    result = apply_identity_migration(plan, execute=True)
    saved = registry.get(term["id"])
    assert result["status"] == "applied"
    assert saved["infotype_number"] == "0002"
    assert saved["aliases"] == []
    assert saved["version"] == term["version"] + 1
    assert saved["source_revision"] == term["source_revision"]


def test_apply_records_checksums_and_replays_without_second_merge():
    registry, source, target, plan = _prepared_pair()
    first = apply_identity_migration(plan, execute=True)
    second = apply_identity_migration(plan, execute=True)
    assert second["status"] == "unchanged"
    assert second["result_checksum"] == first["result_checksum"]
    assert registry.get(source["id"]) is None
    assert registry.get(target["id"]) is not None
    with session_scope() as session:
        records = session.scalars(select(AuditLog).where(AuditLog.action_type == "glossary_identity_migration")).all()
        assert len(records) == 1
        record = records[0]
        assert record.meta["plan_checksum"] == plan_checksum(plan)
        assert record.meta["result_checksum"] == first["result_checksum"]
        assert {row["id"] for row in record.old_value["terms"]} == {source["id"], target["id"]}
        assert {row["id"] for row in record.new_value["terms"]} == {target["id"]}


def test_replay_rejects_post_migration_metadata_change():
    registry, _source, target, plan = _prepared_pair()
    apply_identity_migration(plan, execute=True)
    # Even an old importer that fails to bump revision must make the plan stale.
    with session_scope() as session:
        session.get(DomainTerm, target["id"]).original_description = "Changed externally"
    with pytest.raises(ValueError, match="устарел|stale"):
        apply_identity_migration(plan, execute=True)
    assert registry.get(target["id"])["original_description"] == "Changed externally"


def test_backup_contains_rules_state_and_complete_term_metadata(tmp_path):
    from app.db.models import DomainTermTranslation, GlossaryInfotypeRule, GlossaryInfotypePrefix

    registry, source, _target, plan = _prepared_pair()
    with session_scope() as session:
        session.add(DomainTermTranslation(term_id=source["id"], locale="de", display_name="Quelle", source_revision=1,
                                          updated_by="translator", reviewed_by="reviewer"))
        rule = GlossaryInfotypeRule(name="Custom", number_from=0, number_to=999, created_by="owner", enabled=False)
        rule.prefixes_rel = [GlossaryInfotypePrefix(prefix="custom", normalized_prefix="custom", position=0)]
        session.add(rule)
    path = tmp_path / "metadata.json"
    apply_identity_migration(plan, execute=True, backup_path=path)
    backup = json.loads(path.read_text(encoding="utf-8"))
    assert backup["state"]["identities_ready"] is False
    assert backup["rules"][0]["created_by"] == "owner"
    assert backup["rules"][0]["prefixes"][0]["prefix"] == "custom"
    original = next(row for row in backup["terms"] if row["id"] == source["id"])
    assert original["translations"][0]["reviewed_by"] == "reviewer"
    assert "created_at" in original and "updated_by" in original
    assert registry.get(source["id"]) is None


def test_migration_audit_failure_rolls_back_metadata_and_readiness(monkeypatch):
    from app.services import audit

    term = GlossaryRegistry().create(None, "sap_infotype", "Personnel", infotype_number="0001",
                                    aliases=[GlossaryAliasInput("legacy")])
    state = _set_ready(False)
    original_record = audit.record_in_session

    def fail_migration(session, **kwargs):
        if kwargs["action_type"] == "glossary_identity_migration":
            raise RuntimeError("audit unavailable")
        return original_record(session, **kwargs)

    monkeypatch.setattr(audit, "record_in_session", fail_migration)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        apply_identity_migration({"alias_deletions": [{"term_id": term["id"], "alias_id": term["aliases"][0]["id"]}]}, execute=True)
    with session_scope() as session:
        assert session.get(DomainTermAlias, term["aliases"][0]["id"]) is not None
        assert not session.get(GlossaryState, 1).identities_ready
        assert session.get(GlossaryState, 1).revision == state["glossary_revision"]


def test_metadata_updates_and_multiple_merges_use_initial_versions():
    registry = GlossaryRegistry()
    terms = [registry.create(None, "business_term", name, aliases=[GlossaryAliasInput(name + " alias")])
             for name in ("First", "Second", "Target")]
    _set_ready(False)
    target = terms[-1]
    plan = {"alias_deletions": [{"term_id": target["id"], "version": target["version"],
                                "alias_id": target["aliases"][0]["id"]}],
            "merges": [{"source_term_id": source["id"], "target_term_id": target["id"],
                        "source_version": source["version"], "target_version": target["version"],
                        "request_id": str(uuid4()), "selections": {"original_name": "target"}}
                       for source in terms[:2]]}
    apply_identity_migration(plan, dry_run=True)
    apply_identity_migration(plan, execute=True)
    assert registry.get(target["id"])["version"] == target["version"] + 1
    assert apply_identity_migration(plan, execute=True)["status"] == "unchanged"


def test_dry_run_rejects_a_stale_initial_action_version():
    term = GlossaryRegistry().create(None, "sap_infotype", "Personnel", infotype_number="0001",
                                    aliases=[GlossaryAliasInput("legacy")])
    _set_ready(False)
    with pytest.raises(ValueError, match="Версия термина устарела"):
        apply_identity_migration({
            "term_updates": [{"term_id": term["id"], "version": term["version"], "infotype_number": "0002"}],
            "alias_deletions": [{"term_id": term["id"], "version": term["version"] + 1,
                                 "alias_id": term["aliases"][0]["id"]}],
        }, dry_run=True)


def test_plan_can_resolve_three_legacy_owners_of_one_literal():
    registry = GlossaryRegistry()
    terms = [registry.create(None, "business_term", name) for name in ("First", "Second", "Target")]
    with session_scope() as session:
        for term in terms:
            session.get(DomainTerm, term["id"]).original_name = "Legacy duplicate"
    _set_ready(False)
    plan = {"merges": [{"source_term_id": source["id"], "target_term_id": terms[-1]["id"],
                        "source_version": source["version"], "target_version": terms[-1]["version"],
                        "request_id": str(uuid4())} for source in terms[:2]]}
    apply_identity_migration(plan, dry_run=True)
    apply_identity_migration(plan, execute=True)
    assert registry.get(terms[0]["id"]) is None
    assert registry.get(terms[1]["id"]) is None
    assert registry.get(terms[-1]["id"])["original_name"] == "Legacy duplicate"


def test_incomplete_multi_owner_plan_leaves_all_metadata_and_state_unchanged():
    registry = GlossaryRegistry()
    terms = [registry.create(None, "business_term", name) for name in ("First", "Second", "Target")]
    with session_scope() as session:
        for term in terms:
            session.get(DomainTerm, term["id"]).original_name = "Legacy duplicate"
    state = _set_ready(False)
    plan = {"merges": [{"source_term_id": terms[0]["id"], "target_term_id": terms[-1]["id"],
                        "request_id": str(uuid4())}]}
    with pytest.raises(ValueError):
        apply_identity_migration(plan, execute=True)
    with session_scope() as session:
        assert len(session.scalars(select(DomainTerm)).all()) == 3
        assert session.get(GlossaryState, 1).revision == state["glossary_revision"]
        assert not session.get(GlossaryState, 1).identities_ready
        assert not session.scalars(select(AuditLog).where(AuditLog.action_type == "glossary_identity_migration")).all()
