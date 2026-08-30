"""Тесты фильтра списка документов по загрузчику (query-параметр uploader)."""
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


class TestUploaderFilter:
    def test_absent_returns_all(self, client):
        seed_docs()
        login(client, "demo.editor")
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        assert {d["filename"] for d in resp.json()["documents"]} == {"mine.docx", "other.docx"}

    def test_uploader_filters_by_username(self, client):
        seed_docs()
        login(client, "demo.editor")
        resp = client.get("/api/documents?uploader=demo.editor")
        assert {d["filename"] for d in resp.json()["documents"]} == {"mine.docx"}

    def test_uploader_other_username(self, client):
        seed_docs()
        login(client, "demo.editor")
        resp = client.get("/api/documents?uploader=demo.admin")
        assert {d["filename"] for d in resp.json()["documents"]} == {"other.docx"}

    def test_unknown_uploader_returns_empty(self, client):
        seed_docs()
        login(client, "demo.editor")
        resp = client.get("/api/documents?uploader=nobody")
        assert resp.status_code == 200
        assert resp.json()["documents"] == []

    def test_uploaded_by_present(self, client):
        seed_docs()
        login(client, "demo.editor")
        resp = client.get("/api/documents")
        by_filename = {d["filename"]: d.get("uploaded_by") for d in resp.json()["documents"]}
        assert by_filename["mine.docx"] == "demo.editor"
        assert by_filename["other.docx"] == "demo.admin"


class TestUploadersEndpoint:
    def test_distinct_sorted(self, client):
        DocumentRegistry().create("a" * 16, "a.docx", "doc", 10, uploaded_by="demo.admin")
        DocumentRegistry().create("b" * 16, "b.docx", "doc", 10, uploaded_by="demo.editor")
        DocumentRegistry().create("c" * 16, "c.docx", "doc", 10, uploaded_by="demo.admin")
        login(client, "demo.editor")
        resp = client.get("/api/documents/uploaders")
        assert resp.status_code == 200
        assert resp.json()["uploaders"] == ["demo.admin", "demo.editor"]


def test_disabled_mode_returns_all(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, auth_provider="disabled")
    DocumentRegistry().create("c" * 16, "x.docx", "doc", 10, uploaded_by="demo.editor")
    resp = client.get("/api/documents")
    assert resp.status_code == 200
    assert {d["filename"] for d in resp.json()["documents"]} == {"x.docx"}
