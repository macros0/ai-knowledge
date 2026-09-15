"""Тесты HTTP-заголовков в ответах-ошибках ApiError.

Заголовок — часть контракта ответа наравне со статусом: по Retry-After клиент
понимает, когда повторить, по WWW-Authenticate — как авторизоваться. Раньше
`headers=` уезжал в **extra и печатался в JSON-теле вместо заголовков ответа,
и ни один тест этого не видел — тело-то оставалось валидным.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.config import Settings
from app.main import create_app
from app.services.job_queue import JobQueue
from app.services.rate_limiter import RateLimiter
from app.services.registry import DocumentRegistry
from app.services.export_queue import ExportAdmissionError, ExportDownloadLease

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
        ],
    }
    defaults.update(overrides)
    settings = Settings(**defaults)
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Один лимитер на клиента: окно должно накапливаться между запросами.
    limiter = RateLimiter()
    monkeypatch.setattr("app.api.documents.get_job_queue", lambda: JobQueue(start_worker=False))
    monkeypatch.setattr("app.api.documents.get_rate_limiter", lambda: limiter)
    return make_client(tmp_path, monkeypatch)


class TestApiErrorHeaders:
    def test_headers_are_not_swallowed_into_extra(self):
        """headers — именованный параметр: в тело (extra) он попадать не должен."""
        exc = ApiError(
            status_code=429,
            code="rate_limited",
            detail="слишком часто",
            headers={"Retry-After": "42"},
        )
        assert exc.headers == {"Retry-After": "42"}
        assert "headers" not in exc.extra

    def test_extra_still_reaches_body(self):
        """Регрессия наоборот: **extra по-прежнему разворачивается в тело."""
        exc = ApiError(status_code=503, code="dependency_unavailable", detail="x", service="qdrant")
        assert exc.extra == {"service": "qdrant"}
        assert exc.headers is None


class TestUnauthorizedHeaders:
    def test_401_sends_www_authenticate(self, client):
        """401 без сессии обязан нести WWW-Authenticate: Bearer (auth/service.py)."""
        resp = client.get("/api/documents")
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"
        assert "headers" not in resp.json()


class TestRateLimitHeaders:
    def test_429_sends_retry_after(self, client, monkeypatch, tmp_path):
        """429 массовой перегенерации обязан нести Retry-After (documents.py)."""
        client.post("/api/auth/simulate", json={"username": "demo.admin"})
        reg = DocumentRegistry()
        ids = []
        for i in range(2):
            doc_id = f"hdoc{i:03d}"
            reg.create(doc_id, f"f{i}.docx", "doc", 10)
            ids.append(doc_id)

        settings = Settings(
            _env_file=None,
            data_dir=tmp_path,
            bulk_regenerate_max_ops_per_hour=1,
            bulk_regenerate_max_docs_per_hour=100,
        )
        monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)

        assert client.post("/api/documents/bulk-regenerate", json={"doc_ids": ids}).status_code == 200
        resp = client.post("/api/documents/bulk-regenerate", json={"doc_ids": ids})

        assert resp.status_code == 429
        assert int(resp.headers["Retry-After"]) >= 1
        # Заголовок в заголовках, а не в теле: тело остаётся {detail, code}.
        assert "headers" not in resp.json()
        assert resp.json()["code"] == "rate_limited"


class TestBulkExportErrorHeaders:
    def test_export_rate_limit_sends_retry_after(self, client, monkeypatch):
        class _ExportQueue:
            @staticmethod
            def submit(*args, **kwargs):
                raise ExportAdmissionError(
                    "попробуйте позднее", code="bulk_export_rate_limited", retry_after_seconds=17
                )

        monkeypatch.setattr("app.api.documents.get_export_queue", lambda: _ExportQueue())
        client.post("/api/auth/simulate", json={"username": "demo.admin"})
        response = client.post(
            "/api/documents/bulk-export", json={"doc_ids": ["0123456789abcdef"]}
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "17"
        assert response.json()["code"] == "bulk_export_rate_limited"


class TestBulkExportDownloadContract:
    def test_download_uses_attachment_no_store_and_releases_lease(self, client, monkeypatch, tmp_path):
        archive = tmp_path / "documents-export-71-part-001-of-001.zip"
        archive.write_bytes(b"PK\x03\x04fake")
        released = []

        class _ExportQueue:
            @staticmethod
            def acquire_download(*args, **kwargs):
                return ExportDownloadLease(archive, archive.name, archive.stat().st_size, lambda: released.append(True))

        settings = Settings(_env_file=None, data_dir=tmp_path, bulk_export_download_enabled=True)
        monkeypatch.setattr("app.api.jobs.get_settings", lambda: settings)
        monkeypatch.setattr("app.api.jobs.get_export_queue", lambda: _ExportQueue())
        client.post("/api/auth/simulate", json={"username": "demo.admin"})

        response = client.get("/api/jobs/71/export/1")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in response.headers["content-disposition"]
        assert response.content == archive.read_bytes()
        assert released == [True]

    def test_download_flag_blocks_before_acquiring_lease(self, client, monkeypatch, tmp_path):
        class _ExportQueue:
            @staticmethod
            def acquire_download(*args, **kwargs):
                raise AssertionError("lease must not be acquired")

        settings = Settings(_env_file=None, data_dir=tmp_path, bulk_export_download_enabled=False)
        monkeypatch.setattr("app.api.jobs.get_settings", lambda: settings)
        monkeypatch.setattr("app.api.jobs.get_export_queue", lambda: _ExportQueue())
        client.post("/api/auth/simulate", json={"username": "demo.admin"})
        response = client.get("/api/jobs/71/export/1")
        assert response.status_code == 404
        assert response.json()["code"] == "bulk_export_disabled"
