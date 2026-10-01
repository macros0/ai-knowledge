"""Browser opt-in never grants diagnostic read rights or stores private content."""
import json
from datetime import timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from app.api import diagnostic_client
from app.db.models import AuditLog, DiagnosticSession
from app.db.session import session_scope
from app.services import audit
from app.services.diagnostics.browser import DiagnosticBrowserService
from app.services.diagnostics.recorder import DiagnosticRecorder
from app.services.diagnostics.sessions import DiagnosticControlError
from app.auth.models import User
from tests.test_authz import make_client, login
from tests.test_diagnostics_sessions import service as _session_fixture, actor

service = _session_fixture


@pytest.fixture
def browser(service, monkeypatch, tmp_path):
    recorder = DiagnosticRecorder(service.store, capture_selector=service.active_for,
                                  on_capture_written=service.record_written,
                                  on_capture_failed=service.recording_failed)
    recorder.start()
    service.start("interface", 5, actor())
    browser = DiagnosticBrowserService(service, recorder)
    monkeypatch.setattr(diagnostic_client, "get_browser_service", lambda: browser)
    client = make_client(tmp_path / "app", monkeypatch)
    yield browser, client, recorder
    recorder.stop()


def event(**extra):
    return {"event_id": str(uuid4()), "event_code": "browser_error", "error_code": "browser_error",
            "build_id": "abcdef123", "route_template": "/unknown", "frames": [], **extra}


def join_reader(browser, client):
    code = browser.invite(browser.sessions.status()["session"]["id"], actor())
    login(client, "demo.user")
    response = client.post("/api/diagnostic-client/join", json={"code": code})
    assert response.status_code == 200, response.text
    return response.json()


def test_browser_status_requires_login_and_exposes_only_availability(browser):
    service, client, _ = browser
    assert client.get("/api/diagnostic-client/status").status_code == 401
    login(client, "demo.user")
    response = client.get("/api/diagnostic-client/status")
    assert response.status_code == 200
    assert response.json() == {"available": True}
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/admin/diagnostics/status").status_code == 403
    with session_scope() as db:
        row = db.get(DiagnosticSession, service.sessions.status()["session"]["id"])
        assert not row.participants


@pytest.mark.parametrize("scope,expected", [("system", True), ("interface", True), ("search_chat", False), ("document", False)])
def test_browser_status_tracks_capture_scope_and_manual_stop(browser, scope, expected):
    service, client, _ = browser
    login(client, "demo.user")
    service.sessions.stop(service.sessions.status()["session"]["id"], actor())
    assert client.get("/api/diagnostic-client/status").json() == {"available": False}
    options = {}
    if scope == "document":
        from app.services.registry import DocumentRegistry

        doc_id = "abcdef0123456789"
        DocumentRegistry().create(doc_id, "synthetic.docx", "doc", 10)
        options["doc_id"] = doc_id
    started = service.sessions.start(scope, 5, actor(), **options)
    assert client.get("/api/diagnostic-client/status").json() == {"available": expected}
    service.sessions.stop(started.id, actor())
    assert client.get("/api/diagnostic-client/status").json() == {"available": False}


@pytest.mark.parametrize("reason", ["expired", "disabled", "audit_pending"])
def test_browser_status_hides_capture_that_cannot_accept_browser_events(browser, reason):
    service, client, _ = browser
    login(client, "demo.user")
    if reason == "expired":
        service.sessions.monotonic = lambda: service.sessions._active_view.deadline_mono + 1
    elif reason == "disabled":
        service.sessions.settings.diagnostics_capture_enabled = False
    else:
        service.sessions._audit_gap = True
    assert client.get("/api/diagnostic-client/status").json() == {"available": False}


def test_browser_status_hides_missing_diagnostics(browser, monkeypatch):
    _, client, _ = browser
    login(client, "demo.user")

    def disabled():
        raise DiagnosticControlError("diagnostic_disabled")

    monkeypatch.setattr(diagnostic_client, "get_browser_service", disabled)
    response = client.get("/api/diagnostic-client/status")
    assert response.status_code == 200
    assert response.json() == {"available": False}


