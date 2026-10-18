"""Repeat cross-connection writer contracts on the opt-in acceptance database."""
import pytest

from tests.test_reference_postgres_concurrency import reference_pg_db as _pg_fixture
from tests import test_reference_glossary_concurrency as glossary
from tests import test_reference_translation_concurrency as translations
from tests import test_reference_edit_lock as locks

reference_pg_db = _pg_fixture


@pytest.mark.parametrize("scenario", [
    "glossary_merge", "glossary_backfill_occupied", "glossary_acquire_during_llm", "tag_human_during_llm", "revoke", "release",
])
def test_writer_contracts_on_postgres(reference_pg_db, monkeypatch, scenario):
    if scenario == "glossary_merge":
        glossary.test_merge_cannot_bypass_a_source_lease_and_preserves_both_terms()
    elif scenario == "glossary_backfill_occupied":
        glossary.test_glossary_backfill_skips_a_lease_before_calling_llm(monkeypatch)
    elif scenario == "glossary_acquire_during_llm":
        glossary.test_lease_acquired_during_llm_prevents_machine_commit(monkeypatch)
    elif scenario == "revoke":
        locks.test_revoke_only_expected_epoch_and_acquire_many_rolls_back(monkeypatch)
    elif scenario == "release":
        locks.test_released_state_keeps_content_revision_and_monotonic_epoch(monkeypatch)
    else:
        translations.test_human_edit_during_llm_is_preserved(monkeypatch)
