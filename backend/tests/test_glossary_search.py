from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.auth.models import User
from app.config import Settings
from app.models.schemas import ChatRequest, SearchRequest
from app.services.errors import VectorStoreError
from app.services.fusion import Hit
from app.services.glossary.types import (
    AppliedTerm,
    FormSource,
    MatchGroup,
    MatchSpan,
    QueryPlan,
    ResolvedForm,
)
from app.services.sparse import _term_index, to_sparse_vector


def _plan(query: str, *, dense_query: str | None = None, additions: tuple[str, ...] = (), status: str = "applied") -> QueryPlan:
    return QueryPlan(
        original_query=query,
        dense_query=dense_query or query,
        added_sparse_texts=additions,
        match_groups=(),
        applied_terms=(),
        status=status,  # type: ignore[arg-type]
    )


def _settings(**overrides):
    values = {
        "_env_file": None,
        "auth_provider": "disabled",
        "glossary_query_expansion_enabled": True,
        "search_mode_default": "hybrid",
    }
    values.update(overrides)
    return Settings(**values)


def _identifier_plan(*, enabled: bool = True) -> QueryPlan:
    if not enabled:
        return _plan("инфо-тип 0003", status="disabled")
    group = MatchGroup(
        term_id=1,
        canonical="IT0003",
        kind="sap_infotype",
        canonical_locale="en",
        original_name="Payroll infotype",
        term_version=1,
        source_revision=1,
        spans=(MatchSpan(0, 14, "инфо-тип 0003", "alias", "инфо-тип 0003"),),
        matched_forms=("IT0003",),
        match_type="alias",
    )
    return QueryPlan(
        original_query="инфо-тип 0003",
        dense_query="инфо-тип 0003\n[Domain term: Payroll infotype]",
        added_sparse_texts=("IT0003",),
        match_groups=(group,),
        applied_terms=(),
        status="applied",
    )


def _identifier_hits() -> list[Hit]:
    return [
        Hit(
            "near-code",
            1.0,
            {
                "point_type": "concept",
                "doc_id": "noise-doc",
                "chunk_index": 0,
                "title": "Ссылка на IT00037",
                "content": "Шумовой документ с похожим, но другим кодом IT00037.",
                "tags": [],
                "filepath": "noise-doc/noise.md",
            },
        ),
        Hit(
            "exact-code",
            0.01,
            {
                "point_type": "concept",
                "doc_id": "exact-doc",
                "chunk_index": 0,
                "title": "PY-ES: IT0003",
                "content": "Payroll data for IT0003.",
                "tags": [],
                "filepath": "exact-doc/it0003.md",
            },
        ),
    ]


def _identifier_docs() -> dict[str, dict[str, str]]:
    return {
        "noise-doc": {"filename": "noise.docx"},
        "exact-doc": {"filename": "py-es.docx"},
    }


def test_query_sparse_preserves_original_vector_for_no_match_and_repeated_words():
    from app.services.glossary.query_sparse import build_query_sparse

    query = "base base"
    expected = to_sparse_vector(query, stopwords=frozenset())

    for status in ("disabled", "no_match", "unavailable"):
        actual = build_query_sparse(
            _plan(query, status=status),
            stopwords=frozenset(),
            settings=SimpleNamespace(glossary_sparse_expansion_weight=0.35),
        )
        assert actual.indices == expected.indices
        assert actual.values == expected.values


def test_query_sparse_weights_limited_additions_and_aggregates_collisions():
    from app.services.glossary.query_sparse import build_query_sparse

    original = to_sparse_vector("base base", stopwords=frozenset())
    weight = 0.35
    actual = build_query_sparse(
        _plan(
            "base base",
            additions=("kept", "обязат тестировании", "обязат"),
        ),
        stopwords=frozenset(),
        settings=SimpleNamespace(glossary_sparse_expansion_weight=weight),
    )

    values = dict(zip(actual.indices, actual.values))
    original_values = dict(zip(original.indices, original.values))
    assert actual.indices == sorted(set(actual.indices))
    assert {idx: values[idx] for idx in original.indices} == original_values
    assert values[_term_index("обязат")] == pytest.approx(
        to_sparse_vector("обязат тестировании", stopwords=frozenset()).values[0] * weight
    )
    assert values[_term_index("kept")] == pytest.approx(
        to_sparse_vector("kept", stopwords=frozenset()).values[0] * weight
    )
    assert _term_index("omitted") not in values


