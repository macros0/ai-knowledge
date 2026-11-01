"""A cache revision follows committed catalog edits, not local invalidation."""
from types import SimpleNamespace

import pytest
from starlette.responses import Response

from app.config import Settings
from app.db.models import Tag, ConfigRevision
from app.db.session import session_scope
from app.services.reference_resources import ReferenceKey
from app.services.reference_concurrency import initialize_reference_resource, bump_reference_revision
from app.services.work.coordination import config_revision


@pytest.fixture
def settings(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    return settings


def test_catalog_creation_and_mutation_publish_shared_cache_revision(settings):
    with session_scope() as session:
        session.add(Tag(id=1, canonical_text="one"))
        session.flush()
        state = initialize_reference_resource(session, ReferenceKey("tag", "1"))
        first = config_revision(session, "references")
        assert first > 0
        session.get(Tag, 1).canonical_text = "updated"
        bump_reference_revision(state)
        assert config_revision(session, "references") > first
    with session_scope() as session:
        assert config_revision(session, "references") > first


def test_revision_endpoint_contains_only_opaque_known_versions_and_is_not_cached(settings):
    from app.api.settings import get_config_revisions
    response = Response()
    result = get_config_revisions(response, settings=settings, user=SimpleNamespace(user_id="actor"))
    assert result == {"enabled": True, "revisions": {"references": "0"}}
    assert response.headers["Cache-Control"] == "private, no-store"


def test_failed_reference_edit_does_not_advance_cache_revision(settings):
    with pytest.raises(RuntimeError), session_scope() as session:
        session.add(Tag(id=1, canonical_text="rolled back"))
        session.flush()
        initialize_reference_resource(session, ReferenceKey("tag", "1"))
        raise RuntimeError("audit unavailable")
    with session_scope() as session:
        assert config_revision(session, "references") == 0


def test_revision_wire_format_preserves_bigint_and_hides_unknown_domains(settings):
    from app.api.settings import get_config_revisions
    with session_scope() as session:
        session.add_all([ConfigRevision(domain="references", revision=2**63 - 1),
            ConfigRevision(domain="private", revision=9)])
    result = get_config_revisions(Response(), settings=settings, user=SimpleNamespace(user_id="actor"))
    assert result == {"enabled": True, "revisions": {"references": str(2**63 - 1)}}


def test_legacy_revision_probe_does_not_require_new_coordination_tables(settings, monkeypatch):
    from app.api.settings import get_config_revisions
    settings.okf_deployment_schema = ""
    def forbidden():
        pytest.fail("Legacy probe attempted to read the future schema")
    monkeypatch.setattr("app.db.session.session_scope", forbidden)
    assert get_config_revisions(Response(), settings=settings, user=SimpleNamespace(user_id="actor")) == {
        "enabled": False, "revisions": {}}
