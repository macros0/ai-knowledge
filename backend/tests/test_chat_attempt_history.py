from types import SimpleNamespace
from uuid import uuid4

from app.services import chat_history


def test_stopped_attempt_keeps_sources_and_cannot_finish():
    user = SimpleNamespace(user_id="sim-user", username="demo.user")
    attempt_id = str(uuid4())
    ref = chat_history.begin_attempt(None, user, "вопрос", attempt_id, "full")
    chat_history.save_attempt_sources(ref, user, [{"doc_id": "doc-a", "title": "A"}])
    assert chat_history.finish_attempt(ref, user, status="stopped", answer="")
    assert not chat_history.finish_attempt(ref, user, status="completed", answer="Поздний ответ")
    thread = chat_history.get_thread(ref.session_id, user.user_id)
    assert thread["messages"][-1]["sources"][0]["doc_id"] == "doc-a"
    assert thread["messages"][-1]["retrieval_metadata"]["answer_attempt"]["status"] == "stopped"


def test_retry_same_attempt_does_not_create_more_messages():
    user = SimpleNamespace(user_id="sim-user", username="demo.user")
    attempt_id = str(uuid4())
    first = chat_history.begin_attempt(None, user, "вопрос", attempt_id, "documents")
    second = chat_history.begin_attempt(first.session_id, user, "вопрос", attempt_id, "documents")
    assert first.message_id == second.message_id
    assert len(chat_history.get_thread(first.session_id, user.user_id)["messages"]) == 2
