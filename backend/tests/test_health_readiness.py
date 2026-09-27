"""Readiness (/health/ready) отделена от внешних провайдеров; опрос /health ограничен по времени."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.services import health

OK = {"status": "ok"}
DOWN = {"status": "down", "error": "conn refused"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.config import Settings

    settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled", embedding_provider="fake")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(health, "_cache", {})
    monkeypatch.setattr(health, "_cache_ts", 0.0)
    monkeypatch.setattr(health, "_last_status", {})


def _stub_checks(monkeypatch, **overrides):
    for name in ("llm", "embeddings", "qdrant", "database", "pdf_provider"):
        result = overrides.get(name, OK)
        monkeypatch.setattr(health, f"_check_{name}", lambda result=result: result)


class TestLlmProbeUrl:
    @pytest.mark.parametrize("model", ["ollama/qwen2.5:14b", "ollama_chat/llama3"])
    def test_ollama_native_api_uses_tags(self, model):
        # У нативного API Ollama нет GET /models: 404 давал вечный "degraded".
        assert health._llm_probe_url(model, "http://127.0.0.1:11434/") == "http://127.0.0.1:11434/api/tags"

    @pytest.mark.parametrize(
        ("model", "base"),
        [
            ("openrouter/mistralai/mistral-nemo", "https://openrouter.ai/api/v1"),
            ("openai/gpt-4o", "http://127.0.0.1:8000/v1"),
        ],
    )
    def test_openai_compatible_api_uses_models(self, model, base):
        assert health._llm_probe_url(model, base) == f"{base}/models"


class TestReadiness:
    def test_external_llm_outage_does_not_block_readiness(self, client, monkeypatch):
        _stub_checks(monkeypatch, llm=DOWN, embeddings=DOWN)

        resp = client.get("/health/ready")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"
        assert set(resp.json()["dependencies"]) == {"database", "qdrant", "pdf_parser"}
        # Информационный /health по-прежнему сообщает о деградации для баннера.
        assert client.get("/health").json()["status"] == "degraded"

    @pytest.mark.parametrize("dependency", ["database", "qdrant", "pdf_provider"])
    def test_own_storage_outage_is_not_ready(self, client, monkeypatch, dependency):
        _stub_checks(monkeypatch, **{dependency: DOWN})

        resp = client.get("/health/ready")

        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"


class TestProbeBounds:
    def test_hanging_check_is_reported_down_within_deadline(self, monkeypatch):
        monkeypatch.setattr(health, "_PROBE_DEADLINE_SECONDS", 0.2)
        release = threading.Event()

        def hanging():
            release.wait(5)
            return OK

        started = time.monotonic()
        try:
            results = health._run_checks({"slow": hanging, "fast": lambda: OK})
        finally:
            release.set()

        assert time.monotonic() - started < 1.0
        assert results == {"slow": {"status": "down", "error": "timeout"}, "fast": OK}

    def test_checks_run_in_parallel(self, monkeypatch):
        def slow():
            time.sleep(0.3)
            return OK

        started = time.monotonic()
        health._run_checks({name: slow for name in ("a", "b", "c", "d")})

        assert time.monotonic() - started < 0.9

    def test_concurrent_refresh_returns_previous_result(self, monkeypatch):
        stale = {"status": "ok", "knowledge_profile": "x", "dependencies": {}}
        monkeypatch.setattr(health, "_cache", stale)
        monkeypatch.setattr(health, "_cache_ts", 0.0)

        def fail_probe(checks):
            raise AssertionError("параллельный опрос не должен запускаться")

        monkeypatch.setattr(health, "_run_checks", fail_probe)
        assert health._refresh_lock.acquire(blocking=False)
        try:
            assert health.get_health() is stale
        finally:
            health._refresh_lock.release()


class TestPublicDetails:
    """Эндпоинты здоровья открыты без авторизации: наружу — только статусы."""

    LEAK = {"status": "down", "error": 'connection to server at "10.20.30.40", port 5432 failed: FATAL: user "okf"'}
    PDF = {"status": "ok", "provider": "pypdf-pdfium", "pypdf_version": "6.19.0", "pdfium_version": "153.0"}

    def test_health_hides_errors_and_versions(self, client, monkeypatch):
        _stub_checks(monkeypatch, database=self.LEAK, pdf_provider=self.PDF)

        for path, expected_code in (("/health", 200), ("/health/ready", 503)):
            resp = client.get(path)
            assert resp.status_code == expected_code
            assert "10.20.30.40" not in resp.text and "okf" not in resp.text
            assert "6.19.0" not in resp.text
            deps = resp.json()["dependencies"]
            assert deps["database"] == {"status": "down"}
            assert deps["pdf_parser"] == {"status": "ok"}

    def test_details_are_logged_once_per_transition(self, monkeypatch, caplog):
        caplog.set_level("INFO", logger=health.logger.name)
        checks = {"database": lambda: self.LEAK}

        health._run_checks(checks)
        health._run_checks(checks)
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1 and "10.20.30.40" in warnings[0]

        health._run_checks({"database": lambda: OK})
        assert any("снова доступна" in r.getMessage() for r in caplog.records if r.levelname == "INFO")
