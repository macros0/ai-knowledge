"""Serialized glossary write sessions shared by registry and rule services."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import select, update

from app.db.models import DomainTerm, GlossaryState
from app.db.session import session_scope
from app.services.reference_concurrency import bounded_reference_transaction
from app.services.glossary.normalization import GlossaryMigrationRequiredError


def initialize_glossary_state(session) -> GlossaryState:
    state = session.get(GlossaryState, 1)
    if state is None:
        # A database that already contains terms needs the offline identity
        # audit before normal writes/search can use the new namespace. Fresh
        # databases are immediately ready.
        has_terms = session.execute(select(DomainTerm.id).limit(1)).first() is not None
        state = GlossaryState(id=1, revision=0, identities_ready=not has_terms)
        session.add(state)
        session.flush()
    return state


@contextmanager
def glossary_write_session(*, allow_unready: bool = False, reference_keys=(), reference_context=None):
    """Open a transaction and serialize decisions on the singleton state row."""
    with session_scope() as session, bounded_reference_transaction(session):
        state = initialize_glossary_state(session)
        # A no-op update is portable across PostgreSQL and SQLite and holds the
        # writer row lock for the rest of this transaction.
        session.execute(
            update(GlossaryState)
            .where(GlossaryState.id == 1)
            .values(revision=GlossaryState.revision)
        )
        session.refresh(state)
        if not allow_unready and not state.identities_ready:
            raise GlossaryMigrationRequiredError("Миграция идентичностей глоссария ещё не завершена")
        from app.services.reference_mutation import guard_reference_writes
        session.info["glossary_reference_states"] = guard_reference_writes(session, reference_keys, reference_context)
        yield session, state


def bump_glossary_revision(session, state: GlossaryState) -> int:
    from app.services.reference_concurrency import bump_reference_revision
    from app.services.reference_edit_lock import invalidate_reference_lease
    from app.db.models import GlossaryInfotypeRule
    for key, row in session.info.get("glossary_reference_states", {}).items():
        bump_reference_revision(row)
        model = DomainTerm if key.resource_type == "glossary_term" else GlossaryInfotypeRule
        if session.get(model, int(key.resource_id)) is None:
            invalidate_reference_lease(row)
    state.revision += 1
    session.flush()
    return state.revision
