import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.services.diagnostics.schema import DiagnosticContext
from tests.test_pipeline_queue import queue_pipeline as _queue_pipeline_fixture, paused_document
from tests.test_export_queue import queue as _export_queue_fixture, _document, _User
from tests.test_diagnostics_sessions import service as _session_fixture, actor

queue_pipeline = _queue_pipeline_fixture
export_queue = _export_queue_fixture
session_service = _session_fixture


def test_parallel_requests_and_threads_do_not_share_context():
    from app.services.diagnostics.context import bind_context, current_context, new_operation, run_bound
    parents = [DiagnosticContext(request_id=str(uuid4())) for _ in range(2)]
    contexts = [new_operation(parent, doc_id=f"{i:016x}", generation_id=str(uuid4()))
                for i, parent in enumerate(parents)]
    def work(context):
        with bind_context(context):
            return current_context()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, contexts))
        assert results == contexts
        assert pool.submit(run_bound, contexts[0], current_context).result() == contexts[0]
        assert pool.submit(current_context).result() == DiagnosticContext()
    assert contexts[0].operation_id != contexts[1].operation_id
    assert [context.request_id for context in contexts] == [parent.request_id for parent in parents]
    assert current_context() == DiagnosticContext()


@pytest.mark.parametrize("incoming", [None, "CANARY_SECRET", "\ninvalid", str(uuid4())])
def test_uuid_header_validation(incoming):
    from app.services.diagnostics.context import current_context
    from app.services.diagnostics.middleware import DiagnosticContextMiddleware
    seen, sent = [], []
    async def app(scope, receive, send):
        seen.append(current_context().request_id)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    async def receive():
        return {"type": "http.request", "body": b""}
    async def send(message):
        sent.append(message)
    scope = {"type": "http", "method": "GET", "path": "/CANARY_PATH",
             "query_string": b"password=CANARY_QUERY", "headers": []}
    if incoming:
        scope["headers"] = [(b"x-request-id", incoming.encode())]
    asyncio.run(DiagnosticContextMiddleware(app)(scope, receive, send))
    request_id = dict(sent[0]["headers"])[b"x-request-id"].decode()
    assert str(UUID(request_id)) == request_id
    assert request_id == seen[0] == scope["state"]["request_id"]
    if incoming and incoming == str(UUID(request_id)):
        assert request_id == incoming
    else:
        assert request_id != incoming
    assert current_context() == DiagnosticContext()


def test_upload_and_zip_not_buffered():
    from app.services.diagnostics.middleware import DiagnosticContextMiddleware
    chunks = [b"a" * 131072, b"b" * 131072]
    sent = []
    reads = []
    async def app(scope, receive, send):
        assert not reads
        for index, chunk in enumerate(chunks):
            message = await receive()
            assert message["body"] is chunk
            if index == 0:
                await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": chunk, "more_body": index == 0})
            assert sent[-1]["body"] is chunk
    async def receive():
        index = len(reads)
        reads.append(index)
        return {"type": "http.request", "body": chunks[index], "more_body": index == 0}
    async def send(message):
        sent.append(message)
    scope = {"type": "http", "method": "POST", "headers": [], "path": "/upload"}
    asyncio.run(DiagnosticContextMiddleware(app)(scope, receive, send))
    assert reads == [0, 1]
    assert [message["body"] for message in sent[1:]] == chunks


def test_context_reset_after_cancellation():
    from app.services.diagnostics.context import current_context
    from app.services.diagnostics.middleware import DiagnosticContextMiddleware
    async def app(scope, receive, send):
        assert current_context().request_id
        raise asyncio.CancelledError()
    async def receive():
        raise AssertionError("body read")
    async def send(message):
        raise AssertionError("unexpected response")
    async def run():
        with pytest.raises(asyncio.CancelledError):
            await DiagnosticContextMiddleware(app)({"type": "http", "method": "GET", "headers": []}, receive, send)
        assert current_context() == DiagnosticContext()
    asyncio.run(run())


def test_stream_error_after_200_has_request_id(monkeypatch, tmp_path):
    from app.config import Settings
    from app.services import chat_stream
    from app.services.diagnostics.context import bind_context, current_context
    monkeypatch.setattr(chat_stream, "get_settings", lambda: Settings(_env_file=None, data_dir=tmp_path / "data"))
    context = DiagnosticContext(request_id=str(uuid4()), operation_id=str(uuid4()))
    observed = []
    def work():
        observed.append(current_context())
        raise ValueError("CANARY_PRIVATE_PROVIDER_RESPONSE")
    async def collect():
        with bind_context(context):
            return [json.loads(line) async for line in chat_stream.stream_chat(work)]
    events = asyncio.run(collect())
    assert observed == [context]
    assert events[-1]["type"] == "error"
    assert events[-1]["request_id"] == context.request_id
    assert "CANARY" not in json.dumps(events)