def test_reader_can_report_but_cannot_read_diagnostics(browser):
    service, client, recorder = browser
    joined = join_reader(service, client)
    assert joined["participation_id"]
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=joined["participation_id"])).status_code == 204
    assert client.get("/api/jobs").status_code == 403
    recorder.stop()
    events = [json.loads(line) for path in (service.sessions.store.root / "events").rglob("*.jsonl")
              for line in path.read_text().splitlines()]
    reports = [item for item in events if item["component"] == "browser"]
    assert len(reports) == 1
    assert reports[0]["origin"] == "client_reported"
    assert "sim-user" not in repr(reports)
    assert not any(item["component"] == "browser" for path in (service.sessions.store.root / "events/baseline").glob("*.jsonl")
                   for item in map(json.loads, path.read_text().splitlines()))


def test_expired_or_missing_optin_rejected(browser):
    service, client, _ = browser
    assert client.post("/api/diagnostic-client/events", json=event()).status_code == 401
    login(client, "demo.user")
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=str(uuid4()))).status_code == 403
    joined = join_reader(service, client)
    service.sessions.stop(service.sessions.status()["session"]["id"], actor())
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=joined["participation_id"])).status_code == 403


@pytest.mark.parametrize("extra", [
    {"component": "backend"}, {"origin": "server"}, {"timestamp_utc": "2026-09-27T00:00:00Z"},
    {"message": "CANARY_BODY"}, {"frames": [{"module": "C:/private/CANARY.js", "function": "private", "line": 12}]},
])
def test_client_cannot_forge_server_fields(browser, extra):
    service, client, recorder = browser
    joined = join_reader(service, client)
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=joined["participation_id"], **extra)).status_code == 422
    recorder.stop()
    content = b"".join(path.read_bytes() for path in (service.sessions.store.root / "events").rglob("*.jsonl"))
    assert b"CANARY" not in content


def test_client_body_and_rate_limits(browser):
    service, client, _ = browser
    joined = join_reader(service, client)
    assert client.post("/api/diagnostic-client/events", content=b" " * 4097,
                       headers={"content-type": "application/json"}).status_code == 413
    for _ in range(10):
        assert client.post("/api/diagnostic-client/events", json=event(participation_id=joined["participation_id"])).status_code == 204
    limited = client.post("/api/diagnostic-client/events", json=event(participation_id=joined["participation_id"]))
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


def test_invitation_is_one_time_hashed_and_does_not_add_roles(browser):
    service, client, _ = browser
    joined = join_reader(service, client)
    with session_scope() as db:
        row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.active_slot == 1))
        assert len(row.participants) == 1
        assert row.invitations == {}
        assert joined["participation_id"] in repr(row.participants)
        entries = list(db.scalars(select(AuditLog).where(AuditLog.action_type.like("diagnostic_browser_%"))))
        assert [entry.action_type for entry in entries] == ["diagnostic_browser_invited", "diagnostic_browser_joined"]
    assert client.post("/api/diagnostic-client/join", json={}).status_code == 403
    assert client.post("/api/diagnostic-client/leave", json={"participation_id": joined["participation_id"]}).status_code == 204
    assert client.post("/api/diagnostic-client/leave", json={"participation_id": joined["participation_id"]}).status_code == 204
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=joined["participation_id"])).status_code == 403


def test_invitation_and_membership_commit_fail_closed_when_audit_fails(browser, monkeypatch):
    service, client, _ = browser
    code = service.invite(service.sessions.status()["session"]["id"], actor())
    login(client, "demo.user")
    monkeypatch.setattr(audit, "record_in_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("CANARY_SQL")))
    response = client.post("/api/diagnostic-client/join", json={"code": code})
    assert response.status_code == 503
    assert "CANARY" not in response.text
    with session_scope() as db:
        row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.active_slot == 1))
        assert row.participants == {}
        assert code not in repr(row.invitations)


def test_join_attempts_and_participants_are_bounded(browser):
    service, client, _ = browser
    login(client, "demo.user")
    for _ in range(5):
        assert client.post("/api/diagnostic-client/join", json={"code": "X" * 22}).status_code == 403
    assert client.post("/api/diagnostic-client/join", json={"code": "X" * 22}).status_code == 429
    session_id = service.sessions.status()["session"]["id"]
    for _ in range(10):
        service.invite(session_id, actor())
    with pytest.raises(DiagnosticControlError) as failure:
        service.invite(session_id, actor())
    assert failure.value.code == "diagnostic_rate_limited"