def test_query_sparse_gives_all_configured_forms_the_same_weight():
    from app.services.glossary.query_sparse import build_query_sparse

    structural = ResolvedForm(
        text="it0003",
        normalized="it0003",
        identity_key="infotype:0003",
        boundary_mode="identifier",
        sources=(FormSource(kind="rule_alias", rule_id=1, rule_version=1),),
        can_trigger=True,
        can_search=True,
    )
    group = MatchGroup(
        term_id=None,
        canonical="IT0003",
        kind="sap_infotype",
        canonical_locale="und",
        original_name="IT0003",
        term_version=0,
        source_revision=1,
        spans=(MatchSpan(0, 7, "ИТ 0003", "structural", "ИТ 0003"),),
        matched_forms=("it0003",),
        match_type="structural",
        system_rule="sap_infotype",
        structural_code="IT0003",
        resolved_forms=(structural,),
    )
    plan = QueryPlan(
        original_query="ИТ 0003",
        dense_query="ИТ 0003\nit0003",
        added_sparse_texts=("it0003",),
        match_groups=(group,),
        applied_terms=(),
        status="applied",
    )

    actual = build_query_sparse(
        plan,
        stopwords=frozenset(),
        settings=SimpleNamespace(glossary_sparse_expansion_weight=1.0),
    )
    values = dict(zip(actual.indices, actual.values))
    assert values[_term_index("it0003")] == pytest.approx(to_sparse_vector("it0003", stopwords=frozenset()).values[0])


def test_search_prepares_once_and_passes_expanded_vectors_and_filters(monkeypatch):
    from app.api import search as search_module

    settings = _settings()
    plan = _plan("original", dense_query="expanded dense", additions=("added",))
    calls: list[tuple[str, object]] = []
    hydration_calls: list[dict[str, object]] = []

    class Embedder:
        def embed(self, query):
            calls.append(("embed", query))
            return [1.0]

    class VectorStore:
        def search_composite(self, **kwargs):
            calls.append(("search", kwargs))
            return []

    monkeypatch.setattr(search_module, "get_settings", lambda: settings)
    monkeypatch.setattr(search_module, "prepare_query", lambda *args, **kwargs: calls.append(("prepare", args[0])) or plan)
    monkeypatch.setattr(search_module, "build_query_sparse", lambda *args, **kwargs: calls.append(("sparse", args[0])) or "sparse")
    monkeypatch.setattr(search_module, "get_stopwords", lambda *args, **kwargs: frozenset())
    monkeypatch.setattr(search_module, "_get_embedder", lambda: Embedder())
    monkeypatch.setattr(search_module, "_get_vector_store", lambda: VectorStore())
    monkeypatch.setattr(search_module.get_rate_limiter(), "check_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        search_module,
        "load_visible_retrieval_hits",
        lambda hits, **kwargs: hydration_calls.append(kwargs) or ([], {}),
    )

    response = search_module.search(
        SearchRequest(
            query="original",
            mode="hybrid",
            tags=["tag"],
            source_locales=["de"],
            include_unknown_source_locale=True,
        ),
        User(),
    )

    assert [kind for kind, _ in calls].count("prepare") == 1
    assert ("embed", "expanded dense") in calls
    sparse_call = next(value for kind, value in calls if kind == "sparse")
    assert sparse_call.original_query == "original"
    search_call = next(value for kind, value in calls if kind == "search")
    assert search_call["sparse_vec"] == "sparse"
    assert search_call["tags"] == ["tag"]
    assert search_call["source_locales"] == ["de"]
    assert search_call["include_unknown_source_locale"] is True
    assert hydration_calls == [{"max_concept_chars": 300, "max_chunk_chars": 300}]
    assert response.expansion_status == "applied"


