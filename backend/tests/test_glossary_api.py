from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.settings import get_settings as settings_dependency
from app.config import Settings
from app.main import create_app
from app.services import audit
from app.services.audit import AuditService
from app.db.models import GlossaryState
from app.db.session import session_scope


ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}


def test_merge_preview_exposes_typed_result_and_third_owner_conflicts(client):
    login(client, 'admin')
    from app.services.glossary.registry import GlossaryRegistry
    registry = GlossaryRegistry()
    target = registry.create(None, 'business_term', 'Target')
    owner = registry.create(None, 'business_term', 'Reserved name')
    request = dict(request_id=str(uuid4()), target_term_id=target['id'], target_version=target['version'],
        draft=dict(kind='business_term', original_name='Draft', aliases=[dict(alias='Reserved name')]),
        selections=dict(original_name='target'))
    response = client.post('/api/admin/glossary/merge/preview', json=request)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['result']['id'] == target['id']
    assert preview['removed_term_id'] is None
    assert preview['preview_digest'] == preview['digest']
    assert preview['revision'] == preview['glossary_revision']
    assert preview['requires_confirmation'] is True
    assert any(owner['id'] in (item.get('left_term_id'), item.get('right_term_id'), item.get('term_id'))
               for item in preview['conflicts'])
    request.update(preview_digest=preview['digest'], expected_revision=preview['revision'])
    rejected = client.post('/api/admin/glossary/merge', json=request)
    assert rejected.status_code == 409
    assert rejected.json()['preview']['conflicts']
    assert len(registry.list()) == 2


