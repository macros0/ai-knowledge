"""Тесты ручной правки source_locale (Этап 7 фаза D): guard + сервис + API."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.audit import DOCUMENT_SOURCE_LOCALE_UPDATE, AuditService
from app.services.pipeline import _source_locale_fields
from app.services.registry import get_registry
from app.services.source_locale import normalize_source_locale

import app.api.documents as documents_module

DOC_ID = "0123456789abcdef"

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


@pytest.fixture(autouse=True)
def _neutralize_qdrant_sync(monkeypatch):
    """Не ходить в реальный Qdrant из API-тестов правки языка.

    Синк payload — best-effort фоновая проекция; тесты проверяют сам вызов
    (см. TestPatchSourceLocaleApi.test_syncs_payload), а не сетевой эффект.
    """
    monkeypatch.setattr(
        documents_module, "reindex_document_source_locale",
        lambda doc_id, loc: True,
    )
    monkeypatch.setattr(documents_module, "schedule_source_locale_sync", lambda doc_id: None)


def login(client, username):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


class TestSourceLocaleFields:
    """Guard в pipeline._finalize: ручная правка не перезаписывается."""

    def test_manual_skips_recompute(self):
        assert _source_locale_fields("ru", "manual") == {}

    def test_detected_sets_source(self):
        assert _source_locale_fields("ru", None) == {
            "source_locale": "ru",
            "source_locale_source": "detected",
        }
        assert _source_locale_fields("en", "detected") == {
            "source_locale": "en",
            "source_locale_source": "detected",
        }

    def test_none_resets(self):
        assert _source_locale_fields(None, None) == {
            "source_locale": None,
            "source_locale_source": None,
        }
        assert _source_locale_fields(None, "detected") == {
            "source_locale": None,
            "source_locale_source": None,
        }


class TestNormalizeSourceLocale:
    def test_lowercases_and_strips(self):
        assert normalize_source_locale(" ES ") == "es"
        assert normalize_source_locale("RU") == "ru"
        assert normalize_source_locale("pt") == "pt"
        assert normalize_source_locale("") == ""


class TestPatchSourceLocaleApi:
    def test_editor_sets_locale_normalized(self, client):
        get_registry().create(DOC_ID, "a.docx", "x", 10, tags=[])
        login(client, "demo.editor")

        resp = client.patch(
            f"/api/documents/{DOC_ID}/source-locale", json={"source_locale": " ES "}
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source_locale"] == "es"
        assert data["source_locale_source"] == "manual"

    def test_editor_writes_audit(self, client):
        get_registry().create(DOC_ID, "a.docx", "x", 10, tags=[])
        login(client, "demo.editor")

        resp = client.patch(
            f"/api/documents/{DOC_ID}/source-locale", json={"source_locale": "de"}
        )
        assert resp.status_code == 200, resp.text

        entries = AuditService().query(action_type=DOCUMENT_SOURCE_LOCALE_UPDATE)
        assert len(entries) == 1
        assert entries[0]["target_id"] == DOC_ID

    def test_viewer_forbidden(self, client):
        get_registry().create(DOC_ID, "a.docx", "x", 10, tags=[])
        login(client, "demo.viewer")

        resp = client.patch(
            f"/api/documents/{DOC_ID}/source-locale", json={"source_locale": "de"}
        )
        assert resp.status_code == 403

    def test_missing_document_404(self, client):
        login(client, "demo.editor")
        resp = client.patch(
            f"/api/documents/{DOC_ID}/source-locale", json={"source_locale": "de"}
        )
        assert resp.status_code == 404

    def test_invalid_doc_id_404(self, client):
        login(client, "demo.editor")
        resp = client.patch(
            "/api/documents/not-a-real-id/source-locale", json={"source_locale": "de"}
        )
        assert resp.status_code == 404

    def test_invalid_code_422_with_code_and_detail(self, client):
        # Контракт для фронтенда: code = 'source_locale_invalid', detail — непустая
        # диагностика (fallback для старых клиентов), а не голый 422.
        get_registry().create(DOC_ID, "a.docx", "x", 10, tags=[])
        login(client, "demo.editor")

        resp = client.patch(
            f"/api/documents/{DOC_ID}/source-locale", json={"source_locale": "zz"}
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "source_locale_invalid"
        assert body["detail"]

    def test_null_resets_value_and_source(self, client):
        reg = get_registry()
        reg.create(DOC_ID, "a.docx", "x", 10, tags=[])
        reg.update(DOC_ID, source_locale="de", source_locale_source="manual")
        login(client, "demo.editor")

        resp = client.patch(
            f"/api/documents/{DOC_ID}/source-locale", json={"source_locale": None}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source_locale"] is None
        assert data["source_locale_source"] is None

    def test_syncs_payload(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(
            documents_module, "reindex_document_source_locale",
            lambda doc_id, loc: (calls.append(("sync", doc_id, loc)) or True),
        )
        monkeypatch.setattr(documents_module, "schedule_source_locale_sync",
                            lambda doc_id: calls.append(("schedule", doc_id)))
        get_registry().create(DOC_ID, "a.docx", "x", 10, tags=[])
        login(client, "demo.editor")

        resp = client.patch(
            f"/api/documents/{DOC_ID}/source-locale", json={"source_locale": "es"}
        )
        assert resp.status_code == 200, resp.text
        assert ("sync", DOC_ID, "es") in calls

    def test_sync_fallback_flags_pending(self, client, monkeypatch):
        monkeypatch.setattr(
            documents_module, "reindex_document_source_locale",
            lambda doc_id, loc: False,
        )
        scheduled = []
        monkeypatch.setattr(documents_module, "schedule_source_locale_sync",
                            lambda doc_id: scheduled.append(doc_id))
        get_registry().create(DOC_ID, "a.docx", "x", 10, tags=[])
        login(client, "demo.editor")

        resp = client.patch(
            f"/api/documents/{DOC_ID}/source-locale", json={"source_locale": "de"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["source_locale_sync_pending"] is True
        assert scheduled == [DOC_ID]
