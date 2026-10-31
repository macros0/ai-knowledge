from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.base import Base


def test_alembic_upgrade_nonempty_legacy_database_preserves_document(tmp_path, monkeypatch):
    database = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    new_tables = {"artifact_objects", "artifact_bindings", "upload_receipts", "artifact_read_leases", "artifact_checkpoints"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in new_tables])
    from app.db.models import Document
    with Session(engine) as session:
        session.add(Document(id="0123456789abcdef", filename="legacy.pdf"))
        session.commit()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    from app.config import get_settings
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl00_diagnostic_ingest")
    command.upgrade(config, "rpl01_artifact_objects")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO artifact_objects(object_id,key,sha256,size,media_type,store_id,created_at) "
                                "VALUES(:id,:key,:hash,0,'application/pdf','local',CURRENT_TIMESTAMP)"),
                           {"id": "a" * 32, "key": "objects/" + "a" * 32, "hash": "b" * 64})
        connection.execute(text("INSERT INTO artifact_bindings(owner_type,owner_id,generation_id,logical_name,object_id,doc_id) "
                                "VALUES('original','0123456789abcdef','','original',:object,'0123456789abcdef')"), {"object": "a" * 32})
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT filename FROM documents")).scalar_one() == "legacy.pdf"
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "rpl01b_artifact_lifecycle"
        assert new_tables <= set(inspect(connection).get_table_names())
        assert connection.execute(text("SELECT count(*) FROM artifact_bindings")).scalar_one() == 1
        assert connection.execute(text("SELECT state FROM artifact_objects")).scalar_one() == "ready"
    engine.dispose()
