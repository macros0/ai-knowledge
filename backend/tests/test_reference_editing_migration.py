import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select, text
import pytest

from app.db.models import ReferenceResourceState
from app.db.session import get_engine
from app.services.tag_registry import TagRegistry


def test_migration_preserves_existing_reference_content_and_initializes_revisions(monkeypatch):
    TagRegistry().add(["migration-tag"])
    tag = TagRegistry().all()[0]
    migration = Path(__file__).parents[1] / "alembic/versions/065b1c2d3e4f_reference_editing.py"
    assert migration.exists()
    loader = importlib.util.spec_from_file_location("reference_migration", migration)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    engine = get_engine()
    ReferenceResourceState.__table__.drop(engine)
    with engine.begin() as conn:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(conn)))
        module.upgrade()
        rows = conn.execute(select(ReferenceResourceState.__table__)).mappings().all()
        assert any(f'"tag","{tag["id"]}"' in row["resource_key"] for row in rows)
        assert all(row["revision"] == 1 and row["lease_epoch"] == 0 for row in rows)
        assert "reference_resource_states" in inspect(conn).get_table_names()
    assert TagRegistry().all()[0]["name"] == "migration-tag"


def test_migration_stops_before_schema_changes_when_global_attributes_are_duplicated(monkeypatch):
    from app.db.models import AttributeValue
    from app.db.session import session_scope
    migration = Path(__file__).parents[1] / "alembic/versions/065b1c2d3e4f_reference_editing.py"
    loader = importlib.util.spec_from_file_location("reference_migration_duplicate", migration)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    engine = get_engine()
    ReferenceResourceState.__table__.drop(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX uq_attribute_values_global"))
    with session_scope() as session:
        session.add_all([AttributeValue(attribute_key="module", value="duplicate", org_id=None) for _ in range(2)])
    with engine.begin() as connection:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError, match="Duplicate global attributes"):
            module.upgrade()
        assert "reference_resource_states" not in inspect(connection).get_table_names()
        assert connection.execute(text("SELECT count(*) FROM attribute_values")).scalar_one() == 2
