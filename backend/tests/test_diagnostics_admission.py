"""Important events retain space when success traffic fills the bounded FIFO."""
import json
from uuid import uuid4

from app.services.diagnostics.recorder import DiagnosticRecorder
from app.services.diagnostics.schema import DiagnosticContext, DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore


def test_4096_fifo_reserves_512_slots_for_important_events():
    from app.services.diagnostics.recorder import AdmissionQueue
    queue = AdmissionQueue(4096, default_reserve=512)
    for i in range(3584):
        assert queue.offer(i, important=False)
    assert not queue.offer("ordinary-overflow", important=False)
    for i in range(512):
        assert queue.offer(f"error-{i}", important=True)
    assert not queue.offer("important-overflow", important=True)
    assert queue.qsize() == 4096
    assert queue.get_nowait() == 0
    assert queue.get_nowait() == 1


def test_small_fifo_and_size_one_keep_an_error_slot():
    from app.services.diagnostics.recorder import AdmissionQueue
    for size, normal_capacity in ((8, 7), (1, 0)):
        queue = AdmissionQueue(size, default_reserve=512)
        for i in range(normal_capacity):
            assert queue.offer(i, important=False)
        assert not queue.offer("extra-success", important=False)
        assert queue.offer("error", important=True)
        assert queue.get_nowait() == (0 if normal_capacity else "error")


def test_error_can_follow_success_saturation_in_real_recorder(tmp_path):
    session_id = str(uuid4())
    limits = DiagnosticLimits(min_free_bytes=0, queue_size=8)
    store = DiagnosticStore(tmp_path / "spool", limits)
    recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
    context = DiagnosticContext(request_id=str(uuid4()), operation_kind="interface")
    for _ in range(7):
        assert recorder.emit("request_finished", context=context,
                             fields={"http_status": 200, "route_template": "/api/settings"})
    assert not recorder.emit("request_finished", context=context,
                             fields={"http_status": 200, "route_template": "/api/settings"})
    assert recorder.emit("operation_failed", context=context,
                         fields={"stage": "search", "error_code": "internal_error"})
    recorder.start()
    assert recorder.flush_pending(timeout_seconds=10)
    recorder.stop()
    events = [json.loads(line) for path in (store.root / "events" / session_id).glob("*.jsonl")
              for line in path.read_text(encoding="utf-8").splitlines()]
    assert any(event["event_code"] == "operation_failed" for event in events)
    assert recorder.status()["dropped"] >= 1
