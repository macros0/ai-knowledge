"""Тесты массовых операций (bulk) на уровне API: валидация, four-eyes, rate limit."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.job_queue import JobQueue
from app.services.rate_limiter import RateLimiter
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
            {"user_id": "sim-admin", "username": "demo.admin", "email": "a@d.local", "groups": ["KB_Admin"]},
            {"user_id": "sim-user", "username": "demo.user", "email": "u@d.local", "groups": ["KB_Viewer"]},
        ],
    }
    defaults.update(overrides)
    settings = Settings(**defaults)
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Без worker-потока: submit только создаёт запись и возвращает статус.
    limiter = RateLimiter()
    monkeypatch.setattr("app.api.documents.get_job_queue", lambda: JobQueue(start_worker=False))
    monkeypatch.setattr("app.api.documents.get_rate_limiter", lambda: limiter)
    return make_client(tmp_path, monkeypatch)


def login(client, username="demo.admin"):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


def make_docs(n):
    reg = DocumentRegistry()
    ids = []
    for i in range(n):
        doc_id = f"bdoc{i:03d}"
        reg.create(doc_id, f"f{i}.docx", "doc", 10)
        ids.append(doc_id)
    return ids


class TestBulkPreview:
    def test_preview_lists_missing(self, client):
        ids = make_docs(2)
        login(client)
        resp = client.post(
            "/api/documents/bulk-preview", json={"doc_ids": [ids[0], "missing-id"]}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["requested"] == 2
        assert body["matched"] == 1
        assert body["missing"] == ["missing-id"]


class TestBulkDeleteValidation:
    def test_empty_rejected(self, client):
        login(client)
        resp = client.post("/api/documents/bulk-delete", json={"doc_ids": []})
        assert resp.status_code == 400

    def test_missing_doc_404(self, client):
        login(client)
        resp = client.post("/api/documents/bulk-delete", json={"doc_ids": ["nope"]})
        assert resp.status_code == 404

    def test_over_cap_400(self, client):
        login(client)
        ids = make_docs(51)  # дефолтный bulk_delete_max_docs = 50
        resp = client.post("/api/documents/bulk-delete", json={"doc_ids": ids})
        assert resp.status_code == 400

    def test_below_threshold_queued(self, client):
        login(client)
        ids = make_docs(3)
        resp = client.post("/api/documents/bulk-delete", json={"doc_ids": ids})
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_at_threshold_awaits_approval(self, client):
        login(client)
        ids = make_docs(50)  # approval_threshold_docs_delete = 50
        resp = client.post("/api/documents/bulk-delete", json={"doc_ids": ids})
        assert resp.status_code == 200
        assert resp.json()["status"] == "awaiting_approval"


class TestBulkRegenerateRateLimit:
    def test_second_operation_within_window_429(self, client, monkeypatch, tmp_path):
        login(client)
        ids = make_docs(2)
        settings = Settings(
            **{
                "_env_file": None,
                "data_dir": tmp_path,
                "bulk_regenerate_max_ops_per_hour": 1,
                "bulk_regenerate_max_docs_per_hour": 100,
            }
        )
        monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)

        first = client.post("/api/documents/bulk-regenerate", json={"doc_ids": ids})
        assert first.status_code == 200
        second = client.post("/api/documents/bulk-regenerate", json={"doc_ids": ids})
        assert second.status_code == 429