def test_rules_check_is_read_only(client):
    login(client, 'admin')
    from app.services.glossary.rule_registry import GlossaryRuleRegistry
    registry = GlossaryRuleRegistry()
    rule = registry.create(name='Main', number_from=0, number_to=999, prefixes=['IT'])
    response = client.post('/api/admin/glossary/rules/check', json=dict(
        name='Draft', number_from=900, number_to=1100, prefixes=['IT']))
    assert response.status_code == 200, response.text
    assert response.json()['conflicts'][0]['id'] == rule['id']
    assert len(registry.list()) == 1


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        auth_provider="simulation",
        glossary_query_expansion_enabled=False,
        auth_role_groups=ROLE_GROUPS,
        auth_default_role="viewer",
        auth_sim_users=[
            {"user_id": "viewer", "username": "viewer", "groups": ["KB_Viewer"]},
            {"user_id": "editor", "username": "editor", "groups": ["KB_Editor"]},
            {"user_id": "admin", "username": "admin", "groups": ["KB_Admin"]},
            {"user_id": "security", "username": "security", "groups": ["KB_Security"]},
        ],
    )
    for module in ("app.config", "app.main", "app.auth.api", "app.api.settings"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[settings_dependency] = lambda: settings
    return TestClient(app)


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def login(client, username):
    response = client.post("/api/auth/simulate", json={"username": username})
    assert response.status_code == 200, response.text


def test_chat_settings_exposes_glossary_expansion_state(client):
    login(client, "viewer")
    response = client.get("/api/settings")

    assert response.status_code == 200, response.text
    assert response.json()["glossary_query_expansion_enabled"] is False
    assert response.json()["knowledge_profile"] == "Основной контур"


def create_term(client, **overrides):
    payload = {
        "canonical": "IT0003",
        "kind": "sap_infotype",
        "original_name": "Infotype three",
        "original_description": "Description",
        "canonical_locale": "en",
    }
    payload.update(overrides)
    return client.post("/api/admin/glossary", json=payload)


def test_glossary_crud_is_role_gated_and_audited(client):
    login(client, "editor")
    assert client.get("/api/admin/glossary").status_code == 200
    assert create_term(client).status_code == 403

    login(client, "admin")
    response = create_term(
        client,
        aliases=[
            {
                "alias": "ИТ 0003",
                "locale": "ru",
                "auto_expand": True,
                "search_enabled": True,
            }
        ],
    )
    assert response.status_code == 201, response.text
    term = response.json()
    assert len(term["aliases"]) == 1
    assert AuditService().query(action_type=audit.GLOSSARY_TERM_CREATE)

    listed = client.get("/api/admin/glossary?limit=1&offset=0&q=Infotype").json()
    assert listed["total"] == 1
    assert listed["terms"][0]["id"] == term["id"]

    updated = client.patch(
        f"/api/admin/glossary/{term['id']}",
        json={"version": term["version"], "enabled": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is False


def test_only_admin_can_change_source_and_source_revision_is_audited(client):
    login(client, "admin")
    term = create_term(client).json()
    response = client.patch(
        f"/api/admin/glossary/{term['id']}/source",
        json={
            "version": term["version"],
            "original_name": "Updated name",
            "canonical_locale": "de",
            "enabled": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["source_revision"] == term["source_revision"] + 1
    assert response.json()["enabled"] is False
    assert AuditService().query(action_type=audit.GLOSSARY_SOURCE_UPDATE)

    login(client, "editor")
    forbidden = client.patch(
        f"/api/admin/glossary/{term['id']}/source",
        json={"version": response.json()["version"], "original_name": "Nope"},
    )
    assert forbidden.status_code == 403


def test_glossary_alias_validation_conflict_and_version_errors(client):
    login(client, "admin")
    created = create_term(client).json()

    unsafe = client.post(
        f"/api/admin/glossary/{created['id']}/aliases",
        json={
            "version": created["version"],
            "alias": "PA",
            "auto_expand": True,
            "search_enabled": True,
        },
    )
    assert unsafe.status_code == 422
    assert unsafe.json()["code"] == "glossary_unsafe_auto_expand"

    stale = client.patch(
        f"/api/admin/glossary/{created['id']}",
        json={"version": created["version"] + 1, "enabled": False},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "version_conflict"

    foreign_alias = client.delete(
        f"/api/admin/glossary/{created['id']}/aliases/999999?version={created['version']}"
    )
    assert foreign_alias.status_code == 404
    assert foreign_alias.json()["code"] == "glossary_alias_not_found"


def test_glossary_alias_lifecycle_uses_term_version(client):
    login(client, "admin")
    term = create_term(client).json()
    added = client.post(
        f"/api/admin/glossary/{term['id']}/aliases",
        json={"version": term["version"], "alias": "infotype 3", "auto_expand": True},
    )
    assert added.status_code == 200, added.text
    current = added.json()
    alias_id = next(item["id"] for item in current["aliases"] if item["alias"] == "infotype 3")

    changed = client.patch(
        f"/api/admin/glossary/{term['id']}/aliases/{alias_id}",
        json={"version": current["version"], "alias": "infotype three alt", "auto_expand": True},
    )
    assert changed.status_code == 200, changed.text
    latest = changed.json()
    deleted = client.delete(
        f"/api/admin/glossary/{term['id']}/aliases/{alias_id}?version={latest['version']}"
    )
    assert deleted.status_code == 200, deleted.text


def test_glossary_preview_is_available_to_editor_but_not_user(client):
    login(client, "editor")
    response = client.post("/api/admin/glossary/preview", json={"query": "ИТ 0003"})
    assert response.status_code == 200, response.text
    assert response.json()["original_query"] == "ИТ 0003"
    assert "dense_query" in response.json()

    login(client, "viewer")
    assert client.post("/api/admin/glossary/preview", json={"query": "ИТ 0003"}).status_code == 403


def test_glossary_preview_returns_migration_required_while_state_is_unready(client):
    login(client, "editor")
    with session_scope() as session:
        state = session.get(GlossaryState, 1)
        state.identities_ready = False
    response = client.post("/api/admin/glossary/preview", json={"query": "IT0003"})
    assert response.status_code == 503
    assert response.json()["code"] == "glossary_migration_required"


def test_merge_preview_is_read_only_and_commit_is_audited(client):
    login(client, "admin")
    source = create_term(client, canonical=None, kind="business_term", original_name="Source term", aliases=[{"alias": "source", "auto_expand": True}]).json()
    target = create_term(client, canonical=None, kind="business_term", original_name="Target term").json()
    request = {
        "request_id": str(uuid4()),
        "source_term_id": source["id"],
        "target_term_id": target["id"],
        "source_version": source["version"],
        "target_version": target["version"],
        "selections": {"original_name": "target"},
    }
    preview = client.post("/api/admin/glossary/merge/preview", json=request)
    assert preview.status_code == 200, preview.text
    proposal = preview.json()
    assert proposal["source"]["id"] == source["id"]
    assert proposal["merged"]["id"] == target["id"]
    assert proposal["glossary_revision"] >= 1
    assert client.get(f"/api/admin/glossary/{source['id']}").status_code == 200

    request["preview_digest"] = proposal["digest"]
    request["expected_revision"] = proposal["glossary_revision"]
    committed = client.post("/api/admin/glossary/merge", json=request)
    assert committed.status_code == 200, committed.text
    assert committed.json()["id"] == target["id"]
    assert client.get(f"/api/admin/glossary/{source['id']}").status_code == 404
    assert AuditService().query(action_type=audit.GLOSSARY_TERM_MERGE)


def test_merge_requires_current_preview_and_replays_same_request(client):
    login(client, "admin")
    source = create_term(client, canonical=None, kind="business_term", original_name="Stale source").json()
    target = create_term(client, canonical=None, kind="business_term", original_name="Stale target").json()
    request = {
        "request_id": str(uuid4()),
        "source_term_id": source["id"],
        "target_term_id": target["id"],
        "source_version": source["version"],
        "target_version": target["version"],
        "selections": {"original_description": "source", "original_name": "target"},
    }
    proposal = client.post("/api/admin/glossary/merge/preview", json=request).json()
    changed = client.patch(
        f"/api/admin/glossary/{source['id']}/source",
        json={"version": source["version"], "original_description": "changed after preview"},
    )
    assert changed.status_code == 200, changed.text
    request.update(preview_digest=proposal["digest"], expected_revision=proposal["glossary_revision"])
    stale = client.post("/api/admin/glossary/merge", json=request)
    assert stale.status_code == 409, stale.text
    assert stale.json()['code'] == 'glossary_merge_preview_stale'

    fresh_source = client.get(f"/api/admin/glossary/{source['id']}").json()
    fresh_target = client.get(f"/api/admin/glossary/{target['id']}").json()
    request["source_version"] = fresh_source["version"]
    request["target_version"] = fresh_target["version"]
    proposal = client.post("/api/admin/glossary/merge/preview", json=request).json()
    request["preview_digest"] = proposal["digest"]
    request["expected_revision"] = proposal["glossary_revision"]
    committed = client.post("/api/admin/glossary/merge", json=request)
    assert committed.status_code == 200, committed.text
    replay = client.post("/api/admin/glossary/merge", json=request)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == committed.json()["id"]
    mismatched_replay = client.post('/api/admin/glossary/merge', json={**request, 'selections': {'original_name': 'source'}})
    assert mismatched_replay.status_code == 409
    assert mismatched_replay.json()['code'] == 'glossary_idempotency_conflict'


def test_infotype_rule_forms_share_literal_identity_namespace(client):
    login(client, "admin")
    rule = client.post(
        "/api/admin/glossary/rules",
        json={"name": "IT test", "number_from": 0, "number_to": 999, "prefixes": ["IT"]},
    )
    assert rule.status_code == 201, rule.text
    response = create_term(client, kind="sap_transaction", original_name="IT0003")
    assert response.status_code == 409, response.text


def test_new_infotype_requires_explicit_or_legacy_number(client):
    login(client, "admin")
    response = create_term(client, canonical=None, kind="sap_infotype", original_name="Missing number")
    assert response.status_code == 422, response.text
    valid = create_term(client, canonical=None, kind="sap_infotype", original_name="Payroll status", infotype_number="0003")
    assert valid.status_code == 201, valid.text
    assert valid.json()["infotype_number"] == "0003"


def test_source_patch_updates_number_and_rejects_incompatible_kind(client):
    login(client, "admin")
    term = create_term(client, canonical=None, kind="sap_infotype", original_name="Payroll status", infotype_number="0003").json()
    changed = client.patch(f"/api/admin/glossary/{term['id']}/source", json={"version": term["version"], "infotype_number": "0004"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["infotype_number"] == "0004"
    invalid = create_term(client, canonical=None, kind="sap_transaction", original_name="PA30", infotype_number="0003")
    assert invalid.status_code == 422, invalid.text


def test_source_can_be_explicitly_reclassified_as_infotype(client):
    login(client, 'admin')
    term = create_term(client, canonical=None, kind='sap_transaction', original_name='IT0003').json()
    response = client.patch(f"/api/admin/glossary/{term['id']}/source",
        json=dict(version=term['version'], kind='sap_infotype', infotype_number='0003'))
    assert response.status_code == 200, response.text
    assert response.json()['kind'] == 'sap_infotype'
    assert response.json()['infotype_number'] == '0003'
    assert client.post('/api/admin/glossary/rules', json=dict(name='PA', number_from=0, number_to=999, prefixes=['IT'])).status_code == 201


def test_merge_source_edit_preserves_disabled_state_only_after_explicit_choice(client):
    login(client, 'admin')
    source = create_term(client, canonical=None, kind='business_term', original_name='Source').json()
    target = create_term(client, canonical=None, kind='business_term', original_name='Target').json()
    request = dict(request_id=str(uuid4()), source_term_id=source['id'], target_term_id=target['id'],
                   source_version=source['version'], target_version=target['version'],
                   source_edit=dict(kind='business_term', original_name='Source', canonical_locale=source['canonical_locale'], enabled=False),
                   selections={'original_name': 'target'})
    preview = client.post('/api/admin/glossary/merge/preview', json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()['source']['enabled'] is False
    assert 'enabled' in preview.json()['unresolved_fields']
    unresolved_request = {**request, 'preview_digest': preview.json()['digest'], 'expected_revision': preview.json()['glossary_revision']}
    unresolved_commit = client.post('/api/admin/glossary/merge', json=unresolved_request)
    assert unresolved_commit.status_code == 409
    assert unresolved_commit.json()['code'] == 'glossary_merge_choices_required'
    request['selections']['enabled'] = 'source'
    preview = client.post('/api/admin/glossary/merge/preview', json=request)
    assert preview.status_code == 200, preview.text
    request.update(preview_digest=preview.json()['digest'], expected_revision=preview.json()['glossary_revision'])
    committed = client.post('/api/admin/glossary/merge', json=request)
    assert committed.status_code == 200, committed.text
    assert committed.json()['enabled'] is False
    assert client.get(f"/api/admin/glossary/{source['id']}").status_code == 404


@pytest.mark.parametrize('patch', [
    {'request_id': 'x' * 36},
    {'selections': {'alias_choices': [{'normalized_alias': 'test', 'auto_expand': 'yes', 'search_enabled': True, 'locale': 'en'}]}},
    {'selections': {'unknown_field': 'source'}},
])
def test_merge_request_rejects_invalid_typed_fields(client, patch):
    login(client, 'admin')
    source = create_term(client, canonical=None, kind='business_term', original_name='Source').json()
    target = create_term(client, canonical=None, kind='business_term', original_name='Target').json()
    request = dict(request_id=str(uuid4()), source_term_id=source['id'], target_term_id=target['id'],
                   source_version=source['version'], target_version=target['version'], selections={'original_name': 'target'})
    request.update(patch)
    response = client.post('/api/admin/glossary/merge/preview', json=request)
    assert response.status_code == 422, response.text


def test_merge_alias_choice_keeps_explicit_null_locale(client):
    login(client, 'admin')
    source = create_term(client, canonical=None, kind='business_term', original_name='Source').json()
    target = create_term(client, canonical=None, kind='business_term', original_name='Target').json()
    request = dict(request_id=str(uuid4()), source_term_id=source['id'], target_term_id=target['id'],
                   source_version=source['version'], target_version=target['version'],
                   selections={'original_name': 'target', 'alias_choices': [
                       dict(normalized_alias='source', locale=None, auto_expand=True, search_enabled=True)]})
    preview = client.post('/api/admin/glossary/merge/preview', json=request)
    assert preview.status_code == 200, preview.text
    request.update(preview_digest=preview.json()['digest'], expected_revision=preview.json()['glossary_revision'])
    result = client.post('/api/admin/glossary/merge', json=request)
    assert result.status_code == 200, result.text
    assert result.json()['aliases'][0]['locale'] is None


def test_unready_glossary_rejects_term_and_translation_writes_with_503(client):
    login(client, 'admin')
    term = create_term(client, canonical=None, kind='business_term', original_name='Source').json()
    with session_scope() as session:
        session.get(GlossaryState, 1).identities_ready = False
    responses = [create_term(client, canonical=None, kind='business_term', original_name='New'),
        client.patch(f"/api/admin/glossary/{term['id']}/source", json={'version': term['version'], 'original_name': 'Changed'}),
        client.patch(f"/api/admin/glossary/{term['id']}/translations/ru",
            json={'translation_version': 0, 'source_revision': term['source_revision'], 'display_name': 'Источник'})]
    for response in responses:
        assert response.status_code == 503, response.text
        assert response.json()['code'] == 'glossary_migration_required'


def test_draft_merge_never_creates_temporary_source(client):
    login(client, "admin")
    target = create_term(client, canonical=None, kind="business_term", original_name="Target").json()
    request = {"request_id": str(uuid4()), "target_term_id": target["id"], "target_version": target["version"],
        "draft": {"kind": "business_term", "original_name": "Draft", "canonical_locale": "ru",
                  "aliases": [{"alias": "Target"}, {"alias": "Added form", "auto_expand": True, "search_enabled": True}]},
        "selections": {"original_name": "target", "canonical_locale": "target", "original_description": "target"}}
    preview = client.post("/api/admin/glossary/merge/preview", json=request)
    assert preview.status_code == 200, preview.text
    assert client.get("/api/admin/glossary").json()["total"] == 1
    proposal = preview.json()
    request.update(preview_digest=proposal["digest"], expected_revision=proposal["glossary_revision"])
    result = client.post("/api/admin/glossary/merge", json=request)
    assert result.status_code == 200, result.text
    assert {a["alias"] for a in result.json()["aliases"]} == {"Draft", "Added form"}
    assert client.get("/api/admin/glossary").json()["total"] == 1
    replay = client.post("/api/admin/glossary/merge", json=request)
    assert replay.status_code == 200, replay.text
    assert replay.json() == result.json()


def test_merge_preserves_pending_source_alias_edit_and_its_flags(client):
    login(client, 'admin')
    source = create_term(client, canonical=None, kind='business_term', original_name='Source',
                         aliases=[{'alias': 'Old alias', 'auto_expand': False}]).json()
    target = create_term(client, canonical=None, kind='business_term', original_name='Target',
                         aliases=[{'alias': 'Shared alias', 'auto_expand': False}]).json()
    request = dict(request_id=str(uuid4()), source_term_id=source['id'], source_version=source['version'],
                   target_term_id=target['id'], target_version=target['version'],
                   source_edit=dict(kind='business_term', original_name='Source', canonical_locale='en',
                                    original_description='Description',
                                    aliases=[dict(alias='Shared alias', locale='en', auto_expand=True, search_enabled=True)]),
                   selections=dict(original_name='target', alias_choices=[dict(normalized_alias='shared alias',
                                    locale='en', auto_expand=True, search_enabled=True)]))
    preview = client.post('/api/admin/glossary/merge/preview', json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()['source']['aliases'][0]['alias'] == 'Shared alias'
    assert client.get(f"/api/admin/glossary/{source['id']}").json()['aliases'][0]['alias'] == 'Old alias'
    request.update(preview_digest=preview.json()['digest'], expected_revision=preview.json()['glossary_revision'])
    merged = client.post('/api/admin/glossary/merge', json=request)
    assert merged.status_code == 200, merged.text
    aliases = {item['alias']: item for item in merged.json()['aliases']}
    assert 'Old alias' not in aliases
    assert aliases['Shared alias']['auto_expand'] and aliases['Shared alias']['search_enabled']


def test_glossary_preview_validates_locale_and_exposes_skipped_reasons(client, monkeypatch):
    login(client, "admin")
    first = create_term(client, canonical="PA01", kind="sap_transaction", original_name="Transaction one", aliases=[{"alias": "PA01", "auto_expand": True}]).json()
    second = create_term(client, canonical="PA02", kind="sap_transaction", original_name="Transaction two", aliases=[{"alias": "PA02", "auto_expand": True}]).json()
    assert first["id"] != second["id"]

    class PreviewSettings:
        glossary_max_terms_per_query = 1
        glossary_max_added_aliases_per_term = 4
        glossary_max_added_tokens = 32
        glossary_max_added_chars = 768
        glossary_query_text_max_chars = 8192

    monkeypatch.setattr("app.api.glossary.get_settings", lambda: PreviewSettings())
    response = client.post(
        "/api/admin/glossary/preview",
        json={"query": "PA01 PA02", "locale": "en"},
    )
    assert response.status_code == 200, response.text
    assert any(
        item["canonical"] == "PA02" and item["reason"] == "max_terms_per_query"
        for item in response.json()["skipped_reasons"]
    )

    invalid = client.post(
        "/api/admin/glossary/preview",
        json={"query": "PA01", "locale": "not-a-locale"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "glossary_invalid_locale"


def test_glossary_static_preview_route_is_not_parsed_as_term_id(client):
    login(client, "editor")
    response = client.get("/api/admin/glossary/translations/pending")
    assert response.status_code == 422


def test_alias_preflight_and_persistent_duplicate_lifecycle(client):
    login(client, "admin")
    first = create_term(client, canonical=None, kind="business_term", aliases=[{"alias": "Payroll", "auto_expand": True}]).json()
    second = create_term(client, canonical=None, kind="business_term", original_name="Русское имя").json()
    assert first["canonical"] != second["canonical"]
    checked = client.post("/api/admin/glossary/aliases/check", json={"aliases": [" payroll "], "term_id": second["id"]})
    assert checked.status_code == 200, checked.text
    assert checked.json()["conflicts"][0]["term_id"] == first["id"]
    assert not client.get(f"/api/admin/glossary/{first['id']}").json()["has_duplicates"]
    saved = client.post(f"/api/admin/glossary/{second['id']}/aliases", json={"version": second["version"], "alias": "PAYROLL"})
    assert saved.status_code == 409, saved.text
    assert saved.json()["code"] == "glossary_identity_conflict"
    assert not client.get(f"/api/admin/glossary/{first['id']}").json()["has_duplicates"]
    login(client, "editor")
    assert client.post("/api/admin/glossary/aliases/check", json={"aliases": ["Payroll"]}).status_code == 200
    login(client, "viewer")
    assert client.post("/api/admin/glossary/aliases/check", json={"aliases": ["Payroll"]}).status_code == 403


def test_glossary_pending_route_is_static_and_role_gated(client):
    login(client, "editor")
    response = client.get("/api/admin/glossary/translations/pending?locale=en")
    assert response.status_code == 200, response.text
    assert response.json()["pending"]["missing"] == 0

    login(client, "security")
    assert client.get("/api/admin/glossary?limit=1").status_code == 403


def test_rule_api_is_admin_only_and_revision_is_visible_in_preview(client):
    login(client, "editor")
    payload = {"name": "PA", "number_from": 0, "number_to": 999, "prefixes": ["IT"], "enabled": True}
    assert client.post("/api/admin/glossary/rules", json=payload).status_code == 403
    login(client, "admin")
    created = client.post("/api/admin/glossary/rules", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["prefixes"] == ["it"]
    preview = client.post("/api/admin/glossary/preview", json={"query": "IT0003"})
    assert preview.status_code == 200
    assert preview.json()["glossary_revision"] >= 1


def test_rule_merge_api_preview_commit_and_stale_guard(client):
    login(client, 'admin')
    source = client.post('/api/admin/glossary/rules', json=dict(name='Latin', number_from=0, number_to=999, prefixes=['IT'])).json()
    target = client.post('/api/admin/glossary/rules', json=dict(name='Russian', number_from=0, number_to=999, prefixes=['ИТ'])).json()
    request = dict(source_rule_id=source['id'], source_version=source['version'],
                   target_rule_id=target['id'], target_version=target['version'])
    login(client, 'editor')
    preview = client.post('/api/admin/glossary/rules/merge/preview', json=request)
    assert preview.status_code == 200, preview.text
    request.update(preview_digest=preview.json()['digest'], expected_revision=preview.json()['glossary_revision'])
    assert client.post('/api/admin/glossary/rules/merge', json=request).status_code == 403
    login(client, 'admin')
    bad = {**request, 'preview_digest': '0' * 64}
    assert client.post('/api/admin/glossary/rules/merge', json=bad).status_code == 409
    committed = client.post('/api/admin/glossary/rules/merge', json=request)
    assert committed.status_code == 200, committed.text
    assert set(committed.json()['prefixes']) == {'it', 'ит'}
    assert len(client.get('/api/admin/glossary/rules').json()) == 1


def test_rule_merge_accepts_conflicting_unsaved_draft_without_temporary_row(client):
    login(client, 'admin')
    target = client.post('/api/admin/glossary/rules', json=dict(name='Latin', number_from=0, number_to=999, prefixes=['IT'])).json()
    draft = dict(name='Both', number_from=0, number_to=999, prefixes=['IT', 'ИТ'])
    assert client.post('/api/admin/glossary/rules', json=draft).status_code == 409
    request = dict(draft=draft, target_rule_id=target['id'], target_version=target['version'])
    preview = client.post('/api/admin/glossary/rules/merge/preview', json=request)
    assert preview.status_code == 200, preview.text
    assert len(client.get('/api/admin/glossary/rules').json()) == 1
    request.update(preview_digest=preview.json()['digest'], expected_revision=preview.json()['glossary_revision'])
    result = client.post('/api/admin/glossary/rules/merge', json=request)
    assert result.status_code == 200, result.text
    assert set(result.json()['prefixes']) == {'it', 'ит'}
    assert len(client.get('/api/admin/glossary/rules').json()) == 1


def test_glossary_schema_validation_returns_stable_codes(client):
    login(client, "admin")
    too_long = create_term(client, aliases=[{"alias": "x" * 257}])
    assert too_long.status_code == 422
    assert too_long.json()["code"] == "glossary_invalid_alias"

    invalid_locale = create_term(client, canonical_locale="not-a-locale")
    assert invalid_locale.status_code == 422
    assert invalid_locale.json()["code"] == "glossary_invalid_locale"


def test_cookie_authenticated_glossary_mutation_requires_csrf(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        auth_provider="keycloak_oidc",
        auth_role_groups=ROLE_GROUPS,
        auth_default_role=None,
        keycloak_url="https://sso.test",
        keycloak_realm="test",
        keycloak_client_id="client",
        keycloak_client_secret="secret",
    )
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    client = TestClient(create_app())
    response = client.post(
        "/api/admin/glossary",
        cookies={"session": "opaque", "csrf_token": "token"},
        json={"canonical": "IT0003", "kind": "sap_infotype", "original_name": "Name"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_glossary_translation_patch_is_editor_allowed_and_uses_independent_versions(client):
    login(client, "admin")
    term = create_term(client, canonical="PA20", kind="sap_transaction").json()

    login(client, "editor")
    response = client.patch(
        f"/api/admin/glossary/{term['id']}/translations/ru",
        json={
            "translation_version": 0,
            "source_revision": term["source_revision"],
            "display_name": "Русское имя",
            "description": "Русское описание",
        },
    )
    assert response.status_code == 200, response.text
    translation = response.json()["translations"][0]
    assert translation["reviewed_by"] == "editor"

    stale = client.patch(
        f"/api/admin/glossary/{term['id']}/translations/ru",
        json={
            "translation_version": 0,
            "source_revision": term["source_revision"],
            "display_name": "Конфликт",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "version_conflict"


def test_glossary_translation_backfill_is_admin_only_and_limited_to_ten(client, monkeypatch):
    login(client, "editor")
    forbidden = client.post(
        "/api/admin/glossary/translations/backfill",
        json={"locale": "ru", "term_ids": [1]},
    )
    assert forbidden.status_code == 403

    login(client, "admin")
    monkeypatch.setattr(
        "app.api.glossary.backfill_glossary_translations",
        lambda *args, **kwargs: {"status": "completed", "requested": len(args[1])},
    )
    response = client.post(
        "/api/admin/glossary/translations/backfill",
        json={"locale": "ru", "term_ids": [1], "expected_translation_versions": {1: 0}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["requested"] == 1

    too_many = client.post(
        "/api/admin/glossary/translations/backfill",
        json={"locale": "ru", "term_ids": list(range(1, 12))},
    )
    assert too_many.status_code == 422
