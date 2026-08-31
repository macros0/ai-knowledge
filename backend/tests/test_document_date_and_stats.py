"""Тесты фильтров даты загрузки (date_from/date_to) и статистики разметки (stats)."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.development_registry import get_development_registry
from app.services.registry import DocumentRegistry

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}


def make_client(tmp_path, monkeypatch, **overrides) -> TestClient:
    defaults: dict = {
        "_env_file": None,
        "data_dir": tmp_path,
        "auth_provider": "simulation",
        "auth_role_groups": ROLE_GROUPS,
        "auth_default_role": "viewer",
        "auth_sim_users": [
            {"user_id": "sim-editor", "username": "demo.editor", "email": "e@d.local", "groups": ["KB_Editor"]},
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


def login(client, username="demo.editor"):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


def seed_doc(reg, doc_id, filename, created_at, dev_id=None):
    reg.create(doc_id, filename, "doc", 10, uploaded_by="demo.editor")
    fields = {"created_at": created_at}
    if dev_id is not None:
        fields["development_id"] = dev_id
    reg.update(doc_id, **fields)


def filenames(resp) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {d["filename"] for d in resp.json()["documents"]}


def utc(y, m, d):
    return datetime(y, m, d, 12, 0, 0, tzinfo=timezone.utc)


class TestDateFilter:
    def test_range_inclusive(self, client):
        reg = DocumentRegistry()
        seed_doc(reg, "a" * 16, "jan.docx", utc(2026, 1, 15))
        seed_doc(reg, "b" * 16, "feb.docx", utc(2026, 2, 10))
        seed_doc(reg, "c" * 16, "mar.docx", utc(2026, 3, 1))
        login(client)
        got = filenames(client.get("/api/documents?date_from=2026-02-01&date_to=2026-02-28"))
        assert got == {"feb.docx"}

    def test_from_only(self, client):
        reg = DocumentRegistry()
        seed_doc(reg, "a" * 16, "jan.docx", utc(2026, 1, 15))
        seed_doc(reg, "b" * 16, "mar.docx", utc(2026, 3, 1))
        login(client)
        got = filenames(client.get("/api/documents?date_from=2026-02-01"))
        assert got == {"mar.docx"}

    def test_to_only_includes_end_of_day(self, client):
        reg = DocumentRegistry()
        seed_doc(reg, "a" * 16, "jan.docx", utc(2026, 1, 15))
        seed_doc(reg, "b" * 16, "feb.docx", utc(2026, 2, 28))
        login(client)
        got = filenames(client.get("/api/documents?date_to=2026-02-28"))
        assert got == {"jan.docx", "feb.docx"}

    def test_invalid_date_ignored(self, client):
        reg = DocumentRegistry()
        seed_doc(reg, "a" * 16, "jan.docx", utc(2026, 1, 15))
        login(client)
        got = filenames(client.get("/api/documents?date_from=not-a-date"))
        assert got == {"jan.docx"}


class TestStats:
    def test_empty_base_zero(self, client):
        login(client)
        resp = client.get("/api/documents/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"total": 0, "with_development": 0}

    def test_progress_counts_only_with_development(self, client):
        dev_id = get_development_registry().create("Пр_10", "Проактив", None)["id"]
        reg = DocumentRegistry()
        seed_doc(reg, "a" * 16, "marked.docx", utc(2026, 1, 1), dev_id=dev_id)
        seed_doc(reg, "b" * 16, "unmarked.docx", utc(2026, 1, 1), dev_id=None)
        login(client)
        resp = client.get("/api/documents/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert body["with_development"] == 1

    def test_deleted_excluded_from_stats(self, client):
        reg = DocumentRegistry()
        seed_doc(reg, "a" * 16, "active.docx", utc(2026, 1, 1))
        seed_doc(reg, "b" * 16, "deleted.docx", utc(2026, 1, 1))
        reg.soft_delete("b" * 16, "demo.editor")
        login(client)
        body = client.get("/api/documents/stats").json()
        assert body["total"] == 1
