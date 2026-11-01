"""Payload-free shared gauges retain uncertain model capacity and queue age."""
from datetime import timedelta
from types import SimpleNamespace
import pytest

from app.db.models import WorkItem
from app.db.session import session_scope
from app.services.work.models import WorkSpec
from app.services.work.store import enqueue, _now


def test_metrics_use_database_time_and_keep_expired_model_reservations():
    from app.services.work.coordination import reserve_capacity
    from app.services.work.metrics import shared_metrics
    with session_scope() as session:
        work_id = enqueue(session, WorkSpec("test", 1, {"secret": "CANARY_DOCUMENT"}, "CANARY_DEDUPE"))
        row = session.get(WorkItem, work_id)
        row.created_at = _now(session) - timedelta(seconds=30)
        row.attempts = 3
        lease = reserve_capacity(session, resource="llm", owner_id="CANARY_OWNER",
            limit=1, ttl_seconds=1, release_required=True)
        from app.db.models import ReplicaCapacitySlot
        session.get(ReplicaCapacitySlot, lease.id).expires_at = _now(session) - timedelta(seconds=1)
    settings = SimpleNamespace(llm_global_max_concurrency=1)
    with session_scope() as session:
        metrics = shared_metrics(session, settings)
    assert metrics["queue_depth"] == 1 and metrics["queue_oldest_seconds"] >= 30
    assert metrics["retry_attempts"] == 2
    assert metrics["model_reserved"] == metrics["model_quarantined"] == 1
    assert metrics["model_saturated"] == 1
    assert "CANARY" not in repr(metrics)


def test_worker_metrics_are_private_and_storage_failure_returns_503(monkeypatch):
    import httpx
    from app.worker import WorkerHealthServer
    from app.services.work.runtime import RuntimeHealth
    health = RuntimeHealth()
    health.pulse()
    def unavailable():
        raise RuntimeError("CANARY_DATABASE_PASSWORD")
    server = WorkerHealthServer(health, host="127.0.0.1", port=0, metrics=unavailable)
    server.start()
    try:
        with httpx.Client(trust_env=False) as client:
            response = client.get(f"http://127.0.0.1:{server.address[1]}/metrics")
        assert response.status_code == 503 and "CANARY" not in response.text
    finally:
        server.stop(timeout=1)


@pytest.mark.parametrize("username,status", [(None, 401), ("demo.user", 403),
    ("demo.editor", 403), ("demo.security", 403), ("demo.admin", 200)])
def test_api_metrics_require_administrator(tmp_path, monkeypatch, username, status):
    from tests.test_authz import make_client, login
    from app.config import get_settings
    from app.services.work.runtime import RuntimeHandle
    client = make_client(tmp_path, monkeypatch)
    if username:
        login(client, username)
    settings = get_settings()
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    client.app.state.runtime = RuntimeHandle("api")
    monkeypatch.setattr("app.services.health.get_readiness", lambda: {"status": "ready"})
    response = client.get("/api/admin/replicas/metrics")
    assert response.status_code == status
    if status == 200:
        assert "okf_replica_role_ready 1" in response.text
        assert response.headers["cache-control"] == "no-store"
