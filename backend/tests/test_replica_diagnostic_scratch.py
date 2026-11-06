"""Only inactive owned instance scratch may be collected; children hold OS locks."""
from multiprocessing import get_context
import json
import time

from tests.test_replica_diagnostic_store import stores as _stores_fixture

stores = _stores_fixture


def _orphan_child(root, pipe):
    from app.services.diagnostics.scratch import child_lease
    from pathlib import Path
    with child_lease(Path(root), timeout_seconds=1):
        pipe.send("locked")
        time.sleep(20)


def test_cleaner_preserves_active_writer_and_retires_closed_instance(stores):
    from app.services.diagnostics.scratch import clean_instances
    first, second, settings = stores
    clean_instances(settings)
    assert first.root.is_dir() and second.root.is_dir()
    first.close()
    clean_instances(settings)
    assert not first.root.exists() and second.root.is_dir()


def test_cleaner_preserves_foreign_and_unknown_markers(stores):
    from app.services.diagnostics.scratch import clean_instances
    first, second, settings = stores
    first.close()
    marker = first.root / ".instance-v1.json"
    value = json.loads(marker.read_bytes())
    value["host"] = "different-host"
    marker.write_text(json.dumps(value))
    unknown = first.root.parent / "unrecognized"
    unknown.mkdir()
    (unknown / "keep").write_text("keep")
    clean_instances(settings)
    assert first.root.is_dir() and unknown.is_dir() and second.root.is_dir()


def test_orphan_child_prevents_cleanup_and_owns_a_bounded_lifetime(stores):
    from app.services.diagnostics.scratch import clean_instances
    first, second, settings = stores
    context = get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_orphan_child, args=(str(first.root), child))
    process.start()
    child.close()
    try:
        assert parent.poll(25) and parent.recv() == "locked"
        first.close()
        clean_instances(settings)
        assert first.root.is_dir()
        process.join(5)
        assert process.exitcode == 2
        clean_instances(settings)
        assert not first.root.exists() and second.root.is_dir()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()


def test_live_bundle_parent_keeps_scratch_after_recorder_stops(stores):
    from app.services.diagnostics.scratch import clean_instances, parent_operation
    first, second, settings = stores
    with parent_operation(first.root) as owned:
        assert owned
        first.close()
        clean_instances(settings)
        assert first.root.is_dir()
    clean_instances(settings)
    assert not first.root.exists() and second.root.is_dir()
