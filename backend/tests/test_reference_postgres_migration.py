"""Additive migration acceptance against disposable PostgreSQL, never default DB."""
import importlib.util
from pathlib import Path
import time

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, select, text

from app.db.models import ReferenceResourceState
from app.db.session import get_engine, session_scope
from tests.test_reference_postgres_concurrency import reference_pg_db as pg_fixture

reference_pg_db = pg_fixture


def migration(connection):
    path = Path(__file__).parents[1] / "alembic/versions/065b1c2d3e4f_reference_editing.py"
    loader = importlib.util.spec_from_file_location("reference_pg_migration", path)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def domain_snapshot(connection):
    from app.db.base import Base
    return {table.name: sorted(map(repr, connection.execute(select(table)).all()))
            for table in Base.metadata.sorted_tables if table.name != "reference_resource_states"}


@pytest.mark.parametrize("populated", [False, True])
def test_pg_additive_migration_preserves_all_domain_rows_and_safe_rollback(reference_pg_db, populated):
    if populated:
        from app.services.tag_registry import TagRegistry
        from app.services.development_registry import DevelopmentRegistry
        from app.services.attribute_registry import AttributeRegistry
        from app.services.glossary.registry import GlossaryRegistry
        from app.services.locale_service import create_locale, add_stopword
        from app.services.ui_dictionary import seed_english_copy
        TagRegistry().add(["migration-tag"])
        AttributeRegistry().add("module", "PG")
        DevelopmentRegistry().create("PG-001", "Migration development", module="PG")
        GlossaryRegistry().create(None, "business_term", "Migration term", "Description", "ru", "admin")
        create_locale("zz", "Migration locale")
        add_stopword("zz", "example", "bm25", user=None)
        seed_english_copy("zz")
    engine = get_engine()
    with engine.begin() as connection:
        before = domain_snapshot(connection)
        connection.execute(text("DROP TABLE reference_resource_states"))
        connection.execute(text("DROP INDEX uq_attribute_values_global"))
        started = time.perf_counter()
        migration(connection).upgrade()
        assert domain_snapshot(connection) == before
        rows = connection.execute(select(ReferenceResourceState.__table__)).mappings().all()
        assert all(row["revision"] == 1 and row["lease_epoch"] == 0 for row in rows)
        if populated:
            kinds = {__import__('json').loads(row["resource_key"])[0] for row in rows}
            assert {"tag", "development", "glossary_term", "locale", "stopword_set", "ui_dictionary", "attribute_catalog"} <= kinds
        print(f"migration populated={populated} rows={len(rows)} elapsed={time.perf_counter()-started:.4f}s")
        migration(connection).downgrade()
        assert "reference_resource_states" not in inspect(connection).get_table_names()
        assert domain_snapshot(connection) == before
        migration(connection).upgrade()
    with session_scope() as session:
        row = session.scalar(select(ReferenceResourceState))
        if row is None:
            row = ReferenceResourceState(resource_key='["tag","99",null,null]', revision=2)
            session.add(row)
        else:
            row.revision = 2
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="in use"):
            migration(connection).downgrade()
        assert "reference_resource_states" in inspect(connection).get_table_names()


def test_pg_duplicate_report_precedes_any_schema_change(reference_pg_db):
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE reference_resource_states"))
        connection.execute(text("DROP INDEX uq_attribute_values_global"))
        connection.execute(text("INSERT INTO attribute_values (attribute_key, value, canonical_locale, sort_order, created_at) "
                                "VALUES ('module', 'duplicate', 'ru', 0, CURRENT_TIMESTAMP), "
                                "('module', 'duplicate', 'ru', 0, CURRENT_TIMESTAMP)"))
        before = domain_snapshot(connection)
        with pytest.raises(RuntimeError, match="Duplicate global attributes"):
            migration(connection).upgrade()
        assert domain_snapshot(connection) == before
        assert "reference_resource_states" not in inspect(connection).get_table_names()
        assert "uq_attribute_values_global" not in {index["name"] for index in inspect(connection).get_indexes("attribute_values")}
