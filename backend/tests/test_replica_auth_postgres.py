"""Server-side sessions and revocation remain authoritative on another API."""
from multiprocessing import get_context
from pathlib import Path

from app.config import Settings
from app.auth.identity import AuthenticatedIdentity
from app.db.session import configure_database, get_engine, session_scope
from app.db.models import AuthSession
from tests.support.replica_postgres import disposable_postgres
from tests.test_replica_auth import request


def _settings(root):
    settings = Settings(_env_file=None, data_dir=root, auth_provider="simulation")
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    return settings


def _auth_reader(url, root, session_data, pipe):
    import app.config
    from app.auth.service import require_user
    from app.api.errors import ApiError
    app.config.get_settings = lambda: _settings(Path(root))
    configure_database(url)
    incoming = request(session_data)
    try:
        pipe.send("ready")
        while pipe.poll(30):
            if pipe.recv() == "stop":
                return
            try:
                actor = require_user(incoming)
            except ApiError as exc:
                pipe.send((exc.status_code, exc.code))
            else:
                pipe.send((200, tuple(actor.roles)))
        raise RuntimeError("Auth acceptance control timed out")
    finally:
        get_engine().dispose()
        pipe.close()


def test_revocation_visible_on_second_api_process(tmp_path, monkeypatch):
    from app.auth.service import store_identity, clear_identity
    from app.services.blocklist import Blocklist
    with disposable_postgres() as url:
        settings = _settings(tmp_path / "api-a")
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        login = request()
        store_identity(login, AuthenticatedIdentity(external_id="actor", username="actor",
            groups=["KB_Viewer"], provider="simulation"))
        context = get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_auth_reader,
            args=(url, str(tmp_path / "api-b"), dict(login.session), child))
        process.start()
        child.close()
        def probe():
            parent.send("probe")
            assert parent.poll(15)
            return parent.recv()
        try:
            assert parent.poll(25) and parent.recv() == "ready"
            assert probe() == (200, ("viewer",))
            Blocklist().block("actor", blocked_by="security")
            assert probe()[0] == 403
            Blocklist().unblock("actor")
            assert probe() == (200, ("viewer",))
            with session_scope() as session:
                row = session.query(AuthSession).one()
                row.identity = {**row.identity, "groups": ["KB_Editor"]}
            assert probe() == (200, ("editor",))
            with session_scope() as session:
                row = session.query(AuthSession).one()
                row.identity = {**row.identity, "groups": []}
            assert probe()[0] == 403
            clear_identity(login)
            assert probe()[0] == 401
        finally:
            if process.is_alive():
                parent.send("stop")
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()
        assert process.exitcode == 0
