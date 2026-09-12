from sqlalchemy import Column, Integer, MetaData, Table, create_engine

from scripts.migrate_glossary import inspect_schema, migrate


def test_dev_companion_is_check_only_by_default_and_apply_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("chat_messages", metadata, Column("id", Integer, primary_key=True)).create(engine)

    before = inspect_schema(engine)
    assert before["missing_tables"]
    assert before["missing_history_column"] is True

    after = migrate(engine, apply=True)
    assert after == {
        "missing_tables": [],
        "missing_columns": {},
        "missing_constraints": {},
        "missing_history_column": False,
    }
    assert migrate(engine, apply=True) == after
