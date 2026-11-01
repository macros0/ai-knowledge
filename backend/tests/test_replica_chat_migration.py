"""A chat schema rollback must never lose completed execution identities."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest

from app.config import Settings
from app.db.models import ChatRun, ChatMessage
from app.db.session import get_engine, session_scope
from app.models.schemas import ChatRequest
from app.services import chat_history
from app.services.work.chat import admit_chat_run, finish_chat_run


def test_sqlite_upgrade_roundtrip_preserves_history_and_completed_run(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path,
        database_url=get_engine().url.render_as_string(hide_password=False))
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    legacy = chat_history.begin_attempt(None, user, "old question", str(uuid4()), "full")
    assert chat_history.finish_attempt(legacy, user, status="completed", answer="old answer")
    ChatRun.__table__.drop(get_engine())
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl03_replica_coordination")
    command.upgrade(config, "rpl03a_chat_runs")
    command.downgrade(config, "rpl03_replica_coordination")
    command.upgrade(config, "rpl03a_chat_runs")
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    with session_scope() as session:
        assert session.get(ChatMessage, legacy.message_id).content == "old answer"
        accepted = admit_chat_run(session, settings, ChatRequest(query="new question",
            response_mode="full", attempt_id=str(uuid4()), session_id=legacy.session_id), user, owner_id="api-a")
        finish_chat_run(session, accepted, user, status="completed", answer="new answer")
    with pytest.raises(RuntimeError, match="chat runs"):
        command.downgrade(config, "rpl03_replica_coordination")
    with session_scope() as session:
        assert session.get(ChatMessage, accepted.ref.message_id).content == "new answer"
        assert session.get(ChatMessage, legacy.message_id).content == "old answer"
