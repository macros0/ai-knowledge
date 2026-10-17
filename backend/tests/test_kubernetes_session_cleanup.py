import importlib.util
from pathlib import Path
from sqlalchemy import create_engine,text
import pytest

@pytest.fixture
def cleanup():
    source=Path(__file__).parents[1]/'scripts/invalidate_all_auth_sessions.py'
    spec=importlib.util.spec_from_file_location('dr_sessions',source)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m.invalidate_sessions

@pytest.mark.parametrize('persisted_flows',[False,True])
def test_dr_clears_all_sessions_and_persisted_flows_when_present(cleanup,persisted_flows):
    e=create_engine('sqlite://')
    with e.begin() as c:
        c.execute(text('CREATE TABLE auth_sessions (id TEXT PRIMARY KEY)'))
        c.execute(text("INSERT INTO auth_sessions VALUES ('old-cookie')"))
        if persisted_flows:
            c.execute(text('CREATE TABLE oidc_login_flows (id TEXT PRIMARY KEY)'))
            c.execute(text("INSERT INTO oidc_login_flows VALUES ('old-flow')"))
    result=cleanup(e)
    with e.connect() as c:
        assert c.execute(text('SELECT count(*) FROM auth_sessions')).scalar()==0
        if persisted_flows:assert c.execute(text('SELECT count(*) FROM oidc_login_flows')).scalar()==0
    assert result=={'sessions':1,'flows':int(persisted_flows)}

def test_unknown_session_schema_refuses_cleanup(cleanup):
    e=create_engine('sqlite://')
    with pytest.raises(RuntimeError,match='auth_sessions'):cleanup(e)
