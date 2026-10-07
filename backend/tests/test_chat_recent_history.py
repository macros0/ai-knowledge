"""Recent history pages contain complete turns from active, owned sessions only."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.db.models import ChatMessage
from app.db.session import get_engine, session_scope
from app.services import chat_history as ch
from tests.test_chat_history import _U, create_session, login, make_client


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def recent(client, **params):
    response = client.get("/api/chat/history/recent", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def append_message(session_id, role, content, **fields):
    with session_scope() as s:
        message = ChatMessage(session_id=session_id, role=role, content=content, **fields)
        s.add(message)
        s.flush()
        return message.id


def test_empty_recent_history(client):
    login(client, "demo.user")
    assert recent(client) == {"turns": [], "next_before_id": None}


def test_only_own_active_questions_including_for_admin(client):
    create_session("sim-user", query="foreign")
    deleted = create_session("sim-admin", "demo.admin", query="deleted")
    ch.soft_delete_session(deleted, _U("sim-admin", "demo.admin"))
    own = create_session("sim-admin", "demo.admin", query="own")
    login(client, "demo.admin")

    page = recent(client)
    assert [turn["session_id"] for turn in page["turns"]] == [own]
    assert [message["content"] for message in page["turns"][0]["messages"]] == [
        "own", "ответ",
    ]
    assert page["next_before_id"] is None


def test_pagination_uses_question_ids_across_sessions_with_timestamp_ties(client):
    first = create_session("sim-user", query="one", answer="answer one")
    second = create_session("sim-user", query="two", answer="answer two")
    ch.store_turn(first, _U("sim-user", "demo.user"), "three", "answer three")
    # Equal timestamps must not drop a question at the page boundary.
    with session_scope() as s:
        for message in s.scalars(select(ChatMessage)):
            message.created_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    login(client, "demo.user")

    newest = recent(client, limit=2)
    assert [turn["messages"][0]["content"] for turn in newest["turns"]] == ["two", "three"]
    assert [turn["session_id"] for turn in newest["turns"]] == [second, first]
    assert newest["next_before_id"] == newest["turns"][0]["id"]
    assert all(turn["created_at"] == "2026-10-01T00:00:00" for turn in newest["turns"])

    # A new question between requests cannot shift the older cursor page.
    ch.store_turn(second, _U("sim-user", "demo.user"), "four", "answer four")
    older = recent(client, limit=2, before_id=newest["next_before_id"])
    assert [turn["messages"][0]["content"] for turn in older["turns"]] == ["one"]
    assert [message["content"] for message in older["turns"][0]["messages"]] == [
        "one", "answer one",
    ]
    assert older["next_before_id"] is None
    assert recent(client, before_id=older["turns"][0]["id"]) == {
        "turns": [], "next_before_id": None,
    }


def test_turn_boundaries_follow_same_session_not_interleaved_global_ids(client):
    first = create_session("sim-user", query="initial one")
    second = create_session("sim-user", query="initial two")
    question = append_message(first, "user", "interleaved question")
    append_message(second, "user", "other question")
    append_message(first, "assistant", "first answer")
    append_message(second, "assistant", "other answer")
    append_message(first, "assistant", "additional answer")
    next_question = append_message(first, "user", "question without answer")
    login(client, "demo.user")

    page = recent(client, limit=3)
    assert [turn["messages"][0]["content"] for turn in page["turns"]] == [
        "interleaved question", "other question", "question without answer",
    ]
    assert page["turns"][0]["id"] == question
    assert [message["content"] for message in page["turns"][0]["messages"]] == [
        "interleaved question", "first answer", "additional answer",
    ]
    assert [message["content"] for message in page["turns"][1]["messages"]] == [
        "other question", "other answer",
    ]
    assert page["turns"][2]["id"] == next_question
    assert len(page["turns"][2]["messages"]) == 1


def test_exclude_live_session_does_not_consume_page_slots(client):
    historical = create_session("sim-user", query="history")
    live = create_session("sim-user", query="live")
    ch.store_turn(live, _U("sim-user", "demo.user"), "live again", "answer")
    login(client, "demo.user")

    page = recent(client, limit=1, exclude_session_id=live)
    assert [turn["session_id"] for turn in page["turns"]] == [historical]
    assert page["next_before_id"] is None


@pytest.mark.parametrize("status", ["incomplete", "stopped", "failed", "completed"])
def test_preserves_answer_attempt_status(client, status):
    user = _U("sim-user", "demo.user")
    attempt = ch.begin_attempt(None, user, "question", "attempt-id", "answer")
    if status != "incomplete":
        ch.finish_attempt(attempt, user, status=status, answer="complete answer")
    login(client, "demo.user")

    messages = recent(client)["turns"][0]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["retrieval_metadata"]["answer_attempt"]["status"] == status
    assert messages[1]["content"] == ("complete answer" if status == "completed" else "")


def test_preserves_sources_citations_and_legacy_provenance(client):
    sid = ch.store_turn(
        None, _U("sim-user", "demo.user"), "question", "Answer [1] and [2].",
        sources=[
            {"title": "legacy", "filepath": "legacy.md", "score": 0.8},
            {"title": "canonical", "filepath": "canonical.md", "score": 0.7,
             "source_index": 2, "source_id": "root", "selectable": True, "cited": True},
        ],
        retrieval_metadata={"source_blocks": [{"source_index": 2, "text": "saved excerpt"}]},
    )
    login(client, "demo.user")

    turn = recent(client)["turns"][0]
    assert turn["session_id"] == sid
    assistant = turn["messages"][1]
    assert assistant["content"] == "Answer [1] and [2]."
    assert [source["title"] for source in assistant["sources"]] == ["legacy", "canonical"]
    assert assistant["sources"][0]["selectable"] is False
    assert assistant["sources"][0]["source_id"] is None
    assert assistant["sources"][0]["source_index"] is None
    assert assistant["sources"][1]["selectable"] is True
    assert assistant["sources"][1]["cited"] is True
    assert assistant["retrieval_metadata"] == {}
    # Public history omits internal excerpts; selected-source replay retains them.
    from app.db.models import ChatMessage
    from app.db.session import session_scope
    with session_scope() as session:
        stored = session.query(ChatMessage).filter_by(session_id=sid, role="assistant").one()
        assert stored.retrieval_metadata["source_blocks"] == [{"source_index": 2, "text": "saved excerpt"}]


def test_default_page_has_ten_latest_questions(client):
    sid = create_session("sim-user", query="question 0")
    for index in range(1, 12):
        ch.store_turn(sid, _U("sim-user", "demo.user"), f"question {index}", "answer")
    login(client, "demo.user")

    page = recent(client)
    assert [turn["messages"][0]["content"] for turn in page["turns"]] == [
        "question 2", "question 3", "question 4", "question 5", "question 6",
        "question 7", "question 8", "question 9", "question 10", "question 11",
    ]
    assert page["next_before_id"] == page["turns"][0]["id"]


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 21}, {"before_id": 0}, {"before_id": -1},
    {"exclude_session_id": "invalid"},
])
def test_rejects_invalid_pagination_parameters(client, params):
    login(client, "demo.user")
    assert client.get("/api/chat/history/recent", params=params).status_code == 422


def test_recent_history_requires_authentication(client):
    assert client.get("/api/chat/history/recent").status_code == 401


def test_page_queries_do_not_grow_per_turn(client):
    sessions = [create_session("sim-user", query=f"old question {index}") for index in range(20)]
    for index, sid in enumerate(sessions):
        ch.store_turn(sid, _U("sim-user", "demo.user"), f"question {index}", "answer")
    login(client, "demo.user")
    statements = []
    loaded_messages = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "chat_messages" in statement:
            statements.append(statement)

    def record_loaded(session, instance):
        if isinstance(instance, ChatMessage):
            loaded_messages.append(instance.id)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    event.listen(Session, "loaded_as_persistent", record_loaded)
    try:
        page = recent(client, limit=20)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        event.remove(Session, "loaded_as_persistent", record_loaded)

    assert len(page["turns"]) == 20
    assert len(statements) <= 2, "Each page must batch messages instead of querying each turn"
    assert len(loaded_messages) <= 40, "A recent page must not materialize complete older threads"
