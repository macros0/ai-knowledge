"""Real SDK transport and cache locks, without contacting a model or the internet."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from filelock import Timeout
from huggingface_hub.utils import WeakFileLock
import litellm
import pytest

from app.config import Settings
from app.services.llm_client import LLMClient


@contextmanager
def provider():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, value))
            if self.path.endswith("/embeddings"):
                body = json.dumps({"object": "list", "model": "test", "data": [
                    {"object": "embedding", "index": 0, "embedding": [0.25, 0.75]}],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1}}).encode()
                content_type = "application/json"
            else:
                chunk = {"id": "test", "object": "chat.completion.chunk", "created": 1,
                         "model": "test", "choices": [{"index": 0, "delta": {"content": "ok"},
                         "finish_reason": "stop"}]}
                body = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_actual_litellm_streaming_and_embedding_transport(monkeypatch):
    with provider() as (url, calls):
        settings = Settings(_env_file=None, llm_profile="standard", llm_model="openai/test",
                            llm_base_url=url, llm_api_key="test-only", llm_retry_attempts=1,
                            diagnostics_enabled=False)
        monkeypatch.setattr("app.services.llm_client.get_settings", lambda: settings)
        assert LLMClient(settings=settings).chat("system", "hello") == "ok"
        response = litellm.embedding(model="openai/test", input=["hello"], api_base=url,
                                     api_key="test-only", timeout=5)
        assert response.data[0]["embedding"] == [0.25, 0.75]
        assert [path for path, _ in calls] == ["/v1/chat/completions", "/v1/embeddings"]


def test_real_huggingface_cache_lock_contention_and_reacquisition(tmp_path):
    path = tmp_path / "cache.lock"
    with WeakFileLock(path, timeout=1):
        with pytest.raises(Timeout):
            with WeakFileLock(path, timeout=.05):
                pytest.fail("Contending cache lock acquired")
    with WeakFileLock(path, timeout=1) as lock:
        assert lock.is_locked
