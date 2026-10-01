from app.services.glossary.seed import load_seed, seed_glossary, validate_seed
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.types import GlossaryAliasInput
import pytest


def test_seed_file_contains_only_valid_verified_entries():
    entries = load_seed()

    assert {entry["canonical"] for entry in entries} >= {"IT0003", "PA20"}
    assert len(entries) <= 15
    validate_seed(entries)
    assert all(entry["canonical_locale"] for entry in entries)
    assert all("auto_expand" in alias and "search_enabled" in alias for entry in entries for alias in entry["aliases"])


def test_infotype_seed_does_not_duplicate_structural_russian_form():
    term = next(entry for entry in load_seed() if entry["canonical"] == "IT0003")
    assert "инфотип 3" not in {alias["alias"] for alias in term["aliases"]}


def test_seed_dry_run_is_read_only():
    registry = GlossaryRegistry()

    report = seed_glossary(registry, load_seed(), apply=False)

    assert report["created"] == 0
    assert report["would_create"] >= 2
    assert registry.list() == []


def test_seed_apply_is_idempotent_and_does_not_reactivate_terms():
    registry = GlossaryRegistry()
    entries = load_seed()

    first = seed_glossary(registry, entries, apply=True)
    second = seed_glossary(registry, entries, apply=True)

    assert first["created"] == len(entries)
    assert second["created"] == 0
    assert second["unchanged"] == len(entries)
    assert len(registry.list()) == len(entries)

    term = registry.list()[0]
    registry.update(term["id"], term["version"], enabled=False, updated_by="test")
    third = seed_glossary(registry, entries, apply=True)
    assert third["unchanged"] == len(entries)
    assert registry.get(term["id"])["enabled"] is False


def test_seed_reports_conflict_without_overwriting_existing_source():
    registry = GlossaryRegistry()
    entries = load_seed()
    first = entries[0]
    registry.create(
        first["canonical"],
        first["kind"],
        "Locally edited name",
        first.get("original_description"),
        first["canonical_locale"],
    )

    report = seed_glossary(registry, entries, apply=True)

    assert report["conflict"] == 1
    assert registry.list()[0]["original_name"] == "Locally edited name"


def test_seed_allows_alias_matching_a_technical_identifier():
    entries = load_seed()
    entries[1]["canonical"] = "INTERNAL_ONLY"
    entries[0]["aliases"].append({"alias": entries[1]["canonical"], "auto_expand": False, "search_enabled": False})

    validate_seed(entries)


def test_seed_rolls_back_entire_batch_on_existing_alias_conflict():
    registry = GlossaryRegistry()
    owner = registry.create(None, 'business_term', 'Existing owner', aliases=[GlossaryAliasInput('Reserved form')])
    entries = [dict(canonical=key, kind='business_term', original_name=name,
                    canonical_locale='en', aliases=[])
               for key, name in [('FIRST_NEW', 'First new'), ('SECOND_NEW', 'Reserved form')]]
    with pytest.raises(ValueError):
        seed_glossary(registry, entries, apply=True)
    assert [item['id'] for item in registry.list()] == [owner['id']]


def test_initial_rules_seed_fresh_glossary_once_and_preserve_admin_edits():
    from app.services.glossary import seed
    from app.services.glossary.rule_registry import GlossaryRuleRegistry

    assert seed.ensure_initial_rules() == 4
    registry = GlossaryRuleRegistry()
    rules = registry.list()
    assert [(rule['name'], rule['number_from'], rule['number_to'], rule['enabled'])
            for rule in rules] == [
        ('PA Infotepe', 0, 999, True), ('OM infotipe', 1000, 1999, True),
        ('PT infotipe', 2000, 2999, True), ('PA infotipe', 3000, 8999, True),
    ]
    assert 'it' in rules[0]['prefixes'] and 'it ' in rules[0]['prefixes']
    assert 'инфотипу ' in rules[0]['prefixes']
    assert 'hrp' in rules[1]['prefixes'] and 'hrt' in rules[1]['prefixes']
    assert 'pa' not in rules[1]['prefixes']
    registry.update(rules[0]['id'], rules[0]['version'], enabled=False,
                    prefixes=['custom '], actor_id='admin')
    registry.delete(rules[1]['id'], rules[1]['version'], actor_id='admin')
    before = registry.list()

    assert seed.ensure_initial_rules() == 0
    assert registry.list() == before
    assert GlossaryRegistry().list() == []


def test_initial_rules_do_not_repopulate_glossary_after_all_rules_deleted():
    from app.services.glossary import seed
    from app.services.glossary.rule_registry import GlossaryRuleRegistry

    seed.ensure_initial_rules()
    registry = GlossaryRuleRegistry()
    for rule in registry.list():
        registry.delete(rule['id'], rule['version'], actor_id='admin')
    assert seed.ensure_initial_rules() == 0
    assert registry.list() == []


def test_initial_rules_leave_existing_glossary_untouched():
    from app.services.glossary import seed
    from app.services.glossary.rule_registry import GlossaryRuleRegistry

    GlossaryRegistry().create(None, 'business_term', 'Existing term')
    assert seed.ensure_initial_rules() == 0
    assert GlossaryRuleRegistry().list() == []


def test_default_rules_explicit_seed_is_read_only_then_idempotent():
    from app.services.glossary import seed
    from app.services.glossary.rule_registry import GlossaryRuleRegistry

    registry = GlossaryRegistry()
    rules = seed.load_seed_rules()
    preview = seed_glossary(registry, [], rules=rules)
    assert preview['rules_would_create'] == 4 and preview['rules_created'] == 0
    assert GlossaryRuleRegistry().list() == []
    first = seed_glossary(registry, [], rules=rules, apply=True)
    second = seed_glossary(registry, [], rules=rules, apply=True)
    assert first['rules_created'] == 4
    assert second['rules_created'] == 0 and second['rules_unchanged'] == 4


def test_builtin_term_seed_is_compatible_with_initial_rules():
    from app.services.glossary import seed
    from app.services.glossary.expansion import prepare_query

    registry = GlossaryRegistry()
    entries = load_seed()
    report = seed_glossary(registry, entries, rules=seed.load_seed_rules(), apply=False)
    assert report['conflict'] == 0
    assert report['would_create'] == len(entries)
    seed_glossary(registry, entries, rules=seed.load_seed_rules(), apply=True)
    result = prepare_query('IT0003', ui_locale='en', enabled=True)
    assert result.strict_groups


def test_custom_term_seed_does_not_inject_default_rules(tmp_path, monkeypatch):
    import json
    from scripts.seed_glossary import main
    from app.services.glossary.rule_registry import GlossaryRuleRegistry

    path = tmp_path / 'custom.json'
    path.write_text(json.dumps([dict(canonical='CUSTOM', kind='business_term',
                                    original_name='Custom', canonical_locale='en', aliases=[])]),
                    encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['seed_glossary.py', '--path', str(path), '--apply'])
    assert main() == 0
    assert [term['original_name'] for term in GlossaryRegistry().list()] == ['Custom']
    assert GlossaryRuleRegistry().list() == []
