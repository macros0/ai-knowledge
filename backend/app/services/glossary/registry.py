"""Transactional CRUD and snapshot reads for the domain glossary."""
from __future__ import annotations

from datetime import datetime, timezone
import threading
import re
from contextlib import nullcontext
from uuid import uuid4
from typing import Sequence

from sqlalchemy import and_, delete, event, or_, select, update
from sqlalchemy.orm import object_session, selectinload
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    DomainTerm,
    DomainTermAlias,
    DomainTermTranslation,
    GlossaryInfotypeRule,
    GlossaryIdentityKey,
    GlossaryState,
)
from app.db.session import get_engine, session_scope
from app import error_codes as codes
from app.services import audit
from app.services.glossary.mutation import bump_glossary_revision, glossary_write_session
from app.services.glossary.normalization import (
    GlossaryValidationError,
    GlossaryRedundantAliasError,
    GlossaryMultipleInfotypeNumbersError,
    SUPPORTED_KINDS,
    validate_infotype_number,
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
    InfotypeRuleSnapshot,
)
from app.services.glossary.rules import normalize_rule_prefix


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
    try:
        from app.services.glossary.snapshot import invalidate_snapshot_cache as invalidate_revision_cache
        invalidate_revision_cache()
    except ImportError:
        pass


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


class GlossaryIdentityConflictError(ValueError):
    """A name/alias/infotype identity is already owned by another term."""
    code = codes.GLOSSARY_IDENTITY_CONFLICT

    def __init__(self, conflicts: Sequence[dict] | str):
        if isinstance(conflicts, str):
            self.conflicts = ()
            message = conflicts
        else:
            self.conflicts = tuple(conflicts)
            message = "Идентичность уже принадлежит другому термину"
        super().__init__(message)


class GlossaryVersionConflictError(ValueError):
    code = codes.VERSION_CONFLICT


class GlossaryMergePreviewStaleError(GlossaryVersionConflictError):
    code = codes.GLOSSARY_MERGE_PREVIEW_STALE


class GlossaryIdempotencyConflictError(GlossaryIdentityConflictError):
    code = codes.GLOSSARY_IDEMPOTENCY_CONFLICT


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
        "infotype_number": term.infotype_number,
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


def _identity_values(term: DomainTerm) -> list[tuple[str, str, str]]:
    """Return stable keys for the term name, aliases, and configured number."""
    values = [("literal", normalize_alias(term.original_name), "original_name")]
    values.extend(
        ("literal", alias.normalized_alias, "alias")
        for alias in term.aliases_rel
    )
    if term.infotype_number:
        values.append(("infotype", term.infotype_number, "infotype_number"))
    return [(kind, value, field) for kind, value, field in values if value]


def _identity_conflicts(session, values: Sequence[tuple[str, str, str]], *, term_id: int | None = None,
                        kind: str | None = None, infotype_number: str | None = None) -> list[dict]:
    """Look up all owners of proposed identities in one deterministic query."""
    if not values:
        return []
    keys = {(kind, value) for kind, value, _ in values}
    predicates = [and_(GlossaryIdentityKey.key_kind == kind, GlossaryIdentityKey.key_value == value) for kind, value in keys]
    stmt = select(GlossaryIdentityKey, DomainTerm).join(
        DomainTerm, DomainTerm.id == GlossaryIdentityKey.term_id
    ).where(or_(*predicates))
    if term_id is not None:
        stmt = stmt.where(GlossaryIdentityKey.term_id != term_id)
    result = []
    for key, term in session.execute(stmt.order_by(GlossaryIdentityKey.key_kind, GlossaryIdentityKey.key_value, GlossaryIdentityKey.term_id)):
        result.append({
            "key_kind": key.key_kind,
            "key_value": key.key_value,
            "field_name": key.field_name,
            "term_id": term.id,
            "name": term.original_name,
            "canonical": term.canonical,
            "enabled": term.enabled,
        })
    result.extend(_rule_identity_conflicts(session, values, term_id=term_id, kind=kind, infotype_number=infotype_number))
    return result


