import pytest
from sqlalchemy import create_engine, inspect, text
from app.services.answer_scenario_schema import upgrade_answer_scenario_schema


def test_migration_is_idempotent_and_preserves_existing_tables():
    engine=create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, content TEXT)"))
        c.execute(text("INSERT INTO chat_messages VALUES(1, 'old')"))
        upgrade_answer_scenario_schema(c)
        upgrade_answer_scenario_schema(c)
        assert c.execute(text("SELECT content FROM chat_messages")).scalar_one()=="old"
        assert "answer_scenario_versions" in inspect(c).get_table_names()
        assert "alembic_version" not in inspect(c).get_table_names()


def test_incompatible_existing_schema_rejected():
    engine=create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE answer_scenarios(id TEXT PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="answer_scenarios"):
            upgrade_answer_scenario_schema(c)
