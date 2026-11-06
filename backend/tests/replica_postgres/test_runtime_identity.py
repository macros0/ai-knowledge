"""Installed identity and SQL fencing on an explicitly owned PostgreSQL schema."""
import pytest
from tests.support.replica_postgres import disposable_postgres
from tests.test_replica_runtime_identity_db import installed as installed
from tests import test_replica_runtime_identity_db as cases


@pytest.fixture(autouse=True)
def postgres():
    with disposable_postgres():
        yield


def test_postgres_identity_startup(installed):
    cases.test_startup_requires_matching_installed_identity(installed)


def test_postgres_identity_change_fences_running_replica(installed):
    cases.test_changed_installed_fingerprint_fences_existing_sessions(installed)


def test_postgres_unmigrated_schema_rejected(installed):
    cases.test_startup_rejects_wrong_database_revision(installed)
