from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from tests.test_schema_drift import migrated_db_url as shared_migrated_db_url

migrated_db_url = shared_migrated_db_url


@pytest.mark.parametrize("policy", ["legacy", "review", "generation"])
def test_scope_downgrade_preserves_policy_or_legacy_data(migrated_db_url, policy):
    from alembic import command
    from alembic.config import Config
    from app.db.models import Document
    from app.services.generation_store import begin_generation
    from app.services.processing_scope import automatic_snapshot, create_review
    from docparser.attachment_policy import AttachmentInventory

    engine = create_engine(migrated_db_url)
    try:
        with Session(engine) as session, session.begin():
            session.add(Document(id="0123456789abcdef", filename="legacy.docx", status="done"))
            session.flush()
            inventory = AttachmentInventory(source_file_hash="a" * 64)
            if policy == "review":
                create_review(
                    session, "0123456789abcdef", inventory, base_generation_id=None, actor="test"
                )
            else:
                generation = begin_generation(session, "0123456789abcdef")
                if policy == "generation":
                    generation.processing_scope_json = automatic_snapshot(inventory)
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        if policy == "legacy":
            command.downgrade(config, "062b1c2d3e4f")
            assert "document_processing_reviews" not in inspect(engine).get_table_names()
            with engine.connect() as connection:
                from sqlalchemy import text

                assert connection.scalar(text("SELECT status FROM documents")) == "done"
        else:
            with pytest.raises(RuntimeError, match="Processing"):
                command.downgrade(config, "062b1c2d3e4f")
            assert "document_processing_reviews" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