@pytest.mark.parametrize(
    ("mode", "expected_embed", "expected_sparse"),
    [("dense", True, False), ("bm25", False, True), ("hybrid", True, True)],
)
def test_search_does_not_call_disabled_vector_branch(monkeypatch, mode, expected_embed, expected_sparse):
    from app.api import search as search_module

    settings = _settings()
    plan = _plan("original", dense_query="expanded")
    calls = {"embed": 0, "sparse": 0}

    class Embedder:
        def embed(self, query):
            calls["embed"] += 1
            return [1.0]

    class VectorStore:
        def search_composite(self, **kwargs):
            return []

    monkeypatch.setattr(search_module, "get_settings", lambda: settings)
    monkeypatch.setattr(search_module, "prepare_query", lambda *args, **kwargs: plan)
    monkeypatch.setattr(search_module, "build_query_sparse", lambda *args, **kwargs: calls.__setitem__("sparse", calls["sparse"] + 1) or "sparse")
    monkeypatch.setattr(search_module, "get_stopwords", lambda *args, **kwargs: frozenset())
    monkeypatch.setattr(search_module, "_get_embedder", lambda: Embedder())
    monkeypatch.setattr(search_module, "_get_vector_store", lambda: VectorStore())
    monkeypatch.setattr(search_module.get_rate_limiter(), "check_action", lambda *args, **kwargs: None)

    search_module.search(SearchRequest(query="original", mode=mode), User())

    assert calls["embed"] == int(expected_embed)
    assert calls["sparse"] == int(expected_sparse)


def test_search_snapshot_failure_falls_back_truthfully_without_masking_qdrant(monkeypatch):
    from app.api import search as search_module
    from app.services.glossary import expansion

    settings = _settings()
    calls = []

    def fail_snapshot():
        raise RuntimeError("glossary database unavailable")

    class Embedder:
        def embed(self, query):
            calls.append(("embed", query))
            return [1.0]

    class VectorStore:
        def search_composite(self, **kwargs):
            calls.append(("search", kwargs))
            raise VectorStoreError("Qdrant rejected request")

    monkeypatch.setattr(expansion, "load_glossary_snapshot", fail_snapshot)
    monkeypatch.setattr(search_module, "get_settings", lambda: settings)
    monkeypatch.setattr(search_module, "_get_embedder", lambda: Embedder())
    monkeypatch.setattr(search_module, "_get_vector_store", lambda: VectorStore())
    monkeypatch.setattr(search_module, "get_stopwords", lambda *args, **kwargs: frozenset())
    monkeypatch.setattr(search_module.get_rate_limiter(), "check_action", lambda *args, **kwargs: None)

    with pytest.raises(VectorStoreError, match="Qdrant rejected"):
        search_module.search(SearchRequest(query="original", mode="dense"), User())

    assert calls[0] == ("embed", "original")
    assert len(calls) == 2
    assert calls[1][0] == "search"


def test_chat_prepares_after_session_check_and_does_not_call_llm_for_empty_hits(monkeypatch):
    from app.api import chat as chat_module

    settings = _settings()
    events = []

    class VectorStore:
        def search_composite(self, **kwargs):
            events.append(("search", kwargs))
            return []

    monkeypatch.setattr(chat_module, "get_settings", lambda: settings)
    monkeypatch.setattr(chat_module.get_rate_limiter(), "check_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chat_module.chat_history,
        "check_session_state",
        lambda *args: events.append(("session", None)),
    )
    monkeypatch.setattr(
        chat_module.chat_history,
        "store_turn",
        lambda *args, **kwargs: events.append(("store", kwargs)) or "session-id",
    )
    monkeypatch.setattr(
        chat_module,
        "prepare_query",
        lambda *args, **kwargs: events.append(("prepare", args[0])) or _plan("original", dense_query="expanded"),
    )
    monkeypatch.setattr(chat_module, "get_stopwords", lambda *args, **kwargs: frozenset())
    monkeypatch.setattr(chat_module, "build_query_sparse", lambda *args, **kwargs: events.append(("sparse", None)) or "sparse")
    monkeypatch.setattr(chat_module, "_get_embedder", lambda: SimpleNamespace(embed=lambda query: events.append(("embed", query)) or [1.0]))
    monkeypatch.setattr(chat_module, "_get_vector_store", lambda: VectorStore())
    monkeypatch.setattr(chat_module, "_get_llm", lambda: SimpleNamespace(chat=lambda *args: pytest.fail("LLM must not be called")))

    response = chat_module.chat(
        ChatRequest(query="original", mode="dense", session_id="00000000-0000-0000-0000-000000000001"),
        User(),
    )

    assert [kind for kind, _ in events].index("session") < [kind for kind, _ in events].index("prepare")
    assert ("embed", "expanded") in events
    assert response.expansion_status == "applied"


