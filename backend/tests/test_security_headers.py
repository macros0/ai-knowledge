from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_security_headers_are_present(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    response = TestClient(create_app()).get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers


def test_auth_responses_are_not_cacheable(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    response = TestClient(create_app()).get("/api/auth/me")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_hsts_is_enabled_for_production_https(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        environment="production",
        auth_provider="keycloak_oidc",
        keycloak_url="https://kc",
        keycloak_realm="realm",
        keycloak_client_id="id",
        keycloak_client_secret="secret",
        app_secret_key="x" * 32,
        auth_session_https_only=True,
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    response = TestClient(create_app()).get("/health")
    assert response.headers["Strict-Transport-Security"].startswith("max-age=31536000")
