"""Offline storage selection/fence; ordinary callers never bypass maintenance."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, select

from app.db.models import ArtifactStorageControl

_migration_access = ContextVar("artifact_migration_access", default=False)
STORE_FIELDS = {"backend": "artifact_backend", "store_id": "artifact_store_id", "root": "artifact_root",
    "endpoint": "artifact_s3_endpoint", "bucket": "artifact_s3_bucket", "prefix": "artifact_s3_prefix",
    "region": "artifact_s3_region", "addressing_style": "artifact_s3_addressing_style", "ca_bundle": "artifact_s3_ca_bundle",
    "allow_http": "artifact_s3_allow_http"}


@contextmanager
def migration_access():
    token = _migration_access.set(True)
    try:
        yield
    finally:
        _migration_access.reset(token)


def descriptor(settings):
    result = {"backend": settings.artifact_backend, "store_id": settings.artifact_store_id}
    if result["backend"] in {"local", "rwx"}:
        root = settings.artifact_root or settings.data_dir / "artifacts"
        result["root"] = os.path.normcase(str(Path(root).absolute()))
    elif result["backend"] == "s3":
        result.update({key: str(value) if isinstance(value, Path) else value
                       for key, attr in STORE_FIELDS.items() if key not in {"backend", "store_id", "root"}
                       and (value := getattr(settings, attr)) is not None})
    else:
        raise ValueError("Unknown artifact store descriptor")
    return result


def _writable_roots(settings):
    return [settings.data_dir, settings.diagnostics_dir, *([settings.artifact_scratch_root] if settings.artifact_scratch_root else [])]


def read_control(session):
    if not inspect(session.connection()).has_table(ArtifactStorageControl.__tablename__):
        return None  # Pre-S4 schema; operator must upgrade before migration.
    return session.execute(select(ArtifactStorageControl.__table__)).mappings().one_or_none()


def enforce_session(session):
    if _migration_access.get():
        return
    from app.services.work.maintenance_control import owned_operator
    if owned_operator(session):
        return  # Exact retained offline operator connection, never an env flag.
    from app.services.work.maintenance_control import enforce_session as enforce_maintenance
    enforce_maintenance(session)
    row = read_control(session)
    if row and row["maintenance_token"]:
        raise ValueError("Artifact storage migration maintenance is active")
    from app.config import get_settings
    from app.deployment.runtime_identity import enforce_session_identity
    enforce_session_identity(session, get_settings())


def enforce_write(session):
    if _migration_access.get() or not (session.new or session.dirty or session.deleted or session.info.get("artifact_storage_write")):
        return
    from app.services.work.maintenance_control import owned_operator
    if owned_operator(session):
        return
    row = read_control(session)
    if row and row["active_store_id"] is not None:
        from app.config import get_settings
        settings = get_settings()
        if row["active_store_id"] != settings.artifact_store_id or row["active_descriptor"] != descriptor(settings):
            raise ValueError("Runtime artifact store differs from committed selection")
        if any(Path(path).absolute().is_relative_to(Path(root)) for path in _writable_roots(settings) for root in row["readonly_roots"]):
            raise ValueError("Runtime cache must not write into retired source roots")


def enforce_runtime(settings):
    if _migration_access.get():
        return
    from app.db.session import get_session_factory, current_session
    from contextlib import nullcontext
    parent = current_session()
    with (nullcontext(parent) if parent is not None else get_session_factory()()) as session:
        enforce_session(session)
        row = read_control(session)
        if row and row["active_store_id"] is not None:
            if row["active_store_id"] != settings.artifact_store_id or row["active_descriptor"] != descriptor(settings):
                raise ValueError("Runtime artifact store differs from committed selection")
            for root in row["readonly_roots"]:
                if any(Path(path).absolute().is_relative_to(Path(root)) for path in _writable_roots(settings)):
                    raise ValueError("Runtime cache must not write into retired source roots")


def initialize_control(engine):
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    with engine.begin() as connection:
        connection.execute(insert(ArtifactStorageControl).values(id=1, installation_id=uuid4().hex,
            readonly_roots=[]).on_conflict_do_nothing())