def test_chat_keeps_domain_context_and_original_question_language(monkeypatch):
    from app.api import chat as chat_module
    from app.services.fusion import Hit

    settings = _settings()
    group = MatchGroup(
        term_id=1,
        canonical="IT0003",
        kind="sap_infotype",
        canonical_locale="de",
        original_name="Payroll infotype",
        term_version=1,
        source_revision=1,
        spans=(MatchSpan(0, 9, "инфотип 3", "structural", "IT0003"),),
        matched_forms=("IT0003", "инфотип 3"),
        match_type="structural",
    )
    term = AppliedTerm(
        term_id=1,
        canonical="IT0003",
        kind="sap_infotype",
        display_name="Payroll infotype",
        display_locale="de",
        display_is_machine_translated=False,
        display_is_fallback=True,
        canonical_locale="de",
        term_version=1,
        source_revision=1,
        matched_texts=("инфотип 3",),
        match_type="structural",
        added_forms=("IT0003", "инфотип 3"),
    )
    plan = QueryPlan(
        original_query="инфотип 3",
        dense_query="инфотип 3\n[Domain term: IT0003 — Payroll infotype]",
        added_sparse_texts=("IT0003",),
        match_groups=(group,),
        applied_terms=(term,),
        status="applied",
    )
    llm_prompts = []

    monkeypatch.setattr(chat_module, "get_settings", lambda: settings)
    monkeypatch.setattr(chat_module.get_rate_limiter(), "check_action", lambda *a, **k: None)
    monkeypatch.setattr(chat_module, "prepare_query", lambda *a, **k: plan)
    monkeypatch.setattr(chat_module, "get_stopwords", lambda *a, **k: frozenset())
    monkeypatch.setattr(chat_module._get_embedder(), "embed", lambda query: [1.0])
    monkeypatch.setattr(
        chat_module._get_vector_store(),
        "search_composite",
        lambda **kwargs: [
            Hit(
                "point-1",
                1.0,
                {
                    "point_type": "concept",
                    "doc_id": "doc-1",
                    "chunk_index": 0,
                    "title": "Описание IT0003",
                    "content": "Факт про IT0003",
                    "tags": [],
                    "filepath": "doc-1/concept.md",
                },
            )
        ],
    )
    monkeypatch.setattr(
        chat_module,
        "load_visible_retrieval_hits",
        lambda hits, **kwargs: (hits, {"doc-1": {"filename": "source.docx"}}),
    )
    monkeypatch.setattr(chat_module.chat_history, "store_turn", lambda *a, **k: "session-1")

    def fake_chat(system, user):
        llm_prompts.extend([system, user])
        return "Ответ [1]"

    monkeypatch.setattr(chat_module._get_llm(), "chat", fake_chat)

    response = chat_module.chat(
        ChatRequest(query="инфотип 3", locale="en", mode="dense"),
        User(user_id="user-1"),
    )

    payload = json.loads(response.model_dump_json())
    assert payload["expansion_status"] == "applied"
    assert payload["applied_terms"][0]["used_in"] == ["dense"]
    assert payload["applied_terms"][0]["added_forms"] == ["IT0003", "инфотип 3"]
    assert payload["applied_terms"][0]["matched_texts"] == ["инфотип 3"]
    assert "Response locale: en" in llm_prompts[0]
    assert "User question: инфотип 3" in llm_prompts[1]
    assert "Domain term: IT0003" not in llm_prompts[1]
    assert "Matched domain terms: [Payroll infotype via инфотип 3]" in llm_prompts[1]


def test_search_promotes_exact_added_identifier_before_context_limit(monkeypatch):
    """A low-ranked exact glossary code must survive the pre-merge char limit.

    ``IT00037`` deliberately ranks first: identifier boundaries must prevent it
    from receiving the exact ``IT0003`` glossary priority.
    """
    from app.api import search as search_module

    settings = _settings(chat_max_context_chars=10)
    hits = _identifier_hits()

    monkeypatch.setattr(search_module, "get_settings", lambda: settings)
    monkeypatch.setattr(search_module, "prepare_query", lambda *a, **k: _identifier_plan())
    monkeypatch.setattr(search_module._get_embedder(), "embed", lambda query: [1.0])
    monkeypatch.setattr(
        search_module._get_vector_store(),
        "search_composite",
        lambda **kwargs: hits,
    )
    monkeypatch.setattr(
        search_module,
        "load_visible_retrieval_hits",
        lambda found, **kwargs: (found, _identifier_docs()),
    )
    monkeypatch.setattr(search_module.get_rate_limiter(), "check_action", lambda *a, **k: None)

    response = search_module.search(
        SearchRequest(query="инфо-тип 0003", mode="dense", top_k=1),
        User(),
    )

    assert [hit.title for hit in response.hits] == ["PY-ES: IT0003"]


