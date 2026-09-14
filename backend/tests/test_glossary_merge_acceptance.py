"""Merge invariants exercised against saved rows, not just the preview DTO."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.models import DomainTermTranslation
from app.db.session import session_scope
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.expansion import prepare_query
from app.services.glossary import translations
from app.services.glossary.rule_registry import GlossaryRuleRegistry


def request_for(source, target, **selections):
    return dict(request_id=str(uuid4()), source_term_id=source['id'],
                target_term_id=target['id'], selections={'original_name': 'target', **selections})


def commit(registry, request):
    preview = registry.merge_preview(request)
    return registry.merge(request, preview['digest'], actor_id='review')


def test_distinct_infotype_numbers_rejected_before_delete():
    registry = GlossaryRegistry()
    source = registry.create(None, 'sap_infotype', 'First', infotype_number='0003')
    target = registry.create(None, 'sap_infotype', 'Second', infotype_number='0004')
    with pytest.raises(ValueError):
        commit(registry, request_for(source, target))
    assert registry.get(source['id'])['infotype_number'] == '0003'
    assert registry.get(target['id'])['infotype_number'] == '0004'


def test_replay_rejects_changed_target_with_original_digest():
    registry = GlossaryRegistry()
    source = registry.create(None, 'business_term', 'Source')
    target = registry.create(None, 'business_term', 'Target')
    other = registry.create(None, 'business_term', 'Other')
    request = request_for(source, target)
    preview = registry.merge_preview(request)
    first = registry.merge(request, preview['digest'], actor_id='review')
    assert registry.merge(request, preview['digest'], actor_id='review') == first
    with pytest.raises(ValueError):
        registry.merge({**request, 'target_term_id': other['id']}, preview['digest'], actor_id='review')


def test_source_translation_never_inherits_equal_revision_freshness():
    registry = GlossaryRegistry()
    source = registry.create(None, 'business_term', 'Source', canonical_locale='ru')
    source = registry.update(source['id'], source['version'], original_description='Updated')
    target = registry.create(None, 'business_term', 'Target', canonical_locale='ru')
    with session_scope() as session:
        session.add(DomainTermTranslation(term_id=source['id'], locale='en', display_name='Source translation',
            source_revision=source['source_revision'], version=1, is_machine_translated=False, reviewed_by='reviewer'))
    result = commit(registry, request_for(source, target, original_description='source'))
    saved = registry.get(result['id'])
    translation = saved['translations'][0]
    assert translation['source_revision'] < saved['source_revision']
    assert translation['reviewed_by'] is None


def test_merge_preserves_target_translation_freshness_when_source_text_unchanged():
    registry = GlossaryRegistry()
    source = registry.create(None, 'business_term', 'Source', canonical_locale='ru')
    target = registry.create(None, 'business_term', 'Target', canonical_locale='ru')
    with session_scope() as session:
        session.add(DomainTermTranslation(term_id=target['id'], locale='en', display_name='Target translation',
            source_revision=target['source_revision'], version=1, is_machine_translated=False, reviewed_by='reviewer'))
    merged = commit(registry, request_for(source, target))
    assert merged['source_revision'] == target['source_revision']
    assert merged['translations'][0]['source_revision'] == merged['source_revision']


def test_translation_collision_requires_explicit_choice_and_keeps_chosen_text():
    registry = GlossaryRegistry()
    source = registry.create(None, 'business_term', 'Source', canonical_locale='ru')
    target = registry.create(None, 'business_term', 'Target', canonical_locale='ru')
    with session_scope() as session:
        for term in (source, target):
            session.add(DomainTermTranslation(term_id=term['id'], locale='en', display_name=term['original_name'],
                source_revision=1, version=1, is_machine_translated=False))
    request = request_for(source, target)
    proposal = registry.merge_preview(request)
    assert 'translations[en]' in proposal['unresolved_fields']
    with pytest.raises(ValueError):
        commit(registry, request)
    request['selections']['translation_choices'] = [{'locale': 'en', 'from_term_id': source['id'], 'expected_version': 1}]
    result = commit(registry, request)
    assert result['translations'][0]['display_name'] == 'Source'
    assert result['translations'][0]['source_revision'] < result['source_revision']


def test_translation_changes_are_visible_without_local_cache_invalidation(monkeypatch):
    registry = GlossaryRegistry()
    term = registry.create(None, 'business_term', 'Source', canonical_locale='ru')
    prepare_query('Source', ui_locale='en', enabled=True)
    monkeypatch.setattr(translations, 'invalidate_snapshot_cache', lambda: None)
    translations.set_glossary_translation(term['id'], 'en', display_name='Translated name',
        description=None, translation_version=0, source_revision=term['source_revision'],
        user=SimpleNamespace(user_id='review', username='review'))
    after = prepare_query('Source', ui_locale='en', enabled=True)
    assert after.applied_terms[0].display_name == 'Translated name'


def test_merge_keeps_previous_safe_name_searchable():
    registry = GlossaryRegistry()
    source = registry.create(None, 'sap_transaction', 'PA30')
    target = registry.create(None, 'sap_transaction', 'Maintain personnel')
    assert prepare_query('PA30', ui_locale='en', enabled=True).applied_terms
    commit(registry, request_for(source, target, original_name='target'))
    assert prepare_query('PA30', ui_locale='en', enabled=True).applied_terms


def test_merge_preview_lists_unresolved_fields_and_commit_refuses_them():
    registry = GlossaryRegistry()
    source = registry.create(None, 'business_term', 'Source', original_description='Source description')
    target = registry.create(None, 'business_term', 'Target', original_description='Target description')
    request = request_for(source, target)
    request['selections'] = {}
    preview = registry.merge_preview(request)
    assert set(preview['unresolved_fields']) == {'original_name', 'original_description'}
    with pytest.raises(ValueError):
        registry.merge(request, preview['digest'], actor_id='review')
    assert registry.get(source['id']) is not None
    request['selections'] = dict(original_name='target', original_description='source')
    selected = registry.merge_preview(request)
    assert selected['unresolved_fields'] == []
    saved = registry.merge(request, selected['digest'], actor_id='review')
    assert saved['original_description'] == 'Source description'


def test_preview_digest_binds_rule_revision_even_without_client_revision():
    registry = GlossaryRegistry()
    source = registry.create(None, 'business_term', 'Source')
    target = registry.create(None, 'business_term', 'Target')
    request = request_for(source, target)
    before = registry.merge_preview(request)
    GlossaryRuleRegistry().create(name='PA', number_from=0, number_to=999, prefixes=['IT'])
    after = registry.merge_preview(request)
    assert after['digest'] != before['digest']
    with pytest.raises(ValueError):
        registry.merge(request, before['digest'], actor_id='review')
