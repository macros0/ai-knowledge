"""Тесты Этапа 3: разделение списка документов «мои / все» (query-параметр scope)."""
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


def seed_docs():
    DocumentRegistry().create("a" * 16, "mine.docx", "doc", 10, uploaded_by="demo.editor")
    DocumentRegistry().create("b" * 16, "other.docx", "doc", 20, uploaded_by="demo.admin")


class TestScopeDefault:
    def test_editor_default_is_mine(self, client):
        """Граничный случай: editor без scope (scope=None) должен получить МОИ документы.

        Важно проверить порядок условий: `scope=None` резолвится в «mine» для
        can_own-ролей, а не в «all» по ошибке (можно перепутать местами в коде).
        """
        seed_docs()
        login(client, "demo.editor")
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        assert {d["filename"] for d in resp.json()["documents"]} == {"mine.docx"}

    def test_editor_explicit_mine(self, client):
        seed_docs()
        login(client, "demo.editor")
        resp = client.get("/api/documents?scope=mine")
        assert {d["filename"] for d in resp.json()["documents"]} == {"mine.docx"}

    def test_editor_all_includes_others(self, client):
        seed_docs()
        login(client, "demo.editor")
        resp = client.get("/api/documents?scope=all")
        assert {d["filename"] for d in resp.json()["documents"]} == {"mine.docx", "other.docx"}

    def test_viewer_default_is_all(self, client):
        """viewer не заводит своих документов — без scope видит весь список."""
        seed_docs()
        login(client, "demo.user")
        resp = client.get("/api/documents")
        assert {d["filename"] for d in resp.json()["documents"]} == {"mine.docx", "other.docx"}

    def test_security_default_is_all(self, client):
        seed_docs()
        login(client, "demo.security")
        resp = client.get("/api/documents")
        assert {d["filename"] for d in resp.json()["documents"]} == {"mine.docx", "other.docx"}

    def test_uploaded_by_present(self, client):
        seed_docs()
        login(client, "demo.editor")
        resp = client.get("/api/documents?scope=all")
        by_filename = {d["filename"]: d.get("uploaded_by") for d in resp.json()["documents"]}
        assert by_filename["mine.docx"] == "demo.editor"
        assert by_filename["other.docx"] == "demo.admin"

    def test_invalid_scope_422(self, client):
        seed_docs()
        login(client, "demo.editor")
        assert client.get("/api/documents?scope=nope").status_code == 422


def test_disabled_mode_returns_all(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, auth_provider="disabled")
    DocumentRegistry().create("c" * 16, "x.docx", "doc", 10, uploaded_by="demo.editor")
    resp = client.get("/api/documents")
    assert resp.status_code == 200
    assert {d["filename"] for d in resp.json()["documents"]} == {"x.docx"}