def test_chat_promotes_exact_added_identifier_before_context_limit(monkeypatch):
    from app.api import chat as chat_module

    settings = _settings(chat_max_context_chars=10)
    hits = _identifier_hits()

    monkeypatch.setattr(chat_module, "get_settings", lambda: settings)
    monkeypatch.setattr(chat_module, "prepare_query", lambda *a, **k: _identifier_plan())
    monkeypatch.setattr(chat_module._get_embedder(), "embed", lambda query: [1.0])
    monkeypatch.setattr(
        chat_module._get_vector_store(),
        "search_composite",
        lambda **kwargs: hits,
    )
    monkeypatch.setattr(
        chat_module,
        "load_visible_retrieval_hits",
        lambda found, **kwargs: (found, _identifier_docs()),
    )
    monkeypatch.setattr(chat_module._get_llm(), "chat", lambda *a, **k: "Ответ [1]")
    monkeypatch.setattr(chat_module.chat_history, "store_turn", lambda *a, **k: "session-1")
    monkeypatch.setattr(chat_module.get_rate_limiter(), "check_action", lambda *a, **k: None)

    response = chat_module.chat(
        ChatRequest(query="инфо-тип 0003", mode="dense", top_k=1),
        User(user_id="user-1"),
    )

    assert [source.title for source in response.sources] == ["PY-ES: IT0003"]


def test_search_without_glossary_preserves_existing_rank_order(monkeypatch):
    from app.api import search as search_module

    settings = _settings(chat_max_context_chars=10)
    hits = _identifier_hits()

    monkeypatch.setattr(search_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        search_module,
        "prepare_query",
        lambda *a, **k: _identifier_plan(enabled=False),
    )
    monkeypatch.setattr(search_module._get_embedder(), "embed", lambda query: [1.0])
    monkeypatch.setattr(
        search_module._get_vector_store(),
        "search_composite",
        lambda **kwargs: hits,
    )
    monkeypatch.setattr(
        search_module,
        "load_visible_retrieval_hits",
        lambda found, **kwargs: (found, _identifier_docs()),
    )
    monkeypatch.setattr(search_module.get_rate_limiter(), "check_action", lambda *a, **k: None)

    response = search_module.search(
        SearchRequest(query="инфо-тип 0003", mode="dense", top_k=1, use_glossary=False),
        User(),
    )

    assert [hit.title for hit in response.hits] == ["Ссылка на IT00037"]


def test_chat_does_not_call_llm_when_exact_merge_removes_last_candidate(monkeypatch):
    from app.api import chat as chat_module
    monkeypatch.setattr(chat_module, 'get_settings', lambda: _settings())
    monkeypatch.setattr(chat_module, 'prepare_query', lambda *a, **k: _identifier_plan())
    monkeypatch.setattr(chat_module, '_get_embedder', lambda: SimpleNamespace(embed=lambda query: [1.0]))
    monkeypatch.setattr(chat_module, '_get_vector_store', lambda: SimpleNamespace(search_composite=lambda **kwargs: _identifier_hits()[:1]))
    monkeypatch.setattr(chat_module, 'load_visible_retrieval_hits', lambda hits, **kwargs: (hits, _identifier_docs()))
    monkeypatch.setattr(chat_module, '_get_llm', lambda: SimpleNamespace(chat=lambda *args: pytest.fail('LLM called with no exact context')))
    monkeypatch.setattr(chat_module.chat_history, 'store_turn', lambda *a, **k: 'session-1')
    monkeypatch.setattr(chat_module.get_rate_limiter(), 'check_action', lambda *a, **k: None)
    response = chat_module.chat(ChatRequest(query='инфо-тип 0003', mode='dense', locale='en'), User(user_id='test'))
    assert response.sources == []
    assert response.answer == 'No sources found.'
