"""Тесты ролевой авторизации (require_role): матрица прав на мутации, аудит, задачи, блокировку."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.registry import DocumentRegistry

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}


def make_client(tmp_path: Path, monkeypatch, **overrides) -> TestClient:
    defaults: dict = {
        "_env_file": None,
        "data_dir": tmp_path,
        "auth_provider": "simulation",
        "auth_role_groups": ROLE_GROUPS,
        "auth_default_role": "viewer",
        "auth_sim_users": [
            {"user_id": "sim-user", "username": "demo.user", "email": "u@d.local", "groups": ["KB_Viewer"]},
            {"user_id": "sim-editor", "username": "demo.editor", "email": "e@d.local", "groups": ["KB_Editor"]},
            {"user_id": "sim-admin", "username": "demo.admin", "email": "a@d.local", "groups": ["KB_Admin"]},
            {"user_id": "sim-security", "username": "demo.security", "email": "s@d.local", "groups": ["KB_Security"]},
        ],
    }
    defaults.update(overrides)
    settings = Settings(**defaults)
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def login(client, username):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


@pytest.fixture
def new_doc():
    def _make(doc_id: str) -> str:
        DocumentRegistry().create(doc_id, "a.docx", "doc", 10)
        return doc_id

    return _make


class TestSingleDeleteRoleGate:
    @pytest.mark.parametrize(
        "username,expected",
        [
            ("demo.user", 403),      # viewer — read-only
            ("demo.editor", 200),
            ("demo.admin", 200),
            ("demo.security", 403),  # security — не editor/admin
        ],
    )
    def test_delete_gate(self, client, new_doc, username, expected):
        doc_id = new_doc("role-del")
        login(client, username)
        resp = client.delete(f"/api/documents/{doc_id}")
        assert resp.status_code == expected


class TestBulkPreviewRoleGate:
    @pytest.mark.parametrize(
        "username,expected",
        [
            ("demo.user", 403),
            ("demo.editor", 403),
            ("demo.security", 403),
            ("demo.admin", 200),
        ],
    )
    def test_bulk_preview_gate(self, client, username, expected):
        login(client, username)
        resp = client.post("/api/documents/bulk-preview", json={"doc_ids": []})
        assert resp.status_code == expected


class TestAuditRoleGate:
    @pytest.mark.parametrize(
        "username,expected",
        [("demo.admin", 403), ("demo.security", 200)],
    )
    def test_audit_gate(self, client, username, expected):
        login(client, username)
        resp = client.get("/api/audit")
        assert resp.status_code == expected


class TestJobsRoleGate:
    @pytest.mark.parametrize(
        "username,expected",
        [("demo.security", 403), ("demo.admin", 200)],
    )
    def test_jobs_gate(self, client, username, expected):
        login(client, username)
        resp = client.get("/api/jobs")
        assert resp.status_code == expected


class TestBlockUserRoleGate:
    def test_block_requires_security(self, client):
        login(client, "demo.admin")
        resp = client.post("/api/users/sim-editor/block", json={"reason": "x"})
        assert resp.status_code == 403

        login(client, "demo.security")
        resp = client.post("/api/users/sim-editor/block", json={"reason": "инцидент"})
        assert resp.status_code == 200

    def test_blocked_user_gets_403(self, client):
        login(client, "demo.security")
        assert client.post("/api/users/sim-editor/block", json={"reason": "x"}).status_code == 200

        login(client, "demo.editor")
        resp = client.get("/api/documents")
        assert resp.status_code == 403
        assert "заблокирован" in resp.json()["detail"].lower()

    def test_unblock_restores_access(self, client):
        login(client, "demo.security")
        client.post("/api/users/sim-editor/block", json={"reason": "x"})
        assert client.post("/api/users/sim-editor/unblock").status_code == 200

        login(client, "demo.editor")
        assert client.get("/api/documents").status_code == 200
