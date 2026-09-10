"""Тесты фильтра по source_locale в списке документов + фасеты (Этап 7 фаза D)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.registry import get_registry

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


def seed(doc_id, filename, locale, uploaded_by="demo.editor"):
    reg = get_registry()
    reg.create(doc_id, filename, "doc", 10, tags=[], uploaded_by=uploaded_by)
    if locale is not None:
        reg.update(doc_id, source_locale=locale, source_locale_source="detected")


def filenames(resp):
    return {d["filename"] for d in resp.json()["documents"]}


class TestSourceLocaleListFilter:
    def test_filter_by_codes(self, client):
        seed("a" * 16, "ru.docx", "ru")
        seed("b" * 16, "en.docx", "en")
        seed("c" * 16, "es.docx", "es")
        login(client, "demo.editor")

        resp = client.get("/api/documents?source_locales=ru")
        assert resp.status_code == 200
        assert filenames(resp) == {"ru.docx"}

        resp = client.get("/api/documents?source_locales=ru,en")
        assert filenames(resp) == {"ru.docx", "en.docx"}

    def test_unknown_only(self, client):
        seed("a" * 16, "ru.docx", "ru")
        seed("b" * 16, "none.docx", None)
        login(client, "demo.editor")

        resp = client.get("/api/documents?source_locale_unknown=true")
        assert resp.status_code == 200
        assert filenames(resp) == {"none.docx"}

    def test_codes_or_unknown(self, client):
        seed("a" * 16, "ru.docx", "ru")
        seed("b" * 16, "none.docx", None)
        seed("c" * 16, "es.docx", "es")
        login(client, "demo.editor")

        resp = client.get("/api/documents?source_locales=ru&source_locale_unknown=true")
        assert filenames(resp) == {"ru.docx", "none.docx"}

    def test_no_filter_returns_all(self, client):
        seed("a" * 16, "ru.docx", "ru")
        seed("b" * 16, "none.docx", None)
        login(client, "demo.editor")

        resp = client.get("/api/documents")
        assert filenames(resp) == {"ru.docx", "none.docx"}

    def test_soft_validation_normalizes_case(self, client):
        seed("a" * 16, "ru.docx", "ru")
        login(client, "demo.editor")
        resp = client.get("/api/documents?source_locales=RU")
        assert resp.status_code == 200
        assert filenames(resp) == {"ru.docx"}

    def test_garbage_code_422(self, client):
        seed("a" * 16, "ru.docx", "ru")
        login(client, "demo.editor")
        resp = client.get("/api/documents?source_locales=rus%20sian!")
        assert resp.status_code == 422
        assert resp.json()["code"] == "source_locale_invalid"

    def test_unknown_excluded_when_codes_only(self, client):
        # Документ с NULL не должен «просачиваться» при фильтре по кодам.
        seed("a" * 16, "ru.docx", "ru")
        seed("b" * 16, "none.docx", None)
        login(client, "demo.editor")
        resp = client.get("/api/documents?source_locales=ru")
        assert filenames(resp) == {"ru.docx"}


class TestSourceLocaleFacets:
    def test_facets_counts_and_visibility(self, client):
        seed("a" * 16, "ru1.docx", "ru", uploaded_by="demo.editor")
        seed("b" * 16, "ru2.docx", "ru", uploaded_by="demo.editor")
        seed("c" * 16, "en.docx", "en", uploaded_by="demo.editor")
        seed("d" * 16, "none.docx", None, uploaded_by="demo.editor")
        # Чужой документ — не в scope demo.editor.
        seed("e" * 16, "other.docx", "de", uploaded_by="someone.else")
        login(client, "demo.editor")

        resp = client.get("/api/documents/source-locale-facets?uploader=demo.editor")
        assert resp.status_code == 200
        items = {i["code"]: i["count"] for i in resp.json()["items"]}
        assert items == {"ru": 2, "en": 1, None: 1}

    def test_facets_without_uploader_includes_all(self, client):
        seed("a" * 16, "ru.docx", "ru", uploaded_by="demo.editor")
        seed("b" * 16, "de.docx", "de", uploaded_by="someone.else")
        login(client, "demo.editor")

        resp = client.get("/api/documents/source-locale-facets")
        assert resp.status_code == 200
        items = {i["code"]: i["count"] for i in resp.json()["items"]}
        assert items == {"ru": 1, "de": 1}

    def test_facets_exclude_trash(self, client):
        seed("a" * 16, "ru.docx", "ru")
        reg = get_registry()
        reg.soft_delete("a" * 16)
        login(client, "demo.editor")

        resp = client.get("/api/documents/source-locale-facets")
        assert resp.json()["items"] == []

    def test_facets_require_auth(self, client):
        resp = client.get("/api/documents/source-locale-facets")
        assert resp.status_code in (401, 403)
