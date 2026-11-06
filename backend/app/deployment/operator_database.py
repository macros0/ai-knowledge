"""Bounded offline inputs and target-bound SQL credentials for current operators.

The standalone recovery tool retains its copy of this transport contract because
saved older images do not contain this module. Ordinary roles never call it.
"""
import os
import stat


def read_regular_input(path, *, max_bytes):
    """Follow projected-Secret links, but never wait for a FIFO writer."""
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('A bounded regular offline input is required')
        raw = stream.read(max_bytes + 1)
    if not raw or len(raw) > max_bytes:
        raise ValueError('A bounded regular offline input is required')
    return raw


def operator_database_url(settings, path):
    """Only the operator principal may differ from the signed application URL."""
    if path is None:
        return settings.db_url
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError
    try:
        value = read_regular_input(path, max_bytes=16384).decode('utf-8').strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError()
        expected, actual = make_url(settings.db_url), make_url(value)
        if (expected.drivername not in {'postgresql', 'postgresql+psycopg'}
                or actual.drivername != expected.drivername or not actual.username or not actual.password
                or not expected.host or not expected.database
                or expected.port is not None and not 1 <= expected.port <= 65535
                or actual.host != expected.host or actual.database != expected.database
                or (5432 if actual.port is None else actual.port) != (5432 if expected.port is None else expected.port)
                or actual.query != expected.query
                or expected.query.get('sslmode') != 'verify-full' or not expected.query.get('sslrootcert')):
            raise ValueError()
        return value
    except (OSError, ValueError, UnicodeError, ArgumentError):
        raise ValueError('Operator database file does not match the verified application target') from None