def _matches_rule_form(value: str, rule) -> str | None:
    """Return the four-digit number when a literal is an enabled rule form."""
    normalized = normalize_alias(value)
    for prefix in rule.prefixes_rel:
        key = normalize_rule_prefix(prefix.prefix)
        if not normalized.startswith(key):
            continue
        suffix = normalized[len(key):]
        if len(suffix) == 4 and suffix.isascii() and suffix.isdigit():
            number = int(suffix)
            if rule.number_from <= number <= rule.number_to:
                return suffix
    return None


def _reject_redundant_aliases(session, *, kind, number, name, aliases, preserved_forms=(), rules=None) -> None:
    if rules is None:
        rules = session.execute(select(GlossaryInfotypeRule).options(
            selectinload(GlossaryInfotypeRule.prefixes_rel))).scalars().all()
    name_key = normalize_alias(name)
    for alias in aliases:
        alias_numbers = {value for rule in rules if (value := _matches_rule_form(alias, rule)) is not None}
        if re.fullmatch(r"[0-9]{4}", normalize_alias(alias)):
            alias_numbers.add(normalize_alias(alias))
        if kind == "sap_infotype" and alias_numbers - {number}:
            raise GlossaryMultipleInfotypeNumbersError("Алиас относится к другому номеру инфотипа")
        if normalize_alias(alias) in preserved_forms:
            continue
        if normalize_alias(alias) == name_key or (
            kind == "sap_infotype" and number in alias_numbers and not normalize_alias(alias).isdigit()
        ):
            raise GlossaryRedundantAliasError(f"Алиас уже задан именем или правилом карточки: {alias}")


def _rule_identity_conflicts(session, values, *, term_id: int | None = None,
                             kind: str | None = None, infotype_number: str | None = None) -> list[dict]:
    literals = [(value, field) for kind, value, field in values if kind == "literal"]
    if not literals:
        return []
    rules = session.execute(
        select(GlossaryInfotypeRule).options(
            selectinload(GlossaryInfotypeRule.prefixes_rel)
        ).order_by(GlossaryInfotypeRule.id)
    ).scalars().all()
    owner = session.get(DomainTerm, term_id) if term_id is not None else None
    proposed_kind = kind if kind is not None else owner.kind if owner is not None else None
    proposed_number = infotype_number if kind is not None else owner.infotype_number if owner is not None else None
    conflicts = []
    for value, field in literals:
        for rule in rules:
            number = _matches_rule_form(value, rule)
            if number is None:
                continue
            structural_owner = session.execute(
                select(DomainTerm).where(
                    DomainTerm.kind == "sap_infotype",
                    DomainTerm.infotype_number == number,
                ).order_by(DomainTerm.id)
            ).scalars().first()
            # The proposed classification can own its own structural form,
            # but may not take the number from another saved card.
            if (proposed_kind == "sap_infotype" and proposed_number == number
                    and (structural_owner is None or structural_owner.id == term_id)):
                continue
            conflicts.append({
                "key_kind": "infotype_rule",
                "key_value": normalize_alias(value),
                "field_name": field,
                "rule_id": rule.id,
                "rule_name": rule.name,
                "number": number,
                "term_id": structural_owner.id if structural_owner is not None else None,
                "name": structural_owner.original_name if structural_owner is not None else rule.name,
                "locale": structural_owner.canonical_locale if structural_owner is not None else None,
            })
    return conflicts


def _local_identity_conflicts(term: DomainTerm) -> list[dict]:
    seen: dict[tuple[str, str], str] = {}
    result = []
    for kind, value, field in _identity_values(term):
        previous = seen.get((kind, value))
        if previous is not None:
            result.append({
                "key_kind": kind,
                "key_value": value,
                "field_name": field,
                "conflicting_field": previous,
                "term_id": term.id,
                "name": term.original_name,
            })
        else:
            seen[(kind, value)] = field
    return result


