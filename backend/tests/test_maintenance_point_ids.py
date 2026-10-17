"""Exact temporary point membership without keeping the corpus in a Python set."""
import sqlite3
import pytest
from app.services.maintenance_point_ids import disk_point_ids


def test_disk_membership_deduplicates_and_keeps_exact_string_keys():
 with disk_point_ids(iter(['1','001','a','a','0',''])) as ids:
  assert all(key in ids for key in ('1','001','a','0',''))
  assert 'missing' not in ids and '01' not in ids
  assert len(ids)==5
 with pytest.raises(sqlite3.ProgrammingError):
  'a' in ids


def test_generator_failure_closes_its_temporary_database(monkeypatch):
 import app.services.maintenance_point_ids as module
 original=sqlite3.connect;connections=[]
 def connect(*args,**kwargs):
  connection=original(*args,**kwargs);connections.append(connection);return connection
 monkeypatch.setattr(module.sqlite3,'connect',connect)
 def broken():
  yield 'before-failure'
  raise RuntimeError('synthetic scroll failure')
 with pytest.raises(RuntimeError,match='synthetic scroll failure'):
  with disk_point_ids(broken()):pytest.fail('Incomplete membership cannot be used')
 assert len(connections)==1
 with pytest.raises(sqlite3.ProgrammingError):connections[0].execute('SELECT 1')


def test_body_failure_still_closes_membership():
 with pytest.raises(RuntimeError,match='synthetic writer failure'):
  with disk_point_ids(str(n) for n in range(1000)) as ids:
   assert '999' in ids and '1000' not in ids
   raise RuntimeError('synthetic writer failure')
 with pytest.raises(sqlite3.ProgrammingError):
  '999' in ids