def test_invitation_stops_working_at_monotonic_deadline(browser):
    service, client, _ = browser
    code = service.invite(service.sessions.status()["session"]["id"], actor())
    login(client, "demo.user")
    original = service.sessions.monotonic()
    service.sessions.utcnow = lambda: service.sessions._active["expires_at"] - timedelta(days=10)
    service.sessions.monotonic = lambda: original + 301
    assert client.post("/api/diagnostic-client/join", json={"code": code}).status_code == 403


def test_invitation_replay_and_concurrent_consumption_have_one_winner(browser):
    service, _, _ = browser
    code = service.invite(service.sessions.status()["session"]["id"], actor())
    user = User(user_id="reader-1", username="Reader", roles=["viewer"])
    def attempt(_):
        try:
            return service.join(code, user)["participation_id"]
        except DiagnosticControlError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, range(2)))
    assert results.count("diagnostic_browser_not_joined") == 1
    with session_scope() as db:
        row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.active_slot == 1))
        assert len(row.participants) == 1
        assert code not in repr(row.invitations)
        assert code not in repr(list(db.scalars(select(AuditLog))))


def test_admin_own_browser_join_needs_no_invitation(browser):
    service, client, _ = browser
    login(client, "demo.admin")
    joined = client.post("/api/diagnostic-client/join", json={})
    assert joined.status_code == 200
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=joined.json()["participation_id"])).status_code == 204
    with session_scope() as db:
        row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.active_slot == 1))
        assert set(row.participants) == {joined.json()["participation_id"]}


def test_same_user_tabs_have_independent_opt_in(browser):
    service, client, recorder = browser
    login(client, "demo.admin")
    first = client.post("/api/diagnostic-client/join", json={}).json()["participation_id"]
    assert client.post("/api/diagnostic-client/events", json=event()).status_code == 422
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=str(uuid4()))).status_code == 403
    second = client.post("/api/diagnostic-client/join", json={}).json()["participation_id"]
    assert first != second
    with session_scope() as db:
        row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.active_slot == 1))
        assert set(row.participants) == {first, second}
    assert client.post("/api/diagnostic-client/leave", json={"participation_id": first}).status_code == 204
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=first)).status_code == 403
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=second)).status_code == 204
    recorder.stop()
    content = b"".join(path.read_bytes() for path in (service.sessions.store.root / "events").rglob("*.jsonl"))
    assert first.encode() not in content and second.encode() not in content


def test_global_report_limit_survives_participant_turnover(browser):
    service, _, recorder = browser
    participants = [User(user_id=f"admin-{i}", username="Admin", roles=["admin"]) for i in range(11)]
    joined = {}
    for user in participants[:10]:
        joined[user.user_id] = service.join(None, user)["participation_id"]
        for _ in range(10):
            service.ingest(event(participation_id=joined[user.user_id]), user)
    service.leave(joined[participants[0].user_id], participants[0])
    next_id = service.join(None, participants[10])["participation_id"]
    with pytest.raises(DiagnosticControlError) as failure:
        service.ingest(event(participation_id=next_id), participants[10])
    assert failure.value.code == "diagnostic_rate_limited"
    recorder.stop()
    reports = [json.loads(line) for path in (service.sessions.store.root / "events").rglob("*.jsonl")
               for line in path.read_text().splitlines()]
    assert len(reports) == 100


def test_browser_api_rejects_bad_csrf_before_ingestion(browser, monkeypatch):
    service, client, _ = browser
    joined = join_reader(service, client)
    from app import main
    settings = main.get_settings().model_copy(update={"auth_provider": "keycloak_oidc"})
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=joined["participation_id"])).status_code == 403
    token = client.cookies.get("csrf_token")
    assert client.post("/api/diagnostic-client/events", json=event(participation_id=joined["participation_id"]), headers={"X-CSRF-Token": token}).status_code == 204
