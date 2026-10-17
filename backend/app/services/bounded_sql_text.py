"""Bound SQL text reads without losing Unicode or SQLite embedded NULs."""
from sqlalchemy import LargeBinary, cast, func


def capped_text_expression(session, column, limit):
    if session.get_bind().dialect.name == 'sqlite':
        # SQLite TEXT substr stops at NUL. UTF-8 needs at most four bytes per
        # codepoint; slice bytes in SQL and decode the bounded prefix in Python.
        return func.substr(cast(column, LargeBinary), 1, limit * 4)
    return func.substr(column, 1, limit)


def decode_capped_text(value, limit):
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='ignore')
    return value[:limit] if value is not None else None
