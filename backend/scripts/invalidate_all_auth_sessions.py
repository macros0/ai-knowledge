"""Explicit DR cleanup; call only on a stopped target after deliberate key rotation."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from sqlalchemy import inspect,text
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def invalidate_sessions(engine):
    with engine.begin() as connection:
        tables=set(inspect(connection).get_table_names())
        if 'auth_sessions' not in tables:
            raise RuntimeError('Expected auth_sessions schema is missing')
        sessions=connection.execute(text('DELETE FROM auth_sessions')).rowcount
        flows=connection.execute(text('DELETE FROM oidc_login_flows')).rowcount if 'oidc_login_flows' in tables else 0
    return {'sessions':sessions,'flows':flows}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-stopped-and-key-rotated',action='store_true',required=True)
    parser.parse_args()
    from app.db.session import get_engine
    print(json.dumps(invalidate_sessions(get_engine())))

if __name__=='__main__':main()
