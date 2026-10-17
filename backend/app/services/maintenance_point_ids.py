"""Exact ephemeral corpus membership with a bounded SQLite pager and input batch.

Only opaque point IDs are stored; SQLite deletes its unnamed temporary database
when the connection closes, including construction and maintenance failures.
"""
from contextlib import contextmanager
from itertools import islice
import sqlite3


class _PointIds:
    def __init__(self, connection):
        self.connection = connection

    def __contains__(self, identity):
        return self.connection.execute(
            "SELECT 1 FROM point_ids WHERE id = ?", (identity,)
        ).fetchone() is not None

    def __len__(self):
        return self.connection.execute("SELECT COUNT(*) FROM point_ids").fetchone()[0]


@contextmanager
def disk_point_ids(identities):
    connection = sqlite3.connect("")
    try:
        connection.execute("PRAGMA cache_size = -1024")
        connection.execute("PRAGMA mmap_size = 0")
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA temp_store = FILE")
        connection.execute("CREATE TABLE point_ids(id TEXT PRIMARY KEY) WITHOUT ROWID")
        iterator = iter(identities)
        while batch := list(islice(iterator, 256)):
            connection.executemany(
                "INSERT OR IGNORE INTO point_ids(id) VALUES (?)",
                ((identity,) for identity in batch),
            )
            connection.commit()
        yield _PointIds(connection)
    finally:
        connection.close()
