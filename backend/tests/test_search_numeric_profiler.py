import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from test_scripts.profile_search_asgi import PROFILE, ProfileApplication, timed


def test_threaded_search_profile_contains_only_numbers_and_resets_context():
    sent = []
    def private_work(value):
        assert value == "CANARY_PRIVATE_QUERY"
        return "CANARY_PRIVATE_DOCUMENT"
    private_work = timed("qdrant_ms", private_work)
    async def app(scope, receive, send):
        with ThreadPoolExecutor(max_workers=1) as pool:
            from contextvars import copy_context
            assert pool.submit(copy_context().run, private_work, "CANARY_PRIVATE_QUERY").result() == "CANARY_PRIVATE_DOCUMENT"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"CANARY_PRIVATE_DOCUMENT"})
    async def receive():
        raise AssertionError("profiler must not read body")
    async def send(message):
        sent.append(message)
    asyncio.run(ProfileApplication(app)({"type": "http", "path": "/api/search"}, receive, send))
    header = dict(sent[0]["headers"])[b"x-diag-numeric-profile"]
    assert b"CANARY" not in header
    values = json.loads(header)
    assert len(values) == 8 and values["qdrant_ms"] > 0
    assert all(type(value) in (int, float) and value >= 0 for value in values.values())
    assert sent[1]["body"] == b"CANARY_PRIVATE_DOCUMENT"
    assert PROFILE.get() is None


def test_profile_context_resets_on_failure():
    async def fail(scope, receive, send):
        raise ValueError("CANARY")
    with pytest.raises(ValueError):
        asyncio.run(ProfileApplication(fail)({"type": "http", "path": "/api/search"}, None, None))
    assert PROFILE.get() is None


def test_other_routes_pass_through_without_profile_header():
    sent = []
    async def app(scope, receive, send):
        assert PROFILE.get() is None
        await send({"type": "http.response.start", "status": 200, "headers": []})
    async def send(message):
        sent.append(message)
    asyncio.run(ProfileApplication(app)({"type": "http", "path": "/health"}, None, send))
    assert sent[0]["headers"] == []
