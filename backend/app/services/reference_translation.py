"""Capture before an external call, compare and write in a separate short transaction."""
from dataclasses import dataclass

from sqlalchemy import select

from app.db import models
from app.db.session import session_scope
from app.services.reference_resources import ReferenceKey
from app.services.reference_concurrency import lock_reference_resources, bump_reference_revision
from app.services.reference_edit_lock import live

CONFIG = {
    "tags": (models.Tag, models.TagTranslation, "tag_id", "canonical_text", "text", "tag"),
    "developments": (models.Development, models.DevelopmentTranslation, "development_id", "name", "name", "development"),
    "attributes": (models.AttributeValue, models.AttributeValueTranslation, "attribute_value_id", "label", "label", "attribute_catalog"),
}
SKIPS = ("skipped_locked", "skipped_changed", "skipped_reviewed", "skipped_deleted")


@dataclass(frozen=True)
class TranslationCandidate:
    entity: str
    entity_id: int
    locale: str
    resource_key: ReferenceKey
    reference_revision: int
    source_text: str
    source_locale: str
    translation_snapshot: tuple | None


def _translation(session, entity, identifier, locale):
    _, model, fk, _, _, _ = CONFIG[entity]
    return session.execute(select(model).where(getattr(model, fk) == identifier, model.locale == locale)
        .execution_options(populate_existing=True)).scalar_one_or_none()


def _snapshot(entity, row):
    if row is None:
        return None
    field = CONFIG[entity][4]
    return (getattr(row, field), row.is_machine_translated, row.reviewed_by, row.translated_at)


def capture_translation_candidate(entity, identifier, locale):
    parent_model, _, _, source_field, _, kind = CONFIG[entity]
    with session_scope() as session:
        parent = session.get(parent_model, identifier)
        if parent is None or (entity == "tags" and parent.deleted_at is not None):
            return None, "skipped_deleted"
        key = ReferenceKey(kind, parent.attribute_key, org_id=parent.org_id) if entity == "attributes" else ReferenceKey(kind, str(identifier))
        keys = [key, ReferenceKey("attribute_catalog", parent.attribute_key)] if entity == "attributes" else [key]
        state = lock_reference_resources(session, keys)[key]
        parent = session.get(parent_model, identifier, populate_existing=True)
        if parent is None or (entity == "tags" and parent.deleted_at is not None):
            return None, "skipped_deleted"
        if live(state):
            return None, "skipped_locked"
        row = _translation(session, entity, identifier, locale)
        if row and (row.reviewed_by or not row.is_machine_translated):
            return None, "skipped_reviewed"
        return TranslationCandidate(entity, identifier, locale, key, state.revision,
            getattr(parent, source_field), parent.canonical_locale, _snapshot(entity, row)), None


def commit_translation_candidate(session, candidate, translated_text, *, is_machine=True, actor_name=None):
    entity = candidate.entity
    parent_model, model, fk, source_field, target_field, _ = CONFIG[entity]
    keys = [candidate.resource_key, ReferenceKey("attribute_catalog", candidate.resource_key.resource_id)] if entity == "attributes" else [candidate.resource_key]
    states = lock_reference_resources(session, keys)
    state = states[candidate.resource_key]
    parent = session.get(parent_model, candidate.entity_id, populate_existing=True)
    if parent is None or (entity == "tags" and parent.deleted_at is not None):
        return "skipped_deleted"
    if live(state):
        return "skipped_locked"
    row = _translation(session, entity, candidate.entity_id, candidate.locale)
    if row and (row.reviewed_by or not row.is_machine_translated):
        return "skipped_reviewed"
    if (state.revision != candidate.reference_revision or getattr(parent, source_field) != candidate.source_text
            or parent.canonical_locale != candidate.source_locale or _snapshot(entity, row) != candidate.translation_snapshot):
        return "skipped_changed"
    if row is None:
        row = model(**{fk: candidate.entity_id, "locale": candidate.locale, target_field: translated_text,
                       "is_machine_translated": is_machine})
        session.add(row)
        outcome = "created"
    else:
        if getattr(row, target_field) == translated_text and row.is_machine_translated == is_machine:
            return "unchanged"
        setattr(row, target_field, translated_text)
        row.is_machine_translated = is_machine
        outcome = "updated"
    row.reviewed_by = None
    row.translated_at = None
    bump_reference_revision(state)
    if entity == "attributes" and candidate.resource_key.org_id is not None:
        bump_reference_revision(states[ReferenceKey("attribute_catalog", candidate.resource_key.resource_id)])
    from app.services import audit
    audit.record_in_session(session, action_type=audit.REFERENCE_TRANSLATION_COMMIT,
        username=actor_name, target_type=candidate.resource_key.resource_type,
        target_id=candidate.resource_key.canonical,
        new_value={"locale": candidate.locale, "is_machine_translated": is_machine, "reference_revision": state.revision})
    session.flush()
    return outcome
