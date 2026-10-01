"""A support reference correlates safe events all the way to an exported ZIP."""
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from app.services.diagnostics.context import DiagnosticContext
from tests.test_authz import login
from tests.test_diagnostics_api import api  # noqa: F401 - shared isolated FastAPI fixture


def test_admin_export_contains_reference_and_no_exception_content(request):
    client, queue = request.getfixturevalue("api")
    login(client, "demo.admin")
    request_id = str(uuid4())
    try:
        try:
            raise RuntimeError("CANARY_PRIVATE_QUERY_AND_TOKEN")
        except RuntimeError as cause:
            raise ValueError("CANARY_PRIVATE_DOCUMENT") from cause
    except ValueError as failure:
        assert client.app.state.diagnostics.recorder.emit(
            "llm_parse_failed", context=DiagnosticContext(request_id=request_id),
            exception=failure, fields={"stage": "generate"})
    client.app.state.diagnostics.recorder._queue.join()
    query = client.post("/api/admin/diagnostics/events/query", json={"request_id": request_id})
    assert query.status_code == 200
    assert any(row["event_code"] == "llm_parse_failed" for row in query.json()["events"])
    assert "CANARY_PRIVATE" not in query.text
    created = client.post("/api/admin/diagnostics/bundles", json={"request_id": request_id})
    assert created.status_code == 202, created.text
    bundle_id = created.json()["id"]
    queue.start()
    assert queue.wait_idle(10)
    preview = client.post(f"/api/admin/diagnostics/bundles/{bundle_id}/preview")
    assert preview.status_code == 200
    assert "CANARY_PRIVATE" not in preview.text
    downloaded = client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download")
    assert downloaded.status_code == 200
    assert "CANARY_PRIVATE" not in downloaded.content.decode("latin1")
    with ZipFile(BytesIO(downloaded.content)) as archive:
        assert archive.testzip() is None
        events = [json.loads(line) for line in archive.read("events/backend.jsonl").splitlines()]
        assert any(row["request_id"] == request_id and row["event_code"] == "llm_parse_failed"
                   for row in events)
    for path in Path(queue.store.root).rglob("*.jsonl"):
        assert b"CANARY_PRIVATE" not in path.read_bytes()
