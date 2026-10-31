"""Opt-in actual PostgreSQL probes, each in a fresh schema of a disposable DB."""
import os
from pathlib import Path
import re
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema


@pytest.fixture(autouse=True)
def _db():
    path = os.environ.get("OKF_ARTIFACT_PG_URL_FILE")
    if not path:
        pytest.skip("Explicit disposable artifact PostgreSQL URL file required")
    url = make_url(Path(path).read_text(encoding="utf-8-sig").strip())
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
            or not re.fullmatch(r"replica_test_[a-z0-9]{8,32}", url.database or "") or url.query):
        pytest.fail("Only an isolated loopback replica-test PostgreSQL database is allowed")
    schema = "artifact_" + uuid4().hex
    admin = create_engine(url, connect_args={"connect_timeout": 5})
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        from app.db.session import configure_for_tests, get_engine, init_db
        from app.services.pipeline import reset_pipeline
        scoped = url.update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000"})
        configure_for_tests(scoped.render_as_string(hide_password=False))
        init_db()
        reset_pipeline()
        try:
            yield
        finally:
            reset_pipeline()
            get_engine().dispose()
    finally:
        if created:
            # Only the exact freshly generated schema is ever removed.
            assert re.fullmatch(r"artifact_[0-9a-f]{32}", schema)
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