def test_403_422_500_have_error_reference(monkeypatch, tmp_path):
    from app.config import Settings
    from app import main
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", auth_provider="disabled")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    app = main.create_app()
    @app.get("/diagnostic-test/{number}")
    def endpoint(number: int):
        if number == 403:
            raise HTTPException(403, "Forbidden")
        raise RuntimeError("CANARY_PRIVATE_ERROR")
    client = TestClient(app, raise_server_exceptions=False)
    for path, status in [("403", 403), ("invalid", 422), ("500", 500)]:
        response = client.get(f"/diagnostic-test/{path}")
        assert response.status_code == status
        request_id = response.headers["X-Request-ID"]
        assert str(UUID(request_id)) == request_id
        assert response.json()["request_id"] == request_id
        assert "CANARY_PRIVATE_ERROR" not in response.text


def test_log_handler_inherits_context(tmp_path):
    import logging
    from app.services.diagnostics.context import bind_context
    from app.services.diagnostics.recorder import DiagnosticRecorder
    from app.services.diagnostics.schema import DiagnosticLimits
    from app.services.diagnostics.store import DiagnosticStore
    context = DiagnosticContext(request_id=str(uuid4()))
    recorder = DiagnosticRecorder(DiagnosticStore(tmp_path / "spool", DiagnosticLimits(min_free_bytes=0)))
    recorder.start()
    try:
        with bind_context(context):
            recorder.handler.emit(logging.LogRecord("app.test", logging.ERROR, __file__, 1, "CANARY", (), None))
    finally:
        recorder.stop()
    events = [json.loads(line) for path in (tmp_path / "spool/events").rglob("*.jsonl")
              for line in path.read_text().splitlines()]
    assert events[0]["request_id"] == context.request_id


def test_route_templates_never_contain_actual_path(monkeypatch):
    from types import SimpleNamespace
    from app.services.diagnostics import middleware
    events = []
    monkeypatch.setattr(middleware, "emit_event", lambda code, **kwargs: events.append((code, kwargs)))
    async def app(scope, receive, send):
        scope["route"] = SimpleNamespace(path="/api/documents/{doc_id}")
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    async def receive():
        return {"type": "http.request", "body": b""}
    async def send(message):
        pass
    scope = {"type": "http", "method": "GET", "path": "/api/documents/CANARY",
             "headers": [], "query_string": b"token=CANARY"}
    asyncio.run(middleware.DiagnosticContextMiddleware(app)(scope, receive, send))
    assert events[-1][1]["fields"]["route_template"] == "/api/documents/{doc_id}"
    assert "CANARY" not in repr(events)


def test_pipeline_resume_gets_new_operation_and_parent_request(queue_pipeline):
    from app.services.diagnostics.context import bind_context, current_context
    p = queue_pipeline
    doc_id = "0123456789abcdef"
    paused_document(p, doc_id)
    observed = []
    p._process = lambda *args, **kwargs: observed.append(current_context())
    parent = DiagnosticContext(request_id=str(uuid4()))
    with bind_context(parent):
        p.resume(doc_id)
    first_task = p._threads[doc_id]
    p._test_release_worker.set()
    first_task.result(timeout=10)
    p.registry.update(doc_id, status="paused")
    with bind_context(parent):
        p.resume(doc_id)
    second_task = p._threads.get(doc_id)
    if second_task:
        second_task.result(timeout=10)
    assert len(observed) == 2
    assert [context.doc_id for context in observed] == [doc_id, doc_id]
    assert all(context.request_id == parent.request_id for context in observed)
    assert observed[0].operation_id != observed[1].operation_id
    assert p._executor.submit(current_context).result() == DiagnosticContext()


