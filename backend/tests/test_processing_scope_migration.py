from pathlib import Path
from datetime import UTC, datetime

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

from tests.test_schema_drift import migrated_db_url as shared_migrated_db_url

migrated_db_url = shared_migrated_db_url


# Scope downgrade policy is tested before the deliberately irreversible audit-v2 revision.
@pytest.mark.parametrize("migrated_db_url", ["066b1c2d3e4f"], indirect=True)
@pytest.mark.parametrize("policy", ["legacy", "review", "generation"])
def test_scope_downgrade_preserves_policy_or_legacy_data(migrated_db_url, policy):
    from alembic import command
    from alembic.config import Config
    from app.services.processing_scope import automatic_snapshot
    from docparser.attachment_policy import AttachmentInventory

    engine = create_engine(migrated_db_url)
    try:
        with engine.begin() as connection:
            # The fixture is deliberately historical. Current ORM writes also
            # require newer storage/work columns absent from revision 066.
            metadata = MetaData()
            documents = Table("documents", metadata, autoload_with=connection)
            generations = Table("document_generations", metadata, autoload_with=connection)
            reviews = Table("document_processing_reviews", metadata, autoload_with=connection)
            now = datetime(2026, 11, 2, tzinfo=UTC)
            doc_id = "0123456789abcdef"
            connection.execute(documents.insert().values(
                id=doc_id, filename="legacy.docx", content_type="test/document", size=0,
                status="done", total_chunks=0, processed_chunks=0, okf_concept_count=0,
                created_at=now, updated_at=now,
            ))
            inventory = AttachmentInventory(source_file_hash="a" * 64)
            snapshot = automatic_snapshot(inventory)
            if policy == "review":
                connection.execute(reviews.insert().values(
                    doc_id=doc_id, id="b" * 32, revision=1, state="draft",
                    source_file_hash=snapshot["source_file_hash"], parser_version=snapshot["parser_version"],
                    assessor_version=snapshot["assessor_version"], policy_json=snapshot["policy"],
                    inventory_json=snapshot["inventory"], selection_json=snapshot["decisions"],
                    selection_hash=snapshot["selection_hash"], created_by="test", created_at=now, updated_at=now,
                ))
            else:
                connection.execute(generations.insert().values(
                    id="c" * 32, doc_id=doc_id, phase="preparing", legacy_cleanup_pending=False,
                    created_at=now, processing_scope_json=snapshot if policy == "generation" else None,
                ))
            policy_table = reviews if policy == "review" else generations
            before = connection.execute(select(policy_table)).mappings().all()
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        if policy == "legacy":
            command.downgrade(config, "062b1c2d3e4f")
            assert "document_processing_reviews" not in inspect(engine).get_table_names()
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT status FROM documents")) == "done"
        else:
            with pytest.raises(RuntimeError, match="Processing"):
                command.downgrade(config, "062b1c2d3e4f")
            assert "document_processing_reviews" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            after = Table(policy_table.name, MetaData(), autoload_with=connection)
            if policy != "legacy":
                assert set(after.c.keys()) == set(policy_table.c.keys())
            # An accepted legacy downgrade preserves its generation; a refused
            # downgrade preserves the full review/scope policy without edits.
            rows = connection.execute(select(*(after.c[name] for name in policy_table.c.keys()
                                               if name in after.c))).mappings().all()
            expected = [{key: value for key, value in row.items() if key in after.c} for row in before]
            assert [dict(row) for row in rows] == expected
    finally:
        engine.dispose()
