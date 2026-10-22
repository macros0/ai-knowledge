"""Idempotent dev companion; does not advance the database's Alembic revision."""
import argparse
from sqlalchemy import inspect
from app.db.session import get_engine
from app.services.answer_scenario_schema import upgrade_answer_scenario_schema


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    engine=get_engine()
    if not args.apply:
        print("Dry run. Existing scenario tables:", [name for name in inspect(engine).get_table_names() if name.startswith("answer_scenario")])
        return
    with engine.begin() as connection:
        upgrade_answer_scenario_schema(connection)
    print("Scenario schema ready; Alembic revision unchanged.")


if __name__=="__main__":
    main()
