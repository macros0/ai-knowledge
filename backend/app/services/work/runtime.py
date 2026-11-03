"""Role-aware runtime foundation; importing it starts no threads or executors."""
import threading
import time
import logging
import os
from concurrent.futures import wait
from uuid import uuid4

from sqlalchemy import select, literal

from app.db.session import get_engine, session_scope

from .handlers import HandlerRegistry
from .worker import Worker

logger = logging.getLogger(__name__)


class RuntimeHealth:
    def __init__(self, *, clock=None):
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._last_pulse = None
        self._storage_ready = False
        self._last_probe = None
        self._closed = False

    def pulse(self):
        with self._lock:
            self._last_pulse = self._clock()

    def set_storage_ready(self, ready):
        with self._lock:
            self._storage_ready = bool(ready)
            self._last_probe = self._clock()

    def _live(self):
        return not self._closed and self._last_pulse is not None and 0 <= self._clock() - self._last_pulse <= 90

    def live(self):
        with self._lock:
            return self._live()

    def ready(self):
        with self._lock:
            return (self._live() and self._storage_ready and self._last_probe is not None
                    and 0 <= self._clock() - self._last_probe <= 30)

    def close(self):
        with self._lock:
            self._closed = True
            self._storage_ready = False


class RuntimeHandle:
    def __init__(self, role, *, stop_event=None):
        self.role = role
        self.health = RuntimeHealth()
        self._stop = stop_event or threading.Event()
        self._threads = []
        self._workers = []

    def stop(self, timeout: float) -> bool:
        self._stop.set()
        self.health.close()
        deadline = time.monotonic() + max(0, timeout)
        for thread in self._threads:
            thread.join(max(0, deadline - time.monotonic()))
        futures = [worker._future for worker in self._workers if worker._future is not None]
        if futures:
            wait(futures, timeout=max(0, deadline - time.monotonic()))
        return (all(not thread.is_alive() for thread in self._threads)
                and all(worker._future is None or worker._future.done() for worker in self._workers))

    def _supervise(self):
        while not self._stop.is_set():
            self.health.pulse()
            self._stop.wait(0.25)

    def _probe(self, settings):
        while not self._stop.is_set():
            try:
                self.health.set_storage_ready(probe_storage(settings))
            except Exception as exc:
                self.health.set_storage_ready(False)
                logger.warning("Worker storage probe unavailable (%s)", type(exc).__name__)
            self._stop.wait(min(5, settings.work_poll_seconds))

    def _schedule(self, settings, registry):
        from .scheduler import enqueue_due, periodic_specs
        while not self._stop.is_set():
            if self.health.ready():
                try:
                    for spec in periodic_specs(settings):
                        if self._stop.is_set():
                            return
                        if 1 in registry.supported.get(spec.kind, set()):
                            with session_scope() as session:
                                enqueue_due(session, spec)
                except Exception as exc:
                    logger.warning("Worker scheduling deferred (%s)", type(exc).__name__)
            self._stop.wait(1)

    def _clean_local_scratch(self, settings):
        from .scratch import clean_export_attempts
        cursor = None
        while not self._stop.is_set():
            try:
                cursor = clean_export_attempts(settings, after=cursor)
            except Exception as exc:
                logger.warning("Local export scratch cleanup deferred (%s)", type(exc).__name__)
            self._stop.wait(60)


