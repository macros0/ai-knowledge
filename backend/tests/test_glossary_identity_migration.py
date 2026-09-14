import pytest
from uuid import uuid4

from scripts.audit_glossary_identities import audit_existing_glossary
from scripts.migrate_glossary_identities import _set_ready, apply_identity_migration, plan_checksum
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.types import GlossaryAliasInput
from app.db.models import GlossaryState
from app.db.session import session_scope


def test_audit_reports_name_alias_conflict():
    terms = (
        {"id": 1, "original_name": "Табель", "aliases": []},
        {"id": 2, "original_name": "Время", "aliases": [{"alias": "табель"}]},
    )
    report = audit_existing_glossary(terms)
    assert report["conflict_count"] == 1


def test_migration_requires_explicit_resolutions_and_is_deterministic():
    plan = {"conflicts": [{"key_value": "табель"}]}
    with pytest.raises(ValueError):
        apply_identity_migration(plan)
    resolved = {**plan, "resolutions": [{"source": 2, "target": 1}]}
    assert plan_checksum(resolved) == plan_checksum({"resolutions": [{"target": 1, "source": 2}], "conflicts": plan["conflicts"]})
    assert apply_identity_migration(resolved)["applied"] == 1


def test_apply_mode_executes_only_explicit_merge_resolution():
    registry = GlossaryRegistry()
    source = registry.create(None, "business_term", "Источник")
    target = registry.create(None, "business_term", "Цель")
    plan = {
        "conflicts": [{"key_value": "источник"}],
        "resolutions": [{
            "source": source["id"],
            "target": target["id"],
            "request_id": str(uuid4()),
            "actor_id": "migration-test",
            "selections": {"original_name": "target"},
        }],
    }
    result = apply_identity_migration(plan, execute=True, registry=registry)
    assert result["status"] == "applied"
    assert result["applied"] == 1
    assert registry.get(source["id"]) is None
    assert registry.get(target["id"])["original_name"] == "Цель"


def test_cli_apply_requires_prepared_state_and_restores_ready_after_atomic_merge():
    registry = GlossaryRegistry()
    source = registry.create(None, "business_term", "Legacy source")
    target = registry.create(None, "business_term", "Legacy target")
    with session_scope() as session:
        revision = session.get(GlossaryState, 1).revision
    prepared = _set_ready(False, expected_revision=revision)
    plan = {
        "expected_revision": prepared["glossary_revision"],
        "conflicts": [{"key_value": "legacy source"}],
        "resolutions": [{"source": source["id"], "target": target["id"], "request_id": str(uuid4()),
                         "selections": {"original_name": "target"}}],
    }
    result = apply_identity_migration(plan, execute=True)
    assert result["status"] == "applied"
    with session_scope() as session:
        assert session.get(GlossaryState, 1).identities_ready is True


def test_apply_mode_handles_term_updates_alias_deletions_and_rules_in_one_plan():
    registry = GlossaryRegistry()
    term = registry.create(
        "IT0003",
        "sap_infotype",
        "Legacy infotype",
        infotype_number="0003",
        aliases=[GlossaryAliasInput("старый код", auto_expand=True)],
    )
    alias_id = registry.get(term["id"])["aliases"][0]["id"]
    with session_scope() as session:
        revision = session.get(GlossaryState, 1).revision
    prepared = _set_ready(False, expected_revision=revision)
    plan = {
        "expected_revision": prepared["glossary_revision"],
        "term_updates": [{"term_id": term["id"], "version": term["version"], "infotype_number": "0003"}],
        "alias_deletions": [{"term_id": term["id"], "version": term["version"], "alias_id": alias_id}],
        "rules": [{"name": "PA", "number_from": 0, "number_to": 999, "prefixes": ["IT", "ИТ"]}],
    }
    result = apply_identity_migration(plan, execute=True)
    assert result["status"] == "applied"
    saved = registry.get(term["id"])
    assert saved["infotype_number"] == "0003"
    assert saved["aliases"] == []
    assert registry.get(term["id"]) is not None


