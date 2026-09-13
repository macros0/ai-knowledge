import pytest

from app.services.glossary.seed import load_seed, seed_glossary, validate_seed
from app.services.glossary.registry import GlossaryRegistry


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
