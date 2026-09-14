"""Atomic CRUD for user-owned, query-only infotype rules."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext
import hashlib
import json
from typing import Sequence
from app import error_codes as codes

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.db.models import DomainTerm, GlossaryInfotypePrefix, GlossaryInfotypeRule, GlossaryState
from app.db.session import session_scope
from app.services import audit
from app.services.glossary.mutation import bump_glossary_revision, glossary_write_session
from app.services.glossary.normalization import GlossaryInvalidRuleError, normalize_alias
from app.services.glossary.rules import normalize_rule_prefix, validate_rule


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GlossaryRuleConflictError(ValueError):
    code = codes.GLOSSARY_RULE_OVERLAP
    def __init__(self, conflicts: Sequence[dict]):
        self.conflicts = tuple(conflicts)
        super().__init__("Правило пересекается с существующим правилом")


class GlossaryRuleMergeUnsafeError(GlossaryRuleConflictError):
    code = codes.GLOSSARY_RULE_MERGE_UNSAFE


class GlossaryRuleVersionConflictError(ValueError):
    pass


class GlossaryRuleNotFoundError(LookupError):
    pass


def _clean(name: str, number_from: int, number_to: int, prefixes: Sequence[str]) -> tuple[str, int, int, tuple[str, ...]]:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 128:
        raise GlossaryInvalidRuleError("Название правила должно быть непустым и не длиннее 128 символов")
    if not isinstance(number_from, int) or not isinstance(number_to, int):
        raise GlossaryInvalidRuleError("Границы правила должны быть целыми числами")
    normalized = validate_rule(number_from, number_to, tuple(prefixes), name=name.strip())
    return name.strip(), number_from, number_to, normalized


def _as_dict(rule: GlossaryInfotypeRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "number_from": rule.number_from,
        "number_to": rule.number_to,
        "prefixes": [prefix.prefix for prefix in sorted(rule.prefixes_rel, key=lambda item: item.position)],
        "enabled": rule.enabled,
        "version": rule.version,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        "created_by": rule.created_by,
        "updated_by": rule.updated_by,
    }


def _audit_dict(rule: GlossaryInfotypeRule) -> dict:
    value = _as_dict(rule)
    value["created_at"] = audit.iso_or_str(value["created_at"])
    value["updated_at"] = audit.iso_or_str(value["updated_at"])
    return value


def _overlap(rule: GlossaryInfotypeRule, number_from: int, number_to: int, prefixes: tuple[str, ...]) -> bool:
    if rule.number_to < number_from or number_to < rule.number_from:
        return False
    existing = {prefix.normalized_prefix for prefix in rule.prefixes_rel}
    return bool(existing.intersection(prefixes))


def _matches_rule_form(value: str, number_from: int, number_to: int, prefixes: tuple[str, ...]) -> str | None:
    normalized = normalize_alias(value)
    for prefix in prefixes:
        key = normalize_rule_prefix(prefix)
        if not normalized.startswith(key):
            continue
        suffix = normalized[len(key):]
        if len(suffix) == 4 and suffix.isascii() and suffix.isdigit():
            number = int(suffix)
            if number_from <= number <= number_to:
                return suffix
    return None


def _term_conflicts(session, number_from: int, number_to: int, prefixes: tuple[str, ...]) -> list[dict]:
    terms = session.execute(
        select(DomainTerm).options(selectinload(DomainTerm.aliases_rel)).order_by(DomainTerm.id)
    ).scalars().all()
    conflicts = []
    for term in terms:
        values = [(term.original_name, "original_name")]
        values.extend((alias.alias, "alias") for alias in term.aliases_rel)
        for value, field in values:
            number = _matches_rule_form(value, number_from, number_to, prefixes)
            if number is None:
                continue
            if term.kind == "sap_infotype" and term.infotype_number == number:
                continue
            conflicts.append({
                "key_kind": "infotype_rule",
                "key_value": normalize_alias(value),
                "field_name": field,
                "term_id": term.id,
                "name": term.original_name,
                "number": number,
            })
    return conflicts


class GlossaryRuleRegistry:
    def list(self, *, enabled: bool | None = None) -> list[dict]:
        with glossary_write_session(allow_unready=True) as (session, _state):
            stmt = select(GlossaryInfotypeRule).options(selectinload(GlossaryInfotypeRule.prefixes_rel)).order_by(GlossaryInfotypeRule.id)
            if enabled is not None:
                stmt = stmt.where(GlossaryInfotypeRule.enabled == enabled)
            return [_as_dict(rule) for rule in session.execute(stmt).scalars().all()]

    def check(self, *, number_from: int, number_to: int, prefixes: Sequence[str], exclude_rule_id: int | None = None) -> list[dict]:
        _name, number_from, number_to, prefixes = _clean("check", number_from, number_to, prefixes)
        with glossary_write_session(allow_unready=True) as (session, _state):
            stmt = select(GlossaryInfotypeRule).options(selectinload(GlossaryInfotypeRule.prefixes_rel)).order_by(GlossaryInfotypeRule.id)
            if exclude_rule_id is not None:
                stmt = stmt.where(GlossaryInfotypeRule.id != exclude_rule_id)
            return ([_as_dict(rule) for rule in session.execute(stmt).scalars().all() if _overlap(rule, number_from, number_to, prefixes)]
                    + _term_conflicts(session, number_from, number_to, prefixes))

    def create(self, *, name: str, number_from: int, number_to: int, prefixes: Sequence[str], enabled: bool = True, actor_id: str | None = None, _session=None, _state=None) -> dict:
        name, number_from, number_to, prefixes = _clean(name, number_from, number_to, prefixes)
        with (glossary_write_session() if _session is None else nullcontext((_session, _state))) as (session, state):
            if session.scalar(select(func.count()).select_from(GlossaryInfotypeRule)) >= 200:
                raise GlossaryInvalidRuleError("Допускается не более 200 правил инфотипов")
            conflicts = self._find_conflicts(session, number_from, number_to, prefixes)
            conflicts.extend(_term_conflicts(session, number_from, number_to, prefixes))
            if conflicts:
                raise GlossaryRuleConflictError(conflicts)
            rule = GlossaryInfotypeRule(name=name, number_from=number_from, number_to=number_to, enabled=enabled, created_by=actor_id, updated_by=actor_id)
            rule.prefixes_rel = [GlossaryInfotypePrefix(prefix=prefix, normalized_prefix=prefix, position=index) for index, prefix in enumerate(prefixes)]
            session.add(rule)
            session.flush()
            bump_glossary_revision(session, state)
            audit.record_in_session(session, action_type=audit.GLOSSARY_RULE_CREATE, user_id=actor_id, username=actor_id, target_type="glossary_rule", target_id=str(rule.id), new_value=_audit_dict(rule))
            return _as_dict(rule)

    def update(self, rule_id: int, version: int, *, actor_id: str | None = None, **changes) -> dict:
        with glossary_write_session() as (session, state):
            rule = session.execute(select(GlossaryInfotypeRule).where(GlossaryInfotypeRule.id == rule_id).options(selectinload(GlossaryInfotypeRule.prefixes_rel))).scalar_one_or_none()
            if rule is None:
                raise GlossaryRuleNotFoundError(f"Правило не найдено: {rule_id}")
            if rule.version != version:
                raise GlossaryRuleVersionConflictError(f"Версия правила устарела: {rule_id}")
            name = changes.get("name", rule.name)
            number_from = changes.get("number_from", rule.number_from)
            number_to = changes.get("number_to", rule.number_to)
            prefixes = changes.get("prefixes", [item.prefix for item in rule.prefixes_rel])
            name, number_from, number_to, prefixes = _clean(name, number_from, number_to, prefixes)
            conflicts = self._find_conflicts(session, number_from, number_to, prefixes, exclude_rule_id=rule_id)
            conflicts.extend(_term_conflicts(session, number_from, number_to, prefixes))
            if conflicts:
                raise GlossaryRuleConflictError(conflicts)
            old = _audit_dict(rule)
            rule.name = name
            rule.number_from = number_from
            rule.number_to = number_to
            rule.enabled = changes.get("enabled", rule.enabled)
            rule.version += 1
            rule.updated_at = _now()
            rule.updated_by = actor_id
            session.execute(delete(GlossaryInfotypePrefix).where(GlossaryInfotypePrefix.rule_id == rule_id))
            session.flush()
            rule.prefixes_rel = [GlossaryInfotypePrefix(prefix=prefix, normalized_prefix=prefix, position=index) for index, prefix in enumerate(prefixes)]
            session.flush()
            bump_glossary_revision(session, state)
            audit.record_in_session(session, action_type=audit.GLOSSARY_RULE_UPDATE, user_id=actor_id, username=actor_id, target_type="glossary_rule", target_id=str(rule_id), old_value=old, new_value=_audit_dict(rule))
            return _as_dict(rule)

    def delete(self, rule_id: int, version: int, *, actor_id: str | None = None) -> dict:
        with glossary_write_session() as (session, state):
            rule = session.execute(select(GlossaryInfotypeRule).where(GlossaryInfotypeRule.id == rule_id).options(selectinload(GlossaryInfotypeRule.prefixes_rel))).scalar_one_or_none()
            if rule is None:
                raise GlossaryRuleNotFoundError(f"Правило не найдено: {rule_id}")
            if rule.version != version:
                raise GlossaryRuleVersionConflictError(f"Версия правила устарела: {rule_id}")
            old = _audit_dict(rule)
            session.delete(rule)
            bump_glossary_revision(session, state)
            audit.record_in_session(session, action_type=audit.GLOSSARY_RULE_DELETE, user_id=actor_id, username=actor_id, target_type="glossary_rule", target_id=str(rule_id), old_value=old)
            return old

    def merge_preview(self, request) -> dict:
        """Describe a safe rule merge without changing the database."""
        with session_scope() as session:
            _saved_source, source, target = self._load_merge_source(session, request)
            state = session.get(GlossaryState, 1)
            return self._merge_proposal(source, target, state.revision)

    def _load_merge_source(self, session, request):
        source_id, target_id = _merge_ids(request)
        if source_id is not None:
            saved_source, target = self._load_pair(session, source_id, target_id)
            if request.get('source_edit') is None:
                return saved_source, saved_source, target
            draft = request['source_edit']
        else:
            saved_source = None
            target = session.execute(select(GlossaryInfotypeRule).where(GlossaryInfotypeRule.id == target_id)
                .options(selectinload(GlossaryInfotypeRule.prefixes_rel))).scalar_one_or_none()
            if target is None:
                raise GlossaryRuleNotFoundError("Целевое правило не найдено")
            draft = request['draft']
        name, lower, upper, prefixes = _clean(draft['name'], draft['number_from'], draft['number_to'], draft['prefixes'])
        source = GlossaryInfotypeRule(id=source_id, name=name, number_from=lower, number_to=upper,
            enabled=draft.get('enabled', True), version=saved_source.version if saved_source else 0,
            created_at=saved_source.created_at if saved_source else None,
            updated_at=saved_source.updated_at if saved_source else None)
        source.prefixes_rel = [GlossaryInfotypePrefix(prefix=prefix, normalized_prefix=prefix, position=index)
                               for index, prefix in enumerate(prefixes)]
        return saved_source, source, target

    @staticmethod
    def _merge_proposal(source, target, revision):
        number_from, number_to = _safe_merge_range(source, target)
        if number_from is None or source.enabled != target.enabled:
            raise GlossaryRuleMergeUnsafeError([_as_dict(source), _as_dict(target)])
        prefixes = tuple(dict.fromkeys(
            [item.prefix for item in sorted(target.prefixes_rel, key=lambda item: item.position)]
            + [item.prefix for item in sorted(source.prefixes_rel, key=lambda item: item.position)]
        ))
        validate_rule(number_from, number_to, prefixes, name=target.name)
        result = {
            "source": _as_dict(source), "target": _as_dict(target),
            "number_from": number_from, "number_to": number_to,
            "merged_prefixes": list(prefixes), "glossary_revision": int(revision),
        }
        result["digest"] = _merge_digest(result)
        return result

    def merge(self, request, digest: str, *, actor_id: str | None = None) -> dict:
        source_id, target_id = _merge_ids(request)
        with glossary_write_session() as (session, state):
            saved_source, source, target = self._load_merge_source(session, request)
            for key, current in (('source_version', source.version), ('target_version', target.version), ('expected_revision', state.revision)):
                if request.get(key) is not None and request[key] != current:
                    raise GlossaryRuleVersionConflictError("Предпросмотр объединения правил устарел")
            preview = self._merge_proposal(source, target, state.revision)
            if digest != preview["digest"]:
                raise GlossaryRuleVersionConflictError("Предпросмотр объединения правил устарел")
            number_from, number_to = preview['number_from'], preview['number_to']
            prefixes = tuple(preview['merged_prefixes'])
            conflicts = [rule for rule in self._find_conflicts(session, number_from, number_to, prefixes)
                         if rule['id'] not in (source_id, target_id)]
            conflicts.extend(_term_conflicts(session, number_from, number_to, prefixes))
            if conflicts:
                raise GlossaryRuleConflictError(conflicts)
            old_source, old_target = _audit_dict(saved_source or source), _audit_dict(target)
            if saved_source is not None:
                session.delete(saved_source)
            session.flush()
            session.execute(delete(GlossaryInfotypePrefix).where(GlossaryInfotypePrefix.rule_id == target_id))
            session.flush()
            target.prefixes_rel = [GlossaryInfotypePrefix(prefix=prefix, normalized_prefix=prefix, position=index) for index, prefix in enumerate(prefixes)]
            target.number_from = number_from
            target.number_to = number_to
            target.version += 1
            target.updated_by = actor_id
            target.updated_at = _now()
            session.flush()
            bump_glossary_revision(session, state)
            if saved_source is not None:
                audit.record_in_session(session, action_type=audit.GLOSSARY_RULE_DELETE,
                    user_id=actor_id, username=actor_id, target_type="glossary_rule", target_id=str(source_id),
                    old_value=old_source, new_value={"merged_into": target_id})
            audit.record_in_session(session, action_type=audit.GLOSSARY_RULE_UPDATE,
                user_id=actor_id, username=actor_id, target_type="glossary_rule", target_id=str(target_id),
                old_value=old_target, new_value={"target": _audit_dict(target), "source": _audit_dict(source)})
            return _as_dict(target)

    @staticmethod
    def _load_pair(session, source_id: int, target_id: int) -> tuple[GlossaryInfotypeRule, GlossaryInfotypeRule]:
        if source_id == target_id:
            raise GlossaryRuleConflictError([])
        stmt = select(GlossaryInfotypeRule).where(GlossaryInfotypeRule.id.in_((source_id, target_id))).options(selectinload(GlossaryInfotypeRule.prefixes_rel))
        rows = {rule.id: rule for rule in session.execute(stmt).scalars().all()}
        if source_id not in rows or target_id not in rows:
            raise GlossaryRuleNotFoundError("Одно из правил не найдено")
        return rows[source_id], rows[target_id]

    @staticmethod
    def _find_conflicts(session, number_from: int, number_to: int, prefixes: tuple[str, ...], *, exclude_rule_id: int | None = None) -> list[dict]:
        stmt = select(GlossaryInfotypeRule).options(selectinload(GlossaryInfotypeRule.prefixes_rel)).order_by(GlossaryInfotypeRule.id)
        if exclude_rule_id is not None:
            stmt = stmt.where(GlossaryInfotypeRule.id != exclude_rule_id)
        return [_as_dict(rule) for rule in session.execute(stmt).scalars().all() if _overlap(rule, number_from, number_to, prefixes)]


def _merge_ids(request) -> tuple[int | None, int]:
    if isinstance(request, dict):
        source = request.get("source_rule_id", request.get("source_id"))
        target = request.get("target_rule_id", request.get("target_id"))
    else:
        source = getattr(request, "source_rule_id", getattr(request, "source_id", None))
        target = getattr(request, "target_rule_id", getattr(request, "target_id", None))
    if not isinstance(target, int) or (source is not None and not isinstance(source, int)):
        raise GlossaryInvalidRuleError("Для объединения нужны source_rule_id и target_rule_id")
    if source is None and not isinstance(request.get('draft'), dict):
        raise GlossaryInvalidRuleError("Для объединения нужен исходный черновик правила")
    return source, target


def _merge_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _safe_merge_range(source: GlossaryInfotypeRule, target: GlossaryInfotypeRule) -> tuple[int | None, int | None]:
    """Return a range that preserves exact rule semantics, if safely mergeable."""
    source_prefixes = {item.normalized_prefix for item in source.prefixes_rel}
    target_prefixes = {item.normalized_prefix for item in target.prefixes_rel}
    if source.number_from == target.number_from and source.number_to == target.number_to:
        return source.number_from, source.number_to
    if source_prefixes != target_prefixes:
        return None, None
    if source.number_from <= target.number_to + 1 and target.number_from <= source.number_to + 1:
        return min(source.number_from, target.number_from), max(source.number_to, target.number_to)
    return None, None
