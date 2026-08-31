"""API-тесты удаления неиспользуемых тегов из справочника (Этап 4a)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.audit import TAG_CLEANUP, TAG_DELETE, AuditService
from app.services.registry import get_registry
from app.services.tag_registry import TagRegistry

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        auth_provider="simulation",
        auth_role_groups=ROLE_GROUPS,
        auth_default_role="viewer",
        dedup_enabled=False,
        auth_sim_users=[
            {"user_id": "sim-editor", "username": "demo.editor", "groups": ["KB_Editor"]},
            {"user_id": "sim-viewer", "username": "demo.viewer", "groups": ["KB_Viewer"]},
            {"user_id": "sim-admin", "username": "demo.admin", "groups": ["KB_Admin"]},
        ],
    )
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def login(client, username):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


class TestDeleteTagApi:
    def test_editor_can_delete_unused(self, client):
        TagRegistry().add(["garbage", "keep"])
        get_registry().create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["keep"])
        login(client, "demo.editor")

        resp = client.delete("/api/tags/garbage")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"deleted": "garbage"}
        names = {t["name"] for t in TagRegistry().all()}
        assert names == {"keep"}
        entries = AuditService().query(action_type=TAG_DELETE)
        assert len(entries) == 1
        assert entries[0]["target_id"] == "garbage"

    def test_viewer_forbidden(self, client):
        login(client, "demo.viewer")
        resp = client.delete("/api/tags/garbage")
        assert resp.status_code == 403

    def test_missing_tag_404(self, client):
        login(client, "demo.editor")
        resp = client.delete("/api/tags/nope")
        assert resp.status_code == 404

    def test_used_tag_409(self, client):
        TagRegistry().add(["keep"])
        get_registry().create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["keep"])
        login(client, "demo.editor")
        resp = client.delete("/api/tags/keep")
        assert resp.status_code == 409


class TestCleanupApi:
    def test_cleans_all_unused(self, client):
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["keep"])
        TagRegistry().add(["keep", "g1", "g2"])
        login(client, "demo.editor")

        resp = client.post("/api/tags/cleanup")

        assert resp.status_code == 200, resp.text
        assert sorted(resp.json()["deleted"]) == ["g1", "g2"]
        assert resp.json()["total"] == 2
        assert {t["name"] for t in TagRegistry().all()} == {"keep"}
        entries = AuditService().query(action_type=TAG_CLEANUP)
        assert len(entries) == 1
        assert sorted(entries[0]["new_value"]["deleted"]) == ["g1", "g2"]

    def test_viewer_forbidden(self, client):
        login(client, "demo.viewer")
        resp = client.post("/api/tags/cleanup")
        assert resp.status_code == 403