def test_job_context_persisted_and_restored_outside_request(monkeypatch):
    from types import SimpleNamespace
    from app.services import pipeline
    from app.services.job_queue import JobQueue, BULK_DELETE
    from app.services.registry import DocumentRegistry
    from app.services.diagnostics.context import bind_context, current_context
    doc_id = "0123456789abcdef"
    registry = DocumentRegistry()
    registry.create(doc_id, "private.docx", "docx", 1)
    observed = []
    monkeypatch.setattr(pipeline, "get_pipeline", lambda: SimpleNamespace(
        registry=registry, soft_delete=lambda *args, **kwargs: observed.append(current_context())))
    parent = DiagnosticContext(request_id=str(uuid4()))
    queue = JobQueue(start_worker=False)
    with bind_context(parent):
        job = queue.submit(BULK_DELETE, [doc_id], _User())
    queue._execute(job["id"])
    assert observed[0].request_id == parent.request_id
    assert observed[0].operation_id == job["params"]["diagnostic_context"]["operation_id"]
    assert current_context() == DiagnosticContext()


def test_export_context_restored_outside_request(export_queue, monkeypatch):
    from app.services.diagnostics.context import bind_context, current_context
    queue, settings = export_queue
    _document(settings, "0123456789abcdef", "private.docx")
    parent = DiagnosticContext(request_id=str(uuid4()))
    with bind_context(parent):
        job = queue.submit(["0123456789abcdef"], _User())
    observed = []
    original = queue._rehydrate_parts
    def rehydrate(job):
        observed.append(current_context())
        return original(job)
    monkeypatch.setattr(queue, "_rehydrate_parts", rehydrate)
    queue._execute(job["id"])
    assert queue.get(job["id"])["status"] == "completed"
    assert observed[0].request_id == parent.request_id
    assert observed[0].operation_id == job["params"]["diagnostic_context"]["operation_id"]
    assert current_context() == DiagnosticContext()


def test_search_scope_excludes_non_search_background(session_service):
    session = session_service.start("search_chat", 5, actor())
    assert session_service.active_for(DiagnosticContext(operation_id=str(uuid4())), "operation_failed") is None
    context = DiagnosticContext(request_id=str(uuid4()), operation_id=str(uuid4()), operation_kind="search_chat")
    assert session_service.active_for(context, "operation_failed") == session.id


def test_chat_and_stream_have_operation_context(monkeypatch):
    from app.api import chat
    from app.services.diagnostics.context import current_context
    from tests.test_chat import make_client
    contexts = []
    def embed(*args, **kwargs):
        contexts.append(current_context())
        return [0.0] * 10
    monkeypatch.setattr(chat._get_embedder(), "embed", embed)
    monkeypatch.setattr(chat._get_vector_store(), "search_composite", lambda **kwargs: [])
    client = make_client(monkeypatch)
    responses = [client.post(path, json={"query": "synthetic"})
                 for path in ("/api/chat", "/api/chat/stream")]
    assert all(response.status_code == 200 for response in responses)
    assert len(contexts) == 2
    assert all(context.operation_id for context in contexts)
    assert all(context.operation_kind == "search_chat" for context in contexts)
    assert [context.request_id for context in contexts] == [response.headers["x-request-id"] for response in responses]


def test_generation_identity_is_bound_without_changing_attempt(monkeypatch, tmp_path):
    from app.config import Settings
    from app.db.models import DocumentGenerationState
    from app.db.session import session_scope
    from app.services import pipeline
    from app.services.diagnostics.context import bind_context, current_context, new_operation
    from app.services.registry import DocumentRegistry
    doc_id = "0123456789abcdef"
    instance = pipeline.Pipeline.__new__(pipeline.Pipeline)
    instance.settings = Settings(_env_file=None, data_dir=tmp_path / "data",
                                 diagnostics_dir=tmp_path / "diagnostics")
    instance.registry = DocumentRegistry()
    instance.registry.create(doc_id, "private.docx", "docx", 1)
    source = tmp_path / "source.docx"
    source.write_bytes(b"synthetic")
    observed = []
    class ParserProbeStopped(Exception):
        pass
    def parse(*args, **kwargs):
        observed.append(current_context())
        raise ParserProbeStopped()
    monkeypatch.setattr(pipeline, "parse_document", parse)
    context = new_operation(DiagnosticContext(request_id=str(uuid4())), doc_id=doc_id)
    with bind_context(context):
        with pytest.raises(ParserProbeStopped):
            instance._process(doc_id, str(source), "private.docx", [], resume=False)
        assert current_context() == context
    with session_scope() as db:
        generation_id = db.get(DocumentGenerationState, doc_id).candidate_generation_id
    assert generation_id
    assert observed[0].generation_id == generation_id
    assert observed[0].operation_id == context.operation_id
    assert current_context() == DiagnosticContext()
