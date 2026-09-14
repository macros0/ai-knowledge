"""Revision-aware detached glossary snapshot for query planning."""
from __future__ import annotations

import threading

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.db.models import GlossaryInfotypeRule, GlossaryState
from app.db.session import get_engine, session_scope
from app.services.glossary.registry import get_glossary_registry
from app.services.glossary.types import GlossarySnapshot, InfotypeRuleSnapshot
from app.services.glossary.normalization import GlossaryMigrationRequiredError

_LOCK = threading.RLock()
_ENGINE = None
_REVISION: int | None = None
_CACHE: GlossarySnapshot | None = None


def invalidate_snapshot_cache() -> None:
    global _ENGINE, _REVISION, _CACHE
    with _LOCK:
        _ENGINE = None
        _REVISION = None
        _CACHE = None


def _snapshot_from_session(session, revision: int) -> GlossarySnapshot:
    """Materialize detached terms and rules in the caller's transaction."""
    terms = get_glossary_registry()._snapshot_from_session(session, include_disabled=True)
    rules = session.execute(
        select(GlossaryInfotypeRule)
        .where(GlossaryInfotypeRule.enabled.is_(True))
        .options(selectinload(GlossaryInfotypeRule.prefixes_rel))
        .order_by(GlossaryInfotypeRule.id)
    ).scalars().all()
    return GlossarySnapshot(
        revision=revision,
        terms=terms,
        rules=tuple(
            InfotypeRuleSnapshot(
                rule_id=rule.id,
                name=rule.name,
                number_from=rule.number_from,
                number_to=rule.number_to,
                prefixes=tuple(item.prefix for item in sorted(rule.prefixes_rel, key=lambda item: item.position)),
                enabled=rule.enabled,
                version=rule.version,
            )
            for rule in rules
        ),
    )


def load_glossary_snapshot() -> GlossarySnapshot:
    """Read a stable revision of terms and enabled user rules without ORM leaks."""
    global _ENGINE, _REVISION, _CACHE
    engine = get_engine()
    for _attempt in range(3):
        with session_scope() as session:
            state = session.get(GlossaryState, 1)
            revision = int(state.revision) if state is not None else 0
            if state is not None and not state.identities_ready:
                raise GlossaryMigrationRequiredError("Миграция идентичностей глоссария ещё не завершена")
            with _LOCK:
                if _ENGINE is engine and _REVISION == revision and _CACHE is not None:
                    return _CACHE
            # Build terms in this same read transaction.  Calling the registry
            # snapshot here would introduce a second session and could return
            # a process-local cache from an older revision after another
            # worker committed a glossary mutation.
            snapshot = _snapshot_from_session(session, revision)
            revision_after = session.execute(
                select(GlossaryState.revision).where(GlossaryState.id == 1)
            ).scalar_one_or_none()
            if int(revision_after or 0) != revision:
                continue
        with _LOCK:
            _ENGINE = engine
            _REVISION = revision
            _CACHE = snapshot
        return snapshot
    # Match the writer's portable state-row lock. Acquire it before any reads:
    # PostgreSQL locks this row; SQLite reserves its writer transaction. No
    # revision, timestamp, identity readiness or audit entry is changed.
    with session_scope() as session:
        session.execute(
            update(GlossaryState)
            .where(GlossaryState.id == 1)
            .values(revision=GlossaryState.revision, updated_at=GlossaryState.updated_at)
            .execution_options(synchronize_session=False)
        )
        state = session.get(GlossaryState, 1)
        if state is None or not state.identities_ready:
            raise GlossaryMigrationRequiredError("Миграция идентичностей глоссария ещё не завершена")
        revision = int(state.revision)
        with _LOCK:
            if _ENGINE is engine and _REVISION == revision and _CACHE is not None:
                return _CACHE
        snapshot = _snapshot_from_session(session, revision)
    with _LOCK:
        _ENGINE = engine
        _REVISION = revision
        _CACHE = snapshot
    return snapshot
