from types import SimpleNamespace

from app.services import health


def test_local_health_requires_configured_model(monkeypatch):
    monkeypatch.setattr(health, "get_settings", lambda: SimpleNamespace(
        llm_profile="local_qwen", llm_base_url="http://local/v1",
        llm_api_key="", llm_model="openai//models/qwen.gguf"))
    monkeypatch.setattr(health.httpx, "get", lambda *a, **kw: SimpleNamespace(
        status_code=200, json=lambda: {"data": [{"id": "another-model"}]}))
    assert health._check_llm()["status"] == "down"
    monkeypatch.setattr(health.httpx, "get", lambda *a, **kw: SimpleNamespace(
        status_code=200, json=lambda: {"data": [{"id": "/models/qwen.gguf"}]}))
    assert health._check_llm()["status"] == "ok"
