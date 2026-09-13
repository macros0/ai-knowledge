"""Transactional CRUD and snapshot reads for the domain glossary."""
from __future__ import annotations

from datetime import datetime, timezone
import threading
from uuid import uuid4
from typing import Sequence

from sqlalchemy import delete, event, select, update
from sqlalchemy.orm import object_session, selectinload
from sqlalchemy.exc import IntegrityError

from app.db.models import DomainTerm, DomainTermAlias
from app.db.session import get_engine, session_scope
from app.services import audit
from app.services.glossary.normalization import (
    GlossaryValidationError,
    SUPPORTED_KINDS,
    normalize_alias,
    normalize_canonical,
    validate_alias_options,
    validate_locale,
)
from app.services.glossary.types import (
    GlossaryAliasInput,
    GlossaryAliasSnapshot,
    GlossaryTermSnapshot,
    GlossaryTranslationSnapshot,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_UNSET = object()
_SNAPSHOT_LOCK = threading.RLock()
_SNAPSHOT_ENGINE = None
_SNAPSHOT_CACHE: tuple[GlossaryTermSnapshot, ...] | None = None
_SNAPSHOT_REVISION = 0


def invalidate_snapshot_cache() -> None:
    """Drop the process-local read snapshot after a glossary mutation."""
    global _SNAPSHOT_CACHE, _SNAPSHOT_ENGINE, _SNAPSHOT_REVISION
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE = None
        _SNAPSHOT_ENGINE = None
        _SNAPSHOT_REVISION += 1


def _invalidate_on_commit(session) -> None:
    # Invalidate after commit, so another reader cannot cache pre-commit aliases
    # (especially the other side of a newly created or resolved duplicate).
    event.listen(session, "after_commit", lambda _: invalidate_snapshot_cache(), once=True)


class GlossaryNotFoundError(LookupError):
    pass


class GlossaryCanonicalConflictError(ValueError):
    pass


class GlossaryAliasConflictError(ValueError):
    pass


class GlossaryVersionConflictError(ValueError):
    pass


def _clean_name(value: str) -> str:
    if not isinstance(value, str):
        raise GlossaryValidationError("Название термина должно быть строкой")
    value = value.strip()
    if not value or len(value) > 256:
        raise GlossaryValidationError("Название термина должно быть непустым и не длиннее 256 символов")
    return value


def _clean_description(value: str | None) -> str | None:
    if value is not None and len(value) > 8000:
        raise GlossaryValidationError("Описание термина не может быть длиннее 8000 символов")
    return value


def _alias_dict(alias: DomainTermAlias) -> dict:
    return {
        "id": alias.id,
        "term_id": alias.term_id,
        "alias": alias.alias,
        "normalized_alias": alias.normalized_alias,
        "locale": alias.locale,
        "auto_expand": alias.auto_expand,
        "search_enabled": alias.search_enabled,
        "created_at": alias.created_at,
        "updated_at": alias.updated_at,
        "created_by": alias.created_by,
        "updated_by": alias.updated_by,
    }


def _alias_conflict_map(session, keys: Sequence[str] | None = None) -> dict:
    """Derive both sides from saved aliases, across all locales and statuses."""
    stmt = select(
        DomainTermAlias.id, DomainTermAlias.term_id, DomainTermAlias.alias,
        DomainTermAlias.normalized_alias, DomainTerm.original_name,
        DomainTerm.canonical_locale, DomainTerm.enabled,
    ).join(DomainTerm, DomainTerm.id == DomainTermAlias.term_id)
    if keys is not None:
        if not keys:
            return {}
        stmt = stmt.where(DomainTermAlias.normalized_alias.in_(keys))
    groups = {}
    for row in session.execute(stmt.order_by(DomainTermAlias.id)):
        groups.setdefault(row.normalized_alias, []).append(row)
    result = {}
    for rows in groups.values():
        for row in rows:
            conflicts = [
                {"alias_id": row.id, "alias": row.alias,
                 "normalized_alias": row.normalized_alias, "term_id": other.term_id,
                 "name": other.original_name, "locale": other.canonical_locale,
                 "enabled": other.enabled}
                for other in rows if other.term_id != row.term_id
            ]
            if conflicts:
                result[row.id] = conflicts
    return result


def _term_dict(term: DomainTerm, conflict_map: dict | None = None) -> dict:
    if conflict_map is None:
        session = object_session(term)
        conflict_map = _alias_conflict_map(
            session, [alias.normalized_alias for alias in term.aliases_rel]
        ) if session is not None else {}
    conflicts = [item for alias in term.aliases_rel for item in conflict_map.get(alias.id, [])]
    return {
        "id": term.id,
        "canonical": term.canonical,
        "kind": term.kind,
        "original_name": term.original_name,
        "original_description": term.original_description,
        "canonical_locale": term.canonical_locale,
        "enabled": term.enabled,
        "version": term.version,
        "source_revision": term.source_revision,
        "created_at": term.created_at,
        "updated_at": term.updated_at,
        "created_by": term.created_by,
        "updated_by": term.updated_by,
        "has_duplicates": bool(conflicts),
        "alias_conflicts": conflicts,
        "aliases": [_alias_dict(alias) for alias in sorted(term.aliases_rel, key=lambda item: item.id)],
        "translations": [
            {
                "term_id": item.term_id,
                "locale": item.locale,
                "display_name": item.display_name,
                "description": item.description,
                "source_revision": item.source_revision,
                "version": item.version,
                "is_machine_translated": item.is_machine_translated,
                "reviewed_by": item.reviewed_by,
                "reviewed_at": item.reviewed_at,
                "updated_by": item.updated_by,
                "updated_at": item.updated_at,
            }
            for item in sorted(term.translations_rel, key=lambda value: value.locale)
        ],
    }


def _audit_alias_dict(alias: DomainTermAlias) -> dict:
    value = _alias_dict(alias)
    value["created_at"] = audit.iso_or_str(value["created_at"])
    value["updated_at"] = audit.iso_or_str(value["updated_at"])
    return value


def _audit_term_dict(term: DomainTerm) -> dict:
    value = _term_dict(term)
    value["created_at"] = audit.iso_or_str(value["created_at"])
    value["updated_at"] = audit.iso_or_str(value["updated_at"])
    value["aliases"] = [_audit_alias_dict(alias) for alias in term.aliases_rel]
    for translation in value["translations"]:
        translation["reviewed_at"] = audit.iso_or_str(translation["reviewed_at"])
        translation["updated_at"] = audit.iso_or_str(translation["updated_at"])
    return value


class GlossaryRegistry:
    """All mutations are single transactions with an in-transaction audit."""

    def create(
        self,
        canonical: str | None,
        kind: str,
        original_name: str,
        original_description: str | None = None,
        canonical_locale: str = "und",
        created_by: str | None = None,
        *,
        aliases: Sequence[GlossaryAliasInput] = (),
        ip_address: str | None = None,
        audit_username: str | None = None,
    ) -> dict:
        # Legacy import identifiers remain supported, but UI creates opaque keys.
        if kind not in SUPPORTED_KINDS:
            raise GlossaryValidationError(f"Неизвестный вид термина: {kind}")
        canonical = normalize_canonical(canonical, kind) if canonical else f"TERM_{uuid4().hex.upper()}"
        original_name = _clean_name(original_name)
        original_description = _clean_description(original_description)
        canonical_locale = validate_locale(canonical_locale)

        prepared_aliases = [
            self._prepare_alias(item, kind=kind, created_by=created_by) for item in aliases
        ]
        all_aliases = prepared_aliases
        normalized = [item["normalized_alias"] for item in all_aliases]
        if len(set(normalized)) != len(normalized):
            raise GlossaryAliasConflictError("Алиасы термина дублируются после нормализации")

        with session_scope() as session:
            if session.execute(
                select(DomainTerm.id).where(DomainTerm.canonical == canonical)
            ).scalar_one_or_none() is not None:
                raise GlossaryCanonicalConflictError(f"Код уже занят: {canonical}")
            term = DomainTerm(
                canonical=canonical,
                kind=kind,
                original_name=original_name,
                original_description=original_description,
                canonical_locale=canonical_locale,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(term)
            try:
                session.flush()
            except IntegrityError as exc:
                raise GlossaryCanonicalConflictError(f"Код уже занят: {canonical}") from exc

            for item in all_aliases:
                session.add(
                    DomainTermAlias(
                        term_id=term.id,
                        alias=item["alias"],
                        normalized_alias=item["normalized_alias"],
                        locale=item["locale"],
                        auto_expand=item["auto_expand"],
                        search_enabled=item["search_enabled"],
                        created_by=item["created_by"],
                        updated_by=item["created_by"],
                    )
                )
            try:
                session.flush()
            except IntegrityError as exc:
                raise GlossaryAliasConflictError("Алиас уже есть в этой карточке") from exc

            audit.record_in_session(
                session,
                action_type=audit.GLOSSARY_TERM_CREATE,
                user_id=created_by,
                username=audit_username or created_by,
                target_type=audit.TARGET_GLOSSARY_TERM,
                target_id=str(term.id),
                new_value=_audit_term_dict(term),
                ip_address=ip_address,
            )
            _invalidate_on_commit(session)
            return _term_dict(term)

    def get(self, term_id: int) -> dict | None:
        with session_scope() as session:
            term = session.execute(
                select(DomainTerm)
                .where(DomainTerm.id == term_id)
                .options(
                    selectinload(DomainTerm.aliases_rel),
                    selectinload(DomainTerm.translations_rel),
                )
            ).scalar_one_or_none()
            return _term_dict(term) if term is not None else None

    def list(self, *, enabled: bool | None = None) -> list[dict]:
        with session_scope() as session:
            stmt = select(DomainTerm).options(
                selectinload(DomainTerm.aliases_rel),
                selectinload(DomainTerm.translations_rel),
            ).order_by(DomainTerm.canonical)
            if enabled is not None:
                stmt = stmt.where(DomainTerm.enabled == enabled)
            conflicts = _alias_conflict_map(session)
            return [_term_dict(item, conflicts) for item in session.execute(stmt).scalars().all()]

    def check_aliases(self, aliases: Sequence[str], *, term_id: int | None = None) -> list[dict]:
        """Read-only preflight; the saved duplicate state is always authoritative."""
        keys = {normalize_alias(alias) for alias in aliases if alias.strip()}
        if not keys:
            return []
        with session_scope() as session:
            stmt = select(DomainTermAlias, DomainTerm).join(DomainTerm).where(
                DomainTermAlias.normalized_alias.in_(keys)
            )
            if term_id is not None:
                stmt = stmt.where(DomainTerm.id != term_id)
            return [
                {"alias_id": alias.id, "alias": alias.alias,
                 "normalized_alias": alias.normalized_alias, "term_id": term.id,
                 "name": term.original_name, "locale": term.canonical_locale,
                 "enabled": term.enabled}
                for alias, term in session.execute(stmt.order_by(DomainTerm.id, DomainTermAlias.id))
            ]

    def list_page(
        self,
        *,
        q: str | None = None,
        kind: str | None = None,
        enabled: bool | None = None,
        alias_locale: str | None = None,
        needs_review: bool | None = None,
        target_locale: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Return a deterministic, paginated admin view of the registry."""
        with session_scope() as session:
            stmt = select(DomainTerm).options(
                selectinload(DomainTerm.aliases_rel),
                selectinload(DomainTerm.translations_rel),
            ).order_by(DomainTerm.canonical)
            if kind is not None:
                stmt = stmt.where(DomainTerm.kind == kind)
            if enabled is not None:
                stmt = stmt.where(DomainTerm.enabled == enabled)
            rows = session.execute(stmt).scalars().all()

            needle = q.strip().casefold() if q else None
            filtered: list[DomainTerm] = []
            for term in rows:
                translations = list(term.translations_rel)
                if needle:
                    values = [term.original_name, term.original_description or ""]
                    values.extend(alias.alias for alias in term.aliases_rel)
                    values.extend(item.display_name for item in translations)
                    if not any(needle in value.casefold() for value in values):
                        continue
                if alias_locale is not None and not any(
                    alias.locale == alias_locale for alias in term.aliases_rel
                ):
                    continue
                if target_locale is not None and not any(
                    item.locale == target_locale for item in translations
                ):
                    continue
                if needs_review is not None:
                    has_review = any(
                        item.source_revision != term.source_revision
                        or item.is_machine_translated
                        for item in translations
                    )
                    if needs_review != has_review:
                        continue
                filtered.append(term)

            page = filtered[offset : offset + limit]
            conflicts = _alias_conflict_map(session)
            return {
                "terms": [_term_dict(item, conflicts) for item in page],
                "total": len(filtered),
                "limit": limit,
                "offset": offset,
            }

    def pending_counts(self, locale: str) -> dict[str, int]:
        """Count translation states for active terms without mutating anything."""
        with session_scope() as session:
            rows = session.execute(
                select(DomainTerm).where(DomainTerm.enabled.is_(True)).options(
                    selectinload(DomainTerm.translations_rel)
                )
            ).scalars().all()
            counts = {
                "missing": 0,
                "machine_unreviewed": 0,
                "human_stale": 0,
                "machine_stale": 0,
                "eligible_for_backfill": 0,
            }
            for term in rows:
                if term.canonical_locale == locale:
                    continue
                translation = next(
                    (item for item in term.translations_rel if item.locale == locale), None
                )
                if translation is None:
                    counts["missing"] += 1
                    counts["eligible_for_backfill"] += 1
                elif translation.source_revision != term.source_revision:
                    counts[
                        "machine_stale" if translation.is_machine_translated else "human_stale"
                    ] += 1
                    if translation.is_machine_translated:
                        counts["eligible_for_backfill"] += 1
                elif translation.is_machine_translated:
                    counts["machine_unreviewed"] += 1
                    counts["eligible_for_backfill"] += 1
            return counts

    def snapshot(self) -> tuple[GlossaryTermSnapshot, ...]:
        """Read an eager, detached snapshot for one query-plan build.

        Search must never retain ORM objects or a database session while it is
        embedding, querying Qdrant, or calling the LLM.  The relationships are
        therefore loaded explicitly and copied into frozen DTOs here.
        """
        global _SNAPSHOT_CACHE, _SNAPSHOT_ENGINE
        engine = get_engine()
        with _SNAPSHOT_LOCK:
            if _SNAPSHOT_ENGINE is engine and _SNAPSHOT_CACHE is not None:
                return _SNAPSHOT_CACHE
            revision = _SNAPSHOT_REVISION

        with session_scope() as session:
            stmt = (
                select(DomainTerm)
                .where(DomainTerm.enabled.is_(True))
                .options(
                    selectinload(DomainTerm.aliases_rel),
                    selectinload(DomainTerm.translations_rel),
                )
                .order_by(DomainTerm.id)
            )
            terms = session.execute(stmt).scalars().all()
            conflicts = _alias_conflict_map(session)
            snapshot = tuple(
                GlossaryTermSnapshot(
                    term_id=term.id,
                    canonical=term.canonical,
                    kind=term.kind,
                    original_name=term.original_name,
                    original_description=term.original_description,
                    canonical_locale=term.canonical_locale,
                    enabled=term.enabled,
                    version=term.version,
                    source_revision=term.source_revision,
                    aliases=tuple(
                        GlossaryAliasSnapshot(
                            alias_id=alias.id,
                            alias=alias.alias,
                            normalized_alias=alias.normalized_alias,
                            locale=alias.locale,
                            auto_expand=alias.auto_expand,
                            search_enabled=alias.search_enabled,
                            is_conflicting=alias.id in conflicts,
                        )
                        for alias in sorted(term.aliases_rel, key=lambda item: item.id)
                    ),
                    translations=tuple(
                        GlossaryTranslationSnapshot(
                            locale=translation.locale,
                            display_name=translation.display_name,
                            source_revision=translation.source_revision,
                            is_machine_translated=translation.is_machine_translated,
                        )
                        for translation in term.translations_rel
                    ),
                )
                for term in terms
            )
        with _SNAPSHOT_LOCK:
            if revision == _SNAPSHOT_REVISION:
                _SNAPSHOT_ENGINE = engine
                _SNAPSHOT_CACHE = snapshot
            return snapshot

    def update(
        self,
        term_id: int,
        version: int,
        *,
        enabled: bool | None = None,
        original_name: str | None = None,
        original_description: str | None | object = _UNSET,
        canonical_locale: str | None = None,
        updated_by: str | None = None,
        ip_address: str | None = None,
        audit_username: str | None = None,
    ) -> dict:
        with session_scope() as session:
            term = session.get(DomainTerm, term_id)
            if term is None:
                raise GlossaryNotFoundError(f"Термин не найден: {term_id}")

            values: dict = {}
            old_value = _audit_term_dict(term)
            source_changed = False
            if enabled is not None and enabled != term.enabled:
                values["enabled"] = enabled
            if original_name is not None:
                value = _clean_name(original_name)
                if value != term.original_name:
                    values["original_name"] = value
                    source_changed = True
            if original_description is not _UNSET:
                value = _clean_description(original_description)
                if value != term.original_description:
                    values["original_description"] = value
                    source_changed = True
            if canonical_locale is not None:
                value = validate_locale(canonical_locale)
                if value != term.canonical_locale:
                    values["canonical_locale"] = value
                    source_changed = True
            if not values:
                return _term_dict(term)

            values["version"] = DomainTerm.version + 1
            values["updated_at"] = _utcnow()
            values["updated_by"] = updated_by
            if source_changed:
                values["source_revision"] = DomainTerm.source_revision + 1

            outcome = session.execute(
                update(DomainTerm)
                .where(DomainTerm.id == term_id, DomainTerm.version == version)
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if outcome.rowcount == 0:
                raise GlossaryVersionConflictError(f"Версия термина устарела: {term_id}")

            current = session.get(DomainTerm, term_id, populate_existing=True)
            session.expire(current, ["aliases_rel"])
            audit.record_in_session(
                session,
                action_type=(
                    audit.GLOSSARY_SOURCE_UPDATE
                    if source_changed
                    else audit.GLOSSARY_TERM_UPDATE
                ),
                user_id=updated_by,
                username=audit_username or updated_by,
                target_type=audit.TARGET_GLOSSARY_TERM,
                target_id=str(term_id),
                old_value=old_value,
                new_value=_audit_term_dict(current),
                ip_address=ip_address,
            )
            _invalidate_on_commit(session)
            return _term_dict(current)

    def add_alias(
        self,
        term_id: int,
        version: int,
        alias: str,
        *,
        locale: str | None = None,
        auto_expand: bool = False,
        search_enabled: bool = False,
        updated_by: str | None = None,
        ip_address: str | None = None,
        audit_username: str | None = None,
    ) -> dict:
        with session_scope() as session:
            term = session.get(DomainTerm, term_id)
            if term is None:
                raise GlossaryNotFoundError(f"Термин не найден: {term_id}")
            item = self._prepare_alias(
                GlossaryAliasInput(alias, locale, auto_expand, search_enabled, updated_by),
                kind=term.kind,
                created_by=updated_by,
            )
            if session.execute(
                select(DomainTermAlias.id).where(
                    DomainTermAlias.normalized_alias == item["normalized_alias"],
                    DomainTermAlias.term_id == term_id,
                )
            ).scalar_one_or_none() is not None:
                raise GlossaryAliasConflictError("Такой алиас уже есть в этой карточке")

            outcome = session.execute(
                update(DomainTerm)
                .where(DomainTerm.id == term_id, DomainTerm.version == version)
                .values(
                    version=DomainTerm.version + 1,
                    updated_at=_utcnow(),
                    updated_by=updated_by,
                )
                .execution_options(synchronize_session=False)
            )
            if outcome.rowcount == 0:
                raise GlossaryVersionConflictError(f"Версия термина устарела: {term_id}")

            session.add(
                DomainTermAlias(
                    term_id=term_id,
                    alias=item["alias"],
                    normalized_alias=item["normalized_alias"],
                    locale=item["locale"],
                    auto_expand=item["auto_expand"],
                    search_enabled=item["search_enabled"],
                    created_by=updated_by,
                    updated_by=updated_by,
                )
            )
            try:
                session.flush()
            except IntegrityError as exc:
                raise GlossaryAliasConflictError("Алиас уже есть в этой карточке") from exc

            current = session.get(DomainTerm, term_id, populate_existing=True)
            session.expire(current, ["aliases_rel"])
            audit.record_in_session(
                session,
                action_type=audit.GLOSSARY_ALIAS_CREATE,
                user_id=updated_by,
                username=audit_username or updated_by,
                target_type=audit.TARGET_GLOSSARY_TERM,
                target_id=str(term_id),
                new_value=item,
                ip_address=ip_address,
            )
            _invalidate_on_commit(session)
            return _term_dict(current)

    def delete_alias(
        self,
        term_id: int,
        version: int,
        alias_id: int,
        *,
        updated_by: str | None = None,
        ip_address: str | None = None,
        audit_username: str | None = None,
    ) -> dict:
        with session_scope() as session:
            term = session.get(DomainTerm, term_id)
            if term is None:
                raise GlossaryNotFoundError(f"Термин не найден: {term_id}")
            alias = session.get(DomainTermAlias, alias_id)
            if alias is None or alias.term_id != term_id:
                raise GlossaryNotFoundError(f"Алиас не найден: {alias_id}")
            outcome = session.execute(
                update(DomainTerm)
                .where(DomainTerm.id == term_id, DomainTerm.version == version)
                .values(version=DomainTerm.version + 1, updated_at=_utcnow(), updated_by=updated_by)
                .execution_options(synchronize_session=False)
            )
            if outcome.rowcount == 0:
                raise GlossaryVersionConflictError(f"Версия термина устарела: {term_id}")
            session.execute(delete(DomainTermAlias).where(DomainTermAlias.id == alias_id))
            current = session.get(DomainTerm, term_id, populate_existing=True)
            session.expire(current, ["aliases_rel"])
            audit.record_in_session(
                session,
                action_type=audit.GLOSSARY_ALIAS_DELETE,
                user_id=updated_by,
                username=audit_username or updated_by,
                target_type=audit.TARGET_GLOSSARY_TERM,
                target_id=str(term_id),
                old_value=_audit_alias_dict(alias),
                ip_address=ip_address,
            )
            _invalidate_on_commit(session)
            return _term_dict(current)

    def update_alias(
        self,
        term_id: int,
        version: int,
        alias_id: int,
        *,
        alias: str | None = None,
        locale: str | None = None,
        auto_expand: bool | None = None,
        search_enabled: bool | None = None,
        updated_by: str | None = None,
        ip_address: str | None = None,
        audit_username: str | None = None,
    ) -> dict:
        with session_scope() as session:
            term = session.get(DomainTerm, term_id)
            if term is None:
                raise GlossaryNotFoundError(f"Термин не найден: {term_id}")
            current_alias = session.get(DomainTermAlias, alias_id)
            if current_alias is None or current_alias.term_id != term_id:
                raise GlossaryNotFoundError(f"Алиас не найден: {alias_id}")

            next_alias = current_alias.alias if alias is None else alias.strip()
            next_locale = current_alias.locale if locale is None else validate_locale(locale, allow_und=False)
            next_auto = current_alias.auto_expand if auto_expand is None else auto_expand
            next_search = current_alias.search_enabled if search_enabled is None else search_enabled
            normalized = validate_alias_options(
                next_alias,
                kind=term.kind,
                auto_expand=next_auto,
                search_enabled=next_search,
            )
            if not next_alias or (
                next_alias == current_alias.alias
                and normalized == current_alias.normalized_alias
                and next_locale == current_alias.locale
                and next_auto == current_alias.auto_expand
                and next_search == current_alias.search_enabled
            ):
                return _term_dict(term)
            if normalized != current_alias.normalized_alias and session.execute(
                select(DomainTermAlias.id).where(
                    DomainTermAlias.normalized_alias == normalized,
                    DomainTermAlias.term_id == term_id,
                    DomainTermAlias.id != alias_id,
                )
            ).scalar_one_or_none() is not None:
                raise GlossaryAliasConflictError("Такой алиас уже есть в этой карточке")
            old_alias = _audit_alias_dict(current_alias)

            outcome = session.execute(
                update(DomainTerm)
                .where(DomainTerm.id == term_id, DomainTerm.version == version)
                .values(version=DomainTerm.version + 1, updated_at=_utcnow(), updated_by=updated_by)
                .execution_options(synchronize_session=False)
            )
            if outcome.rowcount == 0:
                raise GlossaryVersionConflictError(f"Версия термина устарела: {term_id}")
            try:
                session.execute(
                    update(DomainTermAlias)
                    .where(DomainTermAlias.id == alias_id)
                    .values(
                        alias=next_alias,
                        normalized_alias=normalized,
                        locale=next_locale,
                        auto_expand=next_auto,
                        search_enabled=next_search,
                        updated_at=_utcnow(),
                        updated_by=updated_by,
                    )
                    .execution_options(synchronize_session=False)
                )
                session.flush()
            except IntegrityError as exc:
                raise GlossaryAliasConflictError("Алиас уже есть в этой карточке") from exc

            session.expire(current_alias)
            current = session.get(DomainTerm, term_id, populate_existing=True)
            session.expire(current, ["aliases_rel"])
            audit.record_in_session(
                session,
                action_type=audit.GLOSSARY_ALIAS_UPDATE,
                user_id=updated_by,
                username=audit_username or updated_by,
                target_type=audit.TARGET_GLOSSARY_TERM,
                target_id=str(term_id),
                old_value=old_alias,
                new_value={
                    "alias": next_alias,
                    "normalized_alias": normalized,
                    "locale": next_locale,
                    "auto_expand": next_auto,
                    "search_enabled": next_search,
                },
                ip_address=ip_address,
            )
            _invalidate_on_commit(session)
            return _term_dict(current)

    @staticmethod
    def _prepare_alias(item: GlossaryAliasInput, *, kind: str, created_by: str | None) -> dict:
        normalized = validate_alias_options(
            item.alias,
            kind=kind,
            auto_expand=item.auto_expand,
            search_enabled=item.search_enabled,
        )
        value = item.alias.strip()
        if not value:
            raise GlossaryValidationError("Алиас не может быть пустым")
        locale = validate_locale(item.locale, allow_und=False) if item.locale is not None else None
        return {
            "alias": value,
            "normalized_alias": normalized,
            "locale": locale,
            "auto_expand": item.auto_expand,
            "search_enabled": item.search_enabled,
            "created_by": item.created_by or created_by,
        }


_INSTANCE: GlossaryRegistry | None = None


def get_glossary_registry() -> GlossaryRegistry:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = GlossaryRegistry()
    return _INSTANCE