def _replace_identity_keys(session, term: DomainTerm) -> None:
    """Rebuild one term's keys inside the caller's write transaction."""
    session.execute(delete(GlossaryIdentityKey).where(GlossaryIdentityKey.term_id == term.id))
    for kind, value, field in _identity_values(term):
        session.add(GlossaryIdentityKey(
            key_kind=kind,
            key_value=value,
            term_id=term.id,
            field_name=field,
        ))


def _rebuild_all_identity_keys(session) -> None:
    session.execute(delete(GlossaryIdentityKey))
    terms = session.execute(
        select(DomainTerm).options(selectinload(DomainTerm.aliases_rel))
    ).scalars().all()
    for term in terms:
        _replace_identity_keys(session, term)


def _ensure_identity_keys(session) -> None:
    """Backfill legacy rows lazily before the first guarded write."""
    if session.execute(select(GlossaryIdentityKey.key_value).limit(1)).first() is None:
        if session.execute(select(DomainTerm.id).limit(1)).first() is not None:
            _rebuild_all_identity_keys(session)


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
        infotype_number: str | None = None,
        aliases: Sequence[GlossaryAliasInput] = (),
        ip_address: str | None = None,
        audit_username: str | None = None,
        _session=None,
        _state=None,
    ) -> dict:
        # Legacy import identifiers remain supported, but UI creates opaque keys.
        if kind not in SUPPORTED_KINDS:
            raise GlossaryValidationError(f"Неизвестный вид термина: {kind}")
        canonical = normalize_canonical(canonical, kind) if canonical else f"TERM_{uuid4().hex.upper()}"
        original_name = _clean_name(original_name)
        original_description = _clean_description(original_description)
        canonical_locale = validate_locale(canonical_locale)
        if infotype_number is not None:
            if not isinstance(infotype_number, str) or not re.fullmatch(r"[0-9]{4}", infotype_number):
                raise GlossaryValidationError("Номер инфотипа должен содержать ровно четыре цифры")
            infotype_number = infotype_number
        elif kind == "sap_infotype" and re.fullmatch(r"IT[0-9]{4}", canonical):
            infotype_number = canonical[2:]
        validate_infotype_number(kind, infotype_number)

        prepared_aliases = [
            self._prepare_alias(item, kind=kind, created_by=created_by) for item in aliases
        ]
        all_aliases = prepared_aliases
        normalized = [item["normalized_alias"] for item in all_aliases]
        if len(set(normalized)) != len(normalized):
            raise GlossaryAliasConflictError("Алиасы термина дублируются после нормализации")

        transaction = glossary_write_session() if _session is None else nullcontext((_session, _state))
        with transaction as (session, state):
            _ensure_identity_keys(session)
            _reject_redundant_aliases(session, kind=kind, number=infotype_number,
                                      name=original_name, aliases=[item['alias'] for item in all_aliases])
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
                infotype_number=infotype_number,
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

            conflicts = _local_identity_conflicts(term) + _identity_conflicts(session, _identity_values(term), term_id=term.id)
            if conflicts:
                raise GlossaryIdentityConflictError(conflicts)
            _replace_identity_keys(session, term)
            bump_glossary_revision(session, state)

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

    def list(self, *, enabled: bool | None = None, _session=None) -> list[dict]:
        with (session_scope() if _session is None else nullcontext(_session)) as session:
            stmt = select(DomainTerm).options(
                selectinload(DomainTerm.aliases_rel),
                selectinload(DomainTerm.translations_rel),
            ).order_by(DomainTerm.canonical)
            if enabled is not None:
                stmt = stmt.where(DomainTerm.enabled == enabled)
            conflicts = _alias_conflict_map(session)
            return [_term_dict(item, conflicts) for item in session.execute(stmt).scalars().all()]

    def check_aliases(self, aliases: Sequence[str], *, term_id: int | None = None,
                      kind: str | None = None, infotype_number: str | None = None) -> list[dict]:
        """Read-only preflight across both saved aliases and term names."""
        keys = {normalize_alias(alias) for alias in aliases if alias.strip()}
        if not keys:
            return []
        with session_scope() as session:
            stmt = select(DomainTermAlias, DomainTerm).join(DomainTerm).where(
                DomainTermAlias.normalized_alias.in_(keys)
            )
            if term_id is not None:
                stmt = stmt.where(DomainTerm.id != term_id)
            result = [
                {"alias_id": alias.id, "alias": alias.alias,
                 "normalized_alias": alias.normalized_alias, "term_id": term.id,
                 "name": term.original_name, "locale": term.canonical_locale,
                 "enabled": term.enabled, "field_name": "alias"}
                for alias, term in session.execute(stmt.order_by(DomainTerm.id, DomainTermAlias.id))
            ]
            name_stmt = select(DomainTerm).where(
                DomainTerm.id != term_id if term_id is not None else True
            )
            for term in session.execute(name_stmt.order_by(DomainTerm.id)).scalars():
                if normalize_alias(term.original_name) in keys:
                    result.append({
                        "alias_id": None,
                        "alias": term.original_name,
                        "normalized_alias": normalize_alias(term.original_name),
                        "term_id": term.id,
                        "name": term.original_name,
                        "locale": term.canonical_locale,
                        "enabled": term.enabled,
                        "field_name": "original_name",
                    })
            result.extend(_rule_identity_conflicts(
                session,
                [("literal", key, "alias") for key in keys],
                term_id=term_id,
                kind=kind,
                infotype_number=infotype_number,
            ))
            return result

    def check_conflicts(self, draft: dict, *, term_id: int | None = None) -> dict:
        """Inspect complete proposed metadata without inserts, revision or audit writes."""
        kind = draft['kind']
        number = draft.get('infotype_number')
        validate_infotype_number(kind, number)
        name = _clean_name(draft['original_name'])
        aliases = [self._prepare_alias(GlossaryAliasInput(**item), kind=kind, created_by=None)
                   for item in draft.get('aliases', [])]
        proposed = DomainTerm(id=term_id, kind=kind, original_name=name, infotype_number=number,
            aliases_rel=[DomainTermAlias(alias=item['alias'], normalized_alias=item['normalized_alias']) for item in aliases])
        with session_scope() as session:
            if term_id is not None and session.get(DomainTerm, term_id) is None:
                raise GlossaryNotFoundError(f'Термин не найден: {term_id}')
            conflicts = _identity_conflicts(session, _identity_values(proposed), term_id=term_id,
                kind=kind, infotype_number=number)
            seen = {normalize_alias(name): 'original_name'}
            redundant = []
            rules = session.execute(select(GlossaryInfotypeRule).options(
                selectinload(GlossaryInfotypeRule.prefixes_rel))).scalars().all()
            for index, alias in enumerate(aliases):
                key = alias['normalized_alias']
                field = f'aliases[{index}]'
                if key in seen:
                    conflicts.append(dict(key_kind='literal', key_value=key, field_name=field,
                        conflicting_field=seen[key], term_id=term_id, name=name))
                else:
                    seen[key] = field
                try:
                    _reject_redundant_aliases(session, kind=kind, number=number, name=name,
                        aliases=[alias['alias']], rules=rules)
                except GlossaryRedundantAliasError as exc:
                    redundant.append(dict(alias=alias['alias'], field_name=field, code=exc.code, detail=str(exc)))
                except GlossaryValidationError as exc:
                    conflicts.append(dict(key_kind='invalid_alias', key_value=key, field_name=field,
                        term_id=term_id, code=exc.code, detail=str(exc)))
            return {'conflicts': conflicts, 'redundant_forms': redundant}

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

    @staticmethod
    def _snapshot_from_session(session, *, include_disabled: bool = False) -> tuple[GlossaryTermSnapshot, ...]:
        """Build detached term DTOs from an already-open read transaction."""
        stmt = (
            select(DomainTerm)
            .options(
                selectinload(DomainTerm.aliases_rel),
                selectinload(DomainTerm.translations_rel),
            )
            .order_by(DomainTerm.id)
        )
        if not include_disabled:
            stmt = stmt.where(DomainTerm.enabled.is_(True))
        terms = session.execute(stmt).scalars().all()
        conflicts = _alias_conflict_map(session)
        return tuple(
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
                infotype_number=(
                    term.infotype_number
                    or (term.canonical[2:] if term.kind == "sap_infotype" and re.fullmatch(r"IT[0-9]{4}", term.canonical) else None)
                ),
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
            snapshot = self._snapshot_from_session(session)
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
        kind: str | None = None,
        original_name: str | None = None,
        original_description: str | None | object = _UNSET,
        canonical_locale: str | None = None,
        infotype_number: str | None | object = _UNSET,
        updated_by: str | None = None,
        ip_address: str | None = None,
        audit_username: str | None = None,
    ) -> dict:
        with glossary_write_session() as (session, state):
            _ensure_identity_keys(session)
            term = session.get(DomainTerm, term_id)
            if term is None:
                raise GlossaryNotFoundError(f"Термин не найден: {term_id}")
            if term.version != version:
                raise GlossaryVersionConflictError(f"Версия термина устарела: {term_id}")
            values: dict = {}
            old_value = _audit_term_dict(term)
            source_changed = False
            next_kind = term.kind if kind is None else kind
            if next_kind not in SUPPORTED_KINDS:
                raise GlossaryValidationError(f"Неизвестный вид термина: {next_kind}")
            next_number = term.infotype_number if infotype_number is _UNSET else infotype_number
            validate_infotype_number(next_kind, next_number)
            if next_kind != term.kind:
                for alias in term.aliases_rel:
                    validate_alias_options(alias.alias, kind=next_kind,
                                           auto_expand=alias.auto_expand, search_enabled=alias.search_enabled)
                values["kind"] = next_kind
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
            if infotype_number is not _UNSET:
                validate_infotype_number(next_kind, infotype_number)
                if infotype_number is not None and (
                    not isinstance(infotype_number, str)
                    or not re.fullmatch(r"[0-9]{4}", infotype_number)
                ):
                    raise GlossaryValidationError("Номер инфотипа должен содержать ровно четыре цифры")
                if infotype_number != term.infotype_number:
                    values["infotype_number"] = infotype_number
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
            session.refresh(current)
            conflicts = _local_identity_conflicts(current) + _identity_conflicts(session, _identity_values(current), term_id=term_id)
            if conflicts:
                raise GlossaryIdentityConflictError(conflicts)
            _replace_identity_keys(session, current)
            bump_glossary_revision(session, state)
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
        with glossary_write_session() as (session, state):
            _ensure_identity_keys(session)
            term = session.get(DomainTerm, term_id)
            if term is None:
                raise GlossaryNotFoundError(f"Термин не найден: {term_id}")
            item = self._prepare_alias(
                GlossaryAliasInput(alias, locale, auto_expand, search_enabled, updated_by),
                kind=term.kind,
                created_by=updated_by,
            )
            _reject_redundant_aliases(session, kind=term.kind, number=term.infotype_number,
                                      name=term.original_name, aliases=[item['alias']])
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
            session.refresh(current)
            conflicts = _local_identity_conflicts(current) + _identity_conflicts(session, _identity_values(current), term_id=term_id)
            if conflicts:
                raise GlossaryIdentityConflictError(conflicts)
            _replace_identity_keys(session, current)
            bump_glossary_revision(session, state)
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
        with glossary_write_session() as (session, state):
            _ensure_identity_keys(session)
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
            session.refresh(current)
            _replace_identity_keys(session, current)
            bump_glossary_revision(session, state)
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
        with glossary_write_session() as (session, state):
            _ensure_identity_keys(session)
            term = session.get(DomainTerm, term_id)
            if term is None:
                raise GlossaryNotFoundError(f"Термин не найден: {term_id}")
            current_alias = session.get(DomainTermAlias, alias_id)
            if current_alias is None or current_alias.term_id != term_id:
                raise GlossaryNotFoundError(f"Алиас не найден: {alias_id}")
            if term.version != version:
                raise GlossaryVersionConflictError(f"Версия термина устарела: {term_id}")

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
            if normalized != current_alias.normalized_alias:
                _reject_redundant_aliases(session, kind=term.kind, number=term.infotype_number,
                                          name=term.original_name, aliases=[next_alias])
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
            session.refresh(current)
            conflicts = _local_identity_conflicts(current) + _identity_conflicts(session, _identity_values(current), term_id=term_id)
            if conflicts:
                raise GlossaryIdentityConflictError(conflicts)
            _replace_identity_keys(session, current)
            bump_glossary_revision(session, state)
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

    def _merge_rows(self, session, request: dict):
        """Load saved owners; validate an optional detached draft without INSERT."""
        source_id = request.get("source_term_id", request.get("source_id"))
        target_id = request.get("target_term_id", request.get("target_id"))
        draft = request.get("draft")
        if not isinstance(target_id, int) or (source_id is None) == (draft is None):
            raise GlossaryValidationError("Для merge нужна цель и карточка либо черновик")
        if draft is None and (not isinstance(source_id, int) or source_id == target_id):
            raise GlossaryValidationError("Нужны разные source и target термины")
        stmt = select(DomainTerm).where(DomainTerm.id.in_([target_id] + ([source_id] if source_id else []))).options(
            selectinload(DomainTerm.aliases_rel), selectinload(DomainTerm.translations_rel))
        rows = {term.id: term for term in session.execute(stmt).scalars()}
        if target_id not in rows or (source_id is not None and source_id not in rows):
            raise GlossaryNotFoundError("Один из терминов для merge не найден")
        target = rows[target_id]
        source = rows.get(source_id)
        if source is not None and request.get('source_edit') is None:
            return source, target, _term_dict(source), _term_dict(target)
        if source is not None:
            draft = request['source_edit']
        kind = draft.get("kind")
        if kind not in SUPPORTED_KINDS:
            raise GlossaryValidationError("Неподдерживаемый вид термина")
        number = draft.get("infotype_number")
        canonical = draft.get("canonical")
        if canonical:
            canonical = normalize_canonical(canonical, kind)
            if number is None and kind == "sap_infotype":
                number = canonical[2:]
        validate_infotype_number(kind, number)
        aliases = [self._prepare_alias(GlossaryAliasInput(**item), kind=kind, created_by=None)
                   for item in draft.get("aliases", [])]
        _reject_redundant_aliases(session, kind=kind, number=number,
            name=draft.get('original_name', ''), aliases=[item['alias'] for item in aliases],
            preserved_forms={normalize_alias(item.alias) for item in source.aliases_rel} if source else ())
        source_value = dict(id=None, canonical=canonical, kind=kind, infotype_number=number,
            original_name=_clean_name(draft.get("original_name")),
            original_description=_clean_description(draft.get("original_description")),
            canonical_locale=validate_locale(draft.get("canonical_locale", "und")),
            aliases=aliases, translations=[], version=0, source_revision=1, enabled=True)
        if source is not None:
            saved = _term_dict(source)
            previous_aliases = {item['normalized_alias']: item for item in saved['aliases']}
            for index, alias in enumerate(aliases):
                previous = previous_aliases.get(alias['normalized_alias'])
                if previous:
                    aliases[index] = {**previous, **alias, 'created_by': previous['created_by']}
            source_changed = any(source_value[field] != saved[field]
                                 for field in ('original_name', 'original_description', 'canonical_locale'))
            source_value.update(id=source.id, canonical=source.canonical, translations=saved['translations'],
                                version=source.version, source_revision=source.source_revision + int(source_changed),
                                enabled=source.enabled if draft.get('enabled') is None else draft['enabled'])
        return source, target, source_value, _term_dict(target)

    @staticmethod
    def _validate_merged_aliases(session, merged, source, target):
        # Existing forms retain their search permissions across a merge. A new
        # draft alias was validated separately; no path may introduce a second
        # infotype number, even when an old business alias changes kind here.
        preserved = {normalize_alias(owner.get('original_name', '')) for owner in (source, target)}
        preserved.update(normalize_alias(item['alias']) for owner in (source, target)
                         for item in owner.get('aliases', []))
        _reject_redundant_aliases(session, kind=merged['kind'], number=merged.get('infotype_number'),
            name=merged['original_name'], aliases=[item['alias'] for item in merged['aliases']],
            preserved_forms=preserved)

    @staticmethod
    def _merge_rules(session):
        return tuple(InfotypeRuleSnapshot(rule_id=rule.id, name=rule.name,
            number_from=rule.number_from, number_to=rule.number_to, enabled=rule.enabled,
            prefixes=tuple(item.prefix for item in rule.prefixes_rel), version=rule.version)
            for rule in session.scalars(select(GlossaryInfotypeRule).options(
                selectinload(GlossaryInfotypeRule.prefixes_rel))))

    def _merge_conflicts(self, session, merged, source_id, target_id):
        from app.services.glossary.identity import collect_identity_conflicts
        others = [item for item in self.list(_session=session) if item['id'] not in (source_id, target_id)]
        conflicts = collect_identity_conflicts(tuple(others + [merged]), self._merge_rules(session))
        return [item for item in conflicts if target_id in
                (item.get('left_term_id'), item.get('right_term_id'), item.get('term_id'))]

    def merge_preview(self, request: dict, *, _session=None, _state=None) -> dict:
        """Return a detached merge proposal and digest without writing anything."""
        from app.services.glossary.merge import build_merge_result, merge_proposal_digest, unresolved_merge_fields

        selections = request.get("selections") or {}
        with (session_scope() if _session is None else nullcontext(_session)) as session:
            state = _state or session.get(GlossaryState, 1)
            source, target, source_value, target_value = self._merge_rows(session, request)
            merged = build_merge_result(source_value, target_value, selections, self._merge_rules(session), allow_unresolved=True)
            self._validate_merged_aliases(session, merged, source_value, target_value)
            digest = merge_proposal_digest(source_value, target_value, selections, merged, state.revision, request.get('draft'))
            return {
                "source": source_value,
                "target": target_value,
                "merged": merged,
                "digest": digest,
                "glossary_revision": int(state.revision),
                "unresolved_fields": unresolved_merge_fields(source_value, target_value, selections),
                "result": merged,
                "removed_term_id": source.id if source is not None else None,
                "revision": int(state.revision),
                "preview_digest": digest,
                "conflicts": self._merge_conflicts(session, merged, source.id if source else None, target.id),
                "requires_confirmation": True,
            }

    def merge(
        self,
        request: dict,
        digest: str,
        *,
        actor_id: str | None = None,
        _session=None,
        _state=None,
        _defer_identity_validation: bool = False,
    ) -> dict:
        """Atomically move one term into another and leave a replay receipt."""
        from app.db.models import GlossaryMergeReceipt
        from app.services.glossary.merge import build_merge_result, merge_proposal_digest, merge_request_hash

        request_id = request.get("request_id")
        selections = request.get("selections") or {}
        if not request_id:
            raise GlossaryValidationError("request_id обязателен для merge")
        request_hash = merge_request_hash(request, digest)
        transaction = (
            glossary_write_session()
            if _session is None
            else nullcontext((_session, _state or _session.get(GlossaryState, 1)))
        )
        with transaction as (session, state):
            if _defer_identity_validation and (_session is None or state.identities_ready):
                raise GlossaryValidationError('Отложенная проверка доступна только подготовленной миграции')
            receipt = session.get(GlossaryMergeReceipt, request_id)
            if receipt is not None:
                if receipt.request_hash != request_hash:
                    raise GlossaryIdempotencyConflictError("request_id уже использован с другим merge")
                if receipt.actor_id != (actor_id or "unknown"):
                    raise GlossaryIdempotencyConflictError("request_id уже использован другим пользователем")
                return receipt.result
            source, target, source_value, target_value = self._merge_rows(session, request)
            def stale_preview(message):
                error = GlossaryMergePreviewStaleError(message)
                error.preview = self.merge_preview(request, _session=session, _state=state)
                return error
            if source is not None and request.get("source_version") is not None and source.version != request["source_version"]:
                raise stale_preview("Версия source устарела")
            if request.get("target_version") is not None and target.version != request["target_version"]:
                raise stale_preview("Версия target устарела")
            expected_revision = request.get("expected_revision")
            if expected_revision is not None and int(state.revision) != int(expected_revision):
                raise stale_preview("Версия глоссария устарела")
            source_audit_value = _audit_term_dict(source) if source is not None else source_value
            target_audit_value = _audit_term_dict(target)
            merged = build_merge_result(source_value, target_value, selections, self._merge_rules(session))
            self._validate_merged_aliases(session, merged, source_value, target_value)
            expected_digest = merge_proposal_digest(source_value, target_value, selections, merged, state.revision, request.get('draft'))
            if digest != expected_digest:
                raise stale_preview("Предпросмотр merge устарел")
            conflicts = [] if _defer_identity_validation else self._merge_conflicts(session, merged, source.id if source else None, target.id)
            if conflicts:
                error = GlossaryIdentityConflictError(conflicts)
                error.preview = self.merge_preview(request, _session=session, _state=state)
                raise error
            for field in ("original_name", "original_description", "canonical_locale", "kind", "infotype_number", "enabled"):
                setattr(target, field, merged.get(field))
            target.version = merged["version"]
            target.source_revision = merged["source_revision"]
            target.updated_at = _utcnow()
            target.updated_by = actor_id
            existing_aliases = {normalize_alias(item.alias): item for item in target.aliases_rel}
            wanted_aliases = {normalize_alias(item["alias"]): item for item in merged["aliases"]}
            for key, alias in existing_aliases.items():
                if key not in wanted_aliases:
                    session.delete(alias)
            for key, value in wanted_aliases.items():
                alias = existing_aliases.get(key)
                if alias is None:
                    alias = DomainTermAlias(term_id=target.id, alias=value["alias"], normalized_alias=key,
                                            created_by=value.get("created_by") or actor_id)
                    session.add(alias)
                alias.locale = value.get("locale")
                alias.auto_expand = value.get("auto_expand", False)
                alias.search_enabled = value.get("search_enabled", False)
                alias.updated_by = actor_id
            existing_translations = {item.locale: item for item in target.translations_rel}
            for value in merged["translations"]:
                translation = existing_translations.get(value["locale"])
                if translation is None:
                    translation = DomainTermTranslation(term_id=target.id, locale=value["locale"])
                    session.add(translation)
                for field in ("display_name", "description", "source_revision", "version",
                              "is_machine_translated", "reviewed_by", "reviewed_at"):
                    setattr(translation, field, value.get(field))
                translation.updated_by = actor_id
            session.execute(delete(GlossaryIdentityKey).where(
                GlossaryIdentityKey.term_id.in_([target.id] + ([source.id] if source is not None else []))
            ))
            if source is not None:
                session.delete(source)
            session.flush()
            session.expire(target, ["aliases_rel", "translations_rel"])
            session.refresh(target)
            conflicts = [] if _defer_identity_validation else _local_identity_conflicts(target) + _identity_conflicts(session, _identity_values(target), term_id=target.id)
            if conflicts:
                raise GlossaryIdentityConflictError(conflicts)
            if not _defer_identity_validation:
                _replace_identity_keys(session, target)
            bump_glossary_revision(session, state)
            session.refresh(target)
            session.expire(target, ["aliases_rel", "translations_rel"])
            receipt_result = _audit_term_dict(target)
            session.add(GlossaryMergeReceipt(request_id=request_id, actor_id=actor_id or "unknown", request_hash=request_hash, result=receipt_result))
            audit.record_in_session(
                session,
                action_type=audit.GLOSSARY_TERM_MERGE,
                user_id=actor_id,
                username=actor_id,
                target_type=audit.TARGET_GLOSSARY_TERM,
                target_id=str(target.id),
                old_value={"source": source_audit_value, "target": target_audit_value},
                new_value={"target": _audit_term_dict(target), "selections": selections,
                           "draft": request.get('draft'), "source_edit": request.get('source_edit')},
            )
            return receipt_result

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
