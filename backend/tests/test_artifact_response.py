"""Opened artifacts retain the HTTP download contract across publication."""
import asyncio

import pytest
from starlette.responses import FileResponse

from app.services.artifact_response import OpenedFileResponse


def _request(response, *, method="GET", headers=None, extensions=None):
    messages = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    asyncio.run(response({
        "type": "http", "method": method, "headers": headers or [],
        "asgi": {"spec_version": "2.4"}, "extensions": extensions or {},
    }, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages)
    return start["status"], dict(start["headers"]), body, messages


@pytest.mark.parametrize("method,request_headers", [
    ("GET", []), ("HEAD", []),
    ("GET", [(b"range", b"bytes=2-5")]),
    ("HEAD", [(b"range", b"bytes=2-5")]),
    ("GET", [(b"range", b"bytes=-3")]),
    ("GET", [(b"range", b"bytes=5-")]),
    ("GET", [(b"range", b"bytes=99-100")]),
    ("GET", [(b"range", b"invalid")]),
    ("GET", [(b"range", b"bytes=1-2"), (b"if-range", b"stale")]),
    ("GET", [(b"range", b"bytes=0-1,7-9")]),
])
def test_opened_download_preserves_file_response_contract(tmp_path, method, request_headers):
    path = tmp_path / "data.bin"
    path.write_bytes(b"0123456789")
    expected = _request(FileResponse(path, media_type="application/octet-stream", filename="data.bin"),
                        method=method, headers=request_headers)
    response = OpenedFileResponse(path, media_type="application/octet-stream", filename="data.bin")
    actual = _request(response, method=method, headers=request_headers)
    assert response._file.closed
    assert actual[0] == expected[0]
    actual_headers, expected_headers = actual[1], expected[1]
    actual_body, expected_body = actual[2], expected[2]
    if b"boundary=" in expected_headers.get(b"content-type", b""):
        expected_boundary = expected_headers[b"content-type"].split(b"boundary=")[1]
        actual_boundary = actual_headers[b"content-type"].split(b"boundary=")[1]
        actual_body = actual_body.replace(actual_boundary, expected_boundary)
        actual_headers[b"content-type"] = expected_headers[b"content-type"]
    assert actual_headers == expected_headers
    assert actual_body == expected_body


def test_opened_download_never_delegates_old_path_to_server(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"old bytes")
    response = OpenedFileResponse(path, media_type="application/octet-stream")
    result = _request(response, extensions={"http.response.pathsend": {}})
    assert result[2] == b"old bytes"
    assert all(message["type"] != "http.response.pathsend" for message in result[3])
    assert response._file.closed


def test_opened_download_closes_handle_on_send_failure(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"old bytes")
    response = OpenedFileResponse(path, media_type="application/octet-stream")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        raise RuntimeError("Disconnected")

    with pytest.raises(RuntimeError, match="Disconnected"):
        asyncio.run(response({"type": "http", "method": "GET", "headers": [],
                              "asgi": {"spec_version": "2.4"}}, receive, send))
    assert response._file.closed