def build_handlers():
    from .document_handler import run_document_job
    from .maintenance import (retire_checkpoint, refresh_duplicates, purge_auth, purge_chat,
        sync_document_visibility, collect_artifacts, cleanup_generations, sweep_generations, purge_exports)
    from .job_handlers import export_job
    from .bulk import run_bulk, run_bulk_item, reconcile_bulk
    from .trash import purge_document, cleanup_document_bytes, sweep_trash
    from .scope import run_scope_job
    from .job_handlers import diagnostic_bundle
    from .index_backfill import backfill_index, sweep_indexes
    from .translations import run_reference_translation, reconcile_translations
    from .glossary_translation import run_glossary_translation
    from .diagnostics import maintain_diagnostics
    from .repair import run_canonical_repair, reconcile_repairs
    from .chat import reconcile_chat_runs
    registry = HandlerRegistry()
    registry.register("document", 1, run_document_job)
    registry.register("checkpoint_retire", 1, retire_checkpoint)
    registry.register("duplicate_refresh", 1, refresh_duplicates)
    registry.register("export", 1, export_job)
    registry.register("auth_purge", 1, purge_auth)
    registry.register("bulk", 1, run_bulk)
    registry.register("bulk_item", 1, run_bulk_item)
    registry.register("bulk_reconcile", 1, reconcile_bulk)
    registry.register("document_visibility", 1, sync_document_visibility)
    registry.register("chat_purge", 1, purge_chat)
    registry.register("artifact_gc", 1, collect_artifacts)
    registry.register("generation_cleanup", 1, cleanup_generations)
    registry.register("generation_sweep", 1, sweep_generations)
    registry.register("export_purge", 1, purge_exports)
    registry.register("trash_purge", 1, purge_document)
    registry.register("trash_cleanup", 1, cleanup_document_bytes)
    registry.register("trash_sweep", 1, sweep_trash)
    registry.register("scope_change", 1, run_scope_job)
    registry.register("diagnostic_bundle", 1, diagnostic_bundle)
    registry.register("diagnostic_bundle", 2, diagnostic_bundle)
    registry.register("index_backfill", 1, backfill_index)
    registry.register("index_backfill_sweep", 1, sweep_indexes)
    registry.register("reference_translation", 1, run_reference_translation)
    registry.register("glossary_translation", 1, run_glossary_translation)
    registry.register("translation_reconcile", 1, reconcile_translations)
    registry.register("diagnostic_maintenance", 1, maintain_diagnostics)
    registry.register("canonical_repair", 1, run_canonical_repair)
    registry.register("repair_reconcile", 1, reconcile_repairs)
    registry.register("chat_run_reconcile", 1, reconcile_chat_runs)
    return registry


def probe_storage(settings):
    """Required storage only; no model call participates in process liveness."""
    from app.services.artifacts.control import enforce_runtime
    from app.services.vector_store import shared_qdrant_client
    if get_engine().dialect.name != "postgresql" or settings.artifact_backend not in {"rwx", "s3"}:
        return False
    enforce_runtime(settings)
    with session_scope() as session:
        session.scalar(select(literal(1)))
    shared_qdrant_client(settings).get_collections()
    return probe_artifacts(settings)


def probe_artifacts(settings):
    from app.services.artifacts import get_blob_store
    store = get_blob_store(settings)
    if settings.artifact_backend == "s3":
        # Prefix-scoped ListBucket grants intentionally do not authorize HEAD
        # bucket, whose request has no prefix. Keep readiness within our scope.
        store.client.list_objects_v2(Bucket=store.bucket, Prefix=store.prefix, MaxKeys=1)
    elif not store.root.is_dir() or not os.access(store.root, os.R_OK | os.W_OK | os.X_OK):
        return False
    return True


def start_runtime(settings, *, stop_event=None) -> RuntimeHandle:
    if settings.app_role in {"api", "combined"}:
        return RuntimeHandle(settings.app_role, stop_event=stop_event)
    if settings.app_role != "worker" or settings.okf_deployment_schema != "replicas-v1":
        raise ValueError("Explicit replicas-v1 worker role required")
    handle = RuntimeHandle("worker", stop_event=stop_event)
    if handle._stop.is_set():
        handle.health.close()
        return handle
    registry = build_handlers()
    handle._threads = [
        threading.Thread(target=handle._supervise, name="work-main-loop", daemon=True),
        threading.Thread(target=handle._probe, args=(settings,), name="work-storage-probe", daemon=True),
        threading.Thread(target=handle._schedule, args=(settings, registry), name="work-scheduler", daemon=True),
        threading.Thread(target=handle._clean_local_scratch, args=(settings,), name="work-local-scratch", daemon=True),
    ]
    identity = uuid4().hex
    for index in range(settings.pipeline_max_workers):
        worker = Worker(settings, registry=registry, owner_id=f"{identity}/{index}")
        handle._workers.append(worker)
        handle._threads.append(threading.Thread(target=worker.run, args=(handle._stop,), kwargs={"ready": handle.health.ready},
            name=f"work-consumer-{index}", daemon=True))
    for thread in handle._threads:
        thread.start()
    return handle
