"""Run the real entire migration chain in a verified disposable empty schema."""
from pathlib import Path
import re

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.session import get_engine
from tests.support.replica_postgres import disposable_postgres
from tests.test_replica_bootstrap import bootstrap_settings as bootstrap_settings


def test_fresh_postgres_migrates_without_runtime_identity_file(bootstrap_settings):
    with disposable_postgres():
        engine = get_engine()
        # This schema was just created by this fixture; no working/public data.
        with engine.begin() as connection:
            schema = connection.scalar(text("SELECT current_schema()"))
            assert re.fullmatch(r"artifact_[0-9a-f]{32}", schema)
            connection.execute(DropSchema(schema, cascade=True))
            connection.execute(CreateSchema(schema))
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "rpl04_runtime_identity"
            assert "replica_runtime_identity" in inspect(connection).get_table_names()
        from app.deployment.bootstrap import install_identity, probe_artifacts
        from app.services.artifacts.control import migration_access
        from app.db.session import session_scope
        receipt = probe_artifacts(bootstrap_settings, installation_id="1" * 32, selection_digest="sha256:" + "3" * 64)
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from sqlalchemy.exc import OperationalError
        barrier = Barrier(2)
        def install():
            barrier.wait(timeout=10)
            try:
                with migration_access(), session_scope() as session:
                    return install_identity(session, bootstrap_settings, receipt)
            except OperationalError as exc:
                # The disposable fixture bounds row-lock waits to two seconds.
                # A losing operator must retry a fresh transaction after the
                # first commit; it must not bypass the lock or adopt a new ID.
                if getattr(exc.orig, "sqlstate", None) != "55P03":
                    raise
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.submit(install), executor.submit(install)
            results = [first.result(timeout=20), second.result(timeout=20)]
        accepted = [document for document in results if document is not None]
        assert accepted and accepted[0]["installation_id"] == "1" * 32
        for document in results:
            if document is None:
                with migration_access(), session_scope() as session:
                    document = install_identity(session, bootstrap_settings, receipt)
            assert document == accepted[0]
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM replica_runtime_identity")) == 1
