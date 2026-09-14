"""A proposed infotype rule can be tried without storing glossary metadata."""
import pytest
from sqlalchemy import event

from app.db.models import GlossaryState
from app.db.session import get_engine, session_scope
from tests.test_glossary_api import make_client, login


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def _request(query, **changes):
    return {"name": "Custom", "number_from": 0, "number_to": 999,
            "prefixes": ["my-тип", "my-тип ", "Y+", "Y+ "], "enabled": True, "query": query, **changes}


def test_draft_rule_preview_groups_exact_spans_and_generates_only_user_forms(client):
    login(client, "editor")
    query = "😀 MY-ТИП\u00a0 0003; Y+0003 и my-тип 0999"
    response = client.post("/api/admin/glossary/rules/preview", json=_request(query))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["query"] == query and result["enabled"] is True
    assert [match["number"] for match in result["matches"]] == ["0003", "0999"]
    match = result["matches"][0]
    assert match["code"] == "IT0003"
    assert set(match["generated_forms"]) == {"my-тип0003", "my-тип 0003", "y+0003", "y+ 0003"}
    assert [span["text"] for span in match["spans"]] == ["MY-ТИП\u00a0 0003", "Y+0003"]
    for matched in result["matches"]:
        for span in matched["spans"]:
            assert query[span["start"]:span["end"]] == span["text"]


@pytest.mark.parametrize("query,expected", [
    ("my-тип0000", ["0000"]), ("my-тип0999", ["0999"]), ("my-тип1000", []),
    ("my-тип003", []), ("my-тип00037", []), ("Xmy-тип0003", []),
    ("my-тип0003Z", []), ("my-тип１２３４", []), ("IT0003", []), ("Y0003", []),
])
def test_rule_preview_obeys_exact_four_ascii_digits_and_identifier_boundaries(client, query, expected):
    login(client, "editor")
    response = client.post("/api/admin/glossary/rules/preview", json=_request(query))
    assert response.status_code == 200, response.text
    assert [match["number"] for match in response.json()["matches"]] == expected


def test_disabled_rule_preview_has_no_matches(client):
    login(client, "admin")
    response = client.post("/api/admin/glossary/rules/preview", json=_request("my-тип0003", enabled=False))
    assert response.status_code == 200, response.text
    assert response.json() == {"query": "my-тип0003", "enabled": False, "matches": []}


@pytest.mark.parametrize("changes", [
    {"prefixes": ["custom", " CUSTOM "]}, {"number_from": 999, "number_to": 0},
    {"prefixes": ["123"]}, {"prefixes": [" "]},
])
def test_rule_preview_rejects_invalid_drafts_even_when_disabled(client, changes):
    login(client, "admin")
    response = client.post("/api/admin/glossary/rules/preview", json=_request("my-тип0003", enabled=False, **changes))
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "glossary_invalid_rule"


def test_rule_preview_is_read_only_and_does_not_require_ready_namespace(client):
    login(client, "editor")
    with session_scope() as session:
        state = session.get(GlossaryState, 1)
        state.identities_ready = False
        session.flush()
        revision, timestamp = state.revision, state.updated_at
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.strip().split()[0].upper())

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.post("/api/admin/glossary/rules/preview", json=_request("my-тип0003"))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert not ({"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"} & set(statements))
    with session_scope() as session:
        state = session.get(GlossaryState, 1)
        assert state.revision == revision and state.identities_ready is False
        assert state.updated_at.replace(tzinfo=None) == timestamp.replace(tzinfo=None)


def test_rule_preview_requires_editor_or_admin(client):
    login(client, "viewer")
    assert client.post("/api/admin/glossary/rules/preview", json=_request("my-тип0003")).status_code == 403
