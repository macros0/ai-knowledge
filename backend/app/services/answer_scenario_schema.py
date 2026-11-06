"""Add only scenario tables; reject incomplete pre-existing schemas."""
from sqlalchemy import inspect
from app.db.models import AnswerScenario, AnswerScenarioVersion


def upgrade_answer_scenario_schema(connection) -> None:
    for table in (AnswerScenario.__table__,AnswerScenarioVersion.__table__):
        inspector=inspect(connection)
        if inspector.has_table(table.name):
            columns={c["name"]:c for c in inspector.get_columns(table.name)}
            expected={c.name for c in table.columns}
            pk=set(inspector.get_pk_constraint(table.name)["constrained_columns"])
            if not expected.issubset(columns) or pk!={c.name for c in table.primary_key}:
                raise RuntimeError(f"Incompatible schema: {table.name}")
            for column in table.columns:
                actual=columns[column.name]
                if actual["type"]._type_affinity is not column.type._type_affinity or (not column.nullable and actual["nullable"] and column.name not in pk):
                    raise RuntimeError(f"Incompatible schema: {table.name}.{column.name}")
        else:
            table.create(connection)
        for index in table.indexes:
            index.create(connection,checkfirst=True)
