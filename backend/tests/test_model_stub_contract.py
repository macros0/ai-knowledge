"""Exercise the test HTTP server through the application's real streaming client."""

import importlib.util
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


@pytest.fixture
def server():
    path = Path(__file__).parents[1] / "test_scripts/model_stub/server.py"
    assert path.exists(), "contract-faithful HTTP model stub is missing"
    spec = importlib.util.spec_from_file_location("model_stub_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = module.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}/v1"
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=3)


def _post(server, model="test-stub", **extra):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "fixture"}], **extra}).encode()
    return urlopen(Request(server + "/chat/completions", data=body, headers={"Content-Type": "application/json"}), timeout=5)


def test_stub_stream_consumed_by_real_llm_client(server, monkeypatch):
    from app.config import Settings
    from app.services import llm_client

    settings = Settings(_env_file=None, llm_model="openai/test-stub", llm_base_url=server,
                        llm_api_key="synthetic", model_mode="stub")
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    result = llm_client.LLMClient().chat("Return a test reply", "fixture")
    assert "ТЕСТОВЫЙ" in result
    assert json.load(urlopen(server + "/models"))["data"][0]["id"] == "test-stub"


@pytest.mark.parametrize("task", ["generation", "classification", "development", "translation"])
def test_stub_matches_current_task_schema(server, task):
    from app.services.llm_profiles import ADAPTERS

    response_format = {"type": "json_schema", "json_schema": {"name": task}}
    with _post(server, response_format=response_format) as response:
        content = json.loads(response.read())["choices"][0]["message"]["content"]
    value = json.loads(content)
    ADAPTERS[task].validate_python(value, strict=True)


def test_stub_translation_preserves_input_count(server):
    body = {"model": "test-stub", "messages": [{"role": "user", "content": '["a","b","c"]'}],
            "response_format": {"json_schema": {"name": "translation"}}}
    request = Request(server + "/chat/completions", data=json.dumps(body).encode())
    response = json.load(urlopen(request))
    assert len(json.loads(response["choices"][0]["message"]["content"])) == 3


@pytest.mark.parametrize("code", [429, 503])
def test_stub_http_failures(server, code):
    with pytest.raises(HTTPError) as failure:
        _post(server, model=f"test-stub-{code}")
    assert failure.value.code == code


def test_stub_interrupted_sse_has_no_success_marker(server):
    with _post(server, model="test-stub-interrupted", stream=True) as response:
        content = response.read().decode()
    assert "delta" in content
    assert "[DONE]" not in content
    assert '"finish_reason": "stop"' not in content
