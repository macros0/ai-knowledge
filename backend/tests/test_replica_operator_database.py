"""Upgrade operator transport is separate from signed application settings."""
import importlib.util
import os
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from tests.test_replica_restore_operator import APP_URL, OP_URL


def resolve(settings, path):
    assert importlib.util.find_spec('app.deployment.operator_database') is not None, 'Shared upgrade operator transport missing'
    from app.deployment.operator_database import operator_database_url
    return operator_database_url(settings, path)


def test_projected_operator_url_preserves_scoped_settings(tmp_path):
    path = tmp_path / 'url'
    path.write_text(OP_URL + '\n')
    settings = SimpleNamespace(db_url=APP_URL)
    assert resolve(settings, path) == OP_URL
    assert resolve(settings, None) == settings.db_url == APP_URL


@pytest.mark.parametrize('change', [
    {'host': 'foreign'}, {'database': 'foreign'}, {'port': 5433}, {'port': 0},
    {'drivername': 'postgresql+psycopg2'}, {'username': None}, {'password': None},
    {'query': {'sslmode': 'disable', 'sslrootcert': '/run/ca.pem'}},
    {'query': {'sslmode': 'verify-full', 'sslrootcert': '/run/foreign.pem'}},
    {'query': {'sslmode': 'verify-full', 'sslrootcert': '/run/ca.pem', 'options': '-csearch_path=foreign'}},
])
def test_operator_url_cannot_change_selected_transport(tmp_path, change):
    path = tmp_path / 'url'
    path.write_text(make_url(OP_URL)._replace(**change).render_as_string(hide_password=False))
    with pytest.raises(ValueError):
        resolve(SimpleNamespace(db_url=APP_URL), path)


@pytest.mark.parametrize('raw', [b'', b'x' * 16385, b'\xff', b'postgresql://bad\x00input', b'not a URL'],
    ids=['empty', 'oversize', 'utf8', 'nul', 'invalid-url'])
def test_invalid_operator_material_is_refused_without_secret_output(tmp_path, raw):
    path = tmp_path / 'url'
    path.write_bytes(raw)
    with pytest.raises(ValueError) as error:
        resolve(SimpleNamespace(db_url=APP_URL), path)
    assert 'OPERATOR_CANARY' not in str(error.value)


@pytest.mark.skipif(os.name == 'nt', reason='Projected Secret symlinks qualified on Linux')
def test_upgrade_operator_projected_symlink(tmp_path):
    path, material = tmp_path / 'url', tmp_path / 'generation'
    material.write_text(OP_URL)
    path.symlink_to(material)
    assert resolve(SimpleNamespace(db_url=APP_URL), path) == OP_URL


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='FIFO refusal qualified on Linux')
def test_upgrade_operator_fifo_refused_without_writer(tmp_path):
    path = tmp_path / 'url'
    os.mkfifo(path)
    with pytest.raises(ValueError):
        resolve(SimpleNamespace(db_url=APP_URL), path)
