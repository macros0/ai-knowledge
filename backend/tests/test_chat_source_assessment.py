"""Assessment orchestration across every answer mode and private-history boundary."""

import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.services.source_assessment.types import ProviderAssessment, ItemDecision
from app.services.llm_profiles import request_scope
from tests.test_chat import make_client, _patch_retrieval


@pytest.mark.parametrize("mode", [None, "fast", "full"])
@pytest.mark.parametrize(
    ("status", "decision", "label"),
    [
        ("completed", "reject", "irrelevant"),
        ("completed", "allow", "relevant"),
        ("completed", "uncertain", "partial"),
        ("unavailable", None, None),
    ],
)
def test_all_modes_preserve_candidates_and_gate_generation(
    monkeypatch, mode, status, decision, label
):
    from app.api import chat as api

    _patch_retrieval(monkeypatch, api, hits=[{"id": "p"}])
    s = Settings(
        _env_file=None,
        auth_provider="disabled",
        source_assessment_enabled=True,
        llm_profile="standard",
    )
    monkeypatch.setattr(api, "get_settings", lambda: s)
    adapter_calls = []

    class Adapter:
        def assess(self, request, **kwargs):
            adapter_calls.append(request)
            if label is None:
                raise ConnectionError("private-text")
            return ProviderAssessment(
                tuple(ItemDecision(i.source_index, label) for i in request.items)
            )

    monkeypatch.setattr(api, "get_assessor", lambda *a: Adapter(), raising=False)
    generations = []
    monkeypatch.setattr(
        api,
        "_get_llm",
        lambda: SimpleNamespace(chat=lambda *a, **k: generations.append(1) or "Answer"),
    )
    monkeypatch.setattr(
        api,
        "_answer_mode",
        lambda *a, **k: (
            "" if a[0].response_mode == "documents" else generations.append(1) or "Answer"
        ),
    )
    payload = {"query": "тест", "assess_sources": True}
    if mode:
        payload.update(response_mode=mode, attempt_id=str(uuid4()))
    response = make_client(monkeypatch).post("/api/chat", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source_assessment"]["status"] == status
    assert data["source_assessment"]["decision"] == decision
    assert len(adapter_calls) == 1 and len(data["sources"]) == 1
    assert not generations if decision == "reject" or mode == "documents" else bool(generations)
    if decision == "reject":
        assert not data["sources"][0]["in_model_context"]
        from app.services.chat_history import get_thread

        messages = get_thread(data["session_id"], "anonymous")["messages"]
        assert messages[-1]["retrieval_metadata"]["source_assessment"]["decision"] == "reject"
        if mode:
            assert messages[-1]["retrieval_metadata"]["answer_attempt"]["status"] == "completed"


@pytest.mark.parametrize(
    ("enabled", "requested", "expected"),
    [(False, True, "deployment_disabled"), (True, False, "user_disabled")],
)
def test_disabled_never_builds_client(monkeypatch, enabled, requested, expected):
    from app.api import chat as api

    _patch_retrieval(monkeypatch, api, hits=[{"id": "p"}])
    monkeypatch.setattr(
        api, "get_settings", lambda: Settings(_env_file=None, source_assessment_enabled=enabled)
    )
    monkeypatch.setattr(
        api, "get_assessor", lambda *a: pytest.fail("client created"), raising=False
    )
    monkeypatch.setattr(api, "_get_llm", lambda: SimpleNamespace(chat=lambda *a, **k: "Answer"))
    data = (
        make_client(monkeypatch)
        .post("/api/chat", json={"query": "тест", "assess_sources": requested})
        .json()
    )
    assert data["source_assessment"]["reason_code"] == expected


def test_empty_skips_factory(monkeypatch):
    from app.api import chat as api

    _patch_retrieval(monkeypatch, api, hits=[])
    monkeypatch.setattr(
        api, "get_settings", lambda: Settings(_env_file=None, source_assessment_enabled=True)
    )
    monkeypatch.setattr(
        api, "get_assessor", lambda *a: pytest.fail("client created"), raising=False
    )
    data = make_client(monkeypatch).post("/api/chat", json={"query": "тест"}).json()
    assert data["source_assessment"]["status"] == "skipped"


def test_sources_precede_assessment_and_internal_snapshot_is_redacted(monkeypatch):
    from app.api import chat as api
    monkeypatch.setattr(api, "_answer_mode", lambda *a, **kw: "Answer")

    _patch_retrieval(monkeypatch, api, hits=[{"id": "p"}])
    s = Settings(_env_file=None, source_assessment_enabled=True)
    monkeypatch.setattr(api, "get_settings", lambda: s)

    class Adapter:
        def assess(self, request, **kwargs):
            return ProviderAssessment((ItemDecision(1, "irrelevant"),))

    monkeypatch.setattr(api, "get_assessor", lambda *a: Adapter(), raising=False)
    events = []
    with request_scope(
        on_progress=lambda e: events.append(("progress", e)),
        on_sources=lambda e: events.append(("sources", e)),
    ):
        response = make_client(monkeypatch).post(
            "/api/chat", json={"query": "тест", "response_mode": "fast"})
        assert response.status_code == 200, response.text
        data = response.json()
    phases = [
        (kind, e.get("status") if isinstance(e, dict) else None)
        for kind, e in events
        if kind == "sources" or e.get("phase") == "source_assessment"
    ]
    assert phases == [("sources", None), ("progress", "running"), ("progress", "completed"), ("sources", None)]
    from app.services.chat_history import get_thread

    metadata = get_thread(data["session_id"], "anonymous")["messages"][-1]["retrieval_metadata"]
    assert "source_assessment_request" not in metadata
    assert metadata["source_assessment_replay"] == {"requested_enabled": None}


def test_cancel_during_assessment_never_generates(monkeypatch):
    from app.api import chat as api
    from app.services.llm_scheduler import LLMCancelled

    _patch_retrieval(monkeypatch, api, hits=[{"id": "p"}])
    s = Settings(_env_file=None, source_assessment_enabled=True)
    cancel = threading.Event()

    class Adapter:
        def assess(self, request, **kwargs):
            cancel.set()
            return ProviderAssessment((ItemDecision(1, "irrelevant"),))

    monkeypatch.setattr(api, "get_assessor", lambda *a: Adapter(), raising=False)
    monkeypatch.setattr(api, "_get_llm", lambda: pytest.fail("generation after stop"))
    user = SimpleNamespace(user_id="anonymous", username="anonymous")
    from app.models.schemas import ChatRequest

    with request_scope(cancel=cancel), pytest.raises(LLMCancelled):
        api._answer(ChatRequest(query="тест"), user, s)


def test_source_change_during_assessment_prevents_final_publication(monkeypatch):
    from app.api import chat as api
    from app.api.errors import ApiError
    from app.models.schemas import ChatRequest

    _patch_retrieval(monkeypatch, api, hits=[{"id": "p"}])

    class Adapter:
        def assess(self, request, **kwargs):
            monkeypatch.setattr(
                api,
                "_validate_final_source_state",
                lambda *a: (_ for _ in ()).throw(
                    ApiError(status_code=409, code="chat_sources_changed", detail="changed")
                ),
            )
            return ProviderAssessment((ItemDecision(1, "relevant"),))

    monkeypatch.setattr(api, "get_assessor", lambda *a: Adapter(), raising=False)
    emitted = []
    with request_scope(on_sources=emitted.append), pytest.raises(ApiError):
        api._answer(
            ChatRequest(query="тест"),
            SimpleNamespace(user_id="anonymous"),
            Settings(_env_file=None, source_assessment_enabled=True),
        )
    # The valid initial snapshot is visible while checking. A changed source
    # must not be published again as an assessed/final candidate list.
    assert len(emitted) == 1


@pytest.mark.parametrize("selected", [False, True])
def test_sources_are_visible_inside_assessor_before_it_finishes(monkeypatch, selected):
    from app.api import chat as api
    from app.models.schemas import ChatRequest, ChatSource
    from app.services.source_assessment.config import resolve_assessment_config
    from app.services import chat_history

    settings = Settings(_env_file=None, source_assessment_enabled=True)
    source = ChatSource(title="source", filepath="doc/source.md", doc_id="doc", score=1, tags=[], selectable=selected)
    blocks = [{"title": "source", "doc_id": "doc", "content": "evidence"}]
    visible = []
    monkeypatch.setattr(api, "_validate_final_source_state", lambda b: None)
    monkeypatch.setattr(chat_history, "save_attempt_sources", lambda *a, **kw: None)

    class Adapter:
        def assess(self, request, **kwargs):
            assert len(visible) == 1
            assert visible[0][0]["doc_id"] == "doc"
            assert visible[0][0]["selectable"] is selected
            return ProviderAssessment((ItemDecision(1, "relevant"),))

    monkeypatch.setattr(api, "get_assessor", lambda *a: Adapter())
    req = ChatRequest(query="question", response_mode="fast")
    with request_scope(on_sources=visible.append):
        outcome, _ = api._run_source_assessment(req, blocks, [source], settings, None,
                                               resolve_assessment_config(settings), None)
    assert outcome.status == "completed"


def test_invalid_initial_sources_are_not_published_or_assessed(monkeypatch):
    from app.api import chat as api
    from app.api.errors import ApiError
    from app.models.schemas import ChatRequest, ChatSource
    from app.services.source_assessment.config import resolve_assessment_config

    settings = Settings(_env_file=None, source_assessment_enabled=True)
    monkeypatch.setattr(api, "_validate_final_source_state", lambda b: (_ for _ in ()).throw(ApiError(status_code=409, code="chat_sources_changed", detail="changed")))
    monkeypatch.setattr(api, "get_assessor", lambda *a: pytest.fail("invalid source sent to model"))
    visible = []
    source = ChatSource(title="source", filepath="doc/source.md", doc_id="doc", score=1, tags=[])
    with request_scope(on_sources=visible.append), pytest.raises(ApiError):
        api._run_source_assessment(ChatRequest(query="question"), [{"doc_id": "doc"}], [source],
                                   settings, None, resolve_assessment_config(settings), None)
    assert visible == []

@pytest.mark.parametrize('requested', [None, True, False])
def test_documents_mode_never_builds_assessor_or_generates(monkeypatch, requested):
    import json
    from app.api import chat as api
    from app.services.chat_history import get_thread

    _patch_retrieval(monkeypatch, api, hits=[{'id': 'p'}])
    monkeypatch.setattr(api, 'get_settings', lambda: Settings(_env_file=None,
        source_assessment_enabled=True, source_assessment_default_enabled=True))
    monkeypatch.setattr(api, 'get_assessor', lambda *a: pytest.fail('documents mode called assessor'))
    monkeypatch.setattr(api, '_get_llm', lambda: pytest.fail('documents mode called answer model'))
    response = make_client(monkeypatch).post('/api/chat/stream', json={
        'query': 'тест', 'response_mode': 'documents', 'assess_sources': requested})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert not any(event.get('phase') == 'source_assessment' for event in events)
    assert events[-1]['type'] == 'result'
    data = events[-1]['data']
    assert len(data['sources']) == 1
    assert data['source_assessment']['status'] == 'disabled'
    assert data['source_assessment']['reason_code'] == 'documents_mode'
    metadata = get_thread(data['session_id'], 'anonymous')['messages'][-1]['retrieval_metadata']
    assert metadata['source_assessment']['status'] == 'disabled'