def test_audit_reserves_disabled_rule_namespace_without_number_card():
    from app.services.glossary.types import InfotypeRuleSnapshot

    terms = ({"id": 1, "kind": "business_term", "original_name": "custom0123", "enabled": False},)
    rules = (InfotypeRuleSnapshot(1, "Custom", 0, 999, ("custom",), False, 1),)
    report = audit_existing_glossary(terms, rules)
    assert report["conflict_count"] == 1
    assert report["conflicts"][0]["number"] == "0123"


@pytest.mark.parametrize("kind,number", [("sap_infotype", None), ("business_term", "0001"), ("unknown", None)])
def test_migration_rejects_invalid_term_metadata_and_stays_unready(kind, number):
    from app.db.models import DomainTerm

    term = GlossaryRegistry().create(None, "business_term", "Legacy")
    with session_scope() as session:
        row = session.get(DomainTerm, term["id"])
        row.kind = kind
        row.infotype_number = number
    _set_ready(False)
    with pytest.raises(ValueError):
        apply_identity_migration({}, execute=True)
    with session_scope() as session:
        assert session.get(GlossaryState, 1).identities_ready is False


@pytest.mark.parametrize("case", ["reserved_literal", "overlapping_rules", "too_many_rules"])
def test_migration_validates_complete_rule_namespace_atomically(case):
    from app.db.models import GlossaryInfotypeRule
    from sqlalchemy import select

    from app.db.models import DomainTerm
    term = GlossaryRegistry().create(None, "business_term", "custom0123")
    with session_scope() as session:
        session.get(DomainTerm, term["id"]).enabled = False
    _set_ready(False)
    rules = [{"name": "Rule", "number_from": 0, "number_to": 999, "prefixes": ["custom"], "enabled": False}]
    if case == "overlapping_rules":
        rules = [{**rules[0], "prefixes": ["other"]}, {**rules[0], "prefixes": ["OTHER"]}]
    elif case == "too_many_rules":
        rules = [{**rules[0], "prefixes": [f"prefix{i}"]} for i in range(201)]
    with pytest.raises(ValueError):
        apply_identity_migration({"rules": rules}, execute=True)
    with session_scope() as session:
        assert session.get(GlossaryState, 1).identities_ready is False
        assert session.scalars(select(GlossaryInfotypeRule)).all() == []


def test_number_and_alias_metadata_changes_preserve_translation_revision():
    from app.db.models import DomainTermTranslation

    registry = GlossaryRegistry()
    term = registry.create(None, "sap_infotype", "Personnel", infotype_number="0001",
                           aliases=[GlossaryAliasInput("legacy name", auto_expand=True)])
    with session_scope() as session:
        session.add(DomainTermTranslation(term_id=term["id"], locale="de", display_name="Personal",
                                          description="Beschreibung", source_revision=term["source_revision"]))
    translations = registry.get(term["id"])["translations"]
    _set_ready(False)
    apply_identity_migration({
        "term_updates": [{"term_id": term["id"], "infotype_number": "0002"}],
        "alias_deletions": [{"term_id": term["id"], "alias_id": term["aliases"][0]["id"]}],
    }, execute=True)
    saved = registry.get(term["id"])
    assert saved["source_revision"] == term["source_revision"]
    assert saved["infotype_number"] == "0002"
    assert saved["aliases"] == []
    assert saved["translations"] == translations


def test_audit_cli_reads_disabled_database_rules_without_rules_file(monkeypatch, capsys):
    from app.db.models import GlossaryInfotypeRule, GlossaryInfotypePrefix
    from scripts.audit_glossary_identities import main
    import json

    from app.db.models import DomainTerm
    term = GlossaryRegistry().create(None, "business_term", "custom0123")
    with session_scope() as session:
        session.get(DomainTerm, term["id"]).enabled = False
    with session_scope() as session:
        rule = GlossaryInfotypeRule(name="Custom", number_from=0, number_to=999, enabled=False)
        rule.prefixes_rel = [GlossaryInfotypePrefix(prefix="custom", normalized_prefix="custom", position=0)]
        session.add(rule)
    monkeypatch.setattr("sys.argv", ["audit_glossary_identities"])
    assert main() == 1
    assert json.loads(capsys.readouterr().out)["conflict_count"] == 1


