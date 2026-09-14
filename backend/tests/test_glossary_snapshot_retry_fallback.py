"""A busy writer cannot exhaust the snapshot reader's optimistic retries."""
from dataclasses import replace
from datetime import timezone

import pytest
from sqlalchemy import event

from app.db.models import DomainTerm, GlossaryInfotypeRule, GlossaryState
from app.db.session import get_engine, session_scope
from app.services.glossary import snapshot as snapshots
from app.services.glossary.normalization import GlossaryMigrationRequiredError
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.rule_registry import GlossaryRuleRegistry


def _force_three_writes(monkeypatch, *, become_unready=False):
    registry = GlossaryRegistry()
    term = registry.create(None, "business_term", "Initial name")
    rule = GlossaryRuleRegistry().create(name="Rule", number_from=0, number_to=999, prefixes=("custom",))
    snapshots.invalidate_snapshot_cache()
    previous = snapshots.load_glossary_snapshot()
    snapshots.invalidate_snapshot_cache()
    original = registry._snapshot_from_session
    observed = {"calls": 0}

    def interrupted_read(session, **kwargs):
        observed["calls"] += 1
        terms = original(session, **kwargs)
        if observed["calls"] <= 3:
            with session_scope() as writer:
                count = observed["calls"]
                writer.get(DomainTerm, term["id"]).original_name = f"Changed {count}"
                writer.get(GlossaryInfotypeRule, rule["id"]).name = f"Rule {count}"
                state = writer.get(GlossaryState, 1)
                state.revision += 1
                if become_unready and count == 3:
                    state.identities_ready = False
                writer.flush()
                observed["revision"] = state.revision
                observed["updated_at"] = state.updated_at
                if become_unready and count == 3:
                    # A cache from another local reader must not bypass the
                    # readiness gate on the locked fallback path.
                    monkeypatch.setattr(snapshots, "_ENGINE", get_engine())
                    monkeypatch.setattr(snapshots, "_REVISION", state.revision)
                    monkeypatch.setattr(snapshots, "_CACHE", replace(previous, revision=state.revision))
        return terms

    monkeypatch.setattr(registry, "_snapshot_from_session", interrupted_read)
    monkeypatch.setattr(snapshots, "get_glossary_registry", lambda: registry)
    return observed


def test_three_revision_retries_fall_back_to_locked_coherent_snapshot(monkeypatch):
    observed = _force_three_writes(monkeypatch)
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = snapshots.load_glossary_snapshot()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert observed["calls"] == 4
    assert result.revision == observed["revision"]
    assert result.terms[0].original_name == "Changed 3"
    assert result.rules[0].name == "Rule 3"
    # Three real test writers and one portable fallback lock.
    locks = [statement for statement in statements if statement.upper().startswith("UPDATE GLOSSARY_STATE")]
    assert len(locks) == 4
    with session_scope() as session:
        state = session.get(GlossaryState, 1)
        assert state.revision == observed["revision"]
        def utc(value):
            return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        assert utc(state.updated_at) == utc(observed["updated_at"])
    assert snapshots.load_glossary_snapshot() is result
    assert observed["calls"] == 4


def test_fallback_checks_readiness_before_returning_a_matching_cache(monkeypatch):
    observed = _force_three_writes(monkeypatch, become_unready=True)
    with pytest.raises(GlossaryMigrationRequiredError):
        snapshots.load_glossary_snapshot()
    assert observed["calls"] == 3
    with session_scope() as session:
        assert session.get(GlossaryState, 1).revision == observed["revision"]
        assert not session.get(GlossaryState, 1).identities_ready
