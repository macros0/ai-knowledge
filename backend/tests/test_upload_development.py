"""Тесты привязки разработки при загрузке (Этап 4a.1): POST /documents?development_id."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.development_registry import get_development_registry

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
        "dedup_enabled": False,
        "dev_detection_enabled": False,
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


def login(client):
    resp = client.post("/api/auth/simulate", json={"username": "demo.editor"})
    assert resp.status_code == 200, resp.text


def _mock_upload_flow(monkeypatch, dest, doc_id="0123456789abcdef"):
    from app.api import documents as docs

    monkeypatch.setattr(docs, "save_upload_stream", lambda *a, **k: (doc_id, dest, 123))
    monkeypatch.setattr(docs._pipeline, "ingest", lambda *a, **k: None)


class TestUploadDevelopmentBinding:
    def test_valid_development_binds_document(self, client, monkeypatch, tmp_path):
        dev = get_development_registry().create("12010", "СЭДО")
        dest = tmp_path / "0123456789abcdef.pdf"
        dest.write_bytes(b"%PDF-1.4")
        _mock_upload_flow(monkeypatch, dest)
        login(client)

        resp = client.post(
            "/api/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            data={"development_id": str(dev["id"])},
        )
        assert resp.status_code == 200, resp.text
        doc = resp.json()
        assert doc["development_id"] == dev["id"]
        assert doc["development_confidence"] == 1.0
        assert doc["development_confirmed_by"] == "demo.editor"
        assert doc["development_suggestion"] is None

    def test_unknown_development_422_with_detail(self, client, monkeypatch, tmp_path):
        dest = tmp_path / "0123456789abcdef.pdf"
        dest.write_bytes(b"%PDF-1.4")
        _mock_upload_flow(monkeypatch, dest)
        login(client)

        resp = client.post(
            "/api/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            data={"development_id": "99999"},
        )
        assert resp.status_code == 422
        # Точное сообщение, как в set_document_development (общий контракт валидации).
        assert resp.json()["detail"] == "Разработка не найдена"

    def test_no_development_param_keeps_previous_shape(self, client, monkeypatch, tmp_path):
        from app.services.audit import DOCUMENT_UPLOAD, AuditService

        dest = tmp_path / "0123456789abcdef.pdf"
        dest.write_bytes(b"%PDF-1.4")
        _mock_upload_flow(monkeypatch, dest)
        login(client)

        resp = client.post(
            "/api/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["development_id"] is None
        entries = AuditService().query(action_type=DOCUMENT_UPLOAD)
        assert entries[0]["new_value"] == {"filename": "a.pdf", "size": 123}

    def test_bound_development_in_upload_audit(self, client, monkeypatch, tmp_path):
        from app.services.audit import DOCUMENT_UPLOAD, AuditService

        dev = get_development_registry().create("12010", "СЭДО")
        dest = tmp_path / "0123456789abcdef.pdf"
        dest.write_bytes(b"%PDF-1.4")
        _mock_upload_flow(monkeypatch, dest)
        login(client)

        resp = client.post(
            "/api/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            data={"development_id": str(dev["id"])},
        )
        assert resp.status_code == 200, resp.text
        entries = AuditService().query(action_type=DOCUMENT_UPLOAD)
        assert entries[0]["new_value"]["development_id"] == dev["id"]
