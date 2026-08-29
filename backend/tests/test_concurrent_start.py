"""Тесты защиты от двух потоков на одном документе и общих локов staging.

resume не проверял живой поток: два быстрых клика «Возобновить» давали два
потока, _start перезатирал _threads/_abort_events, и первый поток становился
неуправляемым. Лок StagingStore был полем экземпляра, а экземпляров на
документ много — взаимного исключения не было вовсе.
"""
import threading
import time
from pathlib import Path

import pytest

from app.services.pipeline import AlreadyProcessingError, Pipeline
from app.services.staging import StagingStore
from tests.test_pipeline_integration import isolated_env  # noqa: F401


class _Blocker:
    """Занимает поток пайплайна, пока тест не разрешит ему завершиться."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.runs = 0

    def __call__(self, *args, **kwargs):
        self.runs += 1
        self.entered.set()
        self.release.wait(timeout=5)


class TestSecondStartRejected:
    def _prepare(self, reg, pipeline, doc_id, status):
        pipeline.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        (pipeline.settings.uploads_dir / f"{doc_id}.doc").write_text("исходник", encoding="utf-8")
        reg.create(doc_id, "test.doc", "doc", 100)
        reg.update(doc_id, status=status)

    def test_second_resume_rejected_while_first_alive(self, isolated_env):
        reg, _ = isolated_env
        pipeline = Pipeline()
        doc_id = "a1b2c3d4e5f60718"
        self._prepare(reg, pipeline, doc_id, "paused")

        blocker = _Blocker()
        pipeline._process = blocker
        pipeline.resume(doc_id)
        assert blocker.entered.wait(timeout=5)

        first_thread = pipeline._threads[doc_id]
        first_abort = pipeline._abort_events[doc_id]

        with pytest.raises(AlreadyProcessingError):
            pipeline.resume(doc_id)

        # первый поток остался управляемым: та же запись в обеих таблицах
        assert pipeline._threads[doc_id] is first_thread
        assert pipeline._abort_events[doc_id] is first_abort
        assert blocker.runs == 1

        blocker.release.set()
        first_thread.join(timeout=5)

    def test_resume_allowed_after_first_finished(self, isolated_env):
        reg, _ = isolated_env
        pipeline = Pipeline()
        doc_id = "a1b2c3d4e5f60718"
        self._prepare(reg, pipeline, doc_id, "paused")

        calls = {"n": 0}
        pipeline._process = lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)

        def wait_for(n: int) -> None:
            for _ in range(500):
                if calls["n"] >= n:
                    return
                time.sleep(0.01)
            raise AssertionError(f"дождались только {calls['n']} из {n}")

        pipeline.resume(doc_id)
        wait_for(1)
        # _run в finally убирает поток из _threads — повтор должен пройти
        pipeline.resume(doc_id)
        wait_for(2)

    def test_regenerate_still_rejects_second_call(self, isolated_env):
        reg, _ = isolated_env
        pipeline = Pipeline()
        doc_id = "a1b2c3d4e5f60718"
        self._prepare(reg, pipeline, doc_id, "done")

        blocker = _Blocker()
        pipeline._process = blocker
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.regenerate(doc_id)
        assert blocker.entered.wait(timeout=5)

        with pytest.raises(AlreadyProcessingError):
            pipeline.regenerate(doc_id)

        blocker.release.set()
        pipeline._threads[doc_id].join(timeout=5)

    def test_regenerate_checks_before_destroying_state(self, isolated_env):
        """Ранняя проверка в regenerate обязана срабатывать ДО сноса бандла."""
        reg, _ = isolated_env
        pipeline = Pipeline()
        doc_id = "a1b2c3d4e5f60718"
        self._prepare(reg, pipeline, doc_id, "done")
        bundle = pipeline.settings.okf_dir / doc_id
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "concept.md").write_text("концепт", encoding="utf-8")

        blocker = _Blocker()
        pipeline._process = blocker
        deleted = {"n": 0}
        pipeline.vector_store.delete_document = lambda *a, **k: deleted.__setitem__("n", deleted["n"] + 1)
        pipeline.regenerate(doc_id)
        assert blocker.entered.wait(timeout=5)

        # первый regenerate законно снёс бандл — воссоздаём, чтобы проверять
        # именно отказ второго вызова
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "concept.md").write_text("концепт", encoding="utf-8")
        deleted["n"] = 0

        with pytest.raises(AlreadyProcessingError):
            pipeline.regenerate(doc_id)

        assert deleted["n"] == 0, "векторы удалены до отказа"
        assert (bundle / "concept.md").is_file(), "бандл снесён до отказа"

        blocker.release.set()
        pipeline._threads[doc_id].join(timeout=5)

    def test_concurrent_starts_produce_one_thread(self, isolated_env):
        """Проверка и регистрация под одним локом: без него оба вызова
        успевают пройти проверку до записи в _threads."""
        reg, _ = isolated_env
        pipeline = Pipeline()
        doc_id = "a1b2c3d4e5f60718"
        self._prepare(reg, pipeline, doc_id, "paused")

        blocker = _Blocker()
        pipeline._process = blocker
        gate = threading.Barrier(8)
        results: list[str] = []
        results_lock = threading.Lock()

        def racer():
            gate.wait(timeout=5)
            try:
                pipeline.resume(doc_id)
                outcome = "started"
            except AlreadyProcessingError:
                outcome = "rejected"
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=racer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        blocker.release.set()
        pipeline._threads[doc_id].join(timeout=5)

        assert results.count("started") == 1, f"запущено несколько потоков: {results}"
        assert blocker.runs == 1


class TestStagingLockShared:
    def test_lock_shared_between_instances(self, tmp_path: Path):
        a = StagingStore("a1b2c3d4e5f60718", staging_root=tmp_path / "st")
        b = StagingStore("a1b2c3d4e5f60718", staging_root=tmp_path / "st")
        assert a._lock is b._lock, "у каждого экземпляра свой лок — исключения нет"

    def test_different_documents_get_different_locks(self, tmp_path: Path):
        a = StagingStore("a1b2c3d4e5f60718", staging_root=tmp_path / "a")
        b = StagingStore("b1b2c3d4e5f60719", staging_root=tmp_path / "b")
        assert a._lock is not b._lock

    def test_concurrent_append_chunk_keeps_every_chunk(self, tmp_path: Path):
        """read-modify-write манифеста из разных экземпляров не теряет чанки."""
        from app.models.schemas import Concept

        root = tmp_path / "st"
        StagingStore("a1b2c3d4e5f60718", staging_root=root).create(total_chunks=12)

        gate = threading.Barrier(12)

        def writer(i: int):
            store = StagingStore("a1b2c3d4e5f60718", staging_root=root)  # свой экземпляр
            gate.wait(timeout=5)
            store.append_chunk(i, [Concept(id=f"c{i}", title=f"Концепт {i}", type="concept", content="тело")])

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        manifest = StagingStore("a1b2c3d4e5f60718", staging_root=root).load()
        assert sorted(manifest["processed_chunks"]) == list(range(12))
        assert sorted(int(k) for k in manifest["chunks_data"]) == list(range(12))
        assert len(manifest["used_slugs"]) == 12


class TestChunkLocksCleanup:
    def test_chunk_lock_removed_with_document(self, isolated_env):
        """_threads и _abort_events чистит _run; у _chunk_locks своего места
        не было — словарь рос до перезапуска процесса."""
        reg, _ = isolated_env
        pipeline = Pipeline()
        doc_id = "a1b2c3d4e5f60718"
        pipeline.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        (pipeline.settings.uploads_dir / f"{doc_id}.doc").write_text("исходник", encoding="utf-8")
        reg.create(doc_id, "test.doc", "doc", 100)
        pipeline.vector_store.delete_document = lambda *a, **k: None

        pipeline._chunk_locks.setdefault(doc_id, threading.Lock())
        assert doc_id in pipeline._chunk_locks

        pipeline.remove(doc_id)
        assert doc_id not in pipeline._chunk_locks

    def test_remove_is_safe_without_lock(self, isolated_env):
        reg, _ = isolated_env
        pipeline = Pipeline()
        doc_id = "b1b2c3d4e5f60719"
        reg.create(doc_id, "test.doc", "doc", 100)
        pipeline.vector_store.delete_document = lambda *a, **k: None
        pipeline.remove(doc_id)  # лока не было — не должно падать
