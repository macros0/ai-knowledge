"""Test-only OpenAI HTTP/SSE server. No outbound requests or payload logging."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import os
import re
import time


def fixture_reply(payload):
    messages = payload.get("messages", [])
    system = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    user = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
    task = payload.get("response_format", {}).get("json_schema", {}).get("name", "")
    if not task:
        if "concept_per_row" in system:
            task = "classification"
        elif "dev_number" in system:
            task = "development"
        elif "source_quotes" in system or '"relations"' in system:
            task = "generation"
        elif "перевод" in system.lower() or "translat" in system.lower():
            task = "translation"
    if task == "generation":
        reply = [{"id": "test-fixture", "title": "ТЕСТОВЫЙ концепт", "type": "concept", "tags": [],
                  "content": "ТЕСТОВЫЕ данные для проверки развёртывания.", "source_quotes": [], "relations": []}]
    elif task == "classification":
        reply = {"concept_per_row": False, "title_col": 0, "description_cols": [],
                 "concept_type": "reference", "extraction_mode": "whole"}
    elif task == "development":
        reply = {"dev_number": None, "dev_name": None, "module": None}
    elif task == "translation":
        match = re.search(r"\[.*\]", user, re.DOTALL)
        try:
            inputs = json.loads(match.group()) if match else [user]
        except (ValueError, AttributeError):
            inputs = [user]
        reply = ["TEST translation" for _ in inputs]
    else:
        return "ТЕСТОВЫЙ ответ: проверка доставки и потокового чата [1]."
    return json.dumps(reply, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):
        pass

    def _json(self, status, body):
        encoded = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/health/live":
            return self._json(200, {"status": "alive", "test_only": True})
        if self.path == "/v1/models":
            return self._json(200, {"object": "list", "data": [{"id": "test-stub", "object": "model"}]})
        self._json(404, {"error": {"message": "Unknown test endpoint"}})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": {"message": "Unknown test endpoint"}})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1048576:
                return self._json(413, {"error": {"message": "Test input limit"}})
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
                raise ValueError("Invalid payload")
        except (ValueError, TypeError):
            return self._json(400, {"error": {"message": "Invalid test request"}})
        model = str(payload.get("model", "test-stub"))
        scenario = os.environ.get("MODEL_STUB_SCENARIO", "success")
        for candidate in ("429", "503", "delay", "interrupted"):
            if model.endswith("-" + candidate):
                scenario = candidate
        if scenario in {"429", "503"}:
            return self._json(int(scenario), {"error": {"message": "Synthetic failure", "type": "test_error"}})
        if scenario == "delay":
            time.sleep(min(30.0, max(0.0, float(os.environ.get("MODEL_STUB_DELAY_SECONDS", "2")))))
        content = fixture_reply(payload)
        base = {"id": "chatcmpl-test", "created": int(time.time()), "model": model}
        try:
            if not payload.get("stream"):
                return self._json(200, {**base, "object": "chat.completion", "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for offset in range(0, len(content), 32):
                chunk = {**base, "object": "chat.completion.chunk", "choices": [
                    {"index": 0, "delta": {"content": content[offset:offset + 32]}, "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
                if scenario == "interrupted":
                    self.close_connection = True
                    return
            final = {**base, "object": "chat.completion.chunk", "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(("data: " + json.dumps(final) + "\n\ndata: [DONE]\n\n").encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def make_server(host="0.0.0.0", port=8000):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    make_server(args.host, args.port).serve_forever()