@pytest.mark.parametrize("collision", ["name", "alias", "number", "existing_rule"])
def test_apply_rejects_existing_namespace_collision_and_rolls_back_all_edits(collision):
    from app.db.models import DomainTerm, DomainTermAlias, GlossaryInfotypeRule, GlossaryInfotypePrefix
    from sqlalchemy import select

    registry = GlossaryRegistry()
    first = registry.create(None, "sap_infotype", "Personnel", infotype_number="0001",
                            aliases=[GlossaryAliasInput("legacy alias", auto_expand=True)])
    second = registry.create(None, "sap_infotype", "Other", infotype_number="0002")
    with session_scope() as session:
        row = session.get(DomainTerm, second["id"])
        row.enabled = False
        if collision == "name":
            row.original_name = "PERSONNEL"
        elif collision == "alias":
            session.add(DomainTermAlias(term_id=second["id"], alias="Personnel", normalized_alias="personnel"))
        elif collision == "number":
            row.infotype_number = "0001"
        else:
            row.original_name = "custom0001"
            rule = GlossaryInfotypeRule(name="Custom", number_from=0, number_to=999, enabled=False)
            rule.prefixes_rel = [GlossaryInfotypePrefix(prefix="custom", normalized_prefix="custom", position=0)]
            session.add(rule)
    prepared = _set_ready(False)
    plan = {"expected_revision": prepared["glossary_revision"],
            "alias_deletions": [{"term_id": first["id"], "alias_id": first["aliases"][0]["id"]}]}
    with pytest.raises(ValueError):
        apply_identity_migration(plan, execute=True)
    with session_scope() as session:
        assert session.get(GlossaryState, 1).identities_ready is False
        assert session.get(GlossaryState, 1).revision == prepared["glossary_revision"]
        assert session.scalar(select(DomainTermAlias.id).where(
            DomainTermAlias.id == first["aliases"][0]["id"])) is not None


@pytest.mark.parametrize("case", ["missing_number", "invalid_number", "rule_overlap", "rule_limit"])
def test_audit_cli_rejects_invalid_namespace_with_structured_report(case, monkeypatch, capsys):
    import json
    from app.services.glossary.types import InfotypeRuleSnapshot
    from scripts import audit_glossary_identities as audit_cli

    terms = ()
    rules = ()
    if case in ("missing_number", "invalid_number"):
        terms = ({"id": 1, "kind": "sap_infotype", "original_name": "Personnel",
                  "infotype_number": None if case == "missing_number" else "１２３４"},)
    elif case == "rule_overlap":
        rules = (InfotypeRuleSnapshot(1, "First", 0, 100, ("custom",), False),
                 InfotypeRuleSnapshot(2, "Second", 100, 200, ("CUSTOM",)))
    else:
        rules = tuple(InfotypeRuleSnapshot(index + 1, f"Rule {index}", 0, 9999, (f"prefix{index}",))
                      for index in range(201))
    monkeypatch.setattr(audit_cli, "load_existing_namespace", lambda: (terms, rules))
    monkeypatch.setattr("sys.argv", ["audit_glossary_identities"])
    assert audit_cli.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is False
    assert report["conflict_count"] == len(report["conflicts"])
    if case == "rule_overlap":
        assert report["conflicts"][0]["key_kind"] == "rule_overlap"
        assert report["validation_errors"] == []
    else:
        assert report["validation_errors"]


def test_audit_accepts_200_valid_rules():
    from app.services.glossary.types import InfotypeRuleSnapshot

    rules = tuple(InfotypeRuleSnapshot(index + 1, f"Rule {index}", 0, 9999, (f"prefix{index}",))
                  for index in range(200))
    report = audit_existing_glossary((), rules)
    assert report["valid"] is True
    assert report["validation_errors"] == []
    assert report["conflict_count"] == 0
