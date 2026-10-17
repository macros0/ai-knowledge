"""Atomic JSON writes keep memory bounded and concurrent outputs complete."""
import json
import threading
import tracemalloc
from concurrent.futures import ThreadPoolExecutor

from app.services.json_atomic import write_json_atomic


def test_large_record_sequence_is_written_without_a_whole_document_buffer(tmp_path):
    payload = {'rows': ['Ж🌍' * 32768 + str(i) for i in range(64)], 'zero': 0, 'false': False}
    path = tmp_path / 'large.json'
    tracemalloc.start()
    try:
        write_json_atomic(path, payload)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert json.loads(path.read_text(encoding='utf8')) == payload
    assert path.stat().st_size > 12_000_000
    assert peak < 4 * 1024 * 1024, f'Whole JSON was retained while writing: {peak}'


def test_concurrent_writers_commit_one_complete_document_and_remove_owned_temps(tmp_path, monkeypatch):
    import app.services.json_atomic as atomic
    target = tmp_path / 'shared.json'
    barrier = threading.Barrier(2)
    replace_lock = threading.Lock()
    replace = atomic.os.replace

    def synchronized_replace(source, destination):
        barrier.wait(timeout=5)
        with replace_lock:
            return replace(source, destination)

    monkeypatch.setattr(atomic.os, 'replace', synchronized_replace)
    inputs = [{'writer': i, 'body': str(i) * 10000} for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write_json_atomic, target, data) for data in inputs]
        errors = [future.exception() for future in futures]
    assert errors == [None, None]
    assert json.loads(target.read_text()) in inputs
    assert not list(tmp_path.glob('.*.tmp-*'))


def test_streamed_serialization_keeps_existing_bytes_and_failed_write_preserves_target(tmp_path):
    import pytest
    payload = {'value': 'Ж🌍\n', 'zero': 0, 'false': False, 'rows': [[1, None], [2, 'a']]}
    target = tmp_path / 'result.json'
    reference = tmp_path / 'reference.json'
    for indent in [None, 0, 2]:
        reference.write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding='utf8')
        write_json_atomic(target, payload, indent=indent)
        assert target.read_bytes() == reference.read_bytes()
    before = target.read_bytes()
    with pytest.raises(TypeError):
        write_json_atomic(target, {'prefix': 'partial', 'bad': object()})
    assert target.read_bytes() == before
    assert not list(tmp_path.glob('.*.tmp-*'))
