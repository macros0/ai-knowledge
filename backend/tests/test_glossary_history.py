from app.services import chat_history
from app.services.glossary.registry import GlossaryRegistry


class _User:
    user_id = "u1"
    username = "user"


def test_store_turn_snapshots_retrieval_metadata_and_history_reads_it():
    metadata = {
        "schema_version": 1,
        "expansion_status": "applied",
        "applied_terms": [{"canonical": "IT0003", "term_version": 1}],
        "rules_version": "glossary-v1",
        "ui_locale": "en",
    }
    session_id = chat_history.store_turn(
        None,
        _User(),
        "ИТ 0003",
        "answer",
        sources=[],
        retrieval_metadata=metadata,
    )
    metadata["applied_terms"][0]["canonical"] = "CHANGED"

    thread = chat_history.get_thread(session_id, "u1")
    assistant = thread["messages"][1]
    assert assistant["retrieval_metadata"]["applied_terms"][0]["canonical"] == "IT0003"


def test_old_history_without_retrieval_metadata_returns_none():
    session_id = chat_history.store_turn(None, _User(), "query", "answer", sources=[])
    thread = chat_history.get_thread(session_id, "u1")
    assert thread["messages"][0]["retrieval_metadata"] is None
    assert thread["messages"][1]["retrieval_metadata"] is None


def test_history_snapshot_survives_term_edit_and_disable():
    registry = GlossaryRegistry()
    term = registry.create("IT0003", "sap_infotype", "Original")
    metadata = {
        "schema_version": 1,
        "expansion_status": "applied",
        "applied_terms": [{"term_id": term["id"], "canonical": "IT0003", "term_version": 1}],
        "rules_version": "glossary-v1",
        "ui_locale": "en",
    }
    session_id = chat_history.store_turn(
        None, _User(), "IT0003", "answer", retrieval_metadata=metadata
    )

    updated = registry.update(term["id"], term["version"], enabled=False)
    assert updated["enabled"] is False
    metadata["applied_terms"][0]["canonical"] = "CHANGED"

    assistant = chat_history.get_thread(session_id, "u1")["messages"][1]
    assert assistant["retrieval_metadata"]["applied_terms"] == [
        {"term_id": term["id"], "canonical": "IT0003", "term_version": 1}
    ]